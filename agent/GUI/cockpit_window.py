"""
ED Cockpit — Agent Main Window
================================
Single ``tk.Toplevel`` that hosts both the Process Monitor and the Client
Manager inside a ``ttk.Notebook`` with one tab each.

Usage
-----
    win = EDCockpitWindow(root, app, quit_on_close=True)
    app.subscribe_actions(win.push_action)

The ``push_action`` method is forwarded to the Client Manager tab so that
live action events from connected clients appear in the action log.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent.core.ed_app import EDApp

from agent.GUI.ed_status_monitor import EDStatusMonitorPanel
from agent.GUI.client_manager import ClientManagerPanel

# ── Theme (must stay in sync with the panel themes) ───────────────────────────
BG        = "#0d0d1e"
PANEL_BG  = "#10102a"
HEADER_BG = "#b87800"
HEADER_FG = "#ffff00"
SEP_COLOR = "#2a2a4a"
ACCENT    = "#4da6ff"
TEXT_FG   = "#ffffff"

FONT_HEAD = ("Consolas", 10, "bold")


class EDCockpitWindow(tk.Toplevel):
    """
    Single agent window containing a ``ttk.Notebook`` with two tabs:

    * **Process Monitor** — ``EDStatusMonitorPanel``
    * **Client Manager**  — ``ClientManagerPanel``

    Parameters
    ----------
    tk_root : tk.Tk
        The application root window (may be hidden).
    app : EDApp
        The running agent core.
    quit_on_close : bool
        When True, closing this window also terminates the tkinter event loop
        (i.e. treats this as the main/last window).  Default: False.
    """

    def __init__(
        self,
        tk_root: tk.Tk,
        app: "EDApp",
        *,
        quit_on_close: bool = False,
    ) -> None:
        super().__init__(tk_root)

        self._app           = app
        self._quit_on_close = quit_on_close

        self.title("ED Cockpit — Agent")
        self.configure(bg=BG)
        self.minsize(680, 420)
        self.resizable(True, True)

        self._apply_notebook_style()
        self._build_notebook()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── Style ──────────────────────────────────────────────────────────────

    def _apply_notebook_style(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")

        style.configure(
            "Cockpit.TNotebook",
            background=BG,
            borderwidth=0,
            tabmargins=[0, 0, 0, 0],
        )
        style.configure(
            "Cockpit.TNotebook.Tab",
            background=SEP_COLOR,
            foreground=HEADER_FG,
            font=FONT_HEAD,
            padding=[16, 6],
            borderwidth=0,
        )
        style.map(
            "Cockpit.TNotebook.Tab",
            background=[("selected", HEADER_BG), ("active", "#1a1a3a")],
            foreground=[("selected", HEADER_FG), ("active", TEXT_FG)],
        )

    # ── Notebook construction ──────────────────────────────────────────────

    def _build_notebook(self) -> None:
        nb = ttk.Notebook(self, style="Cockpit.TNotebook")
        nb.pack(fill="both", expand=True)

        # ── Tab 1: Process Monitor ─────────────────────────────────────
        tab1 = tk.Frame(nb, bg=BG)
        nb.add(tab1, text="  Process Monitor  ")
        self._monitor_panel = EDStatusMonitorPanel(
            tab1, self._app, quit_fn=self._on_quit
        )
        self._monitor_panel.pack(fill="both", expand=True)

        # ── Tab 2: Client Manager ──────────────────────────────────────
        tab2 = tk.Frame(nb, bg=BG)
        nb.add(tab2, text="  Client Manager  ")
        self._cm_panel = ClientManagerPanel(tab2, self._app)
        self._cm_panel.pack(fill="both", expand=True)

    # ── Public API ─────────────────────────────────────────────────────────

    def push_action(self, client_id: str, action: str, key: str) -> None:
        """
        Forward a client action to the Client Manager action log.

        Thread-safe — may be called from the asyncio loop thread.
        """
        self._cm_panel.push_action(client_id, action, key)

    # ── Window / quit handlers ─────────────────────────────────────────────

    def _on_quit(self) -> None:
        """Called by the Quit button inside the Process Monitor panel."""
        self.destroy()
        if self._quit_on_close:
            self.master.quit()

    def _on_close(self) -> None:
        """Called when the user clicks the window's X button."""
        self._app.unsubscribe(self._monitor_panel._queue)
        self.destroy()
        if self._quit_on_close:
            self.master.quit()
