"""
ED Cockpit — Base Panel (Client)
=================================
Abstract base class for all client-side role panels.

A panel is a ``ttk.Frame`` subclass that:
  1. Subscribes to a role's event queue on construction.
  2. Polls its queue via ``after()`` and updates its widgets.
  3. Optionally provides UI controls that trigger ``ActionMessage`` sends.
  4. Unsubscribes cleanly when it is destroyed.

Adding a new panel
------------------
  1. Create ``client/roles/<role>_panel.py`` with a class inheriting BasePanel.
  2. Set ``role_name`` to the canonical role string (from shared.roles_def).
  3. Override ``_build_ui()`` to lay out widgets.
  4. Override ``on_event()`` to handle incoming events from the agent.
  5. Register the class in ``client/roles/__init__.py``.
"""
from __future__ import annotations

import queue
import tkinter as tk
from abc import ABC, abstractmethod
from tkinter import ttk
from typing import Callable, Optional

POLL_MS: int = 100   # how often the panel drains its event queue


class BasePanel(ttk.Frame, ABC):
    """Abstract base for all client-side role panels."""

    role_name: str = ""

    def __init__(
        self,
        parent: tk.Misc,
        event_queue: queue.Queue,
        action_callback: Optional[Callable[[str, str], None]] = None,
        **kwargs,
    ) -> None:
        super().__init__(parent, **kwargs)
        self._queue    = event_queue
        self._action_cb = action_callback
        self._build_ui()
        self._poll()

    # ── Subclass contract ──────────────────────────────────────────────────

    @abstractmethod
    def _build_ui(self) -> None:
        """Lay out all widgets for this panel."""

    @abstractmethod
    def on_event(self, event: str, data: dict) -> None:
        """
        Handle an incoming event from the agent.

        Called from the tkinter thread — safe to update widgets directly.
        """

    # ── Queue polling ─────────────────────────────────────────────────────

    def _poll(self) -> None:
        try:
            while True:
                payload = self._queue.get_nowait()
                self.on_event(payload.get("event", ""), payload.get("data", {}))
        except queue.Empty:
            pass
        self.after(POLL_MS, self._poll)

    # ── Action helper ─────────────────────────────────────────────────────

    def send_action(self, action: str, key: str) -> None:
        """Send a key-press action to the agent (if callback is set)."""
        if self._action_cb:
            self._action_cb(action, key)
