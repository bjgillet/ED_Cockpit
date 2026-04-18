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
  SellOrganicData   — clears all accumulated data and resets the table to empty
                      (data submitted to Vista Genomics; EARNED total is retained).
  CodexEntry        — back-fills the Vista Genomics scan value on the matching
                      species row (first scan step; value comes from the agent's
                      seed / cache lookup).
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from client.GUI.scrollable_panel import ScrollablePanelContainer
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
    _debug = False
    role_name = Role.EXOBIOLOGY

    def _build_ui(self) -> None:
        self.configure(style="TFrame")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        self._scroll = ScrollablePanelContainer(self, bg=BG)
        self._scroll.grid(row=0, column=0, sticky="nsew")
        self._panel_body = self._scroll.body
        self._scroll.bind_mousewheel_targets(self, self._scroll.canvas, self._panel_body)

        # ── Header bar ────────────────────────────────────────────────────
        header = tk.Frame(self._panel_body, bg=HEADER_BG, pady=4)
        header.pack(fill="x")
        tk.Label(
            header,
            text="EXOBIOLOGICAL SURVEY",
            bg=HEADER_BG, fg=HEADER_FG,
            font=FONT_BOLD,
        ).pack(side="left", padx=10)

        # ── Totals strip ──────────────────────────────────────────────────
        totals = tk.Frame(self._panel_body, bg="#10102a", pady=3)
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
        self._table = BioScanTable(self._panel_body, data=[])
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
        self.after_idle(self._scroll.refresh_layout)

    # ── Event dispatch ─────────────────────────────────────────────────────

    def on_event(self, event: str, data: dict) -> None:
        if event == "StateSnapshot":
            self._load_snapshot(data)
        elif event == "FSSBodySignals":
            self._on_fss_body_signals(data)
        elif event == "SAASignalsFound":
            self._on_saa_signals_found(data)
        elif event == "ScanOrganic":
            self._on_scan_organic(data)
        elif event == "SellOrganicData":
            self._on_sell_organic(data)
        elif event == "FirstFootfall":
            self._on_first_footfall(data)
        elif event == "CodexEntry":
            self._on_codex_entry(data)

    # ── Internal handlers ──────────────────────────────────────────────────

    def _on_scan_organic(self, data: dict) -> None:
        system    = data.get("system", "Unknown system")
        body      = data.get("body",   "Unknown body")
        species   = data.get("species", "")
        variant   = data.get("variant", "")
        scan_type = data.get("scan_type", "")
        value     = int(data.get("value", 0))

        # Genus (first word of species name) is the stable slot key.
        # SPECIES column shows only the variant (req 3).
        genus        = species.split()[0] if species else ""
        display_name = variant or genus or " UNKNOWN"
        sp_key       = genus if genus else display_name

        if system not in self._systems:
            self._systems[system] = {}
        if body not in self._systems[system]:
            self._systems[system][body] = {
                "remaining_cr": 0,
                "scanned_cr":   0,
                "species":      {},
            }

        body_entry = self._systems[system][body]

        if sp_key not in body_entry["species"]:
            # Promote an UNKNOWN placeholder if one exists; otherwise create fresh.
            unknown_key = next(
                (k for k in body_entry["species"] if k.startswith("__slot_")),
                None,
            )
            if unknown_key:
                body_entry["species"][sp_key] = body_entry["species"].pop(unknown_key)
            else:
                body_entry["species"][sp_key] = {
                    "display_name": display_name,
                    "genus":        genus,
                    "scan_count":   0,
                    "value":        0,
                    "sold":         False,
                }

        sp = body_entry["species"][sp_key]
        sp["display_name"] = display_name
        sp["genus"]        = genus
        # scan_type encodes progress exactly — idempotent against duplicate events.
        new_count = _SCAN_TYPE_COUNT.get(scan_type, 1)
        sp["scan_count"] = max(sp.get("scan_count", 0), new_count)
        if value:
            sp["value"] = value

        self._rebuild_table()

    def _on_fss_body_signals(self, data: dict) -> None:
        """
        Create UNKNOWN placeholder rows for a body when it has no entry yet.

        Guard is at the *body* level, not the system level, so that multiple
        bodies in the same system each get their own placeholder rows.
        """
        system  = data.get("system", "")
        body    = data.get("body", "")
        signals = data.get("signals", [])
        if not system or not body:
            return
        if body in self._systems.get(system, {}):
            return  # body already has data (richer or placeholder) — don't overwrite
        bio_count = next(
            (int(s.get("Count", 0)) for s in signals
             if s.get("Type_Localised") == "Biological"),
            0,
        )
        if not bio_count:
            return
        self._systems.setdefault(system, {})[body] = {
            "remaining_cr": 0,
            "scanned_cr":   0,
            "species": {
                f"__slot_{i}__": {
                    "display_name": " UNKNOWN",
                    "genus":        "",
                    "scan_count":   0,
                    "value":        0,
                    "sold":         False,
                }
                for i in range(bio_count)
            },
        }
        self._rebuild_table()

    def _on_saa_signals_found(self, data: dict) -> None:
        """
        Replace UNKNOWN placeholders with genus rows for the scanned body.
        Existing scan data keyed by genus is preserved.
        """
        system  = data.get("system", "")
        body    = data.get("body", "")
        signals = data.get("signals", [])
        genuses = data.get("genuses", [])
        if not system or not body:
            return
        bio_count = next(
            (int(s.get("Count", 0)) for s in signals
             if s.get("Type_Localised") == "Biological"),
            0,
        )
        if not bio_count:
            return

        # Index existing scan records by genus for merging
        existing = (self._systems.get(system, {})
                    .get(body, {})
                    .get("species", {}))
        scan_by_genus: dict[str, dict] = {
            rec.get("genus", k): rec
            for k, rec in existing.items()
            if rec.get("scan_count", 0) > 0
        }

        new_species: dict[str, dict] = {}
        for g_entry in genuses[:bio_count]:
            genus = g_entry.get("genus_localised", "")
            if not genus:
                continue
            if genus in scan_by_genus:
                # Preserve and annotate existing scan record
                rec = scan_by_genus[genus]
                rec.setdefault("display_name", genus)
                rec.setdefault("genus", genus)
                new_species[genus] = rec
            else:
                new_species[genus] = {
                    "display_name": genus,
                    "genus":        genus,
                    "scan_count":   0,
                    "value":        0,
                    "sold":         False,
                }

        # Fill remaining slots if SAA returned fewer genera than bio_count
        for i in range(len(new_species), bio_count):
            new_species[f"__slot_{i}__"] = {
                "display_name": " UNKNOWN",
                "genus":        "",
                "scan_count":   0,
                "value":        0,
                "sold":         False,
            }

        # Preserve any scan records whose genus wasn't in the SAA genus list
        for k, rec in existing.items():
            genus = rec.get("genus", "")
            if rec.get("scan_count", 0) > 0 and genus and genus not in new_species:
                new_species[genus] = rec

        old_body = self._systems.get(system, {}).get(body, {})
        self._systems.setdefault(system, {})[body] = {
            "remaining_cr": old_body.get("remaining_cr", 0),
            "scanned_cr":   old_body.get("scanned_cr", 0),
            "species":      new_species,
        }
        self._rebuild_table()

    def _on_first_footfall(self, data: dict) -> None:
        system = data.get("system", "")
        body   = data.get("body",   "")
        if system and body:
            self._first_footfalls.setdefault(system, set()).add(body)
            self._rebuild_table()

    def _on_codex_entry(self, data: dict) -> None:
        """
        Identify a scanned species and back-fill its display name and value.

        The agent sends the base species name (colour stripped) in ``name``,
        e.g. ``"Fonticulua Campestris"``.  Three things can happen:

        1. Genus row already present (SAA placeholder or ScanOrganic arrived
           first) — promote display_name from generic genus to full species
           name and update value if it changed.
        2. UNKNOWN slot placeholder present (FSS count only, no SAA yet) —
           promote the first slot to a named genus row.
        3. No entry at all (CodexEntry arrived before any other event for
           this body) — create a fresh row.
        """

        print (f"CodexEntry received: {data}") if self._debug else None

        species = data.get("name", "")
        value   = int(data.get("value", 0))
        system  = data.get("system", "")
        body    = data.get("body", "")

        if not (species and system and body):
            return

        genus  = species.split()[0] if species else ""
        sp_key = genus if genus else species

        # Ensure the body entry exists (creates it if CodexEntry is first).
        body_entry = (self._systems
                      .setdefault(system, {})
                      .setdefault(body, {
                          "remaining_cr": 0, "scanned_cr": 0, "species": {}
                      }))
        species_dict = body_entry["species"]
        changed = False

        if sp_key in species_dict:
            sp = species_dict[sp_key]
            # Promote display_name from the generic genus ("Fonticulua") to the
            # full species name ("Fonticulua Campestris") when the row has not
            # yet been updated by ScanOrganic (which sets the variant display).
            current = sp.get("display_name", "")
            if current in ("", genus, " UNKNOWN") and species and current != species:
                sp["display_name"] = species
                changed = True
            if value and sp.get("value", 0) != value:
                sp["value"] = value
                changed = True
            print (f"specie name updated: {sp_key} → {sp['display_name']}, value: {sp.get('value', 0)}") if self._debug else None
            print (f" System: {system}, Body: {body}") if self._debug else None

        else:
            # Promote an UNKNOWN slot if one exists; otherwise create fresh.
            unknown_key = next(
                (k for k in species_dict if k.startswith("__slot_")),
                None,
            )
            new_entry = {
                "display_name": genus or species,
                "genus":        genus,
                "scan_count":   0,
                "value":        value,
                "sold":         False,
            }
            if unknown_key:
                species_dict[sp_key] = species_dict.pop(unknown_key)
                species_dict[sp_key].update(new_entry)
            else:
                species_dict[sp_key] = new_entry
            changed = True

        if changed:
            self._rebuild_table()

    def _on_sell_organic(self, data: dict) -> None:
        self._total_earned  += int(data.get("total_value", 0))
        # Data submitted to Vista Genomics — wipe all accumulated expedition
        # data and reset the running totals so the table shows empty.
        self._systems.clear()
        self._total_remaining = 0
        self._total_scanned   = 0
        self._table.clear_collapse_state()
        self._rebuild_table()

    def _load_snapshot(self, data: dict) -> None:
        """
        Pre-populate the panel from a StateSnapshot sent by the agent on connect.

        Merges three data sources in priority order:

        1. ``systems``      — actual scan records (genus key, variant as display).
        2. ``saa_genera``   — DSS genus lists; adds genus placeholders for any
                              slot not already covered by scan data.
        3. ``fss_counts``   — FSS bio signal counts; adds UNKNOWN placeholders
                              only for systems that have *no* data at all.

        Snapshot format (agent wire)::

            {
              "systems":     { "<sys>": { "<body>": [{species, variant, scan_type, value}] } },
              "saa_genera":  { "<sys>": { "<body>": ["Bacterium", ...] } },
              "fss_counts":  { "<sys>": { "<body>": <int> } },
              "first_footfalls": { "<sys>": ["<body>", ...] }
            }
        """
        self._systems.clear()

        # Always merge first_footfalls (permanent, don't discard live FFs).
        for sys_name, bodies in data.get("first_footfalls", {}).items():
            self._first_footfalls.setdefault(sys_name, set()).update(bodies)

        # 1. Scan records — highest priority, genus-keyed, variant as display name.
        for sys_name, bodies in data.get("systems", {}).items():
            for body_name, scans in bodies.items():
                body_entry = (self._systems
                              .setdefault(sys_name, {})
                              .setdefault(body_name, {
                                  "remaining_cr": 0, "scanned_cr": 0, "species": {}
                              }))
                for record in scans:
                    species   = record.get("species", "")
                    variant   = record.get("variant", "")
                    scan_type = record.get("scan_type", "")
                    value     = int(record.get("value", 0))
                    genus     = species.split()[0] if species else ""
                    sp_key    = genus if genus else species
                    display   = variant or genus or " UNKNOWN"
                    body_entry["species"][sp_key] = {
                        "display_name": display,
                        "genus":        genus,
                        "scan_count":   _SCAN_TYPE_COUNT.get(scan_type, 1),
                        "value":        value,
                        "sold":         False,
                    }

        # 2. SAA genera — add genus placeholders not covered by scan data.
        for sys_name, bodies in data.get("saa_genera", {}).items():
            for body_name, genera_list in bodies.items():
                body_species = (self._systems
                                .setdefault(sys_name, {})
                                .setdefault(body_name, {
                                    "remaining_cr": 0, "scanned_cr": 0, "species": {}
                                })
                                ["species"])
                for genus in genera_list:
                    if genus and genus not in body_species:
                        body_species[genus] = {
                            "display_name": genus,
                            "genus":        genus,
                            "scan_count":   0,
                            "value":        0,
                            "sold":         False,
                        }

        # 3. FSS counts — UNKNOWN placeholders for bodies not yet in the table.
        # Guard is at body level (not system level) so that a system that
        # already has one body with SAA/scan data still gets FSS placeholders
        # for its other bodies that haven't been DSS-scanned yet.
        for sys_name, bodies in data.get("fss_counts", {}).items():
            for body_name, count in bodies.items():
                if body_name in self._systems.get(sys_name, {}):
                    continue  # body already has richer data — don't overwrite
                self._systems.setdefault(sys_name, {})[body_name] = {
                    "remaining_cr": 0,
                    "scanned_cr":   0,
                    "species": {
                        f"__slot_{i}__": {
                            "display_name": " UNKNOWN",
                            "genus":        "",
                            "scan_count":   0,
                            "value":        0,
                            "sold":         False,
                        }
                        for i in range(count)
                    },
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
                # First Footfall confirmed for this body → x5 sale multiplier.
                # Applied to display values only; raw values in _systems stay clean.
                ff      = body_name in self._first_footfalls.get(sys_name, set())
                ff_mult = 5 if ff else 1

                body_remaining = 0
                body_scanned   = 0
                species_rows   = []

                for sp_key, sp in bentry["species"].items():
                    done    = sp.get("scan_count", 0) >= _SCANS_REQUIRED
                    gc_done = done and sp.get("sold", False)
                    val     = sp.get("value", 0) * ff_mult

                    if sp.get("sold"):
                        # Already sold — show in scanned column with GC marker
                        remaining_cr = 0
                        scanned_cr   = val
                    elif done:
                        # 3 samples complete, not yet sold — move to scanned
                        remaining_cr = 0
                        scanned_cr   = val
                    else:
                        # Still scanning or placeholder — show potential in remaining
                        remaining_cr = val if val else 0
                        scanned_cr   = 0

                    body_remaining  += remaining_cr
                    body_scanned    += scanned_cr
                    total_remaining += remaining_cr
                    total_scanned   += scanned_cr

                    # display_name introduced for genus/UNKNOWN slots; fall back
                    # to the dict key for any records pre-dating this change.
                    display     = sp.get("display_name", sp_key)
                    placeholder = sp_key.startswith("__slot_")
                    species_rows.append({
                        "name":         display,
                        "remaining_cr": _fmt_cr(remaining_cr) if remaining_cr else "",
                        "scanned_cr":   _fmt_cr(scanned_cr)   if scanned_cr   else "",
                        "hist":         str(sp.get("scan_count", 0)),
                        "done":         "Y" if done else "",
                        "gc":           gc_done,
                        "placeholder":  placeholder,
                    })

                sys_remaining += body_remaining
                sys_scanned   += body_scanned

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
        self.after_idle(self._scroll.refresh_layout)
