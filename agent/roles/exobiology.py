"""
ED Cockpit — Exobiology Role
=============================
Filters Elite Dangerous journal events relevant to exobiology activities
and prepares them for forwarding to subscribed clients.

Events handled
--------------
  ApproachBody      — player approaches a landable body; used to track the
                      current body name and system (not forwarded to clients).
  ScanOrganic       — player performed one scan step on an organic sample.
                      Each species requires 3 scans; the ``ScanType`` field
                      distinguishes Log (1st), Sample (2nd), and Analyse (3rd).
  SellOrganicData   — player sold exobiology data at Vista Genomics.
  CodexEntry        — new codex entry; we forward only biology-category entries.

State persistence
-----------------
  The role maintains a JSON state file at
  ``<config_dir>/exobiology_state.json`` that survives agent restarts.
  It stores the last known system, body name, and per-species scan progress.
  The file is loaded at instantiation and updated after every relevant event.

  State file format::

    {
      "system":       "<system name>",
      "body":         "<body name>",
      "body_id":      <int | null>,
      "scans": [
        { "species": "<localised>", "variant": "<localised>",
          "scan_type": "Log" | "Sample" | "Analyse", "value": <int cr> },
        ...
      ],
      "last_updated": "<ISO-8601 UTC>"
    }

  Scans are cleared when a ``SellOrganicData`` event is received (data sold).

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
      "value":        <int cr>,               # 0 if not yet known
    }

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
      "name":         "<entry name>",
      "category":     "<category>",
      "system":       "<system>",
      "body":         "<body>",
      "is_new_entry": <bool>,
    }
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from agent.roles.base_role import BaseRole
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
        "ScanOrganic",
        "SellOrganicData",
        "CodexEntry",
    })

    def __init__(self) -> None:
        self._config_dir = self._resolve_config_dir()
        self._state_path = self._config_dir / "exobiology_state.json"

        # In-memory state — populated from file at startup
        self._system:    str            = ""
        self._body_name: str            = ""
        self._body_id:   int | None     = None
        self._scans:     dict[str, dict] = {}   # species → scan record

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
 #       if self._journal_updated == False:
 #           self._refresh_state()


    def _refresh_state(self) -> None:
        """If state file is missing, creates one from Elite Dangerous journal, otherwise checks it is up to date with last session"""
        self._journal_updated = True
        journals = sorted(self._state_path.glob("Journal.*.json"), reverse=True)
        if not journals:
            log.info("ExobiologyRole: no previous Elite Dangerous sessions found - starting with empty state")
            return
        else:
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
            self._system    = saved.get("system", "")
            self._body_name = saved.get("body", "")
            self._body_id   = saved.get("body_id")
            for item in saved.get("scans", []):
                species = item.get("species", "")
                if species:
                    self._scans[species] = dict(item)
            log.info("ExobiologyRole: state loaded from %s", self._state_path)
        except Exception as exc:
            log.warning("ExobiologyRole: could not load state: %s", exc)

    def _save_state(self) -> None:
        """Persist current in-memory state to disk."""
        try:
            self._config_dir.mkdir(parents=True, exist_ok=True)
            state = {
                "system":       self._system,
                "body":         self._body_name,
                "body_id":      self._body_id,
                "scans":        list(self._scans.values()),
                "last_updated": datetime.now(timezone.utc).isoformat(),
            }
            self._state_path.write_text(
                json.dumps(state, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as exc:
            log.warning("ExobiologyRole: could not save state: %s", exc)

    # ── BaseRole interface ──────────────────────────────────────────────────

    def filter(self, event_name: str, data: dict) -> dict | None:
        if event_name == "ApproachBody":
            self._body_name = data.get("Body", "")
            self._body_id   = data.get("BodyID")
            self._system    = data.get("StarSystem", "")
            self._save_state()
            return None   # not forwarded to clients
        if event_name == "ScanOrganic":
            return self._handle_scan_organic(data)
        if event_name == "SellOrganicData":
            return self._handle_sell_organic(data)
        if event_name == "CodexEntry":
            return self._handle_codex_entry(data)
        if event_name == "SAASignalsFound":
            return self._handle_SAASignalsFound(data)
        if event_name == "FSSBodySignals":
            return self._handle_FSSBodySignalsFound(data)
        return None

    # ── Event handlers ─────────────────────────────────────────────────────

    def _handle_FSSBodySignalsFound(self, data: dict) -> dict | None:
        signals = data.get("Signals", [])
        return {
            "event": "FSSBodySignalsFound",
            "system": data.get("StarSystem", ""),
            "body":   data.get("BodyName", ""),
            "signals": signals,
        }

    def _handle_SAASignalsFound(self, data: dict) -> dict | None:
        signals = data.get("Signals", [])
        return {
            "event": "SAASignalsFound",
            "system": data.get("StarSystem", ""),
            "body":   data.get("BodyName", ""),
            "signals": signals,
        }
    
    def _handle_scan_organic(self, data: dict) -> dict:
        species   = data.get("Species_Localised") or data.get("Species", "")
        variant   = data.get("Variant_Localised")  or data.get("Variant", "")
        scan_type = data.get("ScanType", "")

        # Estimated value: if the game supplies it use it, otherwise keep previous
        value = int(data.get("SurveyData", {}).get("Value", 0)
                    if isinstance(data.get("SurveyData"), dict) else 0)

        # Update or create the scan record for this species
        record = self._scans.get(species, {
            "species": species,
            "variant": variant,
            "scan_type": scan_type,
            "value": 0,
        })
        record["scan_type"] = scan_type
        if value:
            record["value"] = value
        self._scans[species] = record
        self._save_state()

        return {
            "event":     "ScanOrganic",
            "body":      self._body_name,
            "species":   species,
            "variant":   variant,
            "scan_type": scan_type,
            "system":    self._system,
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
        # Data sold — clear tracked scans
        self._scans.clear()
        self._save_state()
        return {
            "event":       "SellOrganicData",
            "total_value": int(data.get("TotalEarnings", 0)),
            "items":       items,
        }

    @staticmethod
    def _handle_codex_entry(data: dict) -> dict | None:
        category = data.get("Category_Localised") or data.get("Category", "")
        if category not in _BIO_CATEGORIES:
            return None   # not a biology entry — drop
        return {
            "event":        "CodexEntry",
            "entry_id":     data.get("EntryID", 0),
            "name":         (data.get("Name_Localised")
                             or data.get("Name", "")),
            "category":     category,
            "system":       data.get("System", ""),
            "body":         data.get("NearestDestination_Localised")
                            or data.get("NearestDestination", ""),
            "is_new_entry": bool(data.get("IsNewEntry", False)),
        }
