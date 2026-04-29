"""
ED Cockpit — Agent Entry Point
===============================
Creates the EDApp core, starts backend services, opens the single GUI
window (a tabbed cockpit), and runs the tkinter event loop.

Architecture overview
---------------------
                        ┌──────────────────────────────────────────────┐
                        │                EDApp  (core/ed_app.py)       │
                        │                                              │
                        │  ┌─────────────────┐  ┌──────────────────┐  │
                        │  │ EDProcessWatcher│  │  JournalReader   │  │
                        │  │ ED-ProcessScan  │  │  ED-JournalReader│  │
                        │  │ ED-FileScan     │  │  StatusReader    │  │
                        │  └────────┬────────┘  │  ED-StatusReader │  │
                        │           │           └───────┬──────────┘  │
                        │           │ state dict        │ events/status│
                        │           └─────────┬─────────┘             │
                        │               _dispatch()                    │
                        │         ┌─────────────────────┐             │
                        │         │  ED-AsyncioLoop      │             │
                        │         │  WSServer (port 5759)│             │
                        │         └─────────────────────┘             │
                        └───────────────────┬──────────────────────────┘
                                            │ queue.Queue
                                    ┌───────▼────────┐
                                    │ EDCockpitWindow │
                                    │  ┌───────────┐  │
                                    │  │ Process   │  │
                                    │  │ Monitor   │  │  (tab 1)
                                    │  ├───────────┤  │
                                    │  │ Client    │  │
                                    │  │ Manager   │  │  (tab 2)
                                    │  └───────────┘  │
                                    └────────────────┘

        tk.Tk (hidden root — keeps the event loop alive)

The hidden root window is never shown.  The single user-facing window is a
``tk.Toplevel`` (``EDCockpitWindow``) with a ``ttk.Notebook`` that hosts
the Process Monitor and Client Manager as tabs.  Closing the window (or
pressing Quit) calls ``root.quit()``, which terminates ``mainloop()`` and
allows the ``finally`` block below to stop the backend cleanly.

Run
---
    python agent/main.py
"""
from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path

# ── Ensure the project root is on sys.path ────────────────────────────────────
_ROOT = Path(__file__).resolve().parent.parent   # repo root
_AGENT = Path(__file__).resolve().parent          # agent/
for _p in (_ROOT, _AGENT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from agent.core.ed_app import EDApp
from agent.GUI.cockpit_window import EDCockpitWindow


def main() -> None:
    # ── 1. Create and start the application core ──────────────────────────
    app = EDApp()
    app.start()

    # ── 2. Create the hidden tkinter root ─────────────────────────────────
    root = tk.Tk()
    root.withdraw()
    root.title("ED Cockpit")

    # ── 3. Open the single tabbed cockpit window ───────────────────────────
    #       quit_on_close=True → closing the window stops the event loop.
    win = EDCockpitWindow(root, app, quit_on_close=True)
    app.subscribe_actions(win.push_action)

    # ── 4. Run the event loop ─────────────────────────────────────────────
    try:
        root.mainloop()
    finally:
        app.stop()


if __name__ == "__main__":
    main()
