"""
ED Cockpit — Agent Route Panel
================================
ttk.Frame displayed as the third tab in the agent's EDCockpitWindow.

Unlike client panels, this panel has direct access to the RouteRole
instance and calls ``EDApp.plan_route()`` to trigger Spansh API calls.
Clipboard operations write directly to the local tkinter clipboard.

Layout (top to bottom)
-----------------------
  ┌─────────────────────────────────────────────────┐
  │  FLEET CARRIER ROUTE              (section hdr) │
  ├─────────────────────────────────────────────────┤
  │  FC Location :  <system>                        │
  │  Total dist. :  <LY>                            │
  │  Tritium avl.:  <t>                             │
  │  Tritium ndd.:  <t>                             │
  ├─────────────────────────────────────────────────┤
  │  [ Waypoint table — Treeview with scrollbars ]  │
  ├─────────────────────────────────────────────────┤
  │  [ New Route ]  [ Copy Next Waypoint ]          │
  ├─────────────────────────────────────────────────┤
  │  CURRENT SHIP                     (section hdr) │
  │  FC Distance :  <LY>                            │
  │  [ Call Back FC ]                               │
  └─────────────────────────────────────────────────┘
"""
from __future__ import annotations

import queue
import tkinter as tk
from tkinter import ttk, messagebox
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from agent.core.ed_app import EDApp

# ── Theme (in sync with the rest of the agent UI) ─────────────────────────────
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

_POLL_MS  = 150   # queue polling interval


class AgentRoutePanel(ttk.Frame):
    """
    Route panel for the agent window (runs on the tkinter main thread).

    Parameters
    ----------
    parent : tk.Misc
        Parent widget (a ttk.Notebook tab frame).
    app : EDApp
        The running agent core.
    """

    def __init__(self, parent: tk.Misc, app: "EDApp") -> None:
        super().__init__(parent)
        self._app = app

        # Local event queue fed by the RouteRole GUI callback
        self._queue: queue.Queue = queue.Queue()

        # Cache of last-known state for clipboard helpers
        self._state: dict = {}

        self._build_ui()
        self._subscribe_to_role()
        self._poll()

    # ── Subscription ───────────────────────────────────────────────────────

    def _subscribe_to_role(self) -> None:
        from agent.roles.route import RouteRole
        role: Optional[RouteRole] = self._app._roles.get("route")  # type: ignore[assignment]
        if role is None:
            return
        role.subscribe_gui(self._on_role_event)
        # Pre-populate from existing snapshot if available
        snapshot = role.get_snapshot()
        if snapshot:
            self._queue.put(("StateSnapshot", snapshot))

    def _on_role_event(self, event: str, data: dict) -> None:
        """Called from any thread by RouteRole — put onto local queue."""
        self._queue.put((event, data))

    def destroy(self) -> None:
        from agent.roles.route import RouteRole
        role: Optional[RouteRole] = self._app._roles.get("route")  # type: ignore[assignment]
        if role:
            role.unsubscribe_gui(self._on_role_event)
        super().destroy()

    # ── Queue polling ──────────────────────────────────────────────────────

    def _poll(self) -> None:
        try:
            while True:
                event, data = self._queue.get_nowait()
                self._dispatch(event, data)
        except queue.Empty:
            pass
        self.after(_POLL_MS, self._poll)

    def _dispatch(self, event: str, data: dict) -> None:
        self._state = data
        if event in ("StateSnapshot", "RouteLoaded", "RouteProgress",
                     "TritiumUpdate", "ShipMoved"):
            self._refresh_all(data)
        elif event == "RoutePending":
            self._set_status(
                f"Planning route to {data.get('destination', '?')} …", ACCENT
            )
        elif event == "RouteError":
            self._set_status(f"Error: {data.get('message', '?')}", "red")
        elif event == "RouteProgress":
            self._refresh_all(data)

    # ── UI construction ────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        self.configure(style="TFrame")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        outer = tk.Frame(self, bg=BG)
        outer.grid(row=0, column=0, sticky="nsew")
        outer.rowconfigure(1, weight=1)   # table row expands
        outer.columnconfigure(0, weight=1)

        # ── Section 1: Fleet carrier info ──────────────────────────────
        fc_section = tk.Frame(outer, bg=BG)
        fc_section.grid(row=0, column=0, sticky="ew", padx=4, pady=(4, 0))
        fc_section.columnconfigure(0, weight=1)

        self._make_section_header(fc_section, "FLEET CARRIER ROUTE")

        info = tk.Frame(fc_section, bg=PANEL_BG)
        info.pack(fill="x", padx=4, pady=(0, 2))
        info.columnconfigure(1, weight=1)

        self._lbl_fc_loc   = self._make_info_row(info, 0, "FC Location :")
        self._lbl_distance = self._make_info_row(info, 1, "Total dist.  :")
        self._lbl_trit_avl = self._make_info_row(info, 2, "Tritium avl. :")
        self._lbl_trit_ndd = self._make_info_row(info, 3, "Tritium ndd. :")

        # Status line (planning feedback)
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

        # ── Section 3: FC buttons ──────────────────────────────────────
        btn_fc = tk.Frame(outer, bg=BG)
        btn_fc.grid(row=2, column=0, sticky="ew", padx=4, pady=(0, 2))

        _BTN = dict(
            bg=BTN_BG, fg=GREEN_FG,
            activebackground=BTN_ACT, activeforeground=TEXT_FG,
            relief="flat", bd=0, font=FONT_BODY,
            cursor="hand2", padx=10, pady=4,
        )
        tk.Button(btn_fc, text="New Route",
                  command=self._on_new_route, **_BTN).pack(side="left", padx=4, pady=4)
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

        tree.column("system",    width=220, minwidth=140, stretch=True)
        tree.column("distance",  width=90,  minwidth=70,  stretch=False, anchor="e")
        tree.column("fuel_cost", width=80,  minwidth=60,  stretch=False, anchor="e")
        tree.column("done",      width=55,  minwidth=45,  stretch=False, anchor="center")

        tree.tag_configure("done",    foreground=GREY_FG)
        tree.tag_configure("current", foreground=HEADER_FG)
        tree.tag_configure("future",  foreground=TEXT_FG)

        return tree, v_sb, h_sb

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
            done_txt = "yes" if done else "—"

            if done and i < current_idx:
                tag = "done"
            elif i == current_idx:
                tag = "current"
                done_txt = "→"
            else:
                tag = "future"

            self._tree.insert(
                "", "end",
                values=(system, dist_txt, fuel_txt, done_txt),
                tags=(tag,),
            )

        # Scroll to the current waypoint row
        if 0 <= current_idx < len(waypoints):
            children = self._tree.get_children()
            if current_idx < len(children):
                self._tree.see(children[current_idx])

    def _set_status(self, text: str, color: str) -> None:
        self._lbl_status.config(text=text, fg=color)

    # ── Button handlers ────────────────────────────────────────────────────

    def _on_new_route(self) -> None:
        from agent.roles.route import RouteRole
        role: Optional[RouteRole] = self._app._roles.get("route")  # type: ignore[assignment]
        fc_system = role.fc_system if role else ""
        tritium   = role.tritium_available if role else 0.0
        last_dest = role.destination if role else ""
        _NewRouteDialog(self, self._app, fc_system, tritium, last_dest)

    def _on_copy_next(self) -> None:
        system = self._next_waypoint_system()
        if not system:
            messagebox.showinfo("Copy Waypoint", "No next waypoint available.")
            return
        self.clipboard_clear()
        self.clipboard_append(system)
        self._set_status(f"Copied: {system}", GREEN_FG)

    def _on_call_back_fc(self) -> None:
        system = self._state.get("ship_system", "")
        if not system:
            messagebox.showinfo("Call Back FC", "Ship position unknown.")
            return
        self.clipboard_clear()
        self.clipboard_append(system)
        self._set_status(f"Copied: {system}", GREEN_FG)

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


# ── New Route dialog ───────────────────────────────────────────────────────────

class _NewRouteDialog(tk.Toplevel):
    """
    Modal dialog for entering a new FC route destination.

    Shows the current FC position (read-only), the current tritium
    available (read-only), and an editable destination field.
    Buttons: [Plan Route] and [Cancel].
    """

    def __init__(
        self,
        parent:   tk.Misc,
        app:      "EDApp",
        fc_system: str,
        tritium:  float,
        last_dest: str,
    ) -> None:
        super().__init__(parent)
        self._app = app

        self.title("Plan Fleet Carrier Route")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.grab_set()   # modal

        self._build(fc_system, tritium, last_dest)
        self.transient(parent)

        # Centre over parent
        self.update_idletasks()
        pw = parent.winfo_rootx()
        py = parent.winfo_rooty()
        w, h = self.winfo_width(), self.winfo_height()
        self.geometry(f"+{pw + 60}+{py + 60}")

    def _build(self, fc_system: str, tritium: float, last_dest: str) -> None:
        # Header
        hdr = tk.Frame(self, bg=HEADER_BG, pady=4)
        hdr.pack(fill="x")
        tk.Label(hdr, text="  Plan Fleet Carrier Route",
                 bg=HEADER_BG, fg=HEADER_FG, font=FONT_BOLD).pack(side="left", padx=6)

        body = tk.Frame(self, bg=PANEL_BG, padx=16, pady=12)
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)

        def row_label(r: int, text: str) -> None:
            tk.Label(body, text=text, bg=PANEL_BG, fg=ACCENT,
                     font=FONT_BOLD, anchor="w").grid(
                row=r, column=0, sticky="w", pady=3, padx=(0, 8))

        # FC Location (read-only)
        row_label(0, "FC Location :")
        tk.Label(body, text=fc_system or "Unknown",
                 bg=PANEL_BG, fg=TEXT_FG, font=FONT_BODY, anchor="w",
                 width=32).grid(row=0, column=1, sticky="ew")

        # Tritium available (read-only)
        row_label(1, "Tritium avl. :")
        tk.Label(body, text=f"{tritium:.0f} t" if tritium else "—",
                 bg=PANEL_BG, fg=TEXT_FG, font=FONT_BODY, anchor="w",
                 ).grid(row=1, column=1, sticky="ew")

        # Destination entry
        row_label(2, "Destination :")
        self._dest_var = tk.StringVar(value=last_dest)
        entry = tk.Entry(body, textvariable=self._dest_var,
                         bg="#1a1a3a", fg=TEXT_FG, insertbackground=TEXT_FG,
                         font=FONT_BODY, relief="flat", width=34)
        entry.grid(row=2, column=1, sticky="ew", pady=(4, 8))
        entry.focus_set()
        entry.icursor("end")

        # Status label
        self._lbl_status = tk.Label(body, text="", bg=PANEL_BG, fg=GREY_FG,
                                    font=FONT_TINY)
        self._lbl_status.grid(row=3, column=0, columnspan=2, sticky="w")

        # Buttons
        btn_row = tk.Frame(self, bg=BG, pady=8)
        btn_row.pack(fill="x")
        _BTN = dict(bg=BTN_BG, fg=GREEN_FG, activebackground=BTN_ACT,
                    activeforeground=TEXT_FG, relief="flat", bd=0,
                    font=FONT_BODY, cursor="hand2", padx=14, pady=5)

        tk.Button(btn_row, text="Plan Route",
                  command=self._on_plan, **_BTN).pack(side="left", padx=12)
        tk.Button(btn_row, text="Cancel",
                  command=self.destroy,
                  bg=BTN_BG, fg=GREY_FG, activebackground=BTN_ACT,
                  activeforeground=TEXT_FG, relief="flat", bd=0,
                  font=FONT_BODY, cursor="hand2", padx=14, pady=5,
                  ).pack(side="left", padx=4)

        entry.bind("<Return>", lambda _e: self._on_plan())
        entry.bind("<Escape>", lambda _e: self.destroy())

    def _on_plan(self) -> None:
        destination = self._dest_var.get().strip()
        if not destination:
            self._lbl_status.config(text="Please enter a destination system.", fg="red")
            return
        self._app.plan_route(destination)
        self.destroy()
