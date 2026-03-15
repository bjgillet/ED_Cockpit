"""
ED Cockpit — Application Core
=============================
Central application object that owns all persistent backend services and
acts as the single communication hub between them and any GUI window.

Responsibilities
----------------
  • Own and lifecycle-manage the EDProcessWatcher (and future services).
  • Expose a subscribe / unsubscribe API so GUI windows can observe state
    changes without the backend ever knowing anything about tkinter.
  • Dispatch state updates through thread-safe queues so background threads
    never touch widgets directly.

Lifetime model
--------------
  EDApp is created once at startup and lives for the entire application
  session.  GUI windows come and go; each one subscribes on open and
  unsubscribes on close.  Closing a window never stops the backend.

Usage
-----
    from ed_app import EDApp

    app = EDApp()
    app.start()

    # A GUI window registers itself:
    q = app.subscribe()          # returns a queue.Queue
    # … later when the window closes:
    app.unsubscribe(q)

    app.stop()                   # called once at full application exit
"""
from __future__ import annotations

import queue
import threading
from typing import Dict, List

from ed_process_watcher import EDProcessWatcher, EDWatcherState


class EDApp:
    """
    Central application core.

    The EDApp owns every backend service.  GUI windows are pure observers:
    they subscribe to receive state updates via a ``queue.Queue`` and
    unsubscribe when they close.  No GUI code ever lives here.

    Thread safety
    -------------
    ``_dispatch`` is called from the watcher's background thread.  It only
    puts items onto queues — it never calls any tkinter method.  GUI windows
    drain their queues from the tkinter event loop using ``after()``.
    """

    def __init__(self) -> None:
        self._lock        = threading.Lock()
        self._subscribers: List[queue.Queue] = []

        # ── Backend services ──────────────────────────────────────────────
        self.watcher = EDProcessWatcher(on_update=self._dispatch)

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start all backend services."""
        self.watcher.start()

    def stop(self) -> None:
        """Stop all backend services.  Call once at full application exit."""
        self.watcher.stop()

    # ── Observer API ───────────────────────────────────────────────────────

    def subscribe(self) -> queue.Queue:
        """
        Register a new observer window.

        Returns a ``queue.Queue`` that will receive a state ``dict`` on every
        watcher state change.  The current state is delivered immediately so
        the new window does not have to wait for the next poll cycle.
        """
        q: queue.Queue = queue.Queue()
        with self._lock:
            self._subscribers.append(q)
        # Seed with current state so the window renders correctly at once
        q.put(self.watcher.snapshot())
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        """
        Unregister an observer window.  Safe to call even if the queue is
        no longer in the list (e.g. called twice by mistake).
        """
        with self._lock:
            try:
                self._subscribers.remove(q)
            except ValueError:
                pass

    # ── Convenience pass-throughs ─────────────────────────────────────────

    def snapshot(self) -> Dict:
        """Return the current watcher state (thread-safe snapshot)."""
        return self.watcher.snapshot()

    def rescan(self) -> None:
        """Force a fresh detection cycle regardless of current phase."""
        self.watcher.rescan()

    # ── Internal dispatch (called from watcher thread) ────────────────────

    def _dispatch(self, state: Dict) -> None:
        """
        Fan out a state update to all subscribed observer queues.

        IMPORTANT: this method is called from a background thread.
        It must never call any tkinter function.
        """
        with self._lock:
            subs = list(self._subscribers)
        for q in subs:
            q.put(state)
