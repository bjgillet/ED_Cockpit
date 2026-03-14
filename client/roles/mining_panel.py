"""
ED Assist — Mining Panel
==========================
Client-side panel for the Mining role.

Displays live mining data received from the agent:
  • Current asteroid composition (ProspectedAsteroid)
  • Refined ore tally (MiningRefined)
  • Drone launch counter (Collector / Prospector)
  • Cracked asteroid counter (AsteroidCracked)
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from client.roles.base_panel import BasePanel
from shared.roles_def import Role

BG         = "#0d0d1e"
PANEL_BG   = "#10102a"
HEADER_BG  = "#b87800"
HEADER_FG  = "#ffff00"
ACCENT_FG  = "#4da6ff"
TEXT_FG    = "#ffffff"
GREEN_FG   = "#00cc55"
FONT_BOLD  = ("Consolas", 10, "bold")
FONT_BODY  = ("Consolas", 9)
FONT_PATH  = ("Consolas", 8)

_CONTENT_COLORS = {
    "Low":    "#888888",
    "Medium": "#ff8800",
    "High":   "#00cc55",
}


class MiningPanel(BasePanel):
    """Live mining panel: asteroid composition + refined ore tally."""

    role_name = Role.MINING

    def _build_ui(self) -> None:
        self.configure(style="TFrame")

        # ── Header ────────────────────────────────────────────────────────
        hdr = tk.Frame(self, bg=HEADER_BG, pady=4)
        hdr.pack(fill="x")
        tk.Label(hdr, text="ASTEROID MINING", bg=HEADER_BG, fg=HEADER_FG,
                 font=FONT_BOLD).pack(side="left", padx=10)

        # ── Prospected asteroid section ───────────────────────────────────
        self._section("CURRENT ASTEROID")
        ast = tk.Frame(self, bg=PANEL_BG)
        ast.pack(fill="x", padx=4, pady=(0, 4))
        ast.columnconfigure(1, weight=1)

        tk.Label(ast, text="Content:", bg=PANEL_BG, fg=ACCENT_FG,
                 font=FONT_BOLD, anchor="w").grid(row=0, column=0, sticky="w", padx=8, pady=2)
        self._lbl_content = tk.Label(ast, text="—", bg=PANEL_BG, fg=TEXT_FG,
                                     font=FONT_BODY)
        self._lbl_content.grid(row=0, column=1, sticky="w", pady=2)

        tk.Label(ast, text="Motherlode:", bg=PANEL_BG, fg=ACCENT_FG,
                 font=FONT_BOLD, anchor="w").grid(row=1, column=0, sticky="w", padx=8, pady=2)
        self._lbl_motherlode = tk.Label(ast, text="—", bg=PANEL_BG, fg=GREEN_FG,
                                        font=FONT_BODY)
        self._lbl_motherlode.grid(row=1, column=1, sticky="w", pady=2)

        tk.Label(ast, text="Remaining:", bg=PANEL_BG, fg=ACCENT_FG,
                 font=FONT_BOLD, anchor="w").grid(row=2, column=0, sticky="w", padx=8, pady=2)
        self._lbl_remaining = tk.Label(ast, text="—", bg=PANEL_BG, fg=TEXT_FG,
                                       font=FONT_BODY)
        self._lbl_remaining.grid(row=2, column=1, sticky="w", pady=2)

        # Materials list
        self._section("MATERIALS")
        mat_outer = tk.Frame(self, bg=PANEL_BG)
        mat_outer.pack(fill="x", padx=4, pady=(0, 4))
        self._mat_frame = mat_outer

        # ── Refined cargo section ─────────────────────────────────────────
        self._section("REFINED CARGO")
        cargo = tk.Frame(self, bg=PANEL_BG)
        cargo.pack(fill="x", padx=4, pady=(0, 4))
        cargo.columnconfigure(1, weight=1)

        self._cargo_frame = cargo
        self._cargo_rows: dict[str, tk.Label] = {}

        # ── Stats bar ─────────────────────────────────────────────────────
        self._section("SESSION STATS")
        stats = tk.Frame(self, bg=PANEL_BG)
        stats.pack(fill="x", padx=4, pady=(0, 4))

        tk.Label(stats, text="Cracked:", bg=PANEL_BG, fg=ACCENT_FG,
                 font=FONT_BOLD).pack(side="left", padx=(8, 2))
        self._lbl_cracked = tk.Label(stats, text="0", bg=PANEL_BG, fg=TEXT_FG,
                                     font=FONT_BODY)
        self._lbl_cracked.pack(side="left", padx=(0, 16))

        tk.Label(stats, text="Collector drones:", bg=PANEL_BG, fg=ACCENT_FG,
                 font=FONT_BOLD).pack(side="left", padx=(0, 2))
        self._lbl_collectors = tk.Label(stats, text="0", bg=PANEL_BG, fg=TEXT_FG,
                                        font=FONT_BODY)
        self._lbl_collectors.pack(side="left", padx=(0, 16))

        tk.Label(stats, text="Prospectors:", bg=PANEL_BG, fg=ACCENT_FG,
                 font=FONT_BOLD).pack(side="left", padx=(0, 2))
        self._lbl_prospectors = tk.Label(stats, text="0", bg=PANEL_BG, fg=TEXT_FG,
                                         font=FONT_BODY)
        self._lbl_prospectors.pack(side="left")

        # ── Internal counters ─────────────────────────────────────────────
        self._n_cracked     = 0
        self._n_collectors  = 0
        self._n_prospectors = 0
        self._cargo: dict[str, int] = {}

    # ── Event dispatch ─────────────────────────────────────────────────────

    def on_event(self, event: str, data: dict) -> None:
        if event == "ProspectedAsteroid":
            self._on_prospected(data)
        elif event == "AsteroidCracked":
            self._n_cracked += 1
            self._lbl_cracked.config(text=str(self._n_cracked))
        elif event == "MiningRefined":
            self._on_refined(data)
        elif event == "LaunchDrone":
            self._on_drone(data)

    # ── Internal handlers ──────────────────────────────────────────────────

    def _on_prospected(self, data: dict) -> None:
        content    = data.get("content", "")
        motherlode = data.get("motherlode", "")
        remaining  = data.get("remaining", 1.0)

        color = _CONTENT_COLORS.get(content, TEXT_FG)
        self._lbl_content.config(text=content or "—", fg=color)
        self._lbl_motherlode.config(
            text=motherlode if motherlode else "None",
            fg=GREEN_FG if motherlode else TEXT_FG,
        )
        self._lbl_remaining.config(text=f"{remaining * 100:.0f}%")

        # Rebuild materials frame
        for w in self._mat_frame.winfo_children():
            w.destroy()

        self._mat_frame.columnconfigure(1, weight=1)
        for i, m in enumerate(data.get("materials", [])):
            pct = m.get("proportion", 0.0) * 100
            fg  = GREEN_FG if pct >= 20 else (HEADER_FG if pct >= 10 else TEXT_FG)
            tk.Label(self._mat_frame, text=f"  {m.get('name', '—')}",
                     bg=PANEL_BG, fg=fg, font=FONT_BODY,
                     anchor="w").grid(row=i, column=0, sticky="w", padx=8)
            tk.Label(self._mat_frame, text=f"{pct:.1f}%",
                     bg=PANEL_BG, fg=fg, font=FONT_BODY).grid(row=i, column=1, sticky="w")

    def _on_refined(self, data: dict) -> None:
        ore = data.get("type", "Unknown")
        self._cargo[ore] = self._cargo.get(ore, 0) + 1
        self._rebuild_cargo()

    def _on_drone(self, data: dict) -> None:
        drone_type = data.get("drone_type", "")
        if drone_type == "Collector":
            self._n_collectors += 1
            self._lbl_collectors.config(text=str(self._n_collectors))
        elif drone_type == "Prospector":
            self._n_prospectors += 1
            self._lbl_prospectors.config(text=str(self._n_prospectors))

    def _rebuild_cargo(self) -> None:
        for w in self._cargo_frame.winfo_children():
            w.destroy()
        self._cargo_frame.columnconfigure(1, weight=1)
        for i, (ore, count) in enumerate(sorted(self._cargo.items())):
            tk.Label(self._cargo_frame, text=f"  {ore}",
                     bg=PANEL_BG, fg=TEXT_FG, font=FONT_BODY,
                     anchor="w").grid(row=i, column=0, sticky="w", padx=8)
            tk.Label(self._cargo_frame, text=f"{count} t",
                     bg=PANEL_BG, fg=ACCENT_FG, font=FONT_BOLD
                     ).grid(row=i, column=1, sticky="w")

    # ── Helpers ────────────────────────────────────────────────────────────

    def _section(self, title: str) -> None:
        hdr = tk.Frame(self, bg="#2a2a4a", pady=1)
        hdr.pack(fill="x", pady=(4, 0))
        tk.Label(hdr, text=f"  {title}", bg="#2a2a4a", fg=HEADER_FG,
                 font=FONT_BOLD, anchor="w").pack(fill="x", padx=4, pady=2)
