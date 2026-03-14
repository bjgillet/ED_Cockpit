"""
ED Assist — Agent Entry Point
===============================
Creates the EDApp core, starts backend services, opens the initial GUI
window, and runs the tkinter event loop.

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
                                            │ queue.Queue  (per window)
                     ┌──────────────────────┼──────────────────┐
                     │                      │                   │
               ┌─────▼──────┐    ┌──────────▼──┐    (future windows…)
               │ Status      │    │ Client      │
               │ Monitor     │    │ Manager     │
               │ (Toplevel)  │    │ (Toplevel)  │
               └─────────────┘    └─────────────┘

        tk.Tk (hidden root — keeps the event loop alive)

The hidden root window is never shown.  All user-facing windows are
``tk.Toplevel`` instances.  Closing the last window (or pressing Quit)
calls ``root.quit()`` which terminates ``mainloop()`` and allows the
``finally`` block below to stop the backend cleanly.

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
from agent.GUI.ed_status_monitor import EDStatusMonitor
from agent.GUI.client_manager import ClientManager


def main() -> None:
    # ── 1. Create and start the application core ──────────────────────────
    app = EDApp()
    app.start()

    # ── 2. Create the hidden tkinter root ─────────────────────────────────
    #       A hidden Tk() keeps the event loop alive without showing a
    #       blank root window.  All real windows are Toplevel children.
    root = tk.Tk()
    root.withdraw()
    root.title("ED Assist")

    # ── 3. Open the initial windows ───────────────────────────────────────
    #       quit_on_close=True on the Status Monitor → closing it stops
    #       the whole application.
    EDStatusMonitor(root, app, quit_on_close=True)
    cm = ClientManager(root, app)
    app.subscribe_actions(cm.push_action)

    # ── 4. Run the event loop ─────────────────────────────────────────────
    try:
        root.mainloop()
    finally:
        app.stop()


if __name__ == "__main__":
    main()
