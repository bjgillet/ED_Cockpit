"""
ED Cockpit — Status Monitor
==========================
Provides two classes:

``EDStatusMonitorPanel``
    A ``tk.Frame`` subclass — the embeddable process-monitor panel.
    Use this when the panel is placed inside another container (e.g. a
    ``ttk.Notebook`` tab).  Pass a ``quit_fn`` callable that will be
    invoked when the user presses the Quit button.

``EDStatusMonitor``
    Backward-compatible ``tk.Toplevel`` wrapper around
    ``EDStatusMonitorPanel``.  Behaves exactly like the original standalone
    window.

Architecture
------------
The panel is a pure observer.  It receives an EDApp reference on
construction, subscribes to its state queue, and unsubscribes cleanly when
the Quit button is pressed.  It never creates, owns, or stops any backend
service.

State updates arrive through a thread-safe ``queue.Queue`` supplied by EDApp.
The queue is drained every POLL_MS milliseconds via ``after()``, so background
threads never touch any tkinter widget.

Layout
------
  ┌──────────────────────────────────────────────────────────┐
  │  ED COCKPIT — PROCESS MONITOR                             │
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

import json
import queue
import sys
import tkinter as tk
from pathlib import Path
from typing import Callable, Dict, Optional

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


# ── Embeddable panel ──────────────────────────────────────────────────────────

class EDStatusMonitorPanel(tk.Frame):
    """
    Embeddable process-monitor panel.

    Inherits from ``tk.Frame`` so it can be placed inside any container
    (e.g. a ``ttk.Notebook`` tab or a ``tk.Toplevel``).

    Parameters
    ----------
    parent : tk.Widget
        Tkinter parent widget.
    app : EDApp
        The central application core to observe.
    quit_fn : callable, optional
        Called when the user presses the Quit button.  When *None* the
        panel falls back to destroying its own top-level window and calling
        ``quit()`` on the root — suitable for standalone use.
    """

    def __init__(
        self,
        parent: tk.Widget,
        app: EDApp,
        *,
        quit_fn: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__(parent, bg=BG)

        self._app           = app
        self._queue         = app.subscribe()   # thread-safe state queue
        self._last_state:   Dict  = {}
        self._last_memory:  Dict  = {}
        self._blink_state:  bool  = False
        self._quit_fn                    = quit_fn

        self._build_ui()
        self.after(POLL_MS, self._poll)

    # ── UI construction ────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        self._build_title_bar()
        self._build_process_panel()
        self._build_file_panel("JOURNAL FILE",  is_journal=True)
        self._build_file_panel("STATUS FILE",   is_journal=False)
        self._build_journal_memory_panel()
        self._build_button_bar()

    def _build_title_bar(self) -> None:
        bar = tk.Frame(self, bg=HEADER_BG, pady=6)
        bar.pack(fill="x")
        tk.Label(
            bar,
            text="ED COCKPIT — PROCESS MONITOR",
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

        self._copy_mem_btn = tk.Button(
            bar,
            text="⎘  Copy Memory JSON",
            bg="#1a2a4a", fg=TITLE_FG,
            activebackground="#0a1428", activeforeground=TEXT_FG,
            relief="flat", font=FONT_BTN, cursor="hand2",
            padx=12, pady=4,
            command=self._on_copy_memory,
        )
        self._copy_mem_btn.pack(side="left", padx=(0, 6))

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

    def _build_journal_memory_panel(self) -> None:
        self._section_header("LAST KNOWN JOURNAL STATE")
        panel = tk.Frame(self, bg=PANEL_BG)
        panel.pack(fill="x", padx=4, pady=2)
        panel.columnconfigure(1, weight=1)

        self._memory_labels: dict[str, tk.Label] = {}
        rows = [
            ("Commander:", "commander"),
            ("Ship:", "ship"),
            ("Hull / Cargo:", "hull_cargo"),
            ("Fuel Capacity:", "fuel"),
            ("Location:", "location"),
            ("Cargo Inventory:", "inventory"),
        ]
        for row, (title, key) in enumerate(rows):
            tk.Label(
                panel, text=title, bg=PANEL_BG, fg=TITLE_FG,
                font=FONT_BODY, anchor="w",
            ).grid(row=row, column=0, sticky="nw", padx=(12, 6), pady=1)
            lbl = tk.Label(
                panel, text="—", bg=PANEL_BG, fg=TEXT_FG,
                font=FONT_PATH, anchor="w", justify="left", wraplength=520,
            )
            lbl.grid(row=row, column=1, sticky="w", padx=(0, 8), pady=1)
            self._memory_labels[key] = lbl

    # ── Queue polling (tkinter event-loop thread only) ─────────────────────

    def _poll(self) -> None:
        """
        Drain all pending state updates from the queue and refresh the UI.
        Scheduled every POLL_MS ms by tkinter's event loop — never called
        from a background thread.
        """
        if not self.winfo_exists():
            return

        latest: Optional[Dict] = None
        while True:
            try:
                latest = self._queue.get_nowait()
            except queue.Empty:
                break

        if latest is not None and latest != self._last_state:
            self._last_state = latest
            self._refresh(latest)
        self._refresh_journal_memory()

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

    def _refresh_journal_memory(self) -> None:
        snapshot = self._app.journal_memory_snapshot()
        if snapshot == self._last_memory:
            return
        self._last_memory = snapshot

        ship = snapshot.get("ship", {})
        location = snapshot.get("location", {})

        commander = snapshot.get("commander_name", "") or "—"
        ship_type = ship.get("ship", "") or "—"
        ship_name = ship.get("ship_name", "")
        if ship_name:
            ship_text = f"{ship_type} ({ship_name})"
        else:
            ship_text = ship_type

        hull_health = float(ship.get("hull_health", 0.0))
        cargo_capacity = float(ship.get("cargo_capacity", 0.0))
        hull_cargo = f"{hull_health * 100:.0f}% / {cargo_capacity:.0f} t"

        fuel_text = self._format_fuel_capacity(ship.get("fuel_capacity", {}))

        star_system = location.get("star_system", "") or "—"
        body = location.get("body", "") or "—"
        location_text = f"{star_system} / {body}"

        inventory_text = self._format_inventory(snapshot.get("cargo_inventory", []))

        self._memory_labels["commander"].config(text=commander)
        self._memory_labels["ship"].config(text=ship_text)
        self._memory_labels["hull_cargo"].config(text=hull_cargo)
        self._memory_labels["fuel"].config(text=fuel_text)
        self._memory_labels["location"].config(text=location_text)
        self._memory_labels["inventory"].config(text=inventory_text)

    @staticmethod
    def _format_fuel_capacity(value) -> str:
        if isinstance(value, dict):
            main = value.get("Main")
            reserve = value.get("Reserve")
            if main is not None or reserve is not None:
                try:
                    main_txt = f"{float(main):.2f} t" if main is not None else "?"
                    res_txt = f"{float(reserve):.2f} t" if reserve is not None else "?"
                    return f"Main {main_txt}, Reserve {res_txt}"
                except (TypeError, ValueError):
                    pass
            return str(value) if value else "—"
        if isinstance(value, (int, float)):
            return f"{float(value):.2f} t"
        return "—"

    @staticmethod
    def _format_inventory(value) -> str:
        if not isinstance(value, list) or not value:
            return "—"
        names: list[str] = []
        total_units = 0
        for item in value:
            if not isinstance(item, dict):
                continue
            name = item.get("Name_Localised") or item.get("Name") or "Unknown"
            try:
                count = int(item.get("Count", 0))
            except (TypeError, ValueError):
                count = 0
            total_units += max(count, 0)
            names.append(f"{name} x{count}")
            if len(names) >= 5:
                break
        extra = " …" if len(value) > 5 else ""
        return f"{total_units} unit(s) — " + ", ".join(names) + extra

    # ── Button handlers ────────────────────────────────────────────────────

    def _on_rescan(self) -> None:
        self._app.rescan()

    def _on_copy_memory(self) -> None:
        payload = self._app.journal_memory_snapshot()
        text = json.dumps(payload, indent=2, ensure_ascii=False)
        self.clipboard_clear()
        self.clipboard_append(text)

    def _on_quit(self) -> None:
        """Quit button: unsubscribe and terminate the application."""
        self._app.unsubscribe(self._queue)
        if self._quit_fn is not None:
            self._quit_fn()
        else:
            # Standalone fallback: close the enclosing top-level and quit.
            tl = self.winfo_toplevel()
            tl.destroy()
            try:
                tl.master.quit()
            except Exception:
                pass


# ── Backward-compatible Toplevel wrapper ──────────────────────────────────────

class EDStatusMonitor(tk.Toplevel):
    """
    Standalone status monitor window (backward-compatible wrapper).

    Wraps ``EDStatusMonitorPanel`` inside a ``tk.Toplevel``.  Existing
    call-sites that pass ``(tk_root, app, quit_on_close=True)`` continue to
    work unchanged.

    Parameters
    ----------
    tk_root : tk.Tk
        The application root window (may be hidden).
    app : EDApp
        The central application core to observe.
    quit_on_close : bool
        When True, closing this window also terminates the tkinter event loop.
    """

    def __init__(
        self,
        tk_root: tk.Tk,
        app: EDApp,
        *,
        quit_on_close: bool = False,
    ) -> None:
        super().__init__(tk_root)
        self.title("ED Cockpit — Process Monitor")
        self.configure(bg=BG)
        self.resizable(True, False)
        self.minsize(580, 0)

        def _quit_fn() -> None:
            self.destroy()
            if quit_on_close:
                self.master.quit()

        self._panel = EDStatusMonitorPanel(self, app, quit_fn=_quit_fn)
        self._panel.pack(fill="both", expand=True)
        self.protocol("WM_DELETE_WINDOW", _quit_fn)


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
