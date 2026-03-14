"""
ED Assist — Client Manager Window
====================================
Tkinter/ttk window displayed on the agent machine.  Allows the operator to
see all known clients, their last-seen time, and their assigned roles, and
to reassign roles without restarting any client.

Layout (planned)
----------------
  ┌──────────────────────────────────────────────────────────────┐
  │  ED ASSIST — CLIENT MANAGER                                  │
  ├──────────────────────────────────────────────────────────────┤
  │  Client ID        Last Seen      Status    Roles             │
  │  ─────────────────────────────────────────────────────────── │
  │  ed-client-7f3a   2 min ago      Online    Exobio, Session   │
  │  ed-client-2b9c   just now       Online    Mining            │
  │  ed-client-a1d0   never          Pending   —                 │
  ├──────────────────────────────────────────────────────────────┤
  │  [Add Client]  [Edit Roles]  [Revoke]  [Copy Token]          │
  └──────────────────────────────────────────────────────────────┘

TODO — Phase 7: implement full UI.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

# TODO — Phase 7: import ClientRegistry once network layer is implemented


class ClientManager(tk.Toplevel):
    """
    Agent-side client management window — stub, to be implemented in Phase 7.
    """

    def __init__(self, parent: tk.Misc, **kwargs) -> None:
        super().__init__(parent, **kwargs)
        self.title("ED Assist — Client Manager")
        self.resizable(True, True)
        self._build_ui()

    def _build_ui(self) -> None:
        ttk.Label(
            self,
            text="Client Manager — coming in Phase 7",
            padding=20,
        ).pack()
