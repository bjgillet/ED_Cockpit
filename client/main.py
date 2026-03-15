"""
ED Cockpit — Client Entry Point
================================
Connects to the ED Agent, registers for assigned roles, and opens the
appropriate role panels.

Architecture overview
---------------------

        ┌────────────────────────────────────────────┐
        │              EDClient  (core/ed_client.py) │
        │  ┌─────────────────────────────────────┐   │
        │  │  WSConnection  (network/ws_conn.py) │   │
        │  │  asyncio WebSocket — background     │   │
        │  └──────────────┬──────────────────────┘   │
        │                 │ queue.Queue               │
        │         _dispatch() → per-role queues       │
        └─────────────────┼──────────────────────────┘
                          │
        ┌─────────────────▼──────────────────────────┐
        │            MainWindow  (GUI/main_window.py) │
        │  ActivityBar + dynamically loaded panels    │
        └────────────────────────────────────────────┘

        tk.Tk (hidden root — keeps the event loop alive)

Run
---
    python client/main.py
"""
from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path

# ── Ensure the project root is on sys.path ────────────────────────────────────
_ROOT   = Path(__file__).resolve().parent.parent  # repo root
_CLIENT = Path(__file__).resolve().parent          # client/
for _p in (_ROOT, _CLIENT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from client.core.ed_client import EDClient
from client.GUI.main_window import MainWindow


def main() -> None:
    client = EDClient()
    client.start()

    root = tk.Tk()
    root.withdraw()
    root.title("ED Cockpit — Client")

    MainWindow(root, client, quit_on_close=True)

    try:
        root.mainloop()
    finally:
        client.stop()


if __name__ == "__main__":
    main()
