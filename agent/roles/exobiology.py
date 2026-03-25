"""
ED Cockpit — Exobiology Role
=============================
Filters Elite Dangerous journal events relevant to exobiology activities
and prepares them for forwarding to subscribed clients.

Events handled
--------------
  ApproachBody      — player approaches a landable body; used to track the
                      current body name and system (not forwarded to clients).
  SAAScanComplete   — player completed a Detailed Surface Scanner (DSS) mapping
                      of a body.  Initialises a first-footfall context entry
                      ``(system, body) → False`` (scanned, not yet landed on).
                      Not forwarded to clients.
  Disembark         — player steps off ship/SRV onto a planet surface.
                      Treated as a confirmed first footfall when the body has a
                      pending context entry (``False``) from ``SAAScanComplete``
                      — i.e. the player DSS-scanned the body and is now the
                      first to land on it.  The context entry is flipped to
                      ``True`` so subsequent landings are ignored.
  ScanOrganic       — player performed one scan step on an organic sample.
                      Each species requires 3 scans; the ``ScanType`` field
                      distinguishes Log (1st), Sample (2nd), and Analyse (3rd).
  SellOrganicData   — player sold exobiology data at Vista Genomics.
  CodexEntry        — new codex entry; we forward only biology-category entries.
  SAASignalsFound   — detailed surface signals found after DSS scan.
  FSSBodySignals    — body bio/geo signal counts found during FSS scan.

State persistence
-----------------
  The role maintains a JSON state file at
  ``<config_dir>/exobiology_state.json`` that survives agent restarts.

  Data is accumulated across **all** visited systems and bodies during an
  expedition.  Everything is cleared only when a ``SellOrganicData`` event
  is received (player sold data at Vista Genomics): ``systems``,
  ``fss_counts`` and ``saa_genera`` are all wiped.

  State file format::

    {
      "current_system":  "<system name>",
      "current_body":    "<body name>",
      "current_body_id": <int | null>,
      "systems": {
        "<system name>": {
          "<body name>": [
            { "species": "<localised>", "variant": "<localised>",
              "scan_type": "Log" | "Sample" | "Analyse", "value": <int cr> },
            ...
          ]
        }
      },
      "first_footfalls": {
        "<system name>": ["<body name>", ...]
      },
      "last_updated": "<ISO-8601 UTC>"
    }

  ``first_footfalls`` is **never** cleared by ``SellOrganicData``; it is a
  permanent record of bodies where the player achieved first footfall.

  Backward compatibility: old single-system files (with top-level ``system``,
  ``body``, ``scans`` keys) are automatically migrated on first load.

Wire payload shapes
-------------------
  ScanOrganic →
    {
      "event":        "ScanOrganic",
      "body":         "<body name>",          # planet/moon (from ApproachBody)
      "species":      "<localised species>",  # e.g. "Bacterium Aurasus"
      "variant":      "<localised variant>",  # e.g. "Teal"  (may be "")
      "scan_type":    "Log" | "Sample" | "Analyse",
      "system":       "<system name>",
      "value":        <int cr>,               # best known estimate (see below)
    }

  Value resolution order for ScanOrganic
  ----------------------------------------
  1. Journal ``SurveyData.Value``  — exact value from the game (Analyse step only).
  2. Local seed / user cache       — ``agent/data/exobiology_values.json`` covers
                                     all known Odyssey species; values learned from
                                     the journal are merged into a persistent cache.
  3. Remote API fallback           — fired asynchronously for unknown species;
                                     result saved to cache for future sessions.
  4. 0                             — genuinely unknown (new post-patch species
                                     not yet in seed or cache).

  SellOrganicData →
    {
      "event":        "SellOrganicData",
      "total_value":  <int cr>,
      "items": [
        { "species": "<localised>", "value": <int cr>, "bonus": <int cr> },
        ...
      ],
    }

  CodexEntry (biology only) →
    {
      "event":        "CodexEntry",
      "entry_id":     <int>,
      "name":         "<species name (Name_Localised)>",
      "category":     "<category>",
      "system":       "<system>",
      "body":         "<body>",
      "value":        <int cr>,   # Vista Genomics scan value from seed/cache (0 if unknown)
      "is_new_entry": <bool>,
    }

  SAASignalsFound →
    {
      "event":   "SAASignalsFound",
      "system":  "<system>",
      "body":    "<body>",
      "signals": [ ... ],
    }

  FSSBodySignals →
    {
      "event":   "FSSBodySignals",
      "system":  "<system>",
      "body":    "<body>",
      "signals": [ ... ],
    }

  FirstFootfall →
    {
      "event":   "FirstFootfall",
      "system":  "<system name>",
      "body":    "<body name>",
      "body_id": <int | null>,
    }
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from agent.roles.base_role import BaseRole
from agent.roles.value_lookup import ValueLookup
from shared.roles_def import Role

log = logging.getLogger(__name__)

# CodexEntry categories that belong to biology
_BIO_CATEGORIES: frozenset[str] = frozenset({
    "$Codex_Category_Biology;",
    "Biology",
})


class ExobiologyRole(BaseRole):
    """Exobiology role — filters and enriches organic-scan journal events."""

    name = Role.EXOBIOLOGY
    journal_events = frozenset({
        "ApproachBody",
        "Disembark",
        "FSDJump",          # track current system name (FSSBodySignals lacks StarSystem)
        "FSSBodySignals",
        "Location",         # track current system name on game load
        "SAASignalsFound",
        "SAAScanComplete",  # initialise first-footfall context per body
        "ScanOrganic",
        "SellOrganicData",
        "CodexEntry",
    })

    def __init__(self) -> None:
        self._config_dir = self._resolve_config_dir()
        self._state_path = self._config_dir / "exobiology_state.json"
        self._journal_dir: Path | None = None

        # Current location — updated by ApproachBody
        self._system:    str        = ""
        self._body_name: str        = ""
        self._body_id:   int | None = None

        # Multi-system scan accumulator:
        #   system_name → body_name → species_display_name → scan_record
        self._systems: dict[str, dict[str, dict[str, dict]]] = {}

        # First-footfall registry: system_name → [body_name, ...]
        # Permanent — never cleared by SellOrganicData.
        self._first_footfalls: dict[str, list[str]] = {}

        # FSS bio signal counts: system_name → body_name → count
        # Lets the snapshot pre-populate UNKNOWN placeholder rows on the client.
        self._fss_counts: dict[str, dict[str, int]] = {}

        # SAA genus lists: system_name → body_name → [genus_localised, ...]
        # Lets the snapshot pre-populate genus placeholder rows on the client.
        self._saa_genera: dict[str, dict[str, list[str]]] = {}

        # First-footfall context: (system, body) → has_been_footfalled (bool)
        # Populated by SAAScanComplete (False = scanned, not yet landed).
        # Flipped to True on the first subsequent Disembark on that body.
        # Session-only — not persisted; rebuilt from journal replay on restart.
        self._ff_context: dict[tuple[str, str], bool] = {}

        # Value resolver: seed → user cache → async API fallback
        self._value_lookup = ValueLookup(cache_dir=self._config_dir)

        self._load_state()

    # ── Config dir ─────────────────────────────────────────────────────────

    @staticmethod
    def _resolve_config_dir() -> Path:
        if sys.platform == "win32":
            return Path.home() / "AppData" / "Roaming" / "ed-cockpit"
        return Path.home() / ".config" / "ed-cockpit"

    # ── State persistence ───────────────────────────────────────────────────

    def set_journal_dir(self, path: Path | None) -> None:
        """Called by EDApp when the journal directory is known."""
        self._journal_dir = path

    def _refresh_state(self) -> None:
        """Rebuild state from the most recent journal file if available."""
        self._journal_updated = True
        if self._journal_dir is None:
            log.warning("ExobiologyRole: journal directory not set — cannot refresh state")
            return
        journals = sorted(self._journal_dir.glob("Journal.*.json"), reverse=True)
        if not journals:
            log.info("ExobiologyRole: no previous Elite Dangerous sessions found - starting with empty state")
            return
        log.info("ExobiologyRole: found previous session journal file - refreshing state from it if necessary")
        try:
            with journals[0].open(encoding="utf-8") as f:
                for line in f:
                    event = json.loads(line)
                    self.filter(event.get("event", ""), event)
        except Exception as exc:
            log.warning("ExobiologyRole: could not refresh state from journal: %s", exc)

    def _load_state(self) -> None:
        """Load previously saved state from disk (called once at init)."""
        try:
            saved = json.loads(self._state_path.read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning("ExobiologyRole: could not load state: %s", exc)
            return

        # New multi-system format
        if "systems" in saved:
            self._system    = saved.get("current_system", "")
            self._body_name = saved.get("current_body", "")
            self._body_id   = saved.get("current_body_id")
            raw_systems = saved.get("systems", {})
            for sys_name, bodies in raw_systems.items():
                self._systems[sys_name] = {}
                for body_name, scans in bodies.items():
                    self._systems[sys_name][body_name] = {
                        item["species"]: dict(item)
                        for item in scans
                        if item.get("species")
                    }
            for sys_name, bodies in saved.get("first_footfalls", {}).items():
                self._first_footfalls[sys_name] = list(bodies)
            for sys_name, bodies in saved.get("fss_counts", {}).items():
                self._fss_counts[sys_name] = dict(bodies)
            for sys_name, bodies in saved.get("saa_genera", {}).items():
                self._saa_genera[sys_name] = {b: list(g) for b, g in bodies.items()}
            for sys_name, bodies in saved.get("ff_context", {}).items():
                for body_name, value in bodies.items():
                    self._ff_context[(sys_name, body_name)] = bool(value)
            log.info("ExobiologyRole: multi-system state loaded from %s", self._state_path)
            return

        # Legacy single-system format — migrate automatically
        system    = saved.get("system", "")
        body_name = saved.get("body", "")
        body_id   = saved.get("body_id")
        scans     = saved.get("scans", [])

        self._system    = system
        self._body_name = body_name
        self._body_id   = body_id

        if system and body_name and scans:
            self._systems.setdefault(system, {})[body_name] = {
                item["species"]: dict(item)
                for item in scans
                if item.get("species")
            }
            log.info(
                "ExobiologyRole: migrated legacy state (%d scan(s)) from %s",
                len(scans), self._state_path,
            )
        else:
            log.info("ExobiologyRole: loaded empty legacy state from %s", self._state_path)

    def _save_state(self) -> None:
        """Persist current in-memory state to disk."""
        try:
            self._config_dir.mkdir(parents=True, exist_ok=True)

            # Serialise nested dict → system → body → list of records
            serialised_systems: dict = {}
            for sys_name, bodies in self._systems.items():
                serialised_systems[sys_name] = {
                    body_name: list(species_map.values())
                    for body_name, species_map in bodies.items()
                }

            # Serialise _ff_context: (sys, body) → bool  →  sys → {body → bool}
            serialised_ff: dict = {}
            for (sys_name, body_name), value in self._ff_context.items():
                serialised_ff.setdefault(sys_name, {})[body_name] = value

            state = {
                "current_system":  self._system,
                "current_body":    self._body_name,
                "current_body_id": self._body_id,
                "systems":         serialised_systems,
                "first_footfalls": dict(self._first_footfalls),
                "fss_counts":      dict(self._fss_counts),
                "saa_genera":      {s: dict(b) for s, b in self._saa_genera.items()},
                "ff_context":      serialised_ff,
                "last_updated":    datetime.now(timezone.utc).isoformat(),
            }
            self._state_path.write_text(
                json.dumps(state, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as exc:
            log.warning("ExobiologyRole: could not save state: %s", exc)

    # ── BaseRole interface ──────────────────────────────────────────────────

    def get_snapshot(self) -> dict | None:
        """
        Return the full accumulated multi-system scan state.

        Sent to clients as a synthetic ``EventMessage(event="StateSnapshot")``
        immediately after they connect, so their panels pre-populate from
        previously accumulated data without waiting for new journal events.

        Snapshot format::

            {
              "systems": {
                "<system>": {
                  "<body>": [
                    {"species": "...", "variant": "...",
                     "scan_type": "Log"|"Sample"|"Analyse", "value": <int>},
                    ...
                  ]
                }
              },
              "first_footfalls": {
                "<system>": ["<body>", ...]
              }
            }
        """
        if not any([self._systems, self._first_footfalls,
                    self._fss_counts, self._saa_genera]):
            return None
        serialised: dict = {}
        for sys_name, bodies in self._systems.items():
            serialised[sys_name] = {
                body_name: list(species_map.values())
                for body_name, species_map in bodies.items()
            }
        return {
            "systems":         serialised,
            "first_footfalls": dict(self._first_footfalls),
            "fss_counts":      dict(self._fss_counts),
            "saa_genera":      {s: dict(b) for s, b in self._saa_genera.items()},
        }

    def filter(self, event_name: str, data: dict) -> dict | None:
        if event_name in ("FSDJump", "Location"):
            # Keep _system current so FSSBodySignals/SAASignalsFound can fall back to it
            self._system = data.get("StarSystem", self._system)
            return None   # not forwarded to clients
        if event_name == "ApproachBody":
            self._body_name = data.get("Body", "")
            self._body_id   = data.get("BodyID")
            self._system    = data.get("StarSystem", self._system)
            self._save_state()
            return None   # not forwarded to clients
        if event_name == "SAAScanComplete":
            return self._handle_SAAScanComplete(data)
        if event_name == "Disembark":
            return self._handle_disembark(data)
        if event_name == "ScanOrganic":
            return self._handle_scan_organic(data)
        if event_name == "SellOrganicData":
            return self._handle_sell_organic(data)
        if event_name == "CodexEntry":
            return self._handle_codex_entry(data)
        if event_name == "SAASignalsFound":
            return self._handle_SAASignalsFound(data)
        if event_name == "FSSBodySignals":
            return self._handle_FSSBodySignals(data)
        return None

    # ── Event handlers ─────────────────────────────────────────────────────

    def _handle_SAAScanComplete(self, data: dict) -> None:
        """
        Record a pending first-footfall context entry for the DSS-scanned body.

        Called when the player completes a Detailed Surface Scanner mapping.
        Sets ``_ff_context[(system, body)] = False`` meaning "scanned, not yet
        landed on".  If a first footfall for this body was already confirmed in
        a previous session (body is in ``_first_footfalls``), the entry is
        initialised to ``True`` so the Disembark handler won't re-emit the event.
        """
        body   = data.get("BodyName", "") or self._body_name
        system = self._system
        if not body:
            return None

        key            = (system, body)
        already_landed = body in self._first_footfalls.get(system, [])
        if key not in self._ff_context:
            self._ff_context[key] = already_landed
            log.debug(
                "ExobiologyRole: FF context for %s / %s → %s",
                system, body, already_landed,
            )
            self._save_state()   # persist so context survives agent restarts
        return None  # not forwarded to clients

    def _handle_disembark(self, data: dict) -> dict | None:
        """
        Confirm a first footfall when the player lands on a DSS-scanned body.

        A first footfall is confirmed when:
          • the player is on a planet (``OnPlanet=True``), AND
          • ``_ff_context[(system, body)]`` is ``False`` — meaning the body was
            DSS-scanned in this session and has not yet been footfalled.

        The context entry is flipped to ``True`` so repeated landings on the
        same body do not re-emit the event.
        """
        if not data.get("OnPlanet"):
            return None

        system  = data.get("StarSystem", self._system)
        body    = data.get("Body",       self._body_name)
        body_id = data.get("BodyID",     self._body_id)

        if self._ff_context.get((system, body)) is not False:
            return None   # body not DSS-scanned this session, or already footfalled

        self._ff_context[(system, body)] = True
        bodies = self._first_footfalls.setdefault(system, [])
        if body not in bodies:
            bodies.append(body)
        self._save_state()
        log.info("ExobiologyRole: first footfall confirmed on %s / %s", system, body)

        return {
            "event":   "FirstFootfall",
            "system":  system,
            "body":    body,
            "body_id": body_id,
        }

    def _handle_scan_organic(self, data: dict) -> dict:
        species   = data.get("Species_Localised") or data.get("Species", "")
        variant   = data.get("Variant_Localised")  or data.get("Variant", "")
        scan_type = data.get("ScanType", "")
        system    = self._system
        body      = self._body_name

        # Priority 1: exact value from journal (Analyse step only)
        survey        = data.get("SurveyData") if isinstance(data.get("SurveyData"), dict) else {}
        journal_value = int(survey.get("Value", 0))

        # Priority 2 & 3: seed / cache / async API fallback
        lookup_value  = self._value_lookup.get(species) if not journal_value else 0

        value = journal_value or lookup_value

        # Persist journal-supplied value back into the lookup cache
        if journal_value:
            self._value_lookup.update(species, journal_value)

        # Navigate/create the nested path: system → body → species
        bodies = self._systems.setdefault(system, {})
        species_map = bodies.setdefault(body, {})

        record = species_map.get(species, {
            "species":   species,
            "variant":   variant,
            "scan_type": scan_type,
            "value":     0,
        })
        record["scan_type"] = scan_type
        if value:
            record["value"] = value
        species_map[species] = record
        self._save_state()

        return {
            "event":     "ScanOrganic",
            "body":      body,
            "species":   species,
            "variant":   variant,
            "scan_type": scan_type,
            "system":    system,
            "value":     value,
        }

    def _handle_sell_organic(self, data: dict) -> dict:
        items = []
        for entry in data.get("BioData", []):
            items.append({
                "species": (entry.get("Species_Localised")
                            or entry.get("Species", "")),
                "value":   int(entry.get("Value", 0)),
                "bonus":   int(entry.get("Bonus", 0)),
            })
        # Data sold — clear all accumulated expedition data across all systems.
        # first_footfalls is intentionally preserved (permanent record).
        self._systems.clear()
        self._fss_counts.clear()
        self._saa_genera.clear()
        self._save_state()
        log.info("ExobiologyRole: all expedition data cleared after SellOrganicData")
        return {
            "event":       "SellOrganicData",
            "total_value": int(data.get("TotalEarnings", 0)),
            "items":       items,
        }

    def _handle_FSSBodySignals(self, data: dict) -> dict | None:
        system  = data.get("StarSystem", "") or self._system
        body    = data.get("BodyName", "")
        signals = data.get("Signals", [])

        bio_count = next(
            (int(s.get("Count", 0)) for s in signals
             if s.get("Type_Localised") == "Biological"),
            0,
        )
        if bio_count > 0 and system and body:
            self._fss_counts.setdefault(system, {})[body] = bio_count
            self._save_state()

        return {
            "event":   "FSSBodySignals",
            "system":  system,
            "body":    body,
            "signals": signals,
        }

    def _handle_SAASignalsFound(self, data: dict) -> dict | None:
        system  = data.get("StarSystem", "") or self._system
        body    = data.get("BodyName", "")
        signals = data.get("Signals", [])

        # Normalise genus list (Genus_Localised preferred over internal key)
        genera = [
            g.get("Genus_Localised") or g.get("Genus", "")
            for g in data.get("Genuses", [])
        ]
        genera = [g for g in genera if g]

        if genera and system and body:
            self._saa_genera.setdefault(system, {})[body] = genera
            self._save_state()

        return {
            "event":   "SAASignalsFound",
            "system":  system,
            "body":    body,
            "signals": signals,
            "genuses": [{"genus_localised": g} for g in genera],
        }

    def _handle_codex_entry(self, data: dict) -> dict | None:
        category = data.get("Category_Localised") or data.get("Category", "")
        if category not in _BIO_CATEGORIES:
            return None   # not a biology entry — drop

        species = data.get("Name_Localised") or data.get("Name", "")
        system  = self._system
        body    = self._body_name

        # Resolve the Vista Genomics scan value from the local seed / cache.
        # CodexEntry fires on the first scan step and gives us the exact species
        # name, so this is an early opportunity to fill in a value that may not
        # yet be on the scan record (ScanOrganic with ScanType="Log" arrives at
        # the same time but the record might not exist yet).
        value = self._value_lookup.get(species) if species else 0

        if species and value:
            # Back-fill the value on an existing scan record if present
            species_map = self._systems.get(system, {}).get(body, {})
            record = species_map.get(species)
            if record and record.get("value", 0) != value:
                record["value"] = value
                self._save_state()
            log.debug(
                "ExobiologyRole: CodexEntry resolved %d CR for %r on %s / %s",
                value, species, system, body,
            )

        return {
            "event":        "CodexEntry",
            "entry_id":     data.get("EntryID", 0),
            "name":         species,
            "category":     category,
            "system":       system,
            "body":         body,
            "value":        value,
            "is_new_entry": bool(data.get("IsNewEntry", False)),
        }
