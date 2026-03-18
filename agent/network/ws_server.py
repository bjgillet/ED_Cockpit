"""
ED Cockpit — Agent WebSocket Server
====================================
Asyncio-based WebSocket server that accepts client connections, drives the
authentication handshake, and routes messages between the agent core and
connected clients.

Responsibilities
----------------
  • Listen for incoming WebSocket connections (optionally TLS-wrapped).
  • Enforce the register → welcome handshake on every new connection.
  • Maintain the set of authenticated, active client connections.
  • Fan out EventMessages to connected clients whose assigned roles match.
  • Receive ActionMessages from clients, verify their HMAC + sequence number,
    and forward valid actions to the ActionHandler.
  • Push RolesUpdatedMessages when the operator changes role assignments.
  • Handle graceful and unexpected disconnections cleanly.

Handshake protocol
------------------
  1. Client connects (TLS handshake if ssl_context is provided).
  2. Agent waits up to HANDSHAKE_TIMEOUT seconds for a RegisterMessage.
  3. Agent validates client_id + token against ClientRegistry.
     a. If unknown client_id and proposed_roles provided → create record.
     b. If unknown client_id and no proposed_roles → reject (auth_failed).
     c. If known client_id but wrong token → reject (auth_failed).
  4. Agent sends WelcomeMessage with assigned roles.
  5. Normal bidirectional message flow begins.

Thread safety
-------------
  The server runs entirely inside one asyncio event loop on a background
  thread.  ``broadcast()`` is designed to be called from that same loop via
  ``loop.call_soon_threadsafe()``.  It must never be awaited from outside
  the loop — use ``asyncio.run_coroutine_threadsafe()`` instead.

Dependencies
------------
  pip install websockets
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Callable, Optional

try:
    import websockets
    from websockets.server import WebSocketServerProtocol
    _WS_OK = True
except ImportError:
    _WS_OK = False

from agent.network.auth import verify_action_hmac
from agent.network.client_registry import ClientRegistry, ClientRecord
from agent.security.tokens import verify_token_against_hash
from shared.messages import (
    WelcomeMessage, ErrorMessage, RolesUpdatedMessage,
    RegisterMessage, ActionMessage, message_from_json,
)
from shared.version import PROTOCOL_VERSION

log = logging.getLogger(__name__)

HANDSHAKE_TIMEOUT: float = 10.0   # seconds to wait for RegisterMessage
DEFAULT_PORT:      int   = 5759


class _ConnectedClient:
    """Bookkeeping for one authenticated WebSocket connection."""

    def __init__(
        self,
        client_id: str,
        roles:     list[str],
        ws:        "WebSocketServerProtocol",
        token:     str,
    ) -> None:
        self.client_id = client_id
        self.roles     = roles
        self.ws        = ws
        self.token     = token          # raw token — kept for HMAC verify
        self.last_seq  = -1             # last accepted action sequence number


class WSServer:
    """
    Asyncio WebSocket server for the ED Agent.

    Parameters
    ----------
    host : str
        Interface to bind (``"0.0.0.0"`` for all interfaces).
    port : int
        TCP port to listen on (default 5759).
    client_registry : ClientRegistry
        Persistent store of client records.
    action_callback : callable(client_id, action, key), optional
        Called when a verified ActionMessage arrives.
    ssl_context : ssl.SSLContext, optional
        If provided the server uses TLS (``wss://``).
    """

    def __init__(
        self,
        host:             str,
        port:             int,
        client_registry:  ClientRegistry,
        action_callback:  Optional[Callable[[str, str, str], None]] = None,
        connect_callback: Optional[Callable] = None,
        ssl_context=None,
    ) -> None:
        """
        Parameters
        ----------
        connect_callback : async callable(client_id, roles), optional
            Awaited immediately after a client completes the handshake and is
            added to ``_connections``.  Use this to push initial state snapshots.
            Signature: ``async def cb(client_id: str, roles: list[str]) -> None``
        """
        if not _WS_OK:
            raise RuntimeError(
                "websockets is required.  Install with:  pip install websockets"
            )
        self.host         = host
        self.port         = port
        self._registry    = client_registry
        self._action_cb   = action_callback
        self._connect_cb  = connect_callback
        self._ssl         = ssl_context

        self._connections: dict[str, _ConnectedClient] = {}   # client_id → conn
        self._lock        = asyncio.Lock()
        self._server      = None   # websockets.Server instance

    # ── Lifecycle ──────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start listening for connections."""
        self._server = await websockets.serve(
            self._handle_connection,
            self.host,
            self.port,
            ssl=self._ssl,
        )
        log.info("WSServer listening on %s:%d%s",
                 self.host, self.port, " (TLS)" if self._ssl else "")

    async def stop(self) -> None:
        """Stop the server and close all active connections."""
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        async with self._lock:
            for conn in list(self._connections.values()):
                await self._close_ws(conn.ws, 1001, "server shutting down")
            self._connections.clear()
        log.info("WSServer stopped.")

    # ── Broadcasting ───────────────────────────────────────────────────────

    async def broadcast(self, role: str, message: dict) -> None:
        """
        Send a serialised message dict to every client subscribed to ``role``.

        Must be called from within the server's asyncio event loop.
        """
        payload = json.dumps(message)
        async with self._lock:
            targets = [
                c for c in self._connections.values()
                if role in c.roles
            ]
        for conn in targets:
            try:
                await conn.ws.send(payload)
            except Exception:
                pass   # disconnection handled by _handle_connection

    async def push_roles_updated(
        self, client_id: str, new_roles: list[str]
    ) -> None:
        """
        Push a RolesUpdatedMessage to a specific connected client.

        Also updates the in-memory role list for the connection so future
        broadcasts are correctly targeted without needing a reconnect.
        """
        async with self._lock:
            conn = self._connections.get(client_id)
        if conn is None:
            return
        conn.roles = new_roles
        msg = RolesUpdatedMessage(assigned_roles=new_roles)
        try:
            await conn.ws.send(msg.to_json())
        except Exception:
            pass

    async def send_to_client(self, client_id: str, message: dict) -> None:
        """
        Send a single message dict to one specific connected client.

        Used by the connect_callback to push initial state snapshots.
        Silently no-ops if the client is not (or no longer) connected.
        """
        payload = json.dumps(message)
        async with self._lock:
            conn = self._connections.get(client_id)
        if conn is None:
            return
        try:
            await conn.ws.send(payload)
        except Exception:
            pass

    # ── Connection handler ─────────────────────────────────────────────────

    async def _handle_connection(
        self, ws: "WebSocketServerProtocol", path: str = "/"
    ) -> None:
        """Coroutine run for every incoming connection."""
        remote = ws.remote_address
        log.debug("New connection from %s", remote)

        # ── 1. Handshake ──────────────────────────────────────────────────
        conn = await self._do_handshake(ws)
        if conn is None:
            return   # handshake rejected — connection already closed

        async with self._lock:
            self._connections[conn.client_id] = conn

        self._registry.update_last_seen(
            conn.client_id,
            datetime.now(timezone.utc).isoformat(),
        )
        log.info("Client connected: %s  roles=%s", conn.client_id, conn.roles)

        # ── 1b. Initial state snapshot push ──────────────────────────────
        if self._connect_cb:
            try:
                await self._connect_cb(conn.client_id, conn.roles)
            except Exception as exc:
                log.warning("connect_callback raised for %s: %s",
                            conn.client_id, exc)

        # ── 2. Message loop ───────────────────────────────────────────────
        try:
            async for raw in ws:
                await self._handle_message(conn, raw)
        except Exception:
            pass
        finally:
            async with self._lock:
                self._connections.pop(conn.client_id, None)
            log.info("Client disconnected: %s", conn.client_id)

    async def _do_handshake(
        self, ws: "WebSocketServerProtocol"
    ) -> Optional[_ConnectedClient]:
        """
        Execute the register → welcome handshake.

        Returns a ``_ConnectedClient`` on success, or ``None`` on failure
        (the connection is closed before returning None).
        """
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=HANDSHAKE_TIMEOUT)
        except asyncio.TimeoutError:
            await self._send_error(ws, "timeout",
                                   "No RegisterMessage received in time.", fatal=True)
            return None
        except Exception:
            return None

        try:
            msg = message_from_json(raw)
        except (ValueError, Exception):
            await self._send_error(ws, "bad_message",
                                   "Expected RegisterMessage.", fatal=True)
            return None

        if not isinstance(msg, RegisterMessage):
            await self._send_error(ws, "bad_message",
                                   "Expected RegisterMessage.", fatal=True)
            return None

        client_id = msg.client_id.strip()
        token     = msg.token.strip()

        if not client_id:
            await self._send_error(ws, "auth_failed",
                                   "client_id must not be empty.", fatal=True)
            return None

        record: Optional[ClientRecord] = self._registry.get(client_id)

        if record is None:
            # Unknown client — self-registration path
            if not msg.proposed_roles:
                await self._send_error(ws, "auth_failed",
                                       "Unknown client_id and no proposed_roles.",
                                       fatal=True)
                return None
            # Accept with proposed roles (token not verified — first-time setup)
            # In production with TLS this is acceptable; without TLS consider
            # requiring manual agent-side pre-registration.
            from agent.security.tokens import hash_token
            record = ClientRecord(
                client_id  = client_id,
                token_hash = hash_token(token),
                roles      = list(msg.proposed_roles),
            )
            self._registry.add(record)
            log.info("Auto-registered new client: %s  roles=%s",
                     client_id, record.roles)
        else:
            # Known client — verify token
            if not verify_token_against_hash(token, record.token_hash):
                await self._send_error(ws, "auth_failed",
                                       "Invalid token.", fatal=True)
                return None

        welcome = WelcomeMessage(
            assigned_roles   = list(record.roles),
            protocol_version = PROTOCOL_VERSION,
        )
        try:
            await ws.send(welcome.to_json())
        except Exception:
            return None

        return _ConnectedClient(
            client_id = client_id,
            roles     = list(record.roles),
            ws        = ws,
            token     = token,
        )

    # ── Message handling ───────────────────────────────────────────────────

    async def _handle_message(
        self, conn: _ConnectedClient, raw: str
    ) -> None:
        """Dispatch a single message received from an authenticated client."""
        try:
            msg = message_from_json(raw)
        except (ValueError, Exception):
            await self._send_error(conn.ws, "bad_message",
                                   "Could not parse message.")
            return

        if isinstance(msg, ActionMessage):
            await self._handle_action(conn, msg)
        else:
            await self._send_error(conn.ws, "bad_message",
                                   f"Unexpected message type: {msg.type!r}")

    async def _handle_action(
        self, conn: _ConnectedClient, msg: ActionMessage
    ) -> None:
        """Verify and execute an ActionMessage."""
        # Replay protection
        if msg.seq <= conn.last_seq:
            await self._send_error(conn.ws, "replay",
                                   f"Stale sequence number {msg.seq}.")
            return

        # HMAC verification
        if not verify_action_hmac(
            conn.client_id, msg.seq, msg.action, msg.key,
            conn.token, msg.hmac,
        ):
            await self._send_error(conn.ws, "auth_failed",
                                   "HMAC verification failed.")
            return

        conn.last_seq = msg.seq

        if self._action_cb:
            try:
                self._action_cb(conn.client_id, msg.action, msg.key)
            except Exception as exc:
                log.warning("Action callback raised: %s", exc)

    # ── Helpers ────────────────────────────────────────────────────────────

    @staticmethod
    async def _send_error(
        ws: "WebSocketServerProtocol",
        code: str,
        message: str,
        fatal: bool = False,
    ) -> None:
        err = ErrorMessage(code=code, message=message, fatal=fatal)
        try:
            await ws.send(err.to_json())
            if fatal:
                await ws.close(1008, message)
        except Exception:
            pass

    @staticmethod
    async def _close_ws(
        ws: "WebSocketServerProtocol", code: int, reason: str
    ) -> None:
        try:
            await ws.close(code, reason)
        except Exception:
            pass

    # ── Introspection ──────────────────────────────────────────────────────

    @property
    def connected_clients(self) -> list[str]:
        """Return a snapshot list of currently connected client IDs."""
        return list(self._connections.keys())

    async def disconnect_client(self, client_id: str) -> None:
        """Forcibly close the connection for a specific client (if online)."""
        async with self._lock:
            conn = self._connections.pop(client_id, None)
        if conn:
            await self._close_ws(conn.ws, 1008, "revoked by operator")
