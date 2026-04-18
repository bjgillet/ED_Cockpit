"""
ED Cockpit — Mining Panel
==========================
Client-side panel for the Mining role.

Displays live mining data received from the agent:
  • Current asteroid composition (ProspectedAsteroid)
  • Refined ore tally (MiningRefined)
  • Live cargo fill bar updated from Status.json
  • Drone launch counter (Collection / Prospector)
  • Cracked asteroid counter (AsteroidCracked)
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from client.GUI.scrollable_panel import ScrollablePanelContainer
from client.roles.base_panel import BasePanel
from shared.roles_def import Role

BG         = "#0d0d1e"
PANEL_BG   = "#10102a"
HEADER_BG  = "#b87800"
HEADER_FG  = "#ffff00"
ACCENT_FG  = "#4da6ff"
TEXT_FG    = "#ffffff"
GREEN_FG   = "#00cc55"
ORANGE_FG  = "#ff8800"
RED_FG     = "#cc2222"
FONT_BOLD  = ("Consolas", 10, "bold")
FONT_BODY  = ("Consolas", 9)
FONT_PATH  = ("Consolas", 8)

_CONTENT_COLORS = {
    "Low":    "#888888",
    "Medium": "#ff8800",
    "High":   "#00cc55",
}

_BAR_W = 160
class MiningPanel(BasePanel):
    """Live mining panel: asteroid composition + refined ore tally."""
    _debug = False
    role_name = Role.MINING

    def _build_ui(self) -> None:
        self.configure(style="TFrame")
        self._init_progress_styles()
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        self._scroll = ScrollablePanelContainer(self, bg=BG)
        self._scroll.grid(row=0, column=0, sticky="nsew")
        self._panel_body = self._scroll.body
        self._scroll.bind_mousewheel_targets(self, self._scroll.canvas, self._panel_body)

        # ── Header ────────────────────────────────────────────────────────
        hdr = tk.Frame(self._panel_body, bg=HEADER_BG, pady=4)
        hdr.pack(fill="x")
        tk.Label(hdr, text="ASTEROID MINING", bg=HEADER_BG, fg=HEADER_FG,
                 font=FONT_BOLD).pack(side="left", padx=10)

        # ── Prospected asteroid section ───────────────────────────────────
        self._section("CURRENT ASTEROID")
        ast = tk.Frame(self._panel_body, bg=PANEL_BG)
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
        mat_outer = tk.Frame(self._panel_body, bg=PANEL_BG)
        mat_outer.pack(fill="x", padx=4, pady=(0, 4))
        self._mat_frame = mat_outer

        # ── Refined cargo section ─────────────────────────────────────────
        self._section("REFINED CARGO")
        cargo_outer = tk.Frame(self._panel_body, bg=PANEL_BG)
        cargo_outer.pack(fill="x", padx=4, pady=(0, 4))
        cargo_outer.columnconfigure(1, weight=1)

        # Live cargo fill bar (from Status.json)
        tk.Label(cargo_outer, text="Cargo:", bg=PANEL_BG, fg=ACCENT_FG,
                 font=FONT_BOLD, anchor="w"
                 ).grid(row=0, column=0, sticky="w", padx=8, pady=2)
        self._cargo_var = tk.DoubleVar(value=0.0)
        self._cargo_bar = ttk.Progressbar(
            cargo_outer,
            orient="horizontal",
            mode="determinate",
            length=_BAR_W,
            variable=self._cargo_var,
            maximum=1.0,
            style="MiningGreen.Horizontal.TProgressbar",
        )
        self._cargo_bar.grid(row=0, column=1, sticky="w", pady=2)
        self._lbl_cargo_live = tk.Label(
            cargo_outer, text="0 t / 0 t", bg=PANEL_BG, fg=TEXT_FG, font=FONT_PATH)
        self._lbl_cargo_live.grid(row=0, column=2, sticky="w", padx=4, pady=2)

        # Ore breakdown sub-frame
        cargo = tk.Frame(cargo_outer, bg=PANEL_BG)
        cargo.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(2, 0))
        cargo.columnconfigure(1, weight=1)

        self._cargo_frame = cargo
        self._cargo_rows: dict[str, tk.Label] = {}

        # ── Stats bar ─────────────────────────────────────────────────────
        self._section("SESSION STATS")
        stats = tk.Frame(self._panel_body, bg=PANEL_BG)
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

        tk.Label(stats, text="Available Limpets:", bg=PANEL_BG, fg=ACCENT_FG,
                 font=FONT_BOLD).pack(side="left", padx=(16, 2))
        self._lbl_limpets = tk.Label(stats, text="0", bg=PANEL_BG, fg=TEXT_FG,
                                     font=FONT_BODY)
        self._lbl_limpets.pack(side="left")

        # ── Quick actions ─────────────────────────────────────────────────
        self._section("QUICK ACTIONS")
        acts = tk.Frame(self._panel_body, bg=PANEL_BG)
        acts.pack(fill="x", padx=4, pady=(0, 8))

        _BTN = dict(bg="#1a1a3a", fg=ACCENT_FG, activebackground="#2a2a5a",
                    activeforeground=TEXT_FG, relief="flat", bd=0,
                    font=FONT_BODY, cursor="hand2", padx=8, pady=4)

        row1 = tk.Frame(acts, bg=PANEL_BG)
        row1.pack(fill="x", padx=4, pady=(4, 2))

        tk.Button(row1, text="◀ Prev Firegroup",
                  command=lambda: self.send_action("key_press", "prev_firegroup"),
                  **_BTN).pack(side="left", padx=3)
        tk.Button(row1, text="Next Firegroup ▶",
                  command=lambda: self.send_action("key_press", "next_firegroup"),
                  **_BTN).pack(side="left", padx=3)

        row2 = tk.Frame(acts, bg=PANEL_BG)
        row2.pack(fill="x", padx=4, pady=(0, 2))

        tk.Button(row2, text="Deploy Hardpoints",
                  command=lambda: self.send_action("key_press", "deploy_hardpoints"),
                  **_BTN).pack(side="left", padx=3)
        tk.Button(row2, text="Cargo Scoop",
                  command=lambda: self.send_action("key_press", "cargo_scoop"),
                  **_BTN).pack(side="left", padx=3)

        row3 = tk.Frame(acts, bg=PANEL_BG)
        row3.pack(fill="x", padx=4, pady=(0, 4))

        tk.Button(row3, text="Enter FSS",
                  command=lambda: self.send_action("key_press", "enter_fss"),
                  **_BTN).pack(side="left", padx=3)
        tk.Button(row3, text="Boost",
                  command=lambda: self.send_action("key_press", "boost"),
                  **_BTN).pack(side="left", padx=3)

        # ── Internal counters ─────────────────────────────────────────────
        self._n_cracked     = 0
        self._n_collectors  = 0
        self._n_prospectors = 0
        self._cargo: dict[str, int] = {}
        self._cargo_used: float = 0.0
        self._cargo_capacity: float = 0.0
        self._available_limpets: int = 0

        self.after_idle(self._scroll.refresh_layout)

    # ── Event dispatch ─────────────────────────────────────────────────────

    def on_event(self, event: str, data: dict) -> None:
        if event == "StateSnapshot":
            self._load_snapshot(data)
        elif event == "ProspectedAsteroid":
            self._on_prospected(data)
        elif event == "AsteroidCracked":
            self._n_cracked += 1
            self._lbl_cracked.config(text=str(self._n_cracked))
        elif event == "MiningRefined":
            self._on_refined(data)
        elif event == "LaunchDrone":
            print (f"From on_event : Drone launched: {data}") if self._debug else None
            self._on_drone(data)
        elif event == "Status":
            self._on_status(data)
        elif event == "Loadout":
            self._on_loadout(data)
        elif event == "Cargo":
            self._on_cargo(data)
        elif event == "Docked":
            self._on_docked(data)

    # ── Internal handlers ──────────────────────────────────────────────────

    def _load_snapshot(self, data: dict) -> None:
        asteroid = data.get("asteroid", {})
        if asteroid:
            self._on_prospected(asteroid)

        counters = data.get("counters", {})
        self._n_cracked = int(counters.get("cracked", 0))
        self._n_collectors = int(counters.get("collectors", 0))
        self._n_prospectors = int(counters.get("prospectors", 0))
        self._lbl_cracked.config(text=str(self._n_cracked))
        self._lbl_collectors.config(text=str(self._n_collectors))
        self._lbl_prospectors.config(text=str(self._n_prospectors))
        self._available_limpets = int(counters.get("available_limpets", 0))
        self._lbl_limpets.config(text=str(self._available_limpets))

        self._cargo = {
            str(k): int(v) for k, v in data.get("cargo_tally", {}).items()
        }
        self._rebuild_cargo()

        self._cargo_capacity = float(data.get("cargo_capacity", self._cargo_capacity))

        status = data.get("status", {})
        if status:
            self._on_status(status)
        else:
            self._update_cargo_gauge()

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
        self._lbl_remaining.config(text=f"{remaining:.0f}%")

        for w in self._mat_frame.winfo_children():
            w.destroy()

        self._mat_frame.columnconfigure(1, weight=1)
        for i, m in enumerate(data.get("materials", [])):
            pct = m.get("proportion", 0.0)
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
        print (f"Drone launched: {data}") if self._debug else None
        drone_type = data.get("drone_type", "")
        if drone_type == "Collection":
            self._n_collectors += 1
            self._lbl_collectors.config(text=str(self._n_collectors))
            if self._available_limpets > 0:
                self._available_limpets -= 1
        elif drone_type == "Prospector":
            self._n_prospectors += 1
            self._lbl_prospectors.config(text=str(self._n_prospectors))
            if self._available_limpets > 0:
                self._available_limpets -= 1
        self._available_limpets = int(data.get("available_limpets", self._available_limpets))
        self._lbl_limpets.config(text=str(self._available_limpets))

    def _on_status(self, data: dict) -> None:
        cargo_val = data.get("cargo")
        if cargo_val is not None:
            self._cargo_used = float(cargo_val)
        capacity = float(data.get("cargo_capacity", 0.0))
        if capacity > 0:
            self._cargo_capacity = capacity
        self._available_limpets = int(data.get("available_limpets", self._available_limpets))
        self._lbl_limpets.config(text=str(self._available_limpets))
        self._update_cargo_gauge()

    def _on_loadout(self, data: dict) -> None:
        capacity = float(data.get("cargo_capacity", 0.0))
        if capacity > 0:
            self._cargo_capacity = capacity
        self._update_cargo_gauge()

    def _on_cargo(self, data: dict) -> None:
        cargo_val = data.get("cargo")
        if cargo_val is not None:
            self._cargo_used = float(cargo_val)
        self._available_limpets = int(data.get("available_limpets", self._available_limpets))
        self._lbl_limpets.config(text=str(self._available_limpets))
        tally = data.get("refined_cargo_tally")
        if isinstance(tally, dict):
            cleaned: dict[str, int] = {}
            for k, v in tally.items():
                try:
                    count = int(v)
                except (TypeError, ValueError):
                    continue
                if count > 0:
                    cleaned[str(k)] = count
            self._cargo = cleaned
            self._rebuild_cargo()
        self._update_cargo_gauge()

    def _on_docked(self, data: dict) -> None:
        self._lbl_content.config(text="—", fg=TEXT_FG)
        self._lbl_motherlode.config(text="—", fg=TEXT_FG)
        self._lbl_remaining.config(text="—")
        for w in self._mat_frame.winfo_children():
            w.destroy()
        tk.Label(self._mat_frame, text="  No active asteroid", bg=PANEL_BG, fg=TEXT_FG,
                 font=FONT_PATH, anchor="w").grid(row=0, column=0, sticky="w", padx=8)

        self._n_cracked = 0
        self._n_collectors = 0
        self._n_prospectors = 0
        self._lbl_cracked.config(text="0")
        self._lbl_collectors.config(text="0")
        self._lbl_prospectors.config(text="0")
        self._lbl_limpets.config(text=str(self._available_limpets))
        self.after_idle(self._scroll.refresh_layout)

    def _update_cargo_gauge(self) -> None:
        capacity = max(self._cargo_capacity, 0.0)
        used = max(self._cargo_used, 0.0)
        self._cargo_bar.configure(maximum=capacity if capacity > 0 else 1.0)
        self._cargo_var.set(min(used, capacity) if capacity > 0 else 0.0)
        self._lbl_cargo_live.config(text=f"{used:.0f} t / {capacity:.0f} t")
        ratio = (used / capacity) if capacity > 0 else 0.0
        if ratio <= 0.70:
            style = "MiningGreen.Horizontal.TProgressbar"
        elif ratio <= 0.90:
            style = "MiningYellow.Horizontal.TProgressbar"
        else:
            style = "MiningRed.Horizontal.TProgressbar"
        self._cargo_bar.configure(style=style)

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
        self.after_idle(self._scroll.refresh_layout)

    # ── Helpers ────────────────────────────────────────────────────────────

    def _section(self, title: str) -> None:
        hdr = tk.Frame(self._panel_body, bg="#2a2a4a", pady=1)
        hdr.pack(fill="x", pady=(4, 0))
        tk.Label(hdr, text=f"  {title}", bg="#2a2a4a", fg=HEADER_FG,
                 font=FONT_BOLD, anchor="w").pack(fill="x", padx=4, pady=2)

    def _init_progress_styles(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "MiningGreen.Horizontal.TProgressbar",
            troughcolor="#222244",
            background=GREEN_FG,
            bordercolor="#222244",
            lightcolor=GREEN_FG,
            darkcolor=GREEN_FG,
        )
        style.configure(
            "MiningYellow.Horizontal.TProgressbar",
            troughcolor="#222244",
            background=HEADER_FG,
            bordercolor="#222244",
            lightcolor=HEADER_FG,
            darkcolor=HEADER_FG,
        )
        style.configure(
            "MiningRed.Horizontal.TProgressbar",
            troughcolor="#222244",
            background=RED_FG,
            bordercolor="#222244",
            lightcolor=RED_FG,
            darkcolor=RED_FG,
        )
