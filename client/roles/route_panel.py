"""
ED Cockpit — Route Panel (Client)
===================================
Client-side panel for the Route role (fleet-carrier route tracking).

Receives ``EventMessage`` events from the agent and renders the route
table, FC/ship info, and action buttons.

Events handled (``event`` field of the ``EventMessage.data`` dict)
------------------------------------------------------------------
  StateSnapshot  — Initial full state pushed on connect.
  RouteLoaded    — New route calculated; full state payload.
  RouteProgress  — FC jumped; full state payload (same schema as above).
  TritiumUpdate  — Tritium changed; full state payload.
  ShipMoved      — Ship jumped; full state payload.
  RoutePending   — Route calculation in progress.
  RouteError     — Route calculation failed.

Button actions (sent to the agent as ActionMessages)
-----------------------------------------------------
  "Copy Next Waypoint" → sends ``action="clipboard"`` with the system name.
  "Call Back FC"       → sends ``action="clipboard"`` with the ship's
                         current system name.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from client.roles.base_panel import BasePanel
from shared.roles_def import Role

# ── Theme ──────────────────────────────────────────────────────────────────────
BG        = "#0d0d1e"
PANEL_BG  = "#10102a"
HEADER_BG = "#b87800"
HEADER_FG = "#ffff00"
SEP_BG    = "#2a2a4a"
ACCENT    = "#4da6ff"
TEXT_FG   = "#ffffff"
GREEN_FG  = "#00cc55"
GREY_FG   = "#888888"
BTN_BG    = "#1a1a3a"
BTN_ACT   = "#2a2a5a"

FONT_BOLD = ("Consolas", 10, "bold")
FONT_BODY = ("Consolas", 9)
FONT_TINY = ("Consolas", 8)


class RoutePanel(BasePanel):
    """
    Client-side fleet-carrier route panel.

    Inherits queue polling and action sending from ``BasePanel``.
    """

    role_name = Role.ROUTE

    # ── UI construction ────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        self.configure(style="TFrame")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        outer = tk.Frame(self, bg=BG)
        outer.grid(row=0, column=0, sticky="nsew")
        outer.rowconfigure(1, weight=1)
        outer.columnconfigure(0, weight=1)

        # ── Section 1: Fleet carrier info ──────────────────────────────
        fc_section = tk.Frame(outer, bg=BG)
        fc_section.grid(row=0, column=0, sticky="ew", padx=4, pady=(4, 0))
        fc_section.columnconfigure(0, weight=1)

        self._make_section_header(fc_section, "FLEET CARRIER ROUTE")

        info = tk.Frame(fc_section, bg=PANEL_BG)
        info.pack(fill="x", padx=4, pady=(0, 2))
        info.columnconfigure(1, weight=1)

        self._lbl_fc_loc = self._make_info_row(info, 0, "FC Location :")

        # Compact single-line row for distance + tritium figures
        stats_row = tk.Frame(info, bg=PANEL_BG)
        stats_row.grid(row=1, column=0, columnspan=2, sticky="ew", padx=4, pady=1)

        _LBL = dict(bg=PANEL_BG, fg=ACCENT,  font=FONT_BOLD)
        _VAL = dict(bg=PANEL_BG, fg=TEXT_FG, font=FONT_BODY)

        tk.Label(stats_row, text="Total dist. :",  **_LBL).pack(side="left", padx=(4, 2))
        self._lbl_distance = tk.Label(stats_row, text="—", **_VAL)
        self._lbl_distance.pack(side="left", padx=(0, 10))

        tk.Label(stats_row, text="Tritium avl. :", **_LBL).pack(side="left", padx=(0, 2))
        self._lbl_trit_avl = tk.Label(stats_row, text="—", **_VAL)
        self._lbl_trit_avl.pack(side="left", padx=(0, 10))

        tk.Label(stats_row, text="Tritium ndd. :", **_LBL).pack(side="left", padx=(0, 2))
        self._lbl_trit_ndd = tk.Label(stats_row, text="—", **_VAL)
        self._lbl_trit_ndd.pack(side="left", padx=(0, 4))

        self._lbl_status = tk.Label(
            fc_section, text="", bg=BG, fg=GREY_FG,
            font=FONT_TINY, anchor="w",
        )
        self._lbl_status.pack(fill="x", padx=8)

        # ── Section 2: Waypoint table ──────────────────────────────────
        tbl_outer = tk.Frame(outer, bg=BG)
        tbl_outer.grid(row=1, column=0, sticky="nsew", padx=4, pady=2)
        tbl_outer.rowconfigure(0, weight=1)
        tbl_outer.columnconfigure(0, weight=1)

        self._tree, v_sb, h_sb = self._build_treeview(tbl_outer)
        self._tree.grid(row=0, column=0, sticky="nsew")
        v_sb.grid(row=0, column=1, sticky="ns")
        h_sb.grid(row=1, column=0, sticky="ew")

        # ── Section 3: FC action buttons ───────────────────────────────
        btn_fc = tk.Frame(outer, bg=BG)
        btn_fc.grid(row=2, column=0, sticky="ew", padx=4, pady=(0, 2))

        _BTN = dict(
            bg=BTN_BG, fg=GREEN_FG,
            activebackground=BTN_ACT, activeforeground=TEXT_FG,
            relief="flat", bd=0, font=FONT_BODY,
            cursor="hand2", padx=10, pady=4,
        )
        tk.Button(btn_fc, text="Copy Next Waypoint",
                  command=self._on_copy_next, **_BTN).pack(side="left", padx=4, pady=4)

        # ── Section 4: Ship info ───────────────────────────────────────
        ship_section = tk.Frame(outer, bg=BG)
        ship_section.grid(row=3, column=0, sticky="ew", padx=4, pady=(4, 4))
        ship_section.columnconfigure(0, weight=1)

        self._make_section_header(ship_section, "CURRENT SHIP")

        ship_info = tk.Frame(ship_section, bg=PANEL_BG)
        ship_info.pack(fill="x", padx=4, pady=(0, 2))
        ship_info.columnconfigure(1, weight=1)

        self._lbl_fc_dist = self._make_info_row(ship_info, 0, "FC Distance  :")

        btn_ship = tk.Frame(ship_section, bg=BG)
        btn_ship.pack(fill="x", padx=4)
        tk.Button(btn_ship, text="Call Back FC",
                  command=self._on_call_back_fc, **_BTN).pack(side="left", padx=4, pady=4)

        # Internal state cache
        self._state: dict = {}

    def _build_treeview(
        self, parent: tk.Frame
    ) -> tuple[ttk.Treeview, ttk.Scrollbar, ttk.Scrollbar]:
        style = ttk.Style(self)
        style.configure("Route.Treeview",
                        background=PANEL_BG, foreground=TEXT_FG,
                        fieldbackground=PANEL_BG, font=FONT_BODY,
                        rowheight=20)
        style.configure("Route.Treeview.Heading",
                        background=SEP_BG, foreground=HEADER_FG,
                        font=FONT_BOLD)
        style.map("Route.Treeview",
                  background=[("selected", "#2a2a5a")],
                  foreground=[("selected", TEXT_FG)])

        v_sb = ttk.Scrollbar(parent, orient="vertical")
        h_sb = ttk.Scrollbar(parent, orient="horizontal")

        tree = ttk.Treeview(
            parent,
            style="Route.Treeview",
            columns=("system", "distance", "fuel_cost", "done"),
            show="headings",
            yscrollcommand=v_sb.set,
            xscrollcommand=h_sb.set,
        )
        v_sb.config(command=tree.yview)
        h_sb.config(command=tree.xview)

        tree.heading("system",    text="Waypoint")
        tree.heading("distance",  text="Distance")
        tree.heading("fuel_cost", text="Tritium")
        tree.heading("done",      text="Done")

        tree.column("system",    width=200, minwidth=120, stretch=True)
        tree.column("distance",  width=85,  minwidth=65,  stretch=False, anchor="e")
        tree.column("fuel_cost", width=75,  minwidth=55,  stretch=False, anchor="e")
        tree.column("done",      width=50,  minwidth=40,  stretch=False, anchor="center")

        tree.tag_configure("done",   foreground=HEADER_FG)
        tree.tag_configure("future", foreground=TEXT_FG)

        return tree, v_sb, h_sb

    # ── BasePanel contract ─────────────────────────────────────────────────

    def on_event(self, event: str, data: dict) -> None:
        if event in ("StateSnapshot", "RouteLoaded", "RouteProgress",
                     "TritiumUpdate", "ShipMoved"):
            self._state = data
            self._refresh_all(data)
        elif event == "RoutePending":
            dest = data.get("destination", "?")
            self._set_status(f"Planning route to {dest} …", ACCENT)
        elif event == "RouteError":
            self._set_status(f"Error: {data.get('message', '?')}", "red")

    # ── Refresh helpers ────────────────────────────────────────────────────

    def _refresh_all(self, data: dict) -> None:
        fc_sys  = data.get("fc_system", "") or "—"
        t_dist  = data.get("total_distance")
        t_avl   = data.get("tritium_available")
        t_ndd   = data.get("tritium_needed")
        fc_dist = data.get("fc_distance")

        self._lbl_fc_loc.config(text=fc_sys)
        self._lbl_distance.config(
            text=f"{t_dist:.1f} LY" if t_dist is not None else "—"
        )
        self._lbl_trit_avl.config(
            text=f"{t_avl:.0f} t" if t_avl is not None else "—"
        )
        self._lbl_trit_ndd.config(
            text=f"{t_ndd:.0f} t" if t_ndd is not None else "—"
        )
        self._lbl_fc_dist.config(
            text=f"{fc_dist:.1f} LY" if fc_dist is not None else "—"
        )

        self._refresh_table(
            data.get("waypoints", []),
            data.get("current_idx", -1),
        )
        self._set_status("", GREY_FG)

    def _refresh_table(self, waypoints: list, current_idx: int) -> None:
        self._tree.delete(*self._tree.get_children())
        for i, wp in enumerate(waypoints):
            system   = wp.get("system", "")
            distance = wp.get("distance", 0.0)
            fuel     = wp.get("fuel_cost", 0.0)
            done     = wp.get("done", False)

            dist_txt = f"{distance:.1f} LY" if distance else "—"
            fuel_txt = f"{fuel:.0f} t"      if fuel     else "—"

            if done:
                tag      = "done"
                done_txt = "yes"
            else:
                tag      = "future"
                done_txt = "—"

            self._tree.insert(
                "", "end",
                values=(system, dist_txt, fuel_txt, done_txt),
                tags=(tag,),
            )

        if 0 <= current_idx < len(waypoints):
            children = self._tree.get_children()
            if current_idx < len(children):
                self._tree.see(children[current_idx])

    def _set_status(self, text: str, color: str) -> None:
        self._lbl_status.config(text=text, fg=color)

    # ── Button handlers ────────────────────────────────────────────────────

    def _on_copy_next(self) -> None:
        system = self._next_waypoint_system()
        if not system:
            self._set_status("No next waypoint available.", GREY_FG)
            return
        self.send_action("clipboard", system)
        self._set_status(f"Sent to agent clipboard: {system}", GREEN_FG)

    def _on_call_back_fc(self) -> None:
        system = self._state.get("ship_system", "")
        if not system:
            self._set_status("Ship position unknown.", GREY_FG)
            return
        self.send_action("clipboard", system)
        self._set_status(f"Sent to agent clipboard: {system}", GREEN_FG)

    def _next_waypoint_system(self) -> str:
        waypoints   = self._state.get("waypoints", [])
        current_idx = self._state.get("current_idx", -1)
        next_idx    = current_idx + 1
        if 0 <= next_idx < len(waypoints):
            return waypoints[next_idx].get("system", "")
        return ""

    # ── Layout helpers ─────────────────────────────────────────────────────

    def _make_section_header(self, parent: tk.Frame, title: str) -> None:
        hdr = tk.Frame(parent, bg=HEADER_BG, pady=3)
        hdr.pack(fill="x")
        tk.Label(
            hdr, text=f"  {title}", bg=HEADER_BG, fg=HEADER_FG,
            font=FONT_BOLD, anchor="w",
        ).pack(fill="x", padx=4)

    def _make_info_row(
        self, parent: tk.Frame, row: int, label: str
    ) -> tk.Label:
        tk.Label(
            parent, text=label, bg=PANEL_BG, fg=ACCENT,
            font=FONT_BOLD, anchor="w", width=14,
        ).grid(row=row, column=0, sticky="w", padx=8, pady=1)
        val = tk.Label(
            parent, text="—", bg=PANEL_BG, fg=TEXT_FG,
            font=FONT_BODY, anchor="w",
        )
        val.grid(row=row, column=1, sticky="ew", padx=4, pady=1)
        return val


