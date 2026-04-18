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
  Cargo              — full cargo inventory snapshot; used to reconcile
                       refined-material counts and remaining limpets.
  Loadout            — ship loadout snapshot; used to capture cargo capacity.
  Docked             — reset transient mining session sections.

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
      "cargo_capacity": <float t>,
      "available_limpets": <int>,
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
        "Loadout",
        "Cargo",
        "Docked",
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
        self._tracked_refined: set[str] = set()
        self._n_cracked: int = 0
        self._n_collectors: int = 0
        self._n_prospectors: int = 0
        self._available_limpets: int = 0
        self._cargo_capacity: float = 0.0
        self._last_status: dict = {"cargo": 0.0, "cargo_scoop": False}

        self._load_state()

    @staticmethod
    def _resolve_config_dir() -> Path:
        if sys.platform == "win32":
            return Path.home() / "AppData" / "Roaming" / "ed-cockpit"
        return Path.home() / ".config" / "ed-cockpit"

    def sync_from_journal_memory(self, snapshot: dict) -> None:
        """
        Seed cargo capacity/usage from EDApp journal memory bootstrap.

        This helps initialise the gauge correctly even when the current
        runtime has not yet emitted fresh Loadout/Cargo journal events.
        """
        changed = False
        ship = snapshot.get("ship", {}) if isinstance(snapshot, dict) else {}
        location_inv = snapshot.get("cargo_inventory", []) if isinstance(snapshot, dict) else []

        try:
            cap = float(ship.get("cargo_capacity", 0.0))
        except (TypeError, ValueError):
            cap = 0.0
        if cap > 0 and cap != self._cargo_capacity:
            self._cargo_capacity = cap
            changed = True

        if isinstance(location_inv, list):
            used = 0
            inv_map: dict[str, int] = {}
            for item in location_inv:
                if not isinstance(item, dict):
                    continue
                try:
                    count = int(item.get("Count", 0))
                except (TypeError, ValueError):
                    continue
                count = max(count, 0)
                used += count
                name = item.get("Name_Localised") or item.get("Name", "")
                if name:
                    inv_map[str(name)] = count
            used_f = float(used)
            if used_f != float(self._last_status.get("cargo", 0.0)):
                self._last_status["cargo"] = used_f
                changed = True
            limpets = self._extract_limpets(inv_map)
            if limpets != self._available_limpets:
                self._available_limpets = limpets
                changed = True

        if changed:
            self._save_state()

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
        tracked = saved.get("tracked_refined", [])
        if isinstance(tracked, list):
            self._tracked_refined = {str(name) for name in tracked if str(name)}

        counters = saved.get("counters", {})
        self._n_cracked = int(counters.get("cracked", 0))
        self._n_collectors = int(counters.get("collectors", 0))
        self._n_prospectors = int(counters.get("prospectors", 0))
        self._available_limpets = int(counters.get("available_limpets", 0))

        status = saved.get("status", {})
        self._last_status = {
            "cargo": float(status.get("cargo", 0.0)),
            "cargo_scoop": bool(status.get("cargo_scoop", False)),
        }
        self._cargo_capacity = float(saved.get("cargo_capacity", 0.0))

        log.info("MiningRole: state loaded from %s", self._state_path)

    def _save_state(self) -> None:
        try:
            self._config_dir.mkdir(parents=True, exist_ok=True)
            state = {
                "asteroid": dict(self._last_asteroid),
                "cargo_tally": dict(self._cargo_tally),
                "tracked_refined": sorted(self._tracked_refined),
                "counters": {
                    "cracked": self._n_cracked,
                    "collectors": self._n_collectors,
                    "prospectors": self._n_prospectors,
                    "available_limpets": self._available_limpets,
                },
                "status": dict(self._last_status),
                "cargo_capacity": self._cargo_capacity,
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
            self._available_limpets > 0,
            bool(self._last_asteroid.get("materials")),
            bool(self._last_asteroid.get("content")),
            bool(self._last_asteroid.get("motherlode")),
            float(self._last_status.get("cargo", 0.0)) > 0.0,
            self._cargo_capacity > 0.0,
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
                "available_limpets": self._available_limpets,
            },
            "status": dict(self._last_status),
            "cargo_capacity": self._cargo_capacity,
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
        if event_name == "Loadout":
            return self._handle_loadout(data)
        if event_name == "Cargo":
            return self._handle_cargo(data)
        if event_name == "Docked":
            return self._handle_docked(data)
        return None

    def filter_status(self, status: dict) -> dict | None:
        flags = int(status.get("Flags", 0))
        cargo_value = (
            float(status["Cargo"])
            if "Cargo" in status
            else float(self._last_status.get("cargo", 0.0))
        )
        payload = {
            "cargo":       cargo_value,
            "cargo_capacity": self._cargo_capacity,
            "available_limpets": self._available_limpets,
            "cargo_scoop": bool(flags & _FLAG_CARGO_SCOOP),
        }
        changed = payload != self._last_status
        self._last_status = payload
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
            self._tracked_refined.add(ore)
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
            if self._available_limpets > 0:
                self._available_limpets -= 1
        elif drone_type == "Prospector":
            self._n_prospectors += 1
            if self._available_limpets > 0:
                self._available_limpets -= 1
        payload = {
            "event":      "LaunchDrone",
            "drone_type": drone_type,
            "available_limpets": self._available_limpets,
        }
        self._save_state()
        return payload

    def _handle_loadout(self, data: dict) -> dict:
        self._cargo_capacity = float(data.get("CargoCapacity", 0.0))
        self._save_state()
        return {
            "event": "Loadout",
            "ship": data.get("Ship", ""),
            "ship_name": data.get("ShipName", ""),
            "cargo_capacity": self._cargo_capacity,
            "hull_health": float(data.get("HullHealth", 0.0)),
            "fuel_capacity": data.get("FuelCapacity", {}),
        }

    def _handle_cargo(self, data: dict) -> dict:
        inventory = data.get("Inventory")
        inv_map: dict[str, int] = {}
        used = float(self._last_status.get("cargo", 0.0))
        have_inventory = isinstance(inventory, list)
        if have_inventory:
            used = 0.0
            for item in inventory:
                if not isinstance(item, dict):
                    continue
                try:
                    count = int(item.get("Count", 0))
                except (TypeError, ValueError):
                    continue
                count = max(count, 0)
                used += count
                name = item.get("Name_Localised") or item.get("Name", "")
                if name:
                    inv_map[str(name)] = count
        elif "Count" in data:
            try:
                used = float(data.get("Count", used))
            except (TypeError, ValueError):
                pass
        self._last_status["cargo"] = float(used)

        # Keep refined tally aligned with real cargo inventory:
        # if cargo is sold/transferred/refuelled, tracked materials decrease too.
        if have_inventory:
            for name in list(self._tracked_refined):
                current = int(inv_map.get(name, 0))
                if current <= 0:
                    self._cargo_tally.pop(name, None)
                else:
                    self._cargo_tally[name] = current

            limpet_count = self._extract_limpets(inv_map)
            if limpet_count is not None:
                self._available_limpets = limpet_count
        self._save_state()
        return {
            "event": "Cargo",
            "cargo": float(used),
            "available_limpets": self._available_limpets,
            "refined_cargo_tally": dict(self._cargo_tally),
            "inventory": inventory if have_inventory else [],
        }

    def _handle_docked(self, data: dict) -> dict:
        self._last_asteroid = {
            "materials": [],
            "content": "",
            "motherlode": "",
            "remaining": 1.0,
        }
        self._n_cracked = 0
        self._n_collectors = 0
        self._n_prospectors = 0
        self._save_state()
        return {
            "event": "Docked",
            "station": data.get("StationName", ""),
            "system": data.get("StarSystem", ""),
        }

    @staticmethod
    def _extract_limpets(inv_map: dict[str, int]) -> int | None:
        for key, value in inv_map.items():
            k = key.strip().lower()
            if ("limpet" in k) or ("drone" in k):
                return int(value)
        return None
