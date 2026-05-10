"""
ED Cockpit — Route Role (Fleet Carrier)
========================================
Tracks fleet-carrier route progress and ship/FC positions.

This role is FC-only: it follows ``CarrierJump`` events (not ``FSDJump``
for ship jumps) to advance the waypoint index.

Journal events handled
----------------------
  CarrierLocation   — FC arrived at a new system (jump completed).
                      Updates FC system; coordinates are taken from the
                      matching waypoint entry (CarrierLocation carries no
                      StarPos).  Advances current waypoint index.
  CarrierJump       — Legacy / alternative FC jump event.  Same handling
                      as CarrierLocation when StarPos is present.
  CarrierStats      — Opens carrier management panel.  Carries
                      ``FuelLevel`` (current tritium in tonnes).
  CarrierDepositFuel — Tritium deposited onto the carrier.  Updates
                      ``_tritium_available`` via the ``Total`` field.
  FSDJump           — Ship FSD jump.  Updates ship system + StarPos
                      coordinates so FC↔ship distance can be computed.
  Location          — Session start/loading screen.  Updates ship
                      position; if docked on a fleet carrier also
                      updates the known FC system.
  Docked            — Docking event.  If docked on a fleet carrier
                      (StationType="FleetCarrier") updates the FC
                      system and coordinates.

Wire payload shapes
-------------------
All events broadcast to clients have ``EventMessage.event`` set to one
of the following:

  StateSnapshot →  Full state (sent to a newly connecting client).
  RouteLoaded   →  A new route has been calculated and loaded.
  RouteProgress →  FC jumped; current_idx / fc_system updated.
  TritiumUpdate →  Tritium available on FC changed.
  ShipMoved     →  Ship jumped to a new system; fc_distance updated.

All payloads share the same schema (see ``_build_state_dict()``).

GUI subscription
----------------
Agent-side GUI panels may register a callback to be notified of every
state change without going through the WebSocket layer::

    def my_callback(event: str, data: dict) -> None: ...

    role.subscribe_gui(my_callback)
    role.unsubscribe_gui(my_callback)

Callbacks are invoked from whatever thread updates the role state
(JournalReader thread or asyncio executor thread).  GUI callbacks
must be thread-safe — typically they put onto a ``queue.Queue`` that
an ``after()`` poller drains on the tkinter thread.
"""
from __future__ import annotations

import json
import logging
import math
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from agent.roles.base_role import BaseRole
from shared.roles_def import Role

log = logging.getLogger(__name__)


class RouteRole(BaseRole):
    """Fleet-carrier route tracking role."""

    name = Role.ROUTE

    journal_events = frozenset({
        "CarrierLocation",
        "CarrierJump",
        "CarrierStats",
        "CarrierDepositFuel",
        "FSDJump",
        "Location",
        "Docked",
    })

    # Name of the persistence file written inside the agent config dir.
    _STATE_FILE = "route_state.json"

    def __init__(self) -> None:
        self._lock = threading.Lock()

        # ── Persistence ───────────────────────────────────────────────
        self._config_dir: Optional[Path] = None

        # ── Route plan ────────────────────────────────────────────────
        # Each waypoint: {system, x, y, z, distance, fuel_cost}
        self._waypoints:    list[dict[str, Any]] = []
        self._source:       str   = ""
        self._destination:  str   = ""
        self._total_distance: float = 0.0   # sum of all hop distances

        # Index into _waypoints of the FC's current position.
        # -1 means no route or FC not yet matched to any waypoint.
        self._current_idx:  int   = -1

        # ── Fleet carrier state ───────────────────────────────────────
        self._fc_system:    str   = ""
        self._fc_coords:    Optional[tuple[float, float, float]] = None
        self._tritium:      float = 0.0

        # ── Ship state ────────────────────────────────────────────────
        self._ship_system:  str   = ""
        self._ship_coords:  Optional[tuple[float, float, float]] = None

        # ── GUI observer callbacks ────────────────────────────────────
        self._gui_lock:      threading.Lock = threading.Lock()
        self._gui_callbacks: list[Callable[[str, dict], None]] = []

    # ── Public: route management ───────────────────────────────────────────

    def set_route(
        self,
        waypoints:   list[dict[str, Any]],
        destination: str,
        source:      str,
    ) -> dict:
        """
        Load a newly planned route.

        Called after a successful Spansh API response.  Updates all
        route-related state and returns the full state dict for
        broadcasting as a ``RouteLoaded`` event.

        Parameters
        ----------
        waypoints : list[dict]
            Ordered list of waypoint dicts as returned by
            ``agent.tools.spansh.fetch_fleet_carrier_route``.
        destination : str
            Destination system name.
        source : str
            Source system name (should match first waypoint).
        """
        with self._lock:
            self._waypoints   = list(waypoints)
            self._source      = source
            self._destination = destination
            self._total_distance = sum(w["distance"] for w in waypoints)
            # Locate the FC in the new waypoint list
            self._current_idx = self._find_fc_waypoint_index()

        snapshot = self._build_state_dict()
        self._save_state()
        self._notify_gui("RouteLoaded", snapshot)
        log.info(
            "RouteRole: route loaded  %r → %r  (%d waypoints, %.0f LY)",
            source, destination, len(waypoints), self._total_distance,
        )
        return snapshot

    @property
    def fc_system(self) -> str:
        """Current known FC system (thread-safe read)."""
        with self._lock:
            return self._fc_system

    @property
    def destination(self) -> str:
        """Last planned destination (thread-safe read)."""
        with self._lock:
            return self._destination

    @property
    def tritium_available(self) -> float:
        """Current tritium on the FC (thread-safe read)."""
        with self._lock:
            return self._tritium

    # ── Persistence ────────────────────────────────────────────────────────

    def set_config_dir(self, config_dir: Path) -> None:
        """
        Set the agent config directory and restore any saved route state.

        Called once by ``EDApp`` shortly after the role is instantiated.
        Loads ``route_state.json`` from ``config_dir`` if it exists, so the
        last planned route survives an agent restart.
        """
        self._config_dir = config_dir
        self._load_state()

    def _load_state(self) -> None:
        """Restore route state from ``route_state.json`` if present."""
        if self._config_dir is None:
            return
        path = self._config_dir / self._STATE_FILE
        if not path.exists():
            return
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning("RouteRole: could not read %s: %s", path, exc)
            return

        with self._lock:
            self._waypoints      = list(saved.get("waypoints", []))
            self._source         = str(saved.get("source", ""))
            self._destination    = str(saved.get("destination", ""))
            self._total_distance = float(saved.get("total_distance", 0.0))
            self._current_idx    = int(saved.get("current_idx", -1))
            self._fc_system      = str(saved.get("fc_system", ""))
            self._tritium        = float(saved.get("tritium_available", 0.0))
            raw_coords = saved.get("fc_coords")
            self._fc_coords = tuple(raw_coords) if raw_coords and len(raw_coords) == 3 else None  # type: ignore[assignment]

        log.info(
            "RouteRole: restored route from %s  (%d waypoints, dest=%r, "
            "current_idx=%d)",
            path.name, len(self._waypoints), self._destination, self._current_idx,
        )

    def _save_state(self) -> None:
        """Persist current route state to ``route_state.json``."""
        if self._config_dir is None:
            return
        try:
            self._config_dir.mkdir(parents=True, exist_ok=True)
            with self._lock:
                payload = {
                    "waypoints":          self._waypoints,
                    "source":             self._source,
                    "destination":        self._destination,
                    "total_distance":     self._total_distance,
                    "current_idx":        self._current_idx,
                    "fc_system":          self._fc_system,
                    "fc_coords":          list(self._fc_coords) if self._fc_coords else None,
                    "tritium_available":  self._tritium,
                    "last_updated":       datetime.now(timezone.utc).isoformat(),
                }
            path = self._config_dir / self._STATE_FILE
            path.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as exc:
            log.warning("RouteRole: could not save state: %s", exc)

    # ── GUI subscription ───────────────────────────────────────────────────

    def subscribe_gui(self, callback: Callable[[str, dict], None]) -> None:
        """Register a GUI observer callback (thread-safe)."""
        with self._gui_lock:
            if callback not in self._gui_callbacks:
                self._gui_callbacks.append(callback)

    def unsubscribe_gui(self, callback: Callable[[str, dict], None]) -> None:
        """Unregister a previously registered GUI observer (thread-safe)."""
        with self._gui_lock:
            try:
                self._gui_callbacks.remove(callback)
            except ValueError:
                pass

    # ── BaseRole contract ──────────────────────────────────────────────────

    def filter(self, event_name: str, data: dict) -> dict | None:
        handler = _HANDLERS.get(event_name)
        if handler is None:
            return None
        return handler(self, data)

    def get_snapshot(self) -> dict | None:
        with self._lock:
            if not self._waypoints:
                return None
        return self._build_state_dict()

    # ── Internal event handlers ────────────────────────────────────────────

    def _on_carrier_location(self, data: dict) -> dict | None:
        """
        FC arrived at a new system after a jump.

        ``CarrierLocation`` carries no ``StarPos``, so coordinates are
        sourced from the matching waypoint entry in the loaded route.
        If the system is not in the route (e.g. a manual jump outside the
        planned route) the FC position is updated without new coordinates.

        Events with ``CarrierType != "FleetCarrier"`` (e.g. SquadronCarrier)
        are ignored.
        """
        if data.get("CarrierType") != "FleetCarrier":
            return None

        system = str(data.get("StarSystem", ""))
        if not system:
            return None

        with self._lock:
            self._fc_system = system
            if self._waypoints:
                self._current_idx = self._find_fc_waypoint_index()
                if self._current_idx >= 0:
                    wp = self._waypoints[self._current_idx]
                    self._fc_coords = (float(wp["x"]), float(wp["y"]), float(wp["z"]))

        payload = self._build_state_dict()
        payload["event"] = "RouteProgress"
        self._save_state()
        self._notify_gui("RouteProgress", payload)
        log.info(
            "RouteRole: CarrierLocation → %r  (waypoint idx=%d)",
            system, self._current_idx,
        )
        return payload

    def _on_carrier_jump(self, data: dict) -> dict | None:
        system = str(data.get("StarSystem", ""))
        pos    = data.get("StarPos", [])
        coords = _coords(pos)

        with self._lock:
            self._fc_system = system
            if coords:
                self._fc_coords = coords
            if self._waypoints:
                self._current_idx = self._find_fc_waypoint_index()

        payload = self._build_state_dict()
        payload["event"] = "RouteProgress"
        self._save_state()
        self._notify_gui("RouteProgress", payload)
        return payload

    def _on_carrier_stats(self, data: dict) -> dict | None:
        fuel = data.get("FuelLevel")
        if fuel is None:
            return None
        with self._lock:
            self._tritium = float(fuel)

        payload = self._build_state_dict()
        payload["event"] = "TritiumUpdate"
        self._save_state()
        self._notify_gui("TritiumUpdate", payload)
        return payload

    def _on_carrier_deposit_fuel(self, data: dict) -> dict | None:
        total = data.get("Total")
        if total is None:
            return None
        with self._lock:
            self._tritium = float(total)

        payload = self._build_state_dict()
        payload["event"] = "TritiumUpdate"
        self._save_state()
        self._notify_gui("TritiumUpdate", payload)
        return payload

    def _on_fsd_jump(self, data: dict) -> dict | None:
        system = str(data.get("StarSystem", ""))
        pos    = data.get("StarPos", [])
        coords = _coords(pos)

        with self._lock:
            self._ship_system = system
            self._ship_coords = coords

        payload = self._build_state_dict()
        payload["event"] = "ShipMoved"
        self._notify_gui("ShipMoved", payload)
        return payload

    def _on_location(self, data: dict) -> dict | None:
        system = str(data.get("StarSystem", ""))
        pos    = data.get("StarPos", [])
        coords = _coords(pos)
        station_type = str(data.get("StationType", ""))

        with self._lock:
            self._ship_system = system
            self._ship_coords = coords
            # If the player is docked on a fleet carrier, that carrier is here
            if station_type == "FleetCarrier":
                self._fc_system = system
                self._fc_coords = coords
                if self._waypoints:
                    self._current_idx = self._find_fc_waypoint_index()

        payload = self._build_state_dict()
        payload["event"] = "ShipMoved"
        self._notify_gui("ShipMoved", payload)
        return payload

    def _on_docked(self, data: dict) -> dict | None:
        if str(data.get("StationType", "")) != "FleetCarrier":
            return None

        system = str(data.get("StarSystem", ""))
        pos    = data.get("StarPos", [])
        coords = _coords(pos)

        with self._lock:
            self._fc_system = system
            self._fc_coords = coords
            if self._waypoints:
                self._current_idx = self._find_fc_waypoint_index()

        payload = self._build_state_dict()
        payload["event"] = "RouteProgress"
        self._save_state()
        self._notify_gui("RouteProgress", payload)
        return payload

    # ── Helpers ────────────────────────────────────────────────────────────

    def _find_fc_waypoint_index(self) -> int:
        """
        Return the index of the waypoint matching the current FC system.

        Must be called while ``self._lock`` is held.
        Returns -1 if the FC system is unknown or not in the waypoints list.
        """
        if not self._fc_system or not self._waypoints:
            return -1
        fc = self._fc_system.lower()
        for i, wp in enumerate(self._waypoints):
            if wp["system"].lower() == fc:
                return i
        return -1

    def _compute_fc_distance(self) -> Optional[float]:
        """
        3D Euclidean distance (LY) between FC and ship.

        Returns None if either position is unknown.
        Must be called while ``self._lock`` is held.
        """
        if self._fc_coords is None or self._ship_coords is None:
            return None
        dx = self._fc_coords[0] - self._ship_coords[0]
        dy = self._fc_coords[1] - self._ship_coords[1]
        dz = self._fc_coords[2] - self._ship_coords[2]
        return math.sqrt(dx * dx + dy * dy + dz * dz)

    def _tritium_needed(self) -> float:
        """
        Sum of fuel_cost for all remaining (not-yet-done) waypoints.

        A waypoint is "done" when its index <= self._current_idx.
        Must be called while ``self._lock`` is held.
        """
        if not self._waypoints:
            return 0.0
        start = max(self._current_idx + 1, 0)
        return sum(
            self._waypoints[i]["fuel_cost"]
            for i in range(start, len(self._waypoints))
        )

    def _build_state_dict(self) -> dict:
        """Build a full serialisable state snapshot (thread-safe)."""
        with self._lock:
            fc_dist = self._compute_fc_distance()

            waypoints_out = []
            for i, wp in enumerate(self._waypoints):
                waypoints_out.append({
                    "system":    wp["system"],
                    "distance":  wp["distance"],
                    "fuel_cost": wp["fuel_cost"],
                    "done":      i <= self._current_idx,
                })

            return {
                "fc_system":          self._fc_system,
                "fc_coords":          list(self._fc_coords) if self._fc_coords else None,
                "ship_system":        self._ship_system,
                "ship_coords":        list(self._ship_coords) if self._ship_coords else None,
                "fc_distance":        round(fc_dist, 2) if fc_dist is not None else None,
                "tritium_available":  self._tritium,
                "tritium_needed":     round(self._tritium_needed(), 1),
                "source":             self._source,
                "destination":        self._destination,
                "total_distance":     round(self._total_distance, 1),
                "current_idx":        self._current_idx,
                "waypoints":          waypoints_out,
            }

    def _notify_gui(self, event: str, data: dict) -> None:
        """Invoke all registered GUI callbacks (called from any thread)."""
        with self._gui_lock:
            cbs = list(self._gui_callbacks)
        for cb in cbs:
            try:
                cb(event, data)
            except Exception as exc:
                log.warning("RouteRole: GUI callback raised: %s", exc)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _coords(pos: Any) -> Optional[tuple[float, float, float]]:
    """Parse a StarPos list-of-3 into a typed tuple, or return None."""
    try:
        if isinstance(pos, (list, tuple)) and len(pos) == 3:
            return (float(pos[0]), float(pos[1]), float(pos[2]))
    except (TypeError, ValueError):
        pass
    return None


# ── Dispatch table ─────────────────────────────────────────────────────────────

_HANDLERS: dict[str, Callable] = {
    "CarrierLocation":    RouteRole._on_carrier_location,
    "CarrierJump":        RouteRole._on_carrier_jump,
    "CarrierStats":       RouteRole._on_carrier_stats,
    "CarrierDepositFuel": RouteRole._on_carrier_deposit_fuel,
    "FSDJump":            RouteRole._on_fsd_jump,
    "Location":           RouteRole._on_location,
    "Docked":             RouteRole._on_docked,
}
