"""
ED Cockpit — Mining Role
=========================
Filters Elite Dangerous journal events relevant to mining activities.

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

from agent.roles.base_role import BaseRole
from shared.roles_def import Role

_MINING_DRONE_TYPES: frozenset[str] = frozenset({"Collection", "Prospector"})

# ── Status.json flag bit ───────────────────────────────────────────────────
_FLAG_CARGO_SCOOP = 0x00000200


class MiningRole(BaseRole):
    """Mining role — filters and enriches asteroid-mining journal events."""
    _debug=True
    name = Role.MINING
    journal_events = frozenset({
        "AsteroidCracked",
        "ProspectedAsteroid",
        "MiningRefined",
        "LaunchDrone",
    })

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
        return {
            "cargo":       float(status.get("Cargo", 0.0)),
            "cargo_scoop": bool(flags & _FLAG_CARGO_SCOOP),
        }

    # ── Event handlers ─────────────────────────────────────────────────────

    @staticmethod
    def _handle_prospected(data: dict) -> dict:
        materials = []
        for m in data.get("Materials", []):
            materials.append({
                "name":       m.get("Name_Localised") or m.get("Name", ""),
                "proportion": float(m.get("Proportion", 0.0)),
            })
        return {
            "event":      "ProspectedAsteroid",
            "materials":  materials,
            "content":    data.get("Content", ""),
            "motherlode": (data.get("MotherlodeType_Localised")
                           or data.get("MotherlodeType", "")),
            "remaining":  float(data.get("Remaining", 1.0)),
        }

    @staticmethod
    def _handle_cracked(data: dict) -> dict:
        return {
            "event": "AsteroidCracked",
            "body":  data.get("Body", ""),
        }

    @staticmethod
    def _handle_refined(data: dict) -> dict:
        return {
            "event": "MiningRefined",
            "type":  data.get("Type_Localised") or data.get("Type", ""),
        }

    @staticmethod
    def _handle_launch_drone(data: dict) -> dict | None:
        drone_type = data.get("Type", "")
        if drone_type not in _MINING_DRONE_TYPES:
            return None
        return {
            "event":      "LaunchDrone",
            "drone_type": drone_type,
        }
