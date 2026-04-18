"""
ED Cockpit — Journal Memory
===========================
Keeps a lightweight "last known commander/ship/cargo/location" state by:

1) Bootstrapping from the last N journal files at agent startup.
2) Updating continuously from live journal events.
3) Persisting the current snapshot to disk for easy inspection/debug.
"""
from __future__ import annotations

import json
import logging
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_BOOTSTRAP_COUNT = 3


class JournalMemory:
    """In-memory and on-disk cache of key journal-derived values."""

    def __init__(self, config_dir: Path) -> None:
        self._lock = threading.Lock()
        self._config_dir = config_dir
        self._state_path = config_dir / "journal_memory.json"
        self._warmed_from: str | None = None
        self._state: dict[str, Any] = {
            "commander_name": "",
            "cargo_inventory": [],
            "ship": {
                "ship": "",
                "ship_name": "",
                "hull_health": 0.0,
                "cargo_capacity": 0.0,
                "fuel_capacity": {},
            },
            "location": {
                "star_system": "",
                "body": "",
            },
        }
        self._load_state()

    def warm_from_journal(self, journal_path: str | None) -> None:
        """Bootstrap state from the last 3 journal files in the folder."""
        if not journal_path:
            return
        try:
            jpath = Path(journal_path)
            jdir = jpath.parent
            all_logs = sorted(jdir.glob("Journal.*.log"))
            if not all_logs:
                return

            selected = all_logs[-_BOOTSTRAP_COUNT:]
            if jpath.exists() and jpath not in selected:
                selected = (selected + [jpath])[-_BOOTSTRAP_COUNT:]

            with self._lock:
                if self._warmed_from == str(jpath):
                    return
                self._warmed_from = str(jpath)

            for path in selected:
                self._parse_file(path)
            self._save_state()
            log.info(
                "JournalMemory: bootstrapped from %d journal file(s) ending with %s",
                len(selected), jpath.name,
            )
        except Exception as exc:
            log.warning("JournalMemory: bootstrap failed: %s", exc)

    def update_from_event(self, event_name: str, data: dict, persist: bool = True) -> None:
        changed = False
        with self._lock:
            if event_name == "Commander":
                changed = self._set_if_changed("commander_name", str(data.get("Name", "")))

            elif event_name == "Cargo":
                inv = data.get("Inventory", [])
                if isinstance(inv, list):
                    changed = self._set_if_changed("cargo_inventory", deepcopy(inv))

            elif event_name == "Loadout":
                ship = self._state["ship"]
                changed |= self._set_nested_if_changed(ship, "ship", str(data.get("Ship", "")))
                changed |= self._set_nested_if_changed(ship, "ship_name", str(data.get("ShipName", "")))
                changed |= self._set_nested_if_changed(ship, "hull_health", float(data.get("HullHealth", 0.0)))
                changed |= self._set_nested_if_changed(ship, "cargo_capacity", float(data.get("CargoCapacity", 0.0)))
                fuel_cap = data.get("FuelCapacity", {})
                if isinstance(fuel_cap, (dict, int, float)):
                    changed |= self._set_nested_if_changed(ship, "fuel_capacity", deepcopy(fuel_cap))

            elif event_name == "Location":
                loc = self._state["location"]
                changed |= self._set_nested_if_changed(loc, "star_system", str(data.get("StarSystem", "")))
                changed |= self._set_nested_if_changed(loc, "body", str(data.get("Body", "")))

        if changed and persist:
            self._save_state()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._state)

    def _parse_file(self, path: Path) -> None:
        try:
            with path.open(encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    event_name = data.get("event", "")
                    if event_name:
                        self.update_from_event(event_name, data, persist=False)
        except OSError as exc:
            log.debug("JournalMemory: cannot parse %s: %s", path, exc)

    def _load_state(self) -> None:
        try:
            saved = json.loads(self._state_path.read_text(encoding="utf-8"))
        except Exception:
            return
        with self._lock:
            self._state["commander_name"] = str(saved.get("commander_name", ""))
            inv = saved.get("cargo_inventory", [])
            self._state["cargo_inventory"] = inv if isinstance(inv, list) else []

            ship = saved.get("ship", {})
            if isinstance(ship, dict):
                self._state["ship"]["ship"] = str(ship.get("ship", ""))
                self._state["ship"]["ship_name"] = str(ship.get("ship_name", ""))
                self._state["ship"]["hull_health"] = float(ship.get("hull_health", 0.0))
                self._state["ship"]["cargo_capacity"] = float(ship.get("cargo_capacity", 0.0))
                fuel = ship.get("fuel_capacity", {})
                if isinstance(fuel, (dict, int, float)):
                    self._state["ship"]["fuel_capacity"] = deepcopy(fuel)

            loc = saved.get("location", {})
            if isinstance(loc, dict):
                self._state["location"]["star_system"] = str(loc.get("star_system", ""))
                self._state["location"]["body"] = str(loc.get("body", ""))

    def _save_state(self) -> None:
        try:
            self._config_dir.mkdir(parents=True, exist_ok=True)
            payload = self.snapshot()
            self._state_path.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as exc:
            log.warning("JournalMemory: could not save state: %s", exc)

    def _set_if_changed(self, key: str, value: Any) -> bool:
        if self._state.get(key) != value:
            self._state[key] = value
            return True
        return False

    @staticmethod
    def _set_nested_if_changed(obj: dict, key: str, value: Any) -> bool:
        if obj.get(key) != value:
            obj[key] = value
            return True
        return False
