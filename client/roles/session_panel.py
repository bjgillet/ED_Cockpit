"""
ED Assist — Session Monitoring Panel
=======================================
Client-side panel for the Session Monitoring role.

Displays live session data received from the agent:
  • Commander name and current ship
  • Current star system, body, and docked/in-flight/on-foot status
  • Last FSD jump details (star class, distance, fuel)
  • Live ship status: fuel bars, hull/shield %, legal state, activity flags
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
ORANGE_FG = "#ff8800"
GREY_FG   = "#888888"
FONT_BOLD = ("Consolas", 10, "bold")
FONT_BODY = ("Consolas", 9)
FONT_TINY = ("Consolas", 8)

_MAX_TIMELINE = 10

# Canvas bar dimensions
_BAR_W = 160
_BAR_H = 10


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

        # ── Ship status (live from Status.json) ───────────────────────────
        self._section("SHIP STATUS")
        ss = tk.Frame(self, bg=PANEL_BG)
        ss.pack(fill="x", padx=4, pady=(0, 4))
        ss.columnconfigure(1, weight=1)

        # Fuel main
        tk.Label(ss, text="Fuel main:", bg=PANEL_BG, fg=ACCENT,
                 font=FONT_BOLD, anchor="w", width=10
                 ).grid(row=0, column=0, sticky="w", padx=8, pady=2)
        self._fuel_main_canvas = tk.Canvas(
            ss, width=_BAR_W, height=_BAR_H, bg=PANEL_BG,
            highlightthickness=0)
        self._fuel_main_canvas.grid(row=0, column=1, sticky="w", pady=2)
        self._lbl_fuel_main = tk.Label(ss, text="—", bg=PANEL_BG, fg=TEXT_FG,
                                       font=FONT_TINY)
        self._lbl_fuel_main.grid(row=0, column=2, sticky="w", padx=4, pady=2)

        # Fuel reservoir
        tk.Label(ss, text="Reservoir:", bg=PANEL_BG, fg=ACCENT,
                 font=FONT_BOLD, anchor="w", width=10
                 ).grid(row=1, column=0, sticky="w", padx=8, pady=2)
        self._fuel_res_canvas = tk.Canvas(
            ss, width=_BAR_W, height=_BAR_H, bg=PANEL_BG,
            highlightthickness=0)
        self._fuel_res_canvas.grid(row=1, column=1, sticky="w", pady=2)
        self._lbl_fuel_res = tk.Label(ss, text="—", bg=PANEL_BG, fg=TEXT_FG,
                                      font=FONT_TINY)
        self._lbl_fuel_res.grid(row=1, column=2, sticky="w", padx=4, pady=2)

        # Hull
        tk.Label(ss, text="Hull:", bg=PANEL_BG, fg=ACCENT,
                 font=FONT_BOLD, anchor="w", width=10
                 ).grid(row=2, column=0, sticky="w", padx=8, pady=2)
        self._hull_canvas = tk.Canvas(
            ss, width=_BAR_W, height=_BAR_H, bg=PANEL_BG,
            highlightthickness=0)
        self._hull_canvas.grid(row=2, column=1, sticky="w", pady=2)
        self._lbl_hull = tk.Label(ss, text="—", bg=PANEL_BG, fg=TEXT_FG,
                                  font=FONT_TINY)
        self._lbl_hull.grid(row=2, column=2, sticky="w", padx=4, pady=2)

        # Shields
        tk.Label(ss, text="Shields:", bg=PANEL_BG, fg=ACCENT,
                 font=FONT_BOLD, anchor="w", width=10
                 ).grid(row=3, column=0, sticky="w", padx=8, pady=2)
        self._shield_canvas = tk.Canvas(
            ss, width=_BAR_W, height=_BAR_H, bg=PANEL_BG,
            highlightthickness=0)
        self._shield_canvas.grid(row=3, column=1, sticky="w", pady=2)
        self._lbl_shield = tk.Label(ss, text="—", bg=PANEL_BG, fg=TEXT_FG,
                                    font=FONT_TINY)
        self._lbl_shield.grid(row=3, column=2, sticky="w", padx=4, pady=2)

        # Legal state + cargo
        self._make_row(ss, 4, "Legal:",   "_lbl_legal")
        self._make_row(ss, 5, "Cargo:",   "_lbl_cargo_t")

        # Activity flags row
        tk.Label(ss, text="Activity:", bg=PANEL_BG, fg=ACCENT,
                 font=FONT_BOLD, anchor="w", width=10
                 ).grid(row=6, column=0, sticky="w", padx=8, pady=2)
        self._lbl_activity = tk.Label(ss, text="—", bg=PANEL_BG, fg=TEXT_FG,
                                      font=FONT_TINY, anchor="w", wraplength=200)
        self._lbl_activity.grid(row=6, column=1, columnspan=2, sticky="w", pady=2)

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
        self._fuel_main_max: float = 0.0

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

        elif event == "Status":
            self._on_status(data)

    # ── Internal helpers ────────────────────────────────────────────────────

    def _on_status(self, data: dict) -> None:
        fuel_main = float(data.get("fuel_main", 0.0))
        fuel_res  = float(data.get("fuel_reservoir", 0.0))
        hull      = float(data.get("hull_health",   1.0))
        shield    = float(data.get("shield_health", 1.0))
        cargo     = float(data.get("cargo", 0.0))
        legal     = str(data.get("legal_state", ""))

        # Track observed maximum for fuel main to size the bar
        if fuel_main > self._fuel_main_max:
            self._fuel_main_max = fuel_main

        fuel_main_frac = (fuel_main / self._fuel_main_max
                          if self._fuel_main_max > 0 else 0.0)
        # Reservoir max is ~0.63 t; cap at 1.0 t for the bar
        fuel_res_frac = min(fuel_res / 1.0, 1.0)

        self._draw_bar(self._fuel_main_canvas, fuel_main_frac,
                       _fuel_color(fuel_main_frac))
        self._draw_bar(self._fuel_res_canvas,  fuel_res_frac,
                       _fuel_color(fuel_res_frac))
        self._draw_bar(self._hull_canvas,   hull,   _health_color(hull))
        self._draw_bar(self._shield_canvas, shield, ACCENT)

        self._lbl_fuel_main.config(text=f"{fuel_main:.2f} t")
        self._lbl_fuel_res.config(text=f"{fuel_res:.2f} t")
        self._lbl_hull.config(text=f"{hull * 100:.0f}%",
                              fg=_health_color(hull))
        self._lbl_shield.config(text=f"{shield * 100:.0f}%")

        legal_color = GREEN_FG if legal == "Clean" else (
            RED_FG if legal in ("Wanted", "Enemy") else ORANGE_FG)
        self._lbl_legal.config(text=legal or "—", fg=legal_color)
        self._lbl_cargo_t.config(text=f"{cargo:.0f} t")

        flags: list[str] = []
        if data.get("docked"):        flags.append("Docked")
        if data.get("landed"):        flags.append("Landed")
        if data.get("in_srv"):        flags.append("In SRV")
        if data.get("on_foot"):       flags.append("On Foot")
        if data.get("supercruise"):   flags.append("Supercruise")
        if data.get("hyperspace"):    flags.append("Hyperspace")
        self._lbl_activity.config(
            text="  ".join(flags) if flags else "In space")

    @staticmethod
    def _draw_bar(canvas: tk.Canvas, frac: float, color: str) -> None:
        canvas.delete("all")
        # Background track
        canvas.create_rectangle(0, 0, _BAR_W, _BAR_H,
                                 fill="#222244", outline="")
        # Fill
        fill_w = max(1, int(_BAR_W * max(0.0, min(1.0, frac))))
        canvas.create_rectangle(0, 0, fill_w, _BAR_H,
                                 fill=color, outline="")

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


# ── Colour helpers ─────────────────────────────────────────────────────────

def _fuel_color(frac: float) -> str:
    if frac > 0.5:
        return GREEN_FG
    if frac > 0.25:
        return ORANGE_FG
    return RED_FG


def _health_color(frac: float) -> str:
    if frac > 0.6:
        return GREEN_FG
    if frac > 0.3:
        return ORANGE_FG
    return RED_FG
