"""
ED Assist — Navigation Panel
==============================
Client-side panel for the Navigation role.

Displays live surface/planet navigation data received from the agent:
  • Current approach / body status (ApproachBody, LeaveBody)
  • Touchdown / Liftoff coordinates
  • Surface signal sources from DSS scans (SAASignalsFound)
  • Detected biological genuses (from SAASignalsFound)
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
GREY_FG   = "#888888"
FONT_BOLD = ("Consolas", 10, "bold")
FONT_BODY = ("Consolas", 9)
FONT_TINY = ("Consolas", 8)


class NavigationPanel(BasePanel):
    """Live planet/surface navigation panel."""

    role_name = Role.NAVIGATION

    def _build_ui(self) -> None:
        self.configure(style="TFrame")

        # ── Header ────────────────────────────────────────────────────────
        hdr = tk.Frame(self, bg=HEADER_BG, pady=4)
        hdr.pack(fill="x")
        tk.Label(hdr, text="SURFACE NAVIGATION", bg=HEADER_BG, fg=HEADER_FG,
                 font=FONT_BOLD).pack(side="left", padx=10)

        # ── Body status ───────────────────────────────────────────────────
        self._section("CURRENT BODY")
        body = tk.Frame(self, bg=PANEL_BG)
        body.pack(fill="x", padx=4, pady=(0, 4))
        body.columnconfigure(1, weight=1)

        self._make_row(body, 0, "System:",   "_lbl_system")
        self._make_row(body, 1, "Body:",     "_lbl_body")
        self._make_row(body, 2, "Status:",   "_lbl_body_status")

        # ── Landing coordinates ───────────────────────────────────────────
        self._section("LANDING COORDINATES")
        coords = tk.Frame(self, bg=PANEL_BG)
        coords.pack(fill="x", padx=4, pady=(0, 4))
        coords.columnconfigure(1, weight=1)

        self._make_row(coords, 0, "Latitude:",  "_lbl_lat")
        self._make_row(coords, 1, "Longitude:", "_lbl_lon")
        self._make_row(coords, 2, "Body:",      "_lbl_land_body")

        # ── Surface signals ───────────────────────────────────────────────
        self._section("DSS SURFACE SIGNALS")
        sig_outer = tk.Frame(self, bg=PANEL_BG)
        sig_outer.pack(fill="x", padx=4, pady=(0, 4))

        self._signals_frame = sig_outer
        tk.Label(sig_outer, text="  No DSS data", bg=PANEL_BG, fg=GREY_FG,
                 font=FONT_TINY).pack(anchor="w")

        # ── Biology genuses ───────────────────────────────────────────────
        self._section("BIOLOGICAL SIGNALS")
        gen_outer = tk.Frame(self, bg=PANEL_BG)
        gen_outer.pack(fill="x", padx=4, pady=(0, 4))

        self._genuses_frame = gen_outer
        tk.Label(gen_outer, text="  None detected", bg=PANEL_BG, fg=GREY_FG,
                 font=FONT_TINY).pack(anchor="w")

    # ── Event dispatch ─────────────────────────────────────────────────────

    def on_event(self, event: str, data: dict) -> None:
        if event in ("ApproachBody", "LeaveBody"):
            self._on_approach_leave(event, data)
        elif event in ("Touchdown", "Liftoff"):
            self._on_touchdown_liftoff(event, data)
        elif event == "SAASignalsFound":
            self._on_saa_signals(data)
        elif event == "ScanBaryCentre":
            pass  # no dedicated widget for this event

    # ── Internal handlers ──────────────────────────────────────────────────

    def _on_approach_leave(self, event: str, data: dict) -> None:
        self._lbl_system.config(text=data.get("system", "—"))
        self._lbl_body.config(text=data.get("body", "—"))
        if event == "ApproachBody":
            self._lbl_body_status.config(text="Approaching", fg=ACCENT)
        else:
            self._lbl_body_status.config(text="Departing", fg=GREY_FG)
            # Clear signals on departure
            self._clear_frame(self._signals_frame)
            tk.Label(self._signals_frame, text="  No DSS data",
                     bg=PANEL_BG, fg=GREY_FG, font=FONT_TINY).pack(anchor="w")
            self._clear_frame(self._genuses_frame)
            tk.Label(self._genuses_frame, text="  None detected",
                     bg=PANEL_BG, fg=GREY_FG, font=FONT_TINY).pack(anchor="w")

    def _on_touchdown_liftoff(self, event: str, data: dict) -> None:
        lat  = data.get("latitude",  0.0)
        lon  = data.get("longitude", 0.0)
        body = data.get("body", "—")

        if event == "Touchdown":
            self._lbl_lat.config(text=f"{lat:.4f}°", fg=GREEN_FG)
            self._lbl_lon.config(text=f"{lon:.4f}°", fg=GREEN_FG)
            self._lbl_land_body.config(text=body)
            self._lbl_body_status.config(text="Landed", fg=GREEN_FG)
        else:
            self._lbl_body_status.config(text="Airborne", fg=ACCENT)

    def _on_saa_signals(self, data: dict) -> None:
        body    = data.get("body", "—")
        signals = data.get("signals", [])
        genuses = data.get("genuses", [])

        # Update body display
        self._lbl_body.config(text=body)

        # ── Signals ────────────────────────────────────────────────────
        self._clear_frame(self._signals_frame)
        if signals:
            self._signals_frame.columnconfigure(1, weight=1)
            for i, s in enumerate(signals):
                sig_type = s.get("type", "—")
                count    = s.get("count", 0)
                fg = GREEN_FG if "Bio" in sig_type else TEXT_FG
                tk.Label(self._signals_frame, text=f"  {sig_type}",
                         bg=PANEL_BG, fg=fg, font=FONT_TINY,
                         anchor="w").grid(row=i, column=0, sticky="w", padx=8)
                tk.Label(self._signals_frame, text=str(count),
                         bg=PANEL_BG, fg=fg, font=FONT_TINY
                         ).grid(row=i, column=1, sticky="w")
        else:
            tk.Label(self._signals_frame, text="  No signals",
                     bg=PANEL_BG, fg=GREY_FG, font=FONT_TINY).pack(anchor="w")

        # ── Genuses ────────────────────────────────────────────────────
        self._clear_frame(self._genuses_frame)
        if genuses:
            for g in genuses:
                tk.Label(self._genuses_frame, text=f"  • {g}",
                         bg=PANEL_BG, fg=GREEN_FG, font=FONT_TINY,
                         anchor="w").pack(anchor="w")
        else:
            tk.Label(self._genuses_frame, text="  None detected",
                     bg=PANEL_BG, fg=GREY_FG, font=FONT_TINY).pack(anchor="w")

    # ── Layout helpers ─────────────────────────────────────────────────────

    def _make_row(self, parent: tk.Frame, row: int,
                  label: str, attr: str) -> None:
        tk.Label(parent, text=label, bg=PANEL_BG, fg=ACCENT,
                 font=FONT_BOLD, anchor="w", width=11
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

    @staticmethod
    def _clear_frame(frame: tk.Frame) -> None:
        for w in frame.winfo_children():
            w.destroy()
