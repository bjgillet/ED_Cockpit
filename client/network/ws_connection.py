"""
ED Assist — WebSocket Connection Manager (Client)
===================================================
Manages the asyncio WebSocket connection from the client to the agent:
TLS handshake, authentication, automatic reconnection, and message routing.

Responsibilities
----------------
  • Connect to the agent via WebSocket (optionally TLS-wrapped).
  • On connect: send a ``RegisterMessage`` and wait for ``WelcomeMessage``
    or ``ErrorMessage``.
  • Parse every inbound message and call ``on_message(dict)``.
  • Serialise and send outbound messages (thread-safe via a queue).
  • Implement exponential-backoff reconnection on unexpected disconnection.
  • On TLS: verify the agent's certificate fingerprint on first connect
    (Trust On First Use) and pin it for all subsequent connects.

Architecture
------------
  ``WSConnection`` runs as a coroutine (``run()``) inside a dedicated
  asyncio event loop on a background daemon thread.  It is never run in
  the tkinter main thread.

  Outbound messages are pushed via ``send_nowait(msg)`` (thread-safe) which
  puts the dict onto an asyncio Queue.  A second coroutine drains that
  queue and sends on the socket.

Dependencies
------------
  pip install websockets
"""
from __future__ import annotations

import asyncio
import json
import logging
import ssl
from typing import Callable, Optional

try:
    import websockets
    _WS_OK = True
except ImportError:
    _WS_OK = False

from shared.messages import message_from_json, RegisterMessage, WelcomeMessage, ErrorMessage

log = logging.getLogger(__name__)


class WSConnection:
    """
    Asyncio WebSocket connection manager for the ED Client.

    Parameters
    ----------
    host : str
        Agent hostname or IP.
    port : int
        Agent WebSocket port.
    client_id : str
        This client's ID (from config).
    token : str
        Pre-shared authentication token (from config).
    proposed_roles : list[str]
        Roles to propose if this client is not yet known to the agent.
    on_message : callable(dict)
        Called for every inbound message dict (parsed from JSON).
        Invoked from the asyncio thread — caller must queue-dispatch to
        the tkinter thread if needed.
    on_connect : callable(assigned_roles: list[str]), optional
        Called after a successful welcome handshake.
    on_disconnect : callable(), optional
        Called when the connection drops (before reconnect attempt).
    ssl_context : ssl.SSLContext, optional
        If provided, wraps the connection in TLS.
    """

    RECONNECT_DELAY_MIN: float = 2.0
    RECONNECT_DELAY_MAX: float = 60.0
    HANDSHAKE_TIMEOUT:   float = 10.0

    def __init__(
        self,
        host:                str,
        port:                int,
        client_id:           str,
        token:               str,
        proposed_roles:      list[str],
        on_message:          Callable[[dict], None],
        on_connect:          Optional[Callable[[list[str]], None]] = None,
        on_disconnect:       Optional[Callable[[], None]] = None,
        ssl_context:         Optional[ssl.SSLContext] = None,
        pinned_fingerprint:  Optional[str] = None,
    ) -> None:
        if not _WS_OK:
            raise RuntimeError(
                "websockets is required.  Install with:  pip install websockets"
            )
        self.host               = host
        self.port               = port
        self._client_id         = client_id
        self._token             = token
        self._proposed_roles    = proposed_roles
        self._on_message        = on_message
        self._on_connect        = on_connect
        self._on_disconnect     = on_disconnect
        self._ssl               = ssl_context
        self._pinned_fingerprint = pinned_fingerprint

        self._loop:       Optional[asyncio.AbstractEventLoop] = None
        self._stop_event: Optional[asyncio.Event]             = None
        self._send_queue: Optional[asyncio.Queue]             = None
        self._ws          = None   # active websockets connection

    # ── Thread-safe outbound send ──────────────────────────────────────────

    def send_nowait(self, message: dict) -> None:
        """
        Queue a message dict for sending to the agent.

        Thread-safe: can be called from any thread, including the tkinter
        main thread.  The message is serialised and sent by the asyncio loop.
        """
        if self._loop is None or self._send_queue is None:
            return
        self._loop.call_soon_threadsafe(self._send_queue.put_nowait, message)

    def request_stop(self) -> None:
        """Signal the run loop to stop cleanly.  Thread-safe."""
        if self._loop and self._stop_event:
            self._loop.call_soon_threadsafe(self._stop_event.set)

    # ── Main coroutine (runs on background thread) ─────────────────────────

    async def run(self) -> None:
        """
        Connect, maintain, and reconnect the WebSocket until stopped.

        Run this coroutine with ``asyncio.run()`` on a dedicated background
        thread.
        """
        self._loop       = asyncio.get_running_loop()
        self._stop_event = asyncio.Event()
        self._send_queue = asyncio.Queue()

        delay = self.RECONNECT_DELAY_MIN

        while not self._stop_event.is_set():
            try:
                await self._connect_once()
                delay = self.RECONNECT_DELAY_MIN   # reset backoff on success
            except Exception as exc:
                log.warning("WSConnection error: %s — retrying in %.0fs", exc, delay)

            if self._on_disconnect:
                try:
                    self._on_disconnect()
                except Exception:
                    pass

            # Wait before retry, but abort immediately if stop is requested
            try:
                await asyncio.wait_for(
                    asyncio.shield(self._stop_event.wait()),
                    timeout=delay,
                )
                break   # stop was requested during the wait
            except asyncio.TimeoutError:
                pass   # timeout elapsed — retry

            delay = min(delay * 2, self.RECONNECT_DELAY_MAX)

    async def _connect_once(self) -> None:
        """Open one connection, handshake, then run the send/receive loops."""
        uri = f"{'wss' if self._ssl else 'ws'}://{self.host}:{self.port}"
        log.debug("Connecting to %s …", uri)

        async with websockets.connect(uri, ssl=self._ssl) as ws:
            self._ws = ws

            # ── TOFU fingerprint check ────────────────────────────────────
            if self._ssl and self._pinned_fingerprint:
                try:
                    ssl_obj = ws.transport.get_extra_info("ssl_object")
                    if ssl_obj is not None:
                        from agent.security.tls_setup import fingerprint_from_der
                        der = ssl_obj.getpeercert(binary_form=True)
                        if der:
                            actual_fp = fingerprint_from_der(der)
                            if actual_fp != self._pinned_fingerprint:
                                log.error(
                                    "TLS fingerprint mismatch!\n"
                                    "  Expected : %s\n"
                                    "  Got      : %s\n"
                                    "Closing connection.",
                                    self._pinned_fingerprint, actual_fp
                                )
                                await ws.close(1008, "fingerprint mismatch")
                                return
                            log.debug("TLS fingerprint verified: %s", actual_fp)
                except Exception as exc:
                    log.warning("Could not verify TLS fingerprint: %s", exc)

            # ── Handshake ─────────────────────────────────────────────────
            assigned_roles = await self._do_handshake(ws)
            if assigned_roles is None:
                return   # handshake failed — will reconnect

            if self._on_connect:
                try:
                    self._on_connect(assigned_roles)
                except Exception:
                    pass

            # ── Run send + receive concurrently ───────────────────────────
            await asyncio.gather(
                self._receive_loop(ws),
                self._send_loop(ws),
                return_exceptions=True,
            )

    async def _do_handshake(self, ws) -> Optional[list[str]]:
        """
        Send RegisterMessage, wait for WelcomeMessage or ErrorMessage.

        Returns the assigned role list on success, or None on failure.
        """
        reg = RegisterMessage(
            client_id      = self._client_id,
            token          = self._token,
            proposed_roles = self._proposed_roles,
        )
        await ws.send(reg.to_json())

        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=self.HANDSHAKE_TIMEOUT)
        except asyncio.TimeoutError:
            log.warning("Handshake timed out waiting for WelcomeMessage.")
            return None

        try:
            msg = message_from_json(raw)
        except ValueError:
            log.warning("Handshake: unparseable response.")
            return None

        if isinstance(msg, WelcomeMessage):
            log.info("Handshake OK — assigned roles: %s", msg.assigned_roles)
            return msg.assigned_roles

        if isinstance(msg, ErrorMessage):
            log.error("Handshake rejected by agent: [%s] %s", msg.code, msg.message)
            return None

        log.warning("Handshake: unexpected message type %r", msg.type)
        return None

    async def _receive_loop(self, ws) -> None:
        """Receive and dispatch inbound messages until the connection closes."""
        async for raw in ws:
            if self._stop_event.is_set():
                break
            try:
                data = json.loads(raw)
                self._on_message(data)
            except Exception:
                pass

    async def _send_loop(self, ws) -> None:
        """Drain the outbound queue and send messages until stopped."""
        while not self._stop_event.is_set():
            try:
                msg = await asyncio.wait_for(
                    self._send_queue.get(), timeout=0.5
                )
                await ws.send(json.dumps(msg))
            except asyncio.TimeoutError:
                continue
            except Exception:
                break
