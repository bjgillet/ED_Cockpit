"""
ED Cockpit — Client Core
=========================
Central client object.  Owns the WebSocket connection, drives the
register → welcome handshake, and dispatches incoming EventMessages to
per-role subscriber queues consumed by the GUI panels.

Mirrors the role of ``EDApp`` on the agent side.

Responsibilities
----------------
  • Own the WSConnection and run it on a background asyncio thread.
  • On WelcomeMessage: store assigned roles and notify GUI subscribers.
  • On EventMessage: route to the queue(s) of the matching role panel.
  • On RolesUpdatedMessage: update assigned roles and notify GUI subscribers.
  • Expose ``subscribe_role(role)`` / ``unsubscribe_role(role)`` so panels
    receive events without knowing about the network layer.
  • Expose ``send_action(action, key)`` so panels can trigger key presses.
  • Expose ``subscribe_status()``: a single queue that receives connection
    status updates so the GUI can show "connecting…" / "connected" / etc.

Thread model
------------
  Main thread    : tkinter mainloop
  Asyncio thread : WSConnection event loop (ED-ClientLoop)

  All callbacks from the asyncio thread → tkinter arrive through
  ``queue.Queue`` objects drained by ``after()`` in the GUI — same pattern
  as the agent side.

Connection status values delivered to the status queue
-------------------------------------------------------
  {"status": "connecting"}
  {"status": "connected",  "roles": [...]}
  {"status": "disconnected"}
  {"status": "auth_failed", "message": "..."}
"""
from __future__ import annotations

import asyncio
import logging
import queue
import threading
from typing import Callable, Optional

from client.core.config import ClientConfig
from client.network.ws_connection import WSConnection
from shared.messages import (
    EventMessage, RolesUpdatedMessage, ErrorMessage,
    ActionMessage, message_from_dict,
    compute_action_hmac,
)

log = logging.getLogger(__name__)


class EDClient:
    """
    Central client core.

    Parameters
    ----------
    config : ClientConfig, optional
        If omitted a default config is loaded from the standard location.
    """

    def __init__(self, config: Optional[ClientConfig] = None) -> None:
        self.config           = config or ClientConfig()
        self._lock            = threading.Lock()

        # per-role event queues — panel → queue.Queue
        self._role_queues:    dict[str, list[queue.Queue]] = {}
        self._assigned_roles: list[str] = []

        # status queue — consumed by the connection-status indicator
        self._status_queues:  list[queue.Queue] = []

        # action sequence counter
        self._seq:            int = 0

        # asyncio layer
        self._loop:   Optional[asyncio.AbstractEventLoop] = None
        self._ws:     Optional[WSConnection] = None
        self._thread: Optional[threading.Thread] = None

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the WebSocket connection thread."""
        self._thread = threading.Thread(
            target=self._run_asyncio_loop,
            name="ED-ClientLoop",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """Gracefully disconnect and stop the background thread."""
        if self._ws:
            self._ws.request_stop()
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)

    # ── GUI observer API ───────────────────────────────────────────────────

    def subscribe_role(self, role: str) -> queue.Queue:
        """
        Register a panel as a consumer for events of ``role``.

        Returns a ``queue.Queue`` that receives dicts:
            {"event": "<event_name>", "data": {...}}
        """
        q: queue.Queue = queue.Queue()
        with self._lock:
            self._role_queues.setdefault(role, []).append(q)
        return q

    def unsubscribe_role(self, role: str, q: queue.Queue) -> None:
        with self._lock:
            queues = self._role_queues.get(role, [])
            try:
                queues.remove(q)
            except ValueError:
                pass

    def subscribe_status(self) -> queue.Queue:
        """
        Register a consumer for connection status updates.

        Returns a ``queue.Queue`` that receives status dicts (see module
        docstring for values).
        """
        q: queue.Queue = queue.Queue()
        with self._lock:
            self._status_queues.append(q)
        return q

    def unsubscribe_status(self, q: queue.Queue) -> None:
        with self._lock:
            try:
                self._status_queues.remove(q)
            except ValueError:
                pass

    @property
    def assigned_roles(self) -> list[str]:
        with self._lock:
            return list(self._assigned_roles)

    # ── Actions ────────────────────────────────────────────────────────────

    def send_action(self, action: str, key: str) -> None:
        """
        Send a signed ActionMessage to the agent.

        Computes the HMAC using the client's token and an incrementing
        sequence number for replay protection.

        Safe to call from any thread.
        """
        if self._ws is None:
            return
        with self._lock:
            self._seq += 1
            seq = self._seq
        hmac_val = compute_action_hmac(
            self.config.client_id, seq, action, key, self.config.token
        )
        msg = ActionMessage(action=action, key=key, seq=seq, hmac=hmac_val)
        self._ws.send_nowait(msg.to_dict())

    # ── Internal: asyncio thread ───────────────────────────────────────────

    def _run_asyncio_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            loop.run_until_complete(self._run_connection())
        finally:
            loop.close()

    async def _run_connection(self) -> None:
        self._push_status({"status": "connecting"})

        # ── Build TLS context if enabled ──────────────────────────────────
        ssl_ctx = None
        pinned_fp = None
        if self.config.tls_enabled:
            from agent.security.tls_setup import build_client_ssl_context
            ssl_ctx = build_client_ssl_context(
                pinned_fingerprint = self.config.cert_fingerprint or None,
                ca_cert_path       = self.config.resolved_ca_cert_path(),
            )
            pinned_fp = self.config.cert_fingerprint or None

        self._ws = WSConnection(
            host                = self.config.agent_host,
            port                = self.config.agent_port,
            client_id           = self.config.client_id,
            token               = self.config.token,
            proposed_roles      = [],
            on_message          = self._on_message,
            on_connect          = self._on_connected,
            on_disconnect       = self._on_disconnected,
            ssl_context         = ssl_ctx,
            pinned_fingerprint  = pinned_fp,
        )
        await self._ws.run()

    # ── Callbacks (called from asyncio thread) ────────────────────────────

    def _on_connected(self, assigned_roles: list[str]) -> None:
        with self._lock:
            self._assigned_roles = list(assigned_roles)
        self._push_status({"status": "connected", "roles": assigned_roles})
        log.info("EDClient connected — roles: %s", assigned_roles)

        # TOFU: if no fingerprint is pinned yet, extract it from the active
        # connection and persist it so future connects are verified
        if self.config.tls_enabled and not self.config.cert_fingerprint:
            self._try_pin_fingerprint()

    def _on_disconnected(self) -> None:
        self._push_status({"status": "disconnected"})
        log.info("EDClient disconnected — will reconnect.")

    def _on_message(self, data: dict) -> None:
        """Dispatch a raw inbound message dict to the correct handler."""
        try:
            msg = message_from_dict(data)
        except ValueError:
            return

        if isinstance(msg, EventMessage):
            self._dispatch_event(msg.role, msg.event, msg.data)

        elif isinstance(msg, RolesUpdatedMessage):
            with self._lock:
                self._assigned_roles = list(msg.assigned_roles)
            self._push_status({
                "status": "roles_updated",
                "roles":  msg.assigned_roles,
            })
            log.info("Roles updated by agent: %s", msg.assigned_roles)

        elif isinstance(msg, ErrorMessage):
            log.warning("Error from agent: [%s] %s", msg.code, msg.message)
            if msg.fatal:
                self._push_status({
                    "status":  "auth_failed",
                    "message": msg.message,
                })

    def _try_pin_fingerprint(self) -> None:
        """
        Extract the server certificate fingerprint from the active TLS
        connection and persist it (TOFU — Trust On First Use).

        Called from the asyncio thread immediately after a successful
        first-time connection.  Safe to call even if the connection uses
        no TLS or the fingerprint is already pinned.
        """
        if self._ws is None or self._ws._ws is None:
            return
        try:
            ssl_obj = self._ws._ws.transport.get_extra_info("ssl_object")
            if ssl_obj is None:
                return
            der = ssl_obj.getpeercert(binary_form=True)
            if not der:
                return
            from agent.security.tls_setup import fingerprint_from_der
            fp = fingerprint_from_der(der)
            self.config.pin_fingerprint(fp)
            log.info("TLS fingerprint pinned (TOFU): %s", fp)
            self._push_status({"status": "cert_pinned", "fingerprint": fp})
        except Exception as exc:
            log.warning("TOFU fingerprint pinning failed: %s", exc)

    # ── Internal dispatch ─────────────────────────────────────────────────

    def _dispatch_event(self, role: str, event: str, data: dict) -> None:
        with self._lock:
            queues = list(self._role_queues.get(role, []))
        payload = {"event": event, "data": data}
        for q in queues:
            q.put(payload)

    def _push_status(self, status: dict) -> None:
        with self._lock:
            queues = list(self._status_queues)
        for q in queues:
            q.put(status)
