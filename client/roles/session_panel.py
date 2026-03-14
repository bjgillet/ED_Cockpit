"""
ED Assist — Session Monitoring Panel
=======================================
Client-side panel for the Session Monitoring role.

Displays live session data received from the agent:
  • Commander name and current ship
  • Current star system, body, and docked/in-flight/on-foot status
  • Last FSD jump details (star class, distance, fuel)
  • Recent event timeline (last 8 events, newest at top)
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from client.roles.base_panel import BasePanel
from shared.roles_def import Role

BG        = "#0d0d1e"
PANEL_BG  = "#10102a"
HEADER_BG = "#b87800"
HEADER_FG = "#ffff00"
ACCENT    = "#4da6ff"
TEXT_FG   = "#ffffff"
GREEN_FG  = "#00cc55"
RED_FG    = "#cc2222"
FONT_BOLD = ("Consolas", 10, "bold")
FONT_BODY = ("Consolas", 9)
FONT_TINY = ("Consolas", 8)

_MAX_TIMELINE = 10


class SessionPanel(BasePanel):
    """Live session monitoring panel."""

    role_name = Role.SESSION_MONITORING

    def _build_ui(self) -> None:
        self.configure(style="TFrame")

        # ── Header ────────────────────────────────────────────────────────
        hdr = tk.Frame(self, bg=HEADER_BG, pady=4)
        hdr.pack(fill="x")
        tk.Label(hdr, text="SESSION MONITOR", bg=HEADER_BG, fg=HEADER_FG,
                 font=FONT_BOLD).pack(side="left", padx=10)

        # ── CMDR / ship row ───────────────────────────────────────────────
        self._section("COMMANDER")
        cmdr = tk.Frame(self, bg=PANEL_BG)
        cmdr.pack(fill="x", padx=4, pady=(0, 4))
        cmdr.columnconfigure(1, weight=1)

        self._make_row(cmdr, 0, "CMDR:",   "_lbl_cmdr")
        self._make_row(cmdr, 1, "Ship:",   "_lbl_ship")
        self._make_row(cmdr, 2, "Credits:", "_lbl_credits")

        # ── Location ──────────────────────────────────────────────────────
        self._section("LOCATION")
        loc = tk.Frame(self, bg=PANEL_BG)
        loc.pack(fill="x", padx=4, pady=(0, 4))
        loc.columnconfigure(1, weight=1)

        self._make_row(loc, 0, "System:",   "_lbl_system")
        self._make_row(loc, 1, "Body:",     "_lbl_body")
        self._make_row(loc, 2, "Status:",   "_lbl_status")
        self._make_row(loc, 3, "Station:",  "_lbl_station")

        # ── Last jump ─────────────────────────────────────────────────────
        self._section("LAST FSD JUMP")
        jump = tk.Frame(self, bg=PANEL_BG)
        jump.pack(fill="x", padx=4, pady=(0, 4))
        jump.columnconfigure(1, weight=1)

        self._make_row(jump, 0, "System:",    "_lbl_jump_sys")
        self._make_row(jump, 1, "Star:",      "_lbl_jump_star")
        self._make_row(jump, 2, "Distance:",  "_lbl_jump_dist")
        self._make_row(jump, 3, "Fuel used:", "_lbl_jump_fuel")
        self._make_row(jump, 4, "Fuel left:", "_lbl_jump_left")

        # ── Timeline ──────────────────────────────────────────────────────
        self._section("EVENT TIMELINE")
        tl_outer = tk.Frame(self, bg=PANEL_BG)
        tl_outer.pack(fill="both", expand=True, padx=4, pady=(0, 4))

        self._timeline = tk.Listbox(
            tl_outer,
            bg=PANEL_BG, fg=TEXT_FG,
            selectbackground="#1a2a4a",
            font=FONT_TINY,
            relief="flat",
            highlightthickness=0,
            activestyle="none",
        )
        vsb = ttk.Scrollbar(tl_outer, orient="vertical",
                            command=self._timeline.yview)
        self._timeline.configure(yscrollcommand=vsb.set)
        self._timeline.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        # ── State ─────────────────────────────────────────────────────────
        self._timeline_entries: list[str] = []

    # ── Event dispatch ─────────────────────────────────────────────────────

    def on_event(self, event: str, data: dict) -> None:
        if event == "LoadGame":
            self._lbl_cmdr.config(text=data.get("cmdr", "—"))
            self._lbl_ship.config(text=data.get("ship", "—"))
            self._lbl_credits.config(
                text=f"{data.get('credits', 0):,} CR")
            self._push_timeline(f"LoadGame  CMDR {data.get('cmdr', '?')}")

        elif event == "Location":
            self._update_location(data)
            self._push_timeline(
                f"Location  {data.get('system', '?')} / {data.get('body', '?')}")

        elif event == "FSDJump":
            self._update_location(data)
            self._lbl_jump_sys.config(text=data.get("system", "—"))
            self._lbl_jump_star.config(text=data.get("star_class", "—"))
            self._lbl_jump_dist.config(
                text=f"{data.get('distance', 0):.2f} ly")
            self._lbl_jump_fuel.config(
                text=f"{data.get('fuel_used', 0):.2f} t")
            self._lbl_jump_left.config(
                text=f"{data.get('fuel_level', 0):.2f} t")
            self._push_timeline(
                f"FSDJump   → {data.get('system', '?')}  "
                f"({data.get('distance', 0):.1f} ly)")

        elif event == "Docked":
            self._lbl_status.config(text="Docked", fg=GREEN_FG)
            self._lbl_station.config(text=data.get("station", "—"))
            self._push_timeline(
                f"Docked    {data.get('station', '?')} "
                f"({data.get('system', '?')})")

        elif event == "Undocked":
            self._lbl_status.config(text="In flight", fg=ACCENT)
            self._lbl_station.config(text="—")
            self._push_timeline(
                f"Undocked  from {data.get('station', '?')}")

        elif event == "Died":
            self._lbl_status.config(text="DESTROYED", fg=RED_FG)
            self._push_timeline("Died      ☠")

        elif event == "Shutdown":
            self._lbl_status.config(text="Game offline", fg="#888888")
            self._push_timeline("Shutdown")

    # ── Internal helpers ────────────────────────────────────────────────────

    def _update_location(self, data: dict) -> None:
        self._lbl_system.config(text=data.get("system", "—"))
        self._lbl_body.config(text=data.get("body", "—"))
        docked = data.get("docked", False)
        self._lbl_status.config(
            text="Docked" if docked else "In flight",
            fg=GREEN_FG if docked else ACCENT,
        )
        self._lbl_station.config(text=data.get("station", "—"))

    def _push_timeline(self, text: str) -> None:
        self._timeline_entries.insert(0, text)
        if len(self._timeline_entries) > _MAX_TIMELINE:
            self._timeline_entries = self._timeline_entries[:_MAX_TIMELINE]
        self._timeline.delete(0, "end")
        for entry in self._timeline_entries:
            self._timeline.insert("end", f"  {entry}")

    def _make_row(
        self, parent: tk.Frame, row: int, label: str, attr: str
    ) -> None:
        tk.Label(parent, text=label, bg=PANEL_BG, fg=ACCENT,
                 font=FONT_BOLD, anchor="w", width=10
                 ).grid(row=row, column=0, sticky="w", padx=8, pady=1)
        lbl = tk.Label(parent, text="—", bg=PANEL_BG, fg=TEXT_FG,
                       font=FONT_BODY, anchor="w")
        lbl.grid(row=row, column=1, sticky="w", pady=1)
        setattr(self, attr, lbl)

    def _section(self, title: str) -> None:
        bar = tk.Frame(self, bg="#2a2a4a", pady=1)
        bar.pack(fill="x", pady=(4, 0))
        tk.Label(bar, text=f"  {title}", bg="#2a2a4a", fg=HEADER_FG,
                 font=FONT_BOLD, anchor="w").pack(fill="x", padx=4, pady=2)
