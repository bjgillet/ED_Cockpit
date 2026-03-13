"""
ED Assist — Application Entry Point
=====================================
Creates the EDApp core, starts backend services, opens the initial GUI
window, and runs the tkinter event loop.

Architecture overview
---------------------
                        ┌─────────────────────────────────────┐
                        │            EDApp  (ed_app.py)        │
                        │                                      │
                        │  ┌──────────────────────────────┐   │
                        │  │  EDProcessWatcher             │   │
                        │  │  (ed_process_watcher.py)      │   │
                        │  │  Thread: ED-ProcessScan       │   │
                        │  │  Thread: ED-FileScan          │   │
                        │  └──────────────┬───────────────┘   │
                        │                 │ state dict         │
                        │         _dispatch() → queues         │
                        └─────────────────┼───────────────────┘
                                          │ queue.Queue  (per window)
                     ┌────────────────────┼──────────────────┐
                     │                    │                   │
               ┌─────▼──────┐    ┌───────▼─────┐    (future windows…)
               │ Status      │    │ Journal     │
               │ Monitor     │    │ Viewer      │
               │ (Toplevel)  │    │ (Toplevel)  │
               └─────────────┘    └─────────────┘

        tk.Tk (hidden root — keeps the event loop alive)

The hidden root window is never shown.  All user-facing windows are
``tk.Toplevel`` instances.  Closing the last window (or pressing Quit)
calls ``root.quit()`` which terminates ``mainloop()`` and allows the
``finally`` block below to stop the backend cleanly.

Run
---
    python main.py
"""
from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path

# ── Ensure the project root is on sys.path ────────────────────────────────────
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from ed_app import EDApp
from GUI.ed_status_monitor import EDStatusMonitor


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

    # ── 3. Open the initial window ────────────────────────────────────────
    #       quit_on_close=True → closing this window stops the event loop
    EDStatusMonitor(root, app, quit_on_close=True)

    # ── 4. Run the event loop ─────────────────────────────────────────────
    try:
        root.mainloop()
    finally:
        app.stop()


if __name__ == "__main__":
    main()
