"""
ED Cockpit — Exobiology Panel
==============================
Client-side panel for the Exobiology role.

Displays live exobiology data received from the agent:
  • Per-system / per-body / per-species scan progress (via BioScanTable)
  • Total estimated payout (REMAINING) and total collected (EARNED)
  • Sale summary footer updated on SellOrganicData

Data model
----------
The panel maintains an internal dict keyed first by system, then by body.
Each body entry holds a species dict compatible with BioScanTable's format:

    {
        "<system name>": {
            "<body name>": {
                "remaining_cr": <int>,
                "scanned_cr":   <int>,
                "species": {
                    "<display name>": {
                        "scan_count": <int>,
                        "value":      <int>,
                        "sold":       <bool>,
                    },
                    ...
                }
            }
        }
    }

BioScanTable receives a list grouped by system:

    [
        {
            "system":       "<system name>",
            "remaining_cr": "<cr formatted>",
            "scanned_cr":   "<cr formatted>",
            "bodies": [
                {
                    "body":         "<body name>",
                    "remaining_cr": "<cr formatted>",
                    "scanned_cr":   "<cr formatted>",
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
                },
                ...
            ],
        },
        ...
    ]

Events consumed
---------------
  ScanOrganic       — adds / updates a species row for the given system+body.
  SellOrganicData   — clears all accumulated data (data submitted to Vista Genomics).
  CodexEntry        — no widget update; logged for future codex panel.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from client.roles.base_panel import BasePanel
from client.roles.bioscan_table import BioScanTable, BG, HEADER_BG, HEADER_FG, FONT_BOLD
from shared.roles_def import Role

_SCANS_REQUIRED = 3

# Map scan_type string (from journal / snapshot) to completed scan count
_SCAN_TYPE_COUNT: dict[str, int] = {
    "Log":     1,
    "Sample":  2,
    "Analyse": 3,
}


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

        tk.Label(totals, text="SCANNED:", bg="#10102a", fg="#4da6ff",
                 font=("Consolas", 9, "bold")).pack(side="left", padx=(0, 2))
        self._lbl_scanned = tk.Label(totals, text="0 CR",
                                     bg="#10102a", fg="#ffd966",
                                     font=("Consolas", 9))
        self._lbl_scanned.pack(side="left", padx=(0, 16))

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
        # system → body → {remaining_cr, scanned_cr, species: dict[name→state]}
        self._systems: dict[str, dict[str, dict]] = {}
        # Permanent first-footfall registry: system → set of body names.
        # Never cleared on SellOrganicData.
        self._first_footfalls: dict[str, set[str]] = {}
        self._total_remaining: int = 0
        self._total_scanned:   int = 0
        self._total_earned:    int = 0

    # ── Event dispatch ─────────────────────────────────────────────────────

    def on_event(self, event: str, data: dict) -> None:
        if event == "StateSnapshot":
            self._load_snapshot(data)
        elif event == "ScanOrganic":
            self._on_scan_organic(data)
        elif event == "SellOrganicData":
            self._on_sell_organic(data)
        elif event == "FirstFootfall":
            self._on_first_footfall(data)
        # CodexEntry: no widget update in this panel

    # ── Internal handlers ──────────────────────────────────────────────────

    def _on_scan_organic(self, data: dict) -> None:
        system    = data.get("system", "Unknown system")
        body      = data.get("body",   "Unknown body")
        species   = data.get("species", "")
        variant   = data.get("variant", "")
        scan_type = data.get("scan_type", "")
        value     = int(data.get("value", 0))

        display_name = f"{species} - {variant}" if variant else species
        if not display_name:
            display_name = "UNIDENTIFIED (needs DSS scan)"

        # Ensure nested structure exists
        if system not in self._systems:
            self._systems[system] = {}
        if body not in self._systems[system]:
            self._systems[system][body] = {
                "remaining_cr": 0,
                "scanned_cr":   0,
                "species":      {},
            }

        body_entry = self._systems[system][body]
        sp_key = display_name

        if sp_key not in body_entry["species"]:
            body_entry["species"][sp_key] = {
                "scan_count": 0,
                "value":      value,
                "sold":       False,
            }

        sp = body_entry["species"][sp_key]
        # Use scan_type to determine the canonical count (idempotent against
        # duplicate events or snapshot+live-event overlap).
        new_count = _SCAN_TYPE_COUNT.get(scan_type, 1)
        sp["scan_count"] = max(sp["scan_count"], new_count)
        if value:
            sp["value"] = value

        self._rebuild_table()

    def _on_first_footfall(self, data: dict) -> None:
        system = data.get("system", "")
        body   = data.get("body",   "")
        if system and body:
            self._first_footfalls.setdefault(system, set()).add(body)
            self._rebuild_table()

    def _on_sell_organic(self, data: dict) -> None:
        self._total_earned += int(data.get("total_value", 0))
        # Mark all fully-scanned species as sold so they move from
        # REMAINING CR to SCANNED CR (with GC marker).  Partially-scanned
        # species (shouldn't exist at sell time) are left as-is.
        for bodies in self._systems.values():
            for bentry in bodies.values():
                for sp in bentry["species"].values():
                    if sp["scan_count"] >= _SCANS_REQUIRED:
                        sp["sold"] = True
        self._rebuild_table()

    def _load_snapshot(self, data: dict) -> None:
        """
        Pre-populate the panel from a StateSnapshot sent by the agent on connect.

        Replaces any existing in-memory state completely.  The snapshot format
        mirrors the agent's serialised ``_systems`` structure::

            {
              "systems": {
                "<system>": {
                  "<body>": [
                    {"species": "...", "variant": "...",
                     "scan_type": "Log"|"Sample"|"Analyse", "value": <int>},
                    ...
                  ]
                }
              }
            }
        """
        self._systems.clear()
        # Merge first_footfalls from snapshot (don't discard live FFs already received).
        for sys_name, bodies in data.get("first_footfalls", {}).items():
            self._first_footfalls.setdefault(sys_name, set()).update(bodies)
        for sys_name, bodies in data.get("systems", {}).items():
            for body_name, scans in bodies.items():
                for record in scans:
                    species   = record.get("species", "")
                    variant   = record.get("variant", "")
                    scan_type = record.get("scan_type", "")
                    value     = int(record.get("value", 0))

                    display_name = f"{species} - {variant}" if variant else species
                    if not display_name:
                        display_name = "UNIDENTIFIED (needs DSS scan)"

                    if sys_name not in self._systems:
                        self._systems[sys_name] = {}
                    if body_name not in self._systems[sys_name]:
                        self._systems[sys_name][body_name] = {
                            "remaining_cr": 0,
                            "scanned_cr":   0,
                            "species":      {},
                        }

                    scan_count = _SCAN_TYPE_COUNT.get(scan_type, 1)
                    self._systems[sys_name][body_name]["species"][display_name] = {
                        "scan_count": scan_count,
                        "value":      value,
                        "sold":       False,
                    }

        self._rebuild_table()

    # ── Table rebuild ──────────────────────────────────────────────────────

    def _rebuild_table(self) -> None:
        table_data      = []
        total_remaining = 0
        total_scanned   = 0

        for sys_name, bodies in self._systems.items():
            sys_remaining = 0
            sys_scanned   = 0
            body_rows     = []

            for body_name, bentry in bodies.items():
                body_remaining = 0
                body_scanned   = 0
                species_rows   = []

                for sp_name, sp in bentry["species"].items():
                    done    = sp["scan_count"] >= _SCANS_REQUIRED
                    gc_done = done and sp["sold"]
                    val     = sp["value"]

                    if sp["sold"]:
                        # Already sold — show in scanned column with GC marker
                        remaining_cr = 0
                        scanned_cr   = val
                    elif done:
                        # 3 samples complete, not yet sold — move to scanned
                        remaining_cr = 0
                        scanned_cr   = val
                    else:
                        # Still being scanned
                        remaining_cr = val if val else 0
                        scanned_cr   = 0

                    body_remaining += remaining_cr
                    body_scanned   += scanned_cr
                    total_remaining += remaining_cr
                    total_scanned   += scanned_cr

                    species_rows.append({
                        "name":         sp_name,
                        "remaining_cr": _fmt_cr(remaining_cr) if remaining_cr else "",
                        "scanned_cr":   _fmt_cr(scanned_cr)   if scanned_cr   else "",
                        "hist":         str(sp["scan_count"]),
                        "done":         "Y" if done else "",
                        "gc":           gc_done,
                    })

                sys_remaining += body_remaining
                sys_scanned   += body_scanned

                ff = body_name in self._first_footfalls.get(sys_name, set())
                body_rows.append({
                    "body":         body_name,
                    "remaining_cr": _fmt_cr(body_remaining),
                    "scanned_cr":   _fmt_cr(body_scanned),
                    "species":      species_rows,
                    "ff":           ff,
                })

            table_data.append({
                "system":       sys_name,
                "remaining_cr": _fmt_cr(sys_remaining),
                "scanned_cr":   _fmt_cr(sys_scanned),
                "bodies":       body_rows,
            })

        self._total_remaining = total_remaining
        self._total_scanned   = total_scanned
        self._table.load_data(table_data)
        self._lbl_remaining.config(text=f"{_fmt_cr(self._total_remaining)} CR")
        self._lbl_scanned.config(text=f"{_fmt_cr(self._total_scanned)} CR")
        self._lbl_earned.config(text=f"{_fmt_cr(self._total_earned)} CR")
