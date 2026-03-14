"""
ED Assist — Application Core
=============================
Central application object that owns all persistent backend services and
acts as the single communication hub between them, the GUI, and network
clients.

Responsibilities
----------------
  • Own and lifecycle-manage EDProcessWatcher, JournalReader, StatusReader,
    and WSServer.
  • Run a dedicated asyncio event loop on a background daemon thread so the
    network layer (WSServer) never blocks the tkinter main thread.
  • Expose a subscribe / unsubscribe API so GUI windows observe watcher state
    changes through thread-safe queues.
  • When the watcher reaches COMPLETE, start JournalReader + StatusReader.
  • Route every journal event through the role registry and broadcast
    matching EventMessages to the WSServer.
  • Route status updates to a dedicated status subscriber queue so future
    GUI panels can observe raw status data.

Thread model
------------
  Main thread    : tkinter mainloop + GUI windows
  Background(s)  : EDProcessWatcher daemon threads (ED-ProcessScan, ED-FileScan)
                   JournalReader daemon thread (ED-JournalReader)
                   StatusReader daemon thread  (ED-StatusReader)
  Asyncio thread : asyncio event loop running WSServer (ED-AsyncioLoop)

  Callbacks from background threads → asyncio layer:
      loop.call_soon_threadsafe(coro wrapper)
  Callbacks from background threads → tkinter layer:
      queue.Queue + after() poll (existing pattern, unchanged)

Usage
-----
    app = EDApp()
    app.start()
    q = app.subscribe()       # for GUI windows
    app.unsubscribe(q)
    app.stop()
"""
from __future__ import annotations

import asyncio
import logging
import queue
import sys
import threading
from pathlib import Path
from typing import Callable, Dict, List, Optional

from agent.core.action_handler import ActionHandler
from agent.core.ed_process_watcher import EDProcessWatcher, EDWatcherState
from agent.core.journal_reader import JournalReader
from agent.core.status_reader import StatusReader
from agent.network.client_registry import ClientRegistry
from agent.network.ws_server import WSServer
from agent.roles import get_role, all_role_names
from agent.security.tls_setup import ensure_cert, build_server_ssl_context
from shared.messages import EventMessage
from shared.roles_def import ALL_ROLES

log = logging.getLogger(__name__)

# ── Agent configuration defaults ─────────────────────────────────────────────

WS_HOST: str = "0.0.0.0"
WS_PORT: int = 5759

def _default_config_dir() -> Path:
    if sys.platform == "win32":
        base = Path.home() / "AppData" / "Roaming" / "ed-assist"
    else:
        base = Path.home() / ".config" / "ed-assist"
    base.mkdir(parents=True, exist_ok=True)
    return base


class EDApp:
    """
    Central application core for the ED Agent.

    The EDApp owns every backend service.  GUI windows are pure observers:
    they subscribe to receive state updates via ``queue.Queue`` and
    unsubscribe when they close.  No GUI code ever lives here.
    """

    def __init__(
        self,
        config_dir: Optional[Path] = None,
        ws_host:    str = WS_HOST,
        ws_port:    int = WS_PORT,
        ssl_context = None,
        tls_enabled: bool = True,
    ) -> None:
        self._config_dir  = config_dir or _default_config_dir()
        self._ws_host     = ws_host
        self._ws_port     = ws_port

        # ── TLS: auto-generate cert on first run ──────────────────────────
        self._cert_path = self._config_dir / "agent.crt"
        self._key_path  = self._config_dir / "agent.key"

        if ssl_context is not None:
            self._ssl = ssl_context
        elif tls_enabled:
            fingerprint = ensure_cert(self._cert_path, self._key_path)
            log.info("TLS certificate fingerprint: %s", fingerprint)
            print(f"[ED Agent] TLS cert fingerprint: {fingerprint}")
            self._ssl = build_server_ssl_context(self._cert_path, self._key_path)
        else:
            self._ssl = None
            log.warning("TLS is DISABLED — connections are unencrypted.")

        self._lock        = threading.Lock()
        self._subscribers: List[queue.Queue] = []

        # ── Role handlers (instantiated once) ────────────────────────────
        self._roles = {name: get_role(name) for name in all_role_names()}

        # ── Client registry ───────────────────────────────────────────────
        self._registry = ClientRegistry(self._config_dir / "clients.json")

        # ── Action handler (key injection) ────────────────────────────────
        self._action_handler = ActionHandler(config_dir=self._config_dir)
        if self._action_handler.is_functional:
            log.info("ActionHandler: using backend %s",
                     self._action_handler.backend_name)
        else:
            log.warning("ActionHandler: no functional backend — key actions "
                        "will be logged but not delivered to the game.")
        # Write bindings.json on first run so the user can customise it
        ActionHandler.write_default_bindings(self._config_dir)

        # ── Backend services ──────────────────────────────────────────────
        self.watcher = EDProcessWatcher(on_update=self._on_watcher_update)
        self._journal = JournalReader(on_event=self._on_journal_event)
        self._status  = StatusReader(on_status=self._on_status_update)

        # ── Asyncio layer ─────────────────────────────────────────────────
        self._loop:   Optional[asyncio.AbstractEventLoop] = None
        self._ws_server: Optional[WSServer] = None
        self._asyncio_thread: Optional[threading.Thread] = None

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start all backend services including the asyncio WebSocket server."""
        # Start asyncio loop on its own thread first so _loop is available
        self._asyncio_thread = threading.Thread(
            target=self._run_asyncio_loop,
            name="ED-AsyncioLoop",
            daemon=True,
        )
        self._asyncio_thread.start()

        # Give the loop a moment to be created before other threads use it
        _waited = 0
        while self._loop is None and _waited < 50:
            threading.Event().wait(0.05)
            _waited += 1

        self.watcher.start()
        self._journal.start()
        self._status.start()

    def stop(self) -> None:
        """Stop all backend services.  Call once at full application exit."""
        self.watcher.stop()
        self._journal.stop()
        self._status.stop()

        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(
                self._stop_ws_server(), self._loop
            ).result(timeout=5)

    # ── GUI observer API ───────────────────────────────────────────────────

    def subscribe(self) -> queue.Queue:
        """
        Register a GUI window as a watcher-state observer.

        Returns a ``queue.Queue`` that receives a state dict on every change.
        The current state is delivered immediately.
        """
        q: queue.Queue = queue.Queue()
        with self._lock:
            self._subscribers.append(q)
        q.put(self.watcher.snapshot())
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            try:
                self._subscribers.remove(q)
            except ValueError:
                pass

    def snapshot(self) -> Dict:
        return self.watcher.snapshot()

    def rescan(self) -> None:
        self.watcher.rescan()

    # ── Client registry access (for GUI) ──────────────────────────────────

    @property
    def registry(self) -> ClientRegistry:
        return self._registry

    @property
    def cert_path(self) -> Path:
        """Path to the agent's TLS certificate (share with clients)."""
        return self._cert_path

    @property
    def cert_fingerprint(self) -> str:
        """SHA-256 fingerprint of the agent TLS cert, or empty if TLS disabled."""
        if self._ssl and self._cert_path.exists():
            from agent.security.tls_setup import cert_fingerprint
            return cert_fingerprint(self._cert_path)
        return ""

    def update_client_roles(self, client_id: str, roles: list[str]) -> bool:
        """
        Reassign roles for a connected client at runtime.

        Updates the registry and pushes a RolesUpdatedMessage if the client
        is currently connected.
        """
        ok = self._registry.set_roles(client_id, roles)
        if ok and self._loop and self._ws_server:
            asyncio.run_coroutine_threadsafe(
                self._ws_server.push_roles_updated(client_id, roles),
                self._loop,
            )
        return ok

    # ── Watcher callback (called from watcher background thread) ──────────

    def _on_watcher_update(self, state: Dict) -> None:
        """Fan out watcher state to all GUI subscriber queues."""
        with self._lock:
            subs = list(self._subscribers)
        for q in subs:
            q.put(state)

        # When files are found, wire them into the readers
        if state.get("phase") == EDWatcherState.COMPLETE:
            self._journal.set_path(state.get("journal_path"))
            self._status.set_path(state.get("status_path"))
        elif state.get("phase") == EDWatcherState.SEARCHING_PROCESS:
            self._journal.set_path(None)
            self._status.set_path(None)

    # ── Journal callback (called from JournalReader thread) ────────────────

    def _on_journal_event(self, event_name: str, data: dict) -> None:
        """
        Route a raw journal event through role filters and broadcast.

        Called from the ED-JournalReader thread.  Schedules the async
        broadcast coroutines on the asyncio loop thread-safely.
        """
        if self._loop is None:
            return

        timestamp: str = data.get("timestamp", "")

        for role_name, role in self._roles.items():
            if event_name not in role.journal_events:
                continue
            filtered = role.filter(event_name, data)
            if filtered is None:
                continue
            msg = EventMessage(
                role      = role_name,
                event     = event_name,
                timestamp = timestamp,
                data      = filtered,
            )
            asyncio.run_coroutine_threadsafe(
                self._broadcast_event(role_name, msg.to_dict()),
                self._loop,
            )

    # ── Status callback (called from StatusReader thread) ──────────────────

    def _on_status_update(self, status_data: dict) -> None:
        """
        Handle a Status.json update.

        Currently routes the raw status dict to all role handlers that
        declare interest in status updates (future extension point).
        For now it is a no-op placeholder kept for architectural clarity.
        """
        pass

    # ── Asyncio helpers ────────────────────────────────────────────────────

    def _run_asyncio_loop(self) -> None:
        """Entry point for the ED-AsyncioLoop daemon thread."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            loop.run_until_complete(self._start_ws_server())
            loop.run_forever()
        finally:
            loop.close()

    async def _start_ws_server(self) -> None:
        self._ws_server = WSServer(
            host            = self._ws_host,
            port            = self._ws_port,
            client_registry = self._registry,
            action_callback = self._on_action_received,
            ssl_context     = self._ssl,
        )
        await self._ws_server.start()

    async def _stop_ws_server(self) -> None:
        if self._ws_server:
            await self._ws_server.stop()
        if self._loop:
            self._loop.stop()

    async def _broadcast_event(self, role: str, message: dict) -> None:
        if self._ws_server:
            await self._ws_server.broadcast(role, message)

    # ── Action callback (called from WSServer asyncio coroutine) ──────────

    def _on_action_received(
        self, client_id: str, action: str, key: str
    ) -> None:
        """
        Called by WSServer when a verified ActionMessage arrives.

        Forwards the request to the ActionHandler which translates the
        logical key name to a platform-level key injection.
        """
        log.info("Action from %s: %s(%s)", client_id, action, key)
        dispatched = self._action_handler.execute(action, key)
        if not dispatched:
            log.warning(
                "Action not dispatched — client=%s action=%r key=%r "
                "(backend=%s functional=%s)",
                client_id, action, key,
                self._action_handler.backend_name,
                self._action_handler.is_functional,
            )
