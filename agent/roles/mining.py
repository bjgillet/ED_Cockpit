"""
ED Cockpit — Mining Role
=========================
Filters Elite Dangerous journal events relevant to mining activities.

Context File
------------
  Configuration file so mining session state survives agent restart
  File located at <config_dir>/mining.json 

    State file format:
    {
      "ship":{
        "name": <ship name>,
        "cargo_capacity": <float t>,
        "cargo_used": <float t>,
        "cargo": [
          {"item name":<string>, "item_quantity":int}
          ...
          ]
        },
        "limpets": {
            "collection": <int>,
            "prospector": <int>,
            "remaining": <int>,
        },
        "asteroid": {
            "materials": [
                {"name": <string>, "proportion": <int 0-100>},
                ...
            ],
            "remaining": <float 0-1>,
        },
      "last_updated": <ISO 8601 timestamp>
    }
Events handled
--------------
  ProspectedAsteroid — asteroid prospected; reports material composition
                        and the motherlode type if any.
  AsteroidCracked    — asteroid cracked open (for core mining).
  MiningRefined      — one unit of ore refined from the collector limpet
                        hopper into cargo.
  LaunchDrone        — drone (limpet) launched; we forward only
                        Collector and Prospector subtypes.

Wire payload shapes
-------------------
  ProspectedAsteroid →
    {
      "event":       "ProspectedAsteroid",
      "materials":   [{"name": "<loc>", "proportion": <float 0-1>}, ...],
      "content":     "Low" | "Medium" | "High",
      "motherlode":  "<type_localised>" | "",   # empty if not a motherlode
      "remaining":   <float>,  # fraction remaining (1.0 = untouched)
    }

  AsteroidCracked →
    {
      "event":      "AsteroidCracked",
      "body":       "<asteroid designation>",
    }

  MiningRefined →
    {
      "event": "MiningRefined",
      "type":  "<commodity_localised>",   # e.g. "Painite"
    }

  LaunchDrone (collection / prospector only) →
    {
      "event":      "LaunchDrone",
      "drone_type": "Collection" | "Prospector",
    }

Status payload (filter_status) →
    {
      "cargo":          <float t>,
      "cargo_scoop":    <bool>,
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

_MINING_DRONE_TYPES: frozenset[str] = frozenset({"Collection", "Prospector"})
log = logging.getLogger(__name__)

# ── Status.json flag bit ───────────────────────────────────────────────────
_FLAG_CARGO_SCOOP = 0x00000200


class MiningRole(BaseRole):
    """Mining role — filters and enriches asteroid-mining journal events."""
    _debug = False
    name = Role.MINING
    journal_events = frozenset({
        "AsteroidCracked",
        "ProspectedAsteroid",
        "MiningRefined",
        "LaunchDrone",
    })

    def __init__(self) -> None:
        self._config_dir = self._resolve_config_dir()
        self._state_path = self._config_dir / "mining_state.json"

        self._last_asteroid: dict = {
            "materials": [],
            "content": "",
            "motherlode": "",
            "remaining": 1.0,
        }
        self._cargo_tally: dict[str, int] = {}
        self._n_cracked: int = 0
        self._n_collectors: int = 0
        self._n_prospectors: int = 0
        self._last_status: dict = {"cargo": 0.0, "cargo_scoop": False}
        self._max_cargo_observed: float = 0.0

        self._load_state()

    @staticmethod
    def _resolve_config_dir() -> Path:
        if sys.platform == "win32":
            return Path.home() / "AppData" / "Roaming" / "ed-cockpit"
        return Path.home() / ".config" / "ed-cockpit"

    def _load_state(self) -> None:
        try:
            saved = json.loads(self._state_path.read_text(encoding="utf-8"))
        except Exception as exc:
            log.debug("MiningRole: no persisted state loaded: %s", exc)
            return

        asteroid = saved.get("asteroid", {})
        self._last_asteroid = {
            "materials": list(asteroid.get("materials", [])),
            "content": str(asteroid.get("content", "")),
            "motherlode": str(asteroid.get("motherlode", "")),
            "remaining": float(asteroid.get("remaining", 1.0)),
        }
        self._cargo_tally = {
            str(k): int(v) for k, v in saved.get("cargo_tally", {}).items()
        }

        counters = saved.get("counters", {})
        self._n_cracked = int(counters.get("cracked", 0))
        self._n_collectors = int(counters.get("collectors", 0))
        self._n_prospectors = int(counters.get("prospectors", 0))

        status = saved.get("status", {})
        self._last_status = {
            "cargo": float(status.get("cargo", 0.0)),
            "cargo_scoop": bool(status.get("cargo_scoop", False)),
        }
        self._max_cargo_observed = float(saved.get("max_cargo_observed", 0.0))

        log.info("MiningRole: state loaded from %s", self._state_path)

    def _save_state(self) -> None:
        try:
            self._config_dir.mkdir(parents=True, exist_ok=True)
            state = {
                "asteroid": dict(self._last_asteroid),
                "cargo_tally": dict(self._cargo_tally),
                "counters": {
                    "cracked": self._n_cracked,
                    "collectors": self._n_collectors,
                    "prospectors": self._n_prospectors,
                },
                "status": dict(self._last_status),
                "max_cargo_observed": self._max_cargo_observed,
                "last_updated": datetime.now(timezone.utc).isoformat(),
            }
            self._state_path.write_text(
                json.dumps(state, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as exc:
            log.warning("MiningRole: could not save state: %s", exc)

    def get_snapshot(self) -> dict | None:
        has_state = any([
            bool(self._cargo_tally),
            self._n_cracked > 0,
            self._n_collectors > 0,
            self._n_prospectors > 0,
            bool(self._last_asteroid.get("materials")),
            bool(self._last_asteroid.get("content")),
            bool(self._last_asteroid.get("motherlode")),
            float(self._last_status.get("cargo", 0.0)) > 0.0,
            bool(self._last_status.get("cargo_scoop", False)),
        ])
        if not has_state:
            return None
        return {
            "asteroid": dict(self._last_asteroid),
            "cargo_tally": dict(self._cargo_tally),
            "counters": {
                "cracked": self._n_cracked,
                "collectors": self._n_collectors,
                "prospectors": self._n_prospectors,
            },
            "status": dict(self._last_status),
            "max_cargo_observed": self._max_cargo_observed,
        }

    def filter(self, event_name: str, data: dict) -> dict | None:
        if event_name == "ProspectedAsteroid":
            return self._handle_prospected(data)
        if event_name == "AsteroidCracked":
            return self._handle_cracked(data)
        if event_name == "MiningRefined":
            return self._handle_refined(data)
        if event_name == "LaunchDrone":
            return self._handle_launch_drone(data)
        return None

    def filter_status(self, status: dict) -> dict | None:
        flags = int(status.get("Flags", 0))
        payload = {
            "cargo":       float(status.get("Cargo", 0.0)),
            "cargo_scoop": bool(flags & _FLAG_CARGO_SCOOP),
        }
        changed = payload != self._last_status
        self._last_status = payload
        self._max_cargo_observed = max(self._max_cargo_observed, payload["cargo"])
        if changed:
            self._save_state()
        return payload

    # ── Event handlers ─────────────────────────────────────────────────────
    def _handle_prospected(self, data: dict) -> dict:
        materials = []
        for m in data.get("Materials", []):
            materials.append({
                "name":       m.get("Name_Localised") or m.get("Name", ""),
                "proportion": float(m.get("Proportion", 0.0)),
            })
        payload = {
            "event":      "ProspectedAsteroid",
            "materials":  materials,
            "content":    data.get("Content", ""),
            "motherlode": (data.get("MotherlodeType_Localised")
                           or data.get("MotherlodeType", "")),
            "remaining":  float(data.get("Remaining", 1.0)),
        }
        self._last_asteroid = dict(payload)
        self._save_state()
        return payload

    def _handle_cracked(self, data: dict) -> dict:
        self._n_cracked += 1
        payload = {
            "event": "AsteroidCracked",
            "body":  data.get("Body", ""),
        }
        self._save_state()
        return payload

    def _handle_refined(self, data: dict) -> dict:
        ore = data.get("Type_Localised") or data.get("Type", "")
        if ore:
            self._cargo_tally[ore] = self._cargo_tally.get(ore, 0) + 1
        payload = {
            "event": "MiningRefined",
            "type":  ore,
        }
        self._save_state()
        return payload

    def _handle_launch_drone(self, data: dict) -> dict | None:
        drone_type = data.get("Type", "")
        if drone_type not in _MINING_DRONE_TYPES:
            return None
        if drone_type == "Collection":
            self._n_collectors += 1
        elif drone_type == "Prospector":
            self._n_prospectors += 1
        payload = {
            "event":      "LaunchDrone",
            "drone_type": drone_type,
        }
        self._save_state()
        return payload
