"""
ED Assist — Status Monitor
==========================
Tkinter/ttk window that displays the live detection status produced by
the EDApp core (../ed_app.py).

Architecture
------------
This window is a pure observer.  It receives an EDApp reference on
construction, subscribes to its state queue, and unsubscribes cleanly when
it is closed.  It never creates, owns, or stops any backend service.

State updates arrive through a thread-safe ``queue.Queue`` supplied by EDApp.
The queue is drained every POLL_MS milliseconds via ``after()``, so background
threads never touch any tkinter widget.

Layout
------
  ┌──────────────────────────────────────────────────────────┐
  │  ED ASSIST — PROCESS MONITOR                             │
  ├──────────────────────────────────────────────────────────┤
  │  ELITE DANGEROUS PROCESS                                  │
  │   ●  Searching…  /  ●  Running: EliteDangerous64.exe     │
  │      PID: 12345                                           │
  ├──────────────────────────────────────────────────────────┤
  │  JOURNAL FILE                                             │
  │   ●  Waiting…  /  ●  Active                              │
  │      .../Journal.2024-01-15T120000.01.log                 │
  ├──────────────────────────────────────────────────────────┤
  │  STATUS FILE                                              │
  │   ●  Waiting…  /  ●  Active                              │
  │      .../Status.json                                      │
  ├──────────────────────────────────────────────────────────┤
  │            [ Rescan ]                 [ Quit ]            │
  └──────────────────────────────────────────────────────────┘

Indicator colours
-----------------
  ● amber  — actively searching (blinks)
  ● green  — item found / active
  ● dim    — not yet relevant
  ● red    — item lost (process exited after being found)

Platform
--------
  Works on Windows and Linux (Python 3.10+, tkinter).

Standalone use
--------------
  python GUI/ed_status_monitor.py
  (Creates a temporary EDApp for testing purposes.)
"""
from __future__ import annotations

import queue
import sys
import tkinter as tk
from pathlib import Path
from typing import Dict, Optional

# ── Resolve parent package path ───────────────────────────────────────────────
_THIS_DIR   = Path(__file__).resolve().parent
_PARENT_DIR = _THIS_DIR.parent
if str(_PARENT_DIR) not in sys.path:
    sys.path.insert(0, str(_PARENT_DIR))

from agent.core.ed_app import EDApp
from agent.core.ed_process_watcher import EDWatcherState

# ── Theme ─────────────────────────────────────────────────────────────────────

BG          = "#0d0d1e"
PANEL_BG    = "#10102a"
HEADER_BG   = "#b87800"
HEADER_FG   = "#ffff00"
TITLE_FG    = "#4da6ff"
TEXT_FG     = "#ffffff"
PATH_FG     = "#88ccff"
SEP_COLOR   = "#2a2a4a"

C_SEARCHING = "#ff8800"   # amber  — actively scanning
C_FOUND     = "#00cc55"   # green  — item detected
C_LOST      = "#cc2222"   # red    — was found, now gone
C_IDLE      = "#333355"   # dim    — not yet relevant

FONT_TITLE  = ("Consolas", 13, "bold")
FONT_HEAD   = ("Consolas", 10, "bold")
FONT_BODY   = ("Consolas",  9)
FONT_PATH   = ("Consolas",  8)
FONT_BTN    = ("Consolas",  9, "bold")

POLL_MS     = 600         # ms between queue drain cycles
DOT_SIZE    = 12          # indicator circle diameter


# ── Sub-widgets ───────────────────────────────────────────────────────────────

class StatusDot(tk.Canvas):
    """A small coloured circle used as a status indicator."""

    def __init__(self, parent: tk.Widget, **kwargs) -> None:
        super().__init__(
            parent,
            width=DOT_SIZE, height=DOT_SIZE,
            bg=PANEL_BG,
            highlightthickness=0,
            **kwargs,
        )
        self._oval = self.create_oval(
            2, 2, DOT_SIZE - 2, DOT_SIZE - 2,
            fill=C_IDLE, outline="",
        )

    def set_color(self, color: str) -> None:
        self.itemconfig(self._oval, fill=color)


class StatusRow:
    """
    One section row: coloured dot + short status text + path text.
    Laid out inside a parent tk.Frame using grid.
    """

    def __init__(self, parent: tk.Frame, row: int) -> None:
        self.dot = StatusDot(parent)
        self.dot.grid(row=row, column=0, padx=(12, 6), pady=4, sticky="nw")

        self.status_lbl = tk.Label(
            parent, text="",
            bg=PANEL_BG, fg=TEXT_FG, font=FONT_BODY, anchor="w",
        )
        self.status_lbl.grid(row=row, column=1, sticky="w", pady=4)

        self.path_lbl = tk.Label(
            parent, text="",
            bg=PANEL_BG, fg=PATH_FG, font=FONT_PATH,
            anchor="w", wraplength=520, justify="left",
        )
        self.path_lbl.grid(row=row + 1, column=1, sticky="w", pady=(0, 6))

    def update(self, color: str, status_text: str, path_text: str = "") -> None:
        self.dot.set_color(color)
        self.status_lbl.config(text=status_text)
        self.path_lbl.config(text=path_text)


# ── Main window ───────────────────────────────────────────────────────────────

class EDStatusMonitor(tk.Toplevel):
    """
    Status monitor window.

    Observes an EDApp instance by subscribing to its state queue on open
    and unsubscribing on close.  The backend continues running after this
    window is closed.

    Parameters
    ----------
    tk_root : tk.Tk
        The application root window (may be hidden).
    app : EDApp
        The central application core to observe.
    quit_on_close : bool
        When True, closing this window also terminates the tkinter event loop
        (i.e. treats this as the main/last window).  Default: False.
    """

    def __init__(
        self,
        tk_root: tk.Tk,
        app: EDApp,
        *,
        quit_on_close: bool = False,
    ) -> None:
        super().__init__(tk_root)

        self._app           = app
        self._queue         = app.subscribe()   # thread-safe state queue
        self._last_state:   Dict  = {}
        self._blink_state:  bool  = False
        self._quit_on_close: bool = quit_on_close

        self.title("ED Assist — Process Monitor")
        self.configure(bg=BG)
        self.resizable(True, False)
        self.minsize(580, 0)

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(POLL_MS, self._poll)

    # ── UI construction ────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        self._build_title_bar()
        self._build_process_panel()
        self._build_file_panel("JOURNAL FILE",  is_journal=True)
        self._build_file_panel("STATUS FILE",   is_journal=False)
        self._build_button_bar()

    def _build_title_bar(self) -> None:
        bar = tk.Frame(self, bg=HEADER_BG, pady=6)
        bar.pack(fill="x")
        tk.Label(
            bar,
            text="ED ASSIST — PROCESS MONITOR",
            bg=HEADER_BG, fg=HEADER_FG,
            font=FONT_TITLE,
        ).pack(padx=14)

    def _section_header(self, text: str) -> None:
        hdr = tk.Frame(self, bg=SEP_COLOR, pady=1)
        hdr.pack(fill="x", pady=(8, 0))
        tk.Label(
            hdr,
            text=f"  {text}",
            bg=SEP_COLOR, fg=HEADER_FG,
            font=FONT_HEAD, anchor="w",
        ).pack(fill="x", padx=4, pady=3)

    def _build_process_panel(self) -> None:
        self._section_header("ELITE DANGEROUS PROCESS")
        panel = tk.Frame(self, bg=PANEL_BG)
        panel.pack(fill="x", padx=4, pady=2)
        panel.columnconfigure(1, weight=1)
        self._proc_row = StatusRow(panel, row=0)

    def _build_file_panel(self, title: str, *, is_journal: bool) -> None:
        self._section_header(title)
        panel = tk.Frame(self, bg=PANEL_BG)
        panel.pack(fill="x", padx=4, pady=2)
        panel.columnconfigure(1, weight=1)
        row = StatusRow(panel, row=0)
        if is_journal:
            self._journal_row = row
        else:
            self._status_row  = row

    def _build_button_bar(self) -> None:
        bar = tk.Frame(self, bg=BG, pady=10)
        bar.pack(fill="x", padx=14)

        self._rescan_btn = tk.Button(
            bar,
            text="⟳  Rescan",
            bg="#1a2a4a", fg=TITLE_FG,
            activebackground="#0a1428", activeforeground=TEXT_FG,
            relief="flat", font=FONT_BTN, cursor="hand2",
            padx=12, pady=4,
            command=self._on_rescan,
        )
        self._rescan_btn.pack(side="left")

        tk.Button(
            bar,
            text="✕  Quit",
            bg="#2a1a1a", fg="#cc6666",
            activebackground="#1a0a0a", activeforeground="#ff8888",
            relief="flat", font=FONT_BTN, cursor="hand2",
            padx=12, pady=4,
            command=self._on_quit,
        ).pack(side="right")

    # ── Queue polling (tkinter event-loop thread only) ─────────────────────

    def _poll(self) -> None:
        """
        Drain all pending state updates from the queue and refresh the UI.
        Scheduled every POLL_MS ms by tkinter's event loop — never called
        from a background thread.
        """
        latest: Optional[Dict] = None
        while True:
            try:
                latest = self._queue.get_nowait()
            except queue.Empty:
                break

        if latest is not None and latest != self._last_state:
            self._last_state = latest
            self._refresh(latest)

        self._blink_state = not self._blink_state
        self._maybe_blink()

        self.after(POLL_MS, self._poll)

    # ── UI refresh ─────────────────────────────────────────────────────────

    def _refresh(self, snap: Dict) -> None:
        phase = snap.get("phase", EDWatcherState.SEARCHING_PROCESS)

        # ── Process row ───────────────────────────────────────────────
        if snap["process_found"]:
            self._proc_row.update(
                C_FOUND,
                f"Running: {snap['process_name'] or 'EliteDangerous'}",
                f"PID: {snap['process_pid']}",
            )
        elif phase == EDWatcherState.STOPPED:
            self._proc_row.update(C_IDLE, "Watcher stopped.")
        else:
            self._proc_row.update(C_SEARCHING, "Searching for Elite Dangerous…")

        # ── Journal row ───────────────────────────────────────────────
        jpath = snap.get("journal_path")
        if jpath:
            self._journal_row.update(C_FOUND, "Active", jpath)
        elif snap["process_found"]:
            self._journal_row.update(C_SEARCHING, "Searching for Journal file…")
        else:
            self._journal_row.update(C_IDLE, "Waiting for game process…")

        # ── Status file row ───────────────────────────────────────────
        spath = snap.get("status_path")
        if spath:
            self._status_row.update(C_FOUND, "Active", spath)
        elif snap["process_found"]:
            self._status_row.update(C_SEARCHING, "Searching for Status.json…")
        else:
            self._status_row.update(C_IDLE, "Waiting for game process…")

    def _maybe_blink(self) -> None:
        """Pulse amber dots during active search phases."""
        if not self._last_state:
            return
        phase = self._last_state.get("phase", "")
        color = C_SEARCHING if self._blink_state else "#994400"

        if phase == EDWatcherState.SEARCHING_PROCESS:
            self._proc_row.dot.set_color(color)

        if phase == EDWatcherState.SEARCHING_FILES:
            if not self._last_state.get("journal_path"):
                self._journal_row.dot.set_color(color)
            if not self._last_state.get("status_path"):
                self._status_row.dot.set_color(color)

    # ── Button handlers ────────────────────────────────────────────────────

    def _on_rescan(self) -> None:
        self._app.rescan()

    def _on_quit(self) -> None:
        """Quit button: close this window and terminate the application."""
        self._app.unsubscribe(self._queue)
        self.destroy()
        self.master.quit()      # stop the tkinter event loop → main.py exits

    def _on_close(self) -> None:
        """Window close button (X): unsubscribe and close the window."""
        self._app.unsubscribe(self._queue)
        self.destroy()
        if self._quit_on_close:
            self.master.quit()


# ── Standalone entry-point (for development / testing) ───────────────────────

def _standalone() -> None:
    """Run the status monitor with a self-contained EDApp for quick testing."""
    app = EDApp()
    app.start()

    root = tk.Tk()
    root.withdraw()

    EDStatusMonitor(root, app, quit_on_close=True)

    try:
        root.mainloop()
    finally:
        app.stop()


if __name__ == "__main__":
    _standalone()
