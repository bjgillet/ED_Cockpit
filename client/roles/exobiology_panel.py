"""
ED Cockpit — Exobiology Panel
==============================
Client-side panel for the Exobiology role.

Displays live exobiology data received from the agent:
  • Per-body / per-species scan progress (via BioScanTable)
  • Total estimated payout (REMAINING) and total collected (SCANNED)
  • Sale summary footer updated on SellOrganicData

Data model
----------
The panel maintains an internal dict keyed by body name.  Each body entry
holds a list of species dicts compatible with BioScanTable's data format:

    {
        "body":         "<body name>",
        "remaining_cr": "<cr formatted>",   # sum of unsold values
        "scanned_cr":   "<cr formatted>",   # sum of sold values
        "species": [
            {
              "name":         "<species> - <variant>" | "UNIDENTIFIED …",
              "remaining_cr": "<cr>",
              "scanned_cr":   "<cr>",
              "hist":         "<scan count>",
              "done":         "Y" | "",
              "gc":           <bool>,
            },
            ...
        ],
    }

Events consumed
---------------
  ScanOrganic       — adds / updates a species row on the current body.
  SellOrganicData   — moves completed species from REMAINING → SCANNED.
  CodexEntry        — no widget update; logged for future codex panel.
"""
from __future__ import annotations

import queue
import tkinter as tk
from tkinter import ttk

from client.roles.base_panel import BasePanel
from client.roles.bioscan_table import BioScanTable, BG, HEADER_BG, HEADER_FG, FONT_BOLD
from shared.roles_def import Role

# Credits per species scan step (minimum; actual value varies by species).
# The panel uses 0 until the agent sends a real value.
_UNKNOWN_VALUE = "?"

# How many scan steps are required to complete a species
_SCANS_REQUIRED = 3

# Format helpers
def _fmt_cr(value: int) -> str:
    return f"{value:,}" if value else "0"


class ExobiologyPanel(BasePanel):
    """Live exobiology panel backed by BioScanTable."""

    role_name = Role.EXOBIOLOGY

    def _build_ui(self) -> None:
        self.configure(style="TFrame")

        # ── Header bar ────────────────────────────────────────────────────
        header = tk.Frame(self, bg=HEADER_BG, pady=4)
        header.pack(fill="x")
        tk.Label(
            header,
            text="EXOBIOLOGICAL SURVEY",
            bg=HEADER_BG, fg=HEADER_FG,
            font=FONT_BOLD,
        ).pack(side="left", padx=10)

        # ── Totals strip ──────────────────────────────────────────────────
        totals = tk.Frame(self, bg="#10102a", pady=3)
        totals.pack(fill="x")

        tk.Label(totals, text="REMAINING:", bg="#10102a", fg="#4da6ff",
                 font=("Consolas", 9, "bold")).pack(side="left", padx=(10, 2))
        self._lbl_remaining = tk.Label(totals, text="0 CR",
                                       bg="#10102a", fg="#ffffff",
                                       font=("Consolas", 9))
        self._lbl_remaining.pack(side="left", padx=(0, 16))

        tk.Label(totals, text="EARNED:", bg="#10102a", fg="#4da6ff",
                 font=("Consolas", 9, "bold")).pack(side="left", padx=(0, 2))
        self._lbl_earned = tk.Label(totals, text="0 CR",
                                    bg="#10102a", fg="#00cc55",
                                    font=("Consolas", 9))
        self._lbl_earned.pack(side="left")

        # ── BioScan table ─────────────────────────────────────────────────
        self._table = BioScanTable(self, data=[])
        self._table.pack(fill="both", expand=True)

        # ── Internal state ────────────────────────────────────────────────
        # body_name → {"remaining_cr": int, "scanned_cr": int, "species": dict[str→_SpeciesState]}
        self._bodies: dict[str, dict] = {}
        self._total_remaining: int = 0
        self._total_earned:    int = 0

    # ── Event dispatch ─────────────────────────────────────────────────────

    def on_event(self, event: str, data: dict) -> None:
        if event == "ScanOrganic":
            self._on_scan_organic(data)
        elif event == "SellOrganicData":
            self._on_sell_organic(data)
        # CodexEntry: no widget update in this panel

    # ── Internal handlers ──────────────────────────────────────────────────

    def _on_scan_organic(self, data: dict) -> None:
        body     = data.get("body", "Unknown body")
        species  = data.get("species", "")
        variant  = data.get("variant", "")
        scan_type = data.get("scan_type", "")
        value    = int(data.get("value", 0))

        display_name = f"{species} - {variant}" if variant else species
        if not display_name:
            display_name = "UNIDENTIFIED (needs DSS scan)"

        if body not in self._bodies:
            self._bodies[body] = {
                "remaining_cr": 0,
                "scanned_cr":   0,
                "species":      {},
            }

        body_entry = self._bodies[body]
        sp_key = display_name

        if sp_key not in body_entry["species"]:
            body_entry["species"][sp_key] = {
                "scan_count": 0,
                "value":      value,
                "sold":       False,
            }

        sp = body_entry["species"][sp_key]
        sp["scan_count"] = min(sp["scan_count"] + 1, _SCANS_REQUIRED)
        if value:
            sp["value"] = value

        self._rebuild_table()

    def _on_sell_organic(self, data: dict) -> None:
        self._total_earned += int(data.get("total_value", 0))

        # Mark all fully-scanned species as sold
        for item in data.get("items", []):
            sp_name = item.get("species", "")
            for body_entry in self._bodies.values():
                for key, sp in body_entry["species"].items():
                    if sp_name and sp_name in key:
                        sp["sold"] = True

        self._rebuild_table()

    # ── Table rebuild ──────────────────────────────────────────────────────

    def _rebuild_table(self) -> None:
        table_data = []
        total_remaining = 0

        for body_name, bentry in self._bodies.items():
            body_remaining = 0
            body_scanned   = 0
            species_rows   = []

            for sp_name, sp in bentry["species"].items():
                done     = sp["scan_count"] >= _SCANS_REQUIRED
                gc_done  = done and sp["sold"]
                val      = sp["value"]

                remaining_cr = val if (done and not sp["sold"]) else 0
                scanned_cr   = val if sp["sold"] else 0
                hist         = str(sp["scan_count"])

                if not done:
                    remaining_cr = val if val else 0

                body_remaining += remaining_cr
                body_scanned   += scanned_cr
                total_remaining += remaining_cr

                species_rows.append({
                    "name":         sp_name,
                    "remaining_cr": _fmt_cr(remaining_cr) if remaining_cr else "",
                    "scanned_cr":   _fmt_cr(scanned_cr)   if scanned_cr   else "",
                    "hist":         hist,
                    "done":         "Y" if done else "",
                    "gc":           gc_done,
                })

            table_data.append({
                "body":         body_name,
                "remaining_cr": _fmt_cr(body_remaining) if body_remaining else "0",
                "scanned_cr":   _fmt_cr(body_scanned)   if body_scanned   else "0",
                "species":      species_rows,
            })

        self._total_remaining = total_remaining
        self._table.load_data(table_data)
        self._lbl_remaining.config(text=f"{_fmt_cr(self._total_remaining)} CR")
        self._lbl_earned.config(text=f"{_fmt_cr(self._total_earned)} CR")
