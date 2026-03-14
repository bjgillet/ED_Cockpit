"""
ED Assist — Session Monitoring Role
=====================================
Filters Elite Dangerous journal events relevant to general session
monitoring: game state, system / station location, commander status.

Events handled
--------------
  LoadGame          — new game session (CMDR name, credits, ship).
  Location          — initial location snapshot at session start or after
                      a loading screen (system, body, docked status).
  FSDJump           — jumped to a new system.
  Docked            — docked at a station or outpost.
  Undocked          — undocked.
  Died              — commander died.
  Shutdown          — game shutdown event.

Wire payload shapes
-------------------
  LoadGame →
    {
      "event":    "LoadGame",
      "cmdr":     "<commander name>",
      "ship":     "<ship type localised>",
      "credits":  <int>,
      "loan":     <int>,
    }

  Location →
    {
      "event":          "Location",
      "system":         "<system name>",
      "body":           "<body name>",
      "body_type":      "<body type>",
      "docked":         <bool>,
      "station":        "<station name>" | "",
      "station_type":   "<station type>" | "",
      "allegiance":     "<allegiance>" | "",
      "security":       "<security>" | "",
    }

  FSDJump →
    {
      "event":          "FSDJump",
      "system":         "<system name>",
      "body":           "<body name>",
      "star_class":     "<star class>",
      "distance":       <float ly>,
      "fuel_used":      <float t>,
      "fuel_level":     <float t>,
    }

  Docked →
    {
      "event":          "Docked",
      "system":         "<system name>",
      "station":        "<station name>",
      "station_type":   "<station type>",
      "services":       ["<service>", ...],
    }

  Undocked →
    {
      "event":   "Undocked",
      "station": "<station name>",
    }

  Died →
    {
      "event":   "Died",
      "killers": [{"name": "<name>", "ship": "<ship>", "rank": "<rank>"}, ...],
    }

  Shutdown →
    { "event": "Shutdown" }
"""
from __future__ import annotations

from agent.roles.base_role import BaseRole
from shared.roles_def import Role


class SessionRole(BaseRole):
    """Session monitoring role — key game-state transitions."""

    name = Role.SESSION_MONITORING
    journal_events = frozenset({
        "LoadGame",
        "Location",
        "FSDJump",
        "Docked",
        "Undocked",
        "Died",
        "Shutdown",
    })

    def filter(self, event_name: str, data: dict) -> dict | None:
        handler = _HANDLERS.get(event_name)
        if handler is None:
            return None
        return handler(data)


# ── Static event handlers ──────────────────────────────────────────────────

def _load_game(data: dict) -> dict:
    return {
        "event":   "LoadGame",
        "cmdr":    data.get("Commander", ""),
        "ship":    (data.get("Ship_Localised") or data.get("Ship", "")),
        "credits": int(data.get("Credits", 0)),
        "loan":    int(data.get("Loan", 0)),
    }


def _location(data: dict) -> dict:
    return {
        "event":        "Location",
        "system":       data.get("StarSystem", ""),
        "body":         data.get("Body", ""),
        "body_type":    data.get("BodyType", ""),
        "docked":       bool(data.get("Docked", False)),
        "station":      data.get("StationName", ""),
        "station_type": (data.get("StationType_Localised")
                         or data.get("StationType", "")),
        "allegiance":   data.get("SystemAllegiance", ""),
        "security":     (data.get("SystemSecurity_Localised")
                         or data.get("SystemSecurity", "")),
    }


def _fsd_jump(data: dict) -> dict:
    return {
        "event":      "FSDJump",
        "system":     data.get("StarSystem", ""),
        "body":       data.get("Body", ""),
        "star_class": data.get("StarClass", ""),
        "distance":   float(data.get("JumpDist", 0.0)),
        "fuel_used":  float(data.get("FuelUsed", 0.0)),
        "fuel_level": float(data.get("FuelLevel", 0.0)),
    }


def _docked(data: dict) -> dict:
    services = [
        svc.get("Name_Localised") or svc.get("Name", "")
        for svc in data.get("StationServices", [])
    ]
    return {
        "event":        "Docked",
        "system":       data.get("StarSystem", ""),
        "station":      data.get("StationName", ""),
        "station_type": (data.get("StationType_Localised")
                         or data.get("StationType", "")),
        "services":     services,
    }


def _undocked(data: dict) -> dict:
    return {
        "event":   "Undocked",
        "station": data.get("StationName", ""),
    }


def _died(data: dict) -> dict:
    killers = []
    for k in data.get("Killers", []):
        killers.append({
            "name":  k.get("Name", ""),
            "ship":  k.get("Ship", ""),
            "rank":  k.get("Rank", ""),
        })
    # Single-killer case (no "Killers" list)
    if not killers and data.get("KillerName"):
        killers.append({
            "name": data.get("KillerName", ""),
            "ship": data.get("KillerShip", ""),
            "rank": data.get("KillerRank", ""),
        })
    return {
        "event":   "Died",
        "killers": killers,
    }


def _shutdown(data: dict) -> dict:
    return {"event": "Shutdown"}


_HANDLERS: dict[str, object] = {
    "LoadGame":  _load_game,
    "Location":  _location,
    "FSDJump":   _fsd_jump,
    "Docked":    _docked,
    "Undocked":  _undocked,
    "Died":      _died,
    "Shutdown":  _shutdown,
}
