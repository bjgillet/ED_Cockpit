"""
ED Assist — Navigation Role
=============================
Filters Elite Dangerous journal events relevant to planet/surface
navigation: approach, landing, surface signals, and barycentre scans.

Events handled
--------------
  ApproachBody      — entering orbital-glide or low-altitude approach
                      to a planet or moon.
  LeaveBody         — leaving a planet's atmosphere / departure climb.
  Touchdown         — ship touched down on a planetary surface.
  Liftoff           — ship lifted off from a planetary surface.
  SAASignalsFound   — surface-scan signals detected after a DSS probe.
  ScanBaryCentre    — barycentre scan result (binary/multiple systems).

Wire payload shapes
-------------------
  ApproachBody / LeaveBody →
    {
      "event":      "ApproachBody" | "LeaveBody",
      "system":     "<system name>",
      "body":       "<body name>",
      "body_id":    <int>,
    }

  Touchdown / Liftoff →
    {
      "event":      "Touchdown" | "Liftoff",
      "latitude":   <float>,
      "longitude":  <float>,
      "body":       "<body name>",
      "on_station": <bool>,
    }

  SAASignalsFound →
    {
      "event":    "SAASignalsFound",
      "body":     "<body name>",
      "body_id":  <int>,
      "signals":  [{"type": "<localised>", "count": <int>}, ...],
      "genuses":  ["<localised genus>", ...],
    }

  ScanBaryCentre →
    {
      "event":         "ScanBaryCentre",
      "system":        "<system name>",
      "body_id":       <int>,
      "semi_major_au": <float>,
      "orbital_inc":   <float deg>,
      "eccentricity":  <float>,
    }

Status payload (filter_status) — only emitted when positional data is present →
    {
      "latitude":     <float deg>,
      "longitude":    <float deg>,
      "heading":      <int deg 0-359>,
      "altitude":     <float m>,
      "body_radius":  <float m>,
      "body_name":    "<string>",
      "landed":       <bool>,
      "in_srv":       <bool>,
    }
"""
from __future__ import annotations

from agent.roles.base_role import BaseRole
from shared.roles_def import Role

# ── Status.json flag bits ──────────────────────────────────────────────────
_FLAG_LANDED = 0x00000002
_FLAG_IN_SRV = 0x00040000


class NavigationRole(BaseRole):
    """Navigation role — planet approach, landing, and surface signals."""

    name = Role.NAVIGATION
    journal_events = frozenset({
        "ApproachBody",
        "LeaveBody",
        "Touchdown",
        "Liftoff",
        "SAASignalsFound",
        "ScanBaryCentre",
    })

    def filter(self, event_name: str, data: dict) -> dict | None:
        handler = _HANDLERS.get(event_name)
        if handler is None:
            return None
        return handler(data)

    def filter_status(self, status: dict) -> dict | None:
        # Latitude is only present when the ship is near a planetary body.
        if "Latitude" not in status:
            return None
        flags = int(status.get("Flags", 0))
        return {
            "latitude":    float(status.get("Latitude",     0.0)),
            "longitude":   float(status.get("Longitude",    0.0)),
            "heading":     int(status.get("Heading",        0)),
            "altitude":    float(status.get("Altitude",     0.0)),
            "body_radius": float(status.get("PlanetRadius", 0.0)),
            "body_name":   str(status.get("BodyName",       "")),
            "landed":      bool(flags & _FLAG_LANDED),
            "in_srv":      bool(flags & _FLAG_IN_SRV),
        }


# ── Static event handlers ──────────────────────────────────────────────────

def _approach_leave(data: dict) -> dict:
    return {
        "event":   data.get("event", ""),
        "system":  data.get("StarSystem", ""),
        "body":    data.get("Body", ""),
        "body_id": int(data.get("BodyID", 0)),
    }


def _touch_liftoff(data: dict) -> dict:
    return {
        "event":      data.get("event", ""),
        "latitude":   float(data.get("Latitude", 0.0)),
        "longitude":  float(data.get("Longitude", 0.0)),
        "body":       data.get("Body", ""),
        "on_station": bool(data.get("PlayerControlled", True)
                          and data.get("NearestDestination", "") != ""),
    }


def _saa_signals(data: dict) -> dict:
    signals = []
    for s in data.get("Signals", []):
        signals.append({
            "type":  s.get("Type_Localised") or s.get("Type", ""),
            "count": int(s.get("Count", 0)),
        })
    genuses = [
        g.get("Genus_Localised") or g.get("Genus", "")
        for g in data.get("Genuses", [])
    ]
    return {
        "event":   "SAASignalsFound",
        "body":    data.get("BodyName", ""),
        "body_id": int(data.get("BodyID", 0)),
        "signals": signals,
        "genuses": genuses,
    }


def _scan_barycentre(data: dict) -> dict:
    return {
        "event":         "ScanBaryCentre",
        "system":        data.get("StarSystem", ""),
        "body_id":       int(data.get("BodyID", 0)),
        "semi_major_au": float(data.get("SemiMajorAxis", 0.0)) / 1.496e11,
        "orbital_inc":   float(data.get("OrbitalInclination", 0.0)),
        "eccentricity":  float(data.get("Eccentricity", 0.0)),
    }


_HANDLERS: dict[str, object] = {
    "ApproachBody":   _approach_leave,
    "LeaveBody":      _approach_leave,
    "Touchdown":      _touch_liftoff,
    "Liftoff":        _touch_liftoff,
    "SAASignalsFound": _saa_signals,
    "ScanBaryCentre": _scan_barycentre,
}
