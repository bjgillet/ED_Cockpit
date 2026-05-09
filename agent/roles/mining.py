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
  Docked             — reset asteroid data and session counters; refined-cargo
                       tally and available limpets are preserved and stay in sync
                       via subsequent Cargo / CargoTransfer events.
  CargoTransfer      — cargo moved between ship and fleet carrier (or vice-versa).
                       Updates refined tally and limpets immediately so the panel
                       stays correct even when a Cargo snapshot does not follow.
  BuyDrones          — limpets purchased; increases available limpets.
  SellDrones         — limpets sold; decreases available limpets.
  EjectCargo         — item(s) ejected from cargo.  If ``Type`` contains
                       "drone", ``Count`` limpets are removed from the
                       available count.  Otherwise the commodity is removed
                       from the refined-cargo tally by ``Count`` units.

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

  CargoTransfer →
    {
      "event":               "CargoTransfer",
      "refined_cargo_tally": {<ore>: <int t>, ...},
      "available_limpets":   <int>,
    }

  EjectCargo →
    {
      "event":               "EjectCargo",
      "refined_cargo_tally": {<ore>: <int t>, ...},
      "available_limpets":   <int>,
      "cargo":               <float t>,   # updated cargo used after ejection
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
import threading
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
        "CargoTransfer",
        "CarrierDepositFuel",
        "Docked",
        "BuyDrones",
        "SellDrones",
        "EjectCargo",
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
        # Maps internal lowercase name → display name for all refined materials
        # ever seen.  Populated from MiningRefined (Type/Type_Localised) and
        # Cargo (Name/Name_Localised) so we can resolve e.g.
        # "lowtemperaturediamond" → "Low Temperature Diamonds" even when
        # CargoTransfer or Cargo events omit the localised field.
        self._name_map: dict[str, str] = {}
        self._n_cracked: int = 0
        self._n_collectors: int = 0
        self._n_prospectors: int = 0
        self._available_limpets: int = 0
        self._cargo_capacity: float = 0.0
        self._last_status: dict = {"cargo": 0.0, "cargo_scoop": False}

        # Commodity prices fetched from Inara in a background thread.
        self._prices: dict[str, dict] = {}
        self._prices_lock = threading.Lock()
        # Set to True once prices are loaded so filter_status() can push them
        # to already-connected clients on the next Status tick (fixes the race
        # where get_snapshot() runs before the background thread finishes).
        self._prices_pending_push: bool = False
        threading.Thread(
            target=self._fetch_prices_bg,
            name="ED-InaraPrices",
            daemon=True,
        ).start()

        self._load_state()

    def _fetch_prices_bg(self) -> None:
        """Daemon thread: load or refresh Inara commodity prices."""
        try:
            from agent.tools.inara import fetch_commodity_prices
            cache_path = self._config_dir / "commodity_prices.json"
            prices = fetch_commodity_prices(cache_path)
            with self._prices_lock:
                self._prices = prices
                if prices:
                    # Signal filter_status() to piggyback prices on the next
                    # Status tick so already-connected clients get them even
                    # when get_snapshot() ran before this thread finished.
                    self._prices_pending_push = True
        except Exception as exc:
            log.warning("MiningRole: could not fetch commodity prices: %s", exc)

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
            if limpets is not None and limpets != self._available_limpets:
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
        name_map_raw = saved.get("name_map", {})
        if isinstance(name_map_raw, dict):
            self._name_map = {str(k): str(v) for k, v in name_map_raw.items()}

        counters = saved.get("counters", {})
        if not isinstance(counters, dict):
            counters = {}
        self._n_cracked = self._to_int(counters.get("cracked"), default=0)
        self._n_collectors = self._to_int(counters.get("collectors"), default=0)
        self._n_prospectors = self._to_int(counters.get("prospectors"), default=0)
        # Backward compatibility: support old typo key "avaiable_limpets".
        raw_limpets = counters.get("available_limpets", counters.get("avaiable_limpets"))
        self._available_limpets = self._to_int(raw_limpets, default=0)

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
                "name_map": dict(self._name_map),
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
        with self._prices_lock:
            prices = dict(self._prices)
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
            "commodity_prices": prices,
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
        if event_name == "CargoTransfer":
            return self._handle_cargo_transfer(data)
        if event_name == "CarrierDepositFuel":
            return self._handle_carrier_deposit_fuel(data)
        if event_name == "BuyDrones":
            return self._handle_buy_drones(data)
        if event_name == "SellDrones":
            return self._handle_sell_drones(data)
        if event_name == "EjectCargo":
            return self._handle_eject_cargo(data)
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

        # If the background price-fetch finished after the last get_snapshot()
        # call, piggyback the prices on this Status tick so connected clients
        # receive them without needing to reconnect.
        with self._prices_lock:
            if self._prices_pending_push and self._prices:
                payload["commodity_prices"] = dict(self._prices)
                self._prices_pending_push = False

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
        # The journal Type field may use a localisation-key wrapper such as
        # "$lowtemperaturediamond_name;" — normalise it to the plain internal
        # name ("lowtemperaturediamond") so it matches what CargoTransfer sends.
        internal = self._strip_journal_key(data.get("Type", "") or "")
        if ore:
            self._tracked_refined.add(ore)
            self._cargo_tally[ore] = self._cargo_tally.get(ore, 0) + 1
            if internal:
                self._name_map[internal] = ore
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

    def _handle_cargo(self, data: dict) -> dict | None:
        # Ignore non-ship cargo events (e.g. SRV).
        vessel = str(data.get("Vessel", "Ship"))
        if vessel.lower() not in ("ship", ""):
            return None

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

        # Keep refined tally aligned with real cargo inventory.
        # Build the name map from any items that carry both Name and
        # Name_Localised, so future lookups by internal name work correctly
        # (e.g. "lowtemperaturediamond" → "Low Temperature Diamonds").
        if have_inventory:
            for item in inventory:
                if not isinstance(item, dict):
                    continue
                display  = (item.get("Name_Localised") or "").strip()
                internal = self._strip_journal_key(item.get("Name") or "")
                if display and internal:
                    self._name_map[internal] = display

            # Reconcile: for each tracked refined material check whether it
            # is still in the ship's cargo.  Use _resolve_display to handle
            # entries whose Name_Localised was absent (internal name only).
            inv_map_ci = {k.lower(): v for k, v in inv_map.items()}
            for name in list(self._tracked_refined):
                current = self._lookup_in_inv(name, inv_map_ci)
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

    def _handle_cargo_transfer(self, data: dict) -> dict | None:
        """
        Handle a CargoTransfer journal event (fleet carrier ↔ ship transfers).

        The game emits CargoTransfer when the player moves goods between the
        ship hold and a fleet carrier's hold or tritium reserve.  A Cargo
        snapshot with no Inventory (only Count) typically follows; we cannot
        rely on it for reconciliation so all tally/limpet changes are made
        here.

        Note: the journal writes Direction in lowercase ("tocarrier" /
        "toship"), so we normalise to lowercase before comparing.
        """
        transfers = data.get("Transfers", [])
        if not isinstance(transfers, list) or not transfers:
            return None

        changed = False
        for t in transfers:
            if not isinstance(t, dict):
                continue
            raw_name = t.get("Type_Localised") or t.get("Type", "")
            # Direction is lowercase in the actual journal ("tocarrier" / "toship").
            direction = str(t.get("Direction", "")).lower()
            try:
                count = int(t.get("Count", 0))
            except (TypeError, ValueError):
                count = 0
            count = max(count, 0)
            if not raw_name or count == 0:
                continue

            name_lower = raw_name.strip().lower()
            is_limpet = "limpet" in name_lower or name_lower == "drones"

            if is_limpet:
                if direction == "tocarrier":
                    self._available_limpets = max(0, self._available_limpets - count)
                elif direction == "toship":
                    self._available_limpets += count
                changed = True
            else:
                # Only touch materials we already track as refined.
                tracked_name = self._find_tracked_name(raw_name)
                if tracked_name is not None:
                    if direction == "tocarrier":
                        new_count = max(0, self._cargo_tally.get(tracked_name, 0) - count)
                        if new_count == 0:
                            self._cargo_tally.pop(tracked_name, None)
                        else:
                            self._cargo_tally[tracked_name] = new_count
                        changed = True
                    # "toship" for refined materials is not handled here:
                    # those are carrier stock of unknown origin, and the
                    # subsequent Cargo snapshot is the ground truth.

        if changed:
            self._save_state()

        return {
            "event": "CargoTransfer",
            "refined_cargo_tally": dict(self._cargo_tally),
            "available_limpets": self._available_limpets,
        }

    def _find_tracked_name(self, name: str) -> str | None:
        """Return the `_tracked_refined` display-name key that matches *name*.

        Tries in order:
        1. Look up *name* as an internal (non-localised) key in ``_name_map``
           (e.g. "lowtemperaturediamond" → "Low Temperature Diamonds").
        2. Direct case-insensitive comparison against tracked display names.
        Returns None if no match is found.
        """
        name_lower = name.strip().lower()
        # Map-based resolution first (handles internal names with no spaces).
        display = self._name_map.get(name_lower)
        if display and display in self._tracked_refined:
            return display
        # Fallback: direct case-insensitive match on display names.
        for tracked in self._tracked_refined:
            if tracked.lower() == name_lower:
                return tracked
        return None

    def _lookup_in_inv(self, display_name: str, inv_map_ci: dict[str, int]) -> int:
        """Look up a tracked display name in a case-insensitive inventory map.

        Tries the display name directly, then falls back to any known internal
        name so that entries missing ``Name_Localised`` (keyed by their
        lowercase internal name, e.g. "lowtemperaturediamond") are still found.
        """
        # Direct case-insensitive hit.
        count = inv_map_ci.get(display_name.lower(), 0)
        if count:
            return count
        # Fallback: find the internal name for this display name and try it.
        for internal, display in self._name_map.items():
            if display == display_name:
                count = inv_map_ci.get(internal, 0)
                if count:
                    return count
        return 0

    def _handle_carrier_deposit_fuel(self, data: dict) -> dict | None:
        """
        Handle a CarrierDepositFuel journal event.

        Fired when the player deposits Tritium from their ship cargo into
        the fleet carrier's fuel reserve.  The commodity is always Tritium;
        the journal field ``Amount`` is how many tonnes were deducted from
        the ship.

        Example:
            { "event":"CarrierDepositFuel", "CarrierID":3710914304,
              "Amount":66, "Total":1000 }
        """
        try:
            amount = int(data.get("Amount", 0))
        except (TypeError, ValueError):
            amount = 0
        if amount <= 0:
            return None

        # The fuel commodity is always Tritium — find it in the tally using
        # the same case-insensitive / name-map resolution as CargoTransfer.
        tracked_name = self._find_tracked_name("tritium")
        if tracked_name is not None:
            new_count = max(0, self._cargo_tally.get(tracked_name, 0) - amount)
            if new_count == 0:
                self._cargo_tally.pop(tracked_name, None)
            else:
                self._cargo_tally[tracked_name] = new_count
            self._save_state()

        return {
            "event": "CarrierDepositFuel",
            "amount": amount,
            "refined_cargo_tally": dict(self._cargo_tally),
            "available_limpets": self._available_limpets,
        }

    def _handle_docked(self, data: dict) -> dict:
        self._last_asteroid = {
            "materials": [],
            "content": "",
            "motherlode": "",
            "remaining": 1.0,
        }
        # Refined-cargo tally and available limpets are intentionally NOT reset
        # here — they remain valid until actual cargo changes (sell, transfer to
        # fleet carrier or tritium reserve) are reflected back via Cargo events.
        self._n_cracked = 0
        self._n_collectors = 0
        self._n_prospectors = 0
        self._save_state()
        return {
            "event": "Docked",
            "station": data.get("StationName", ""),
            "system": data.get("StarSystem", ""),
            "refined_cargo_tally": dict(self._cargo_tally),
            "available_limpets": self._available_limpets,
        }

    def _handle_buy_drones(self, data: dict) -> dict:
        try:
            amount = int(data.get("Count", 0))
        except (TypeError, ValueError):
            amount = 0
        amount = max(amount, 0)

        if amount:
            self._available_limpets += amount
            self._last_status["cargo"] = float(self._last_status.get("cargo", 0.0)) + float(amount)
            self._save_state()
        return {
            "event": "BuyDrones",
            "count": amount,
            "available_limpets": self._available_limpets,
            "cargo": float(self._last_status.get("cargo", 0.0)),
        }

    def _handle_sell_drones(self, data: dict) -> dict:
        try:
            amount = int(data.get("Count", 0))
        except (TypeError, ValueError):
            amount = 0
        amount = max(amount, 0)

        if amount:
            self._available_limpets = max(0, self._available_limpets - amount)
            self._last_status["cargo"] = max(
                0.0,
                float(self._last_status.get("cargo", 0.0)) - float(amount),
            )
            self._save_state()
        return {
            "event": "SellDrones",
            "count": amount,
            "available_limpets": self._available_limpets,
            "cargo": float(self._last_status.get("cargo", 0.0)),
        }

    def _handle_eject_cargo(self, data: dict) -> dict | None:
        """
        Handle an EjectCargo journal event.

        ``Type`` "drones" (case-insensitive, partial match) → decrease
        available limpets by ``Count``.
        Any other ``Type`` → remove ``Count`` units from the refined-cargo
        tally (no-op if the commodity is not currently tracked).
        In both cases the running cargo-used figure is reduced by ``Count``.
        """
        try:
            count = int(data.get("Count", 0))
        except (TypeError, ValueError):
            count = 0
        if count <= 0:
            return None

        raw_type   = str(data.get("Type", "")).strip()
        type_lower = raw_type.lower()

        if "drone" in type_lower:
            self._available_limpets = max(0, self._available_limpets - count)
        else:
            tracked_name = self._find_tracked_name(raw_type)
            if tracked_name is not None:
                new_count = max(0, self._cargo_tally.get(tracked_name, 0) - count)
                if new_count == 0:
                    self._cargo_tally.pop(tracked_name, None)
                else:
                    self._cargo_tally[tracked_name] = new_count

        self._last_status["cargo"] = max(
            0.0,
            float(self._last_status.get("cargo", 0.0)) - float(count),
        )
        self._save_state()
        log.info(
            "MiningRole: EjectCargo — type=%r count=%d  limpets=%d",
            raw_type, count, self._available_limpets,
        )
        return {
            "event":               "EjectCargo",
            "refined_cargo_tally": dict(self._cargo_tally),
            "available_limpets":   self._available_limpets,
            "cargo":               float(self._last_status.get("cargo", 0.0)),
        }

    @staticmethod
    def _strip_journal_key(raw: str) -> str:
        """Normalise a journal localisation key to its plain commodity name.

        The ED journal sometimes wraps internal names in a localisation key
        format: ``$lowtemperaturediamond_name;`` or ``$tritium_name;``.
        This strips the ``$`` prefix and any ``_name;`` / ``_name_plural;``
        suffix so the result matches what ``CargoTransfer`` sends (e.g.
        ``"lowtemperaturediamond"``, ``"tritium"``).
        """
        s = raw.strip().lower()
        if s.startswith("$"):
            s = s[1:]
        for suffix in ("_name_plural;", "_name;", ";"):
            if s.endswith(suffix):
                s = s[: -len(suffix)]
                break
        return s

    @staticmethod
    def _extract_limpets(inv_map: dict[str, int]) -> int | None:
        for key, value in inv_map.items():
            k = key.strip().lower()
            if ("limpet" in k) or ("drone" in k):
                return int(value)
        return None

    @staticmethod
    def _to_int(value, default: int = 0) -> int:
        try:
            if value is None:
                return default
            return int(value)
        except (TypeError, ValueError):
            return default
