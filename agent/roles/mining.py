"""
ED Cockpit — Mining Role
=========================
Filters Elite Dangerous journal events relevant to mining activities.

Commodity naming convention
----------------------------
All internal state and wire payloads use the **internal** commodity name
(lowercase, no spaces) that the ED journal stores in ``Name`` / ``Type``
fields (e.g. ``"lowtemperaturediamond"``, ``"opal"``).

A parallel ``_name_map`` dict maps each internal name to its localised
display name (e.g. ``"lowtemperaturediamond" → "Low Temperature Diamonds"``).
Display names come from the journal's ``Name_Localised`` / ``Type_Localised``
fields and are therefore correct for the player's game language automatically.

The Ardent price cache is also keyed by internal name, so commodity price
lookups are a direct dict access with no normalisation required.

Context File
------------
  Configuration file so mining session state survives agent restart.
  File located at <config_dir>/mining_state.json

    State file format:
    {
      "asteroid": {
        "materials": [
          {
            "name":          "<internal>",
            "localized_name": "<display>",
            "proportion":    <float 0-100>,
            "is_motherlode": <bool>,
            "price":         <int avg_sell cr>
          },
          ...
        ],
        "content":   "<Low|Medium|High>",
        "remaining": <float 0-100>
      },
      "cargo_tally":     {"<internal>": <int t>, ...},
      "tracked_refined": ["<internal>", ...],
      "name_map":        {"<internal>": "<display>", ...},
      "counters": {
        "cracked":          <int>,
        "collectors":       <int>,
        "prospectors":      <int>,
        "available_limpets": <int>
      },
      "status":         {"cargo": <float t>, "cargo_scoop": <bool>},
      "cargo_capacity": <float t>,
      "last_updated":   "<ISO 8601 timestamp>"
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
  BuyDrones          — limpets purchased; increases available limpets.
  SellDrones         — limpets sold; decreases available limpets.
  EjectCargo         — item(s) ejected from cargo.

Wire payload shapes
-------------------
  ProspectedAsteroid →
    {
      "event":     "ProspectedAsteroid",
      "materials": [
        {
          "name":          "<internal>",   # e.g. "lowtemperaturediamond"
          "localized_name": "<display>",   # e.g. "Low Temperature Diamonds"
          "proportion":    <float 0-100>,  # percentage of asteroid composition
          "is_motherlode": <bool>,
          "price":         <int>,          # avg sell Cr/t from Inara (0 = unknown)
        },
        ...
      ],
      "content":   "Low" | "Medium" | "High",
      "remaining": <float>,  # fraction remaining (1.0 = untouched)
    }

  AsteroidCracked →
    {
      "event": "AsteroidCracked",
      "body":  "<asteroid designation>",
    }

  MiningRefined →
    {
      "event":          "MiningRefined",
      "type":           "<internal>",   # e.g. "lowtemperaturediamond"
      "type_localised": "<display>",    # e.g. "Low Temperature Diamonds"
    }

  LaunchDrone (collection / prospector only) →
    {
      "event":             "LaunchDrone",
      "drone_type":        "Collection" | "Prospector",
      "available_limpets": <int>,
    }

  CargoTransfer | CarrierDepositFuel | EjectCargo →
    {
      "event":               "CargoTransfer" | "CarrierDepositFuel" | "EjectCargo",
      "refined_cargo_tally": {"<internal>": <int t>, ...},
      "available_limpets":   <int>,
      "name_map":            {"<internal>": "<display>", ...},
      # EjectCargo also carries:
      "cargo":               <float t>,   # updated cargo used after ejection
    }

  Docked →
    {
      "event":               "Docked",
      "station":             "<name>",
      "system":              "<system>",
      "refined_cargo_tally": {"<internal>": <int t>, ...},
      "available_limpets":   <int>,
      "name_map":            {"<internal>": "<display>", ...},
    }

Status payload (filter_status) →
    {
      "cargo":             <float t>,
      "cargo_capacity":    <float t>,
      "available_limpets": <int>,
      "cargo_scoop":       <bool>,
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

        # Last prospected asteroid.  Materials list items:
        #   {name, localized_name, proportion, is_motherlode, price}
        self._last_asteroid: dict = {
            "materials": [],
            "content":   "",
            "remaining": 1.0,
        }

        # Cargo tally keyed by INTERNAL name (e.g. "lowtemperaturediamond" → 5)
        self._cargo_tally: dict[str, int] = {}
        # Set of internal names of materials ever seen in MiningRefined events
        self._tracked_refined: set[str] = set()
        # internal name → localised display name (built from journal events)
        self._name_map: dict[str, str] = {}

        self._n_cracked: int = 0
        self._n_collectors: int = 0
        self._n_prospectors: int = 0
        self._available_limpets: int = 0
        self._cargo_capacity: float = 0.0
        self._last_status: dict = {"cargo": 0.0, "cargo_scoop": False}

        # Commodity prices fetched from Inara in a background thread.
        # Keyed by internal name (e.g. "lowtemperaturediamond").
        self._prices: dict[str, dict] = {}
        self._prices_lock = threading.Lock()
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
                internal  = str(item.get("Name", "")).strip().lower()
                localised = item.get("Name_Localised") or internal
                if internal:
                    inv_map[internal] = count
                    self._name_map[internal] = localised
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

        # ── asteroid ──────────────────────────────────────────────────────
        asteroid_raw = saved.get("asteroid", {})
        materials = []
        for m in asteroid_raw.get("materials", []):
            if not isinstance(m, dict):
                continue
            # New format has "localized_name"; old format only had "name" (display).
            materials.append({
                "name":          str(m.get("name", "")),
                "localized_name": str(m.get("localized_name", m.get("name", ""))),
                "proportion":    float(m.get("proportion", 0.0)),
                "is_motherlode": bool(m.get("is_motherlode", False)),
                "price":         int(m.get("price", 0)),
            })
        # Migrate old "motherlode" string field → mark matching material
        old_motherlode = str(asteroid_raw.get("motherlode", ""))
        if old_motherlode and not any(m["is_motherlode"] for m in materials):
            for m in materials:
                if m["localized_name"] == old_motherlode or m["name"] == old_motherlode:
                    m["is_motherlode"] = True
                    break
        self._last_asteroid = {
            "materials": materials,
            "content":   str(asteroid_raw.get("content", "")),
            "remaining": float(asteroid_raw.get("remaining", 1.0)),
        }

        # ── cargo tally (internal-name keys) ──────────────────────────────
        self._cargo_tally = {
            str(k): int(v) for k, v in saved.get("cargo_tally", {}).items()
        }
        tracked = saved.get("tracked_refined", [])
        if isinstance(tracked, list):
            self._tracked_refined = {str(n) for n in tracked if n}
        name_map_raw = saved.get("name_map", {})
        if isinstance(name_map_raw, dict):
            self._name_map = {str(k): str(v) for k, v in name_map_raw.items()}

        # ── counters ──────────────────────────────────────────────────────
        counters = saved.get("counters", {})
        if not isinstance(counters, dict):
            counters = {}
        self._n_cracked     = self._to_int(counters.get("cracked"),     default=0)
        self._n_collectors  = self._to_int(counters.get("collectors"),  default=0)
        self._n_prospectors = self._to_int(counters.get("prospectors"), default=0)
        raw_limpets = counters.get("available_limpets", counters.get("avaiable_limpets"))
        self._available_limpets = self._to_int(raw_limpets, default=0)

        status = saved.get("status", {})
        self._last_status = {
            "cargo":       float(status.get("cargo", 0.0)),
            "cargo_scoop": bool(status.get("cargo_scoop", False)),
        }
        self._cargo_capacity = float(saved.get("cargo_capacity", 0.0))

        log.info("MiningRole: state loaded from %s", self._state_path)

    def _save_state(self) -> None:
        try:
            self._config_dir.mkdir(parents=True, exist_ok=True)
            state = {
                "asteroid":       dict(self._last_asteroid),
                "cargo_tally":    dict(self._cargo_tally),
                "tracked_refined": sorted(self._tracked_refined),
                "name_map":       dict(self._name_map),
                "counters": {
                    "cracked":           self._n_cracked,
                    "collectors":        self._n_collectors,
                    "prospectors":       self._n_prospectors,
                    "available_limpets": self._available_limpets,
                },
                "status":         dict(self._last_status),
                "cargo_capacity": self._cargo_capacity,
                "last_updated":   datetime.now(timezone.utc).isoformat(),
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
            float(self._last_status.get("cargo", 0.0)) > 0.0,
            self._cargo_capacity > 0.0,
            bool(self._last_status.get("cargo_scoop", False)),
        ])
        if not has_state:
            return None
        with self._prices_lock:
            prices = dict(self._prices)
        return {
            "asteroid":        dict(self._last_asteroid),
            "cargo_tally":     dict(self._cargo_tally),
            "name_map":        dict(self._name_map),
            "counters": {
                "cracked":           self._n_cracked,
                "collectors":        self._n_collectors,
                "prospectors":       self._n_prospectors,
                "available_limpets": self._available_limpets,
            },
            "status":          dict(self._last_status),
            "cargo_capacity":  self._cargo_capacity,
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
            "cargo":             cargo_value,
            "cargo_capacity":    self._cargo_capacity,
            "available_limpets": self._available_limpets,
            "cargo_scoop":       bool(flags & _FLAG_CARGO_SCOOP),
        }
        changed = payload != self._last_status
        self._last_status = payload
        if changed:
            self._save_state()

        with self._prices_lock:
            if self._prices_pending_push and self._prices:
                payload["commodity_prices"] = dict(self._prices)
                self._prices_pending_push = False

        return payload

    # ── Event handlers ─────────────────────────────────────────────────────

    def _handle_prospected(self, data: dict) -> dict:
        with self._prices_lock:
            prices = dict(self._prices)

        materials: list[dict] = []

        for m in data.get("Materials", []):
            internal  = str(m.get("Name", "")).strip().lower()
            localised = m.get("Name_Localised") or internal
            proportion = float(m.get("Proportion", 0.0))
            price = prices.get(internal, {}).get("avg_sell", 0)
            if internal:
                self._name_map[internal] = localised
            materials.append({
                "name":          internal,
                "localized_name": localised,
                "proportion":    proportion,
                "is_motherlode": False,
                "price":         price,
            })

        # Mark the motherlode material in-place (it is already in Materials[])
        ml_internal  = str(data.get("MotherlodeType", "")).strip().lower()
        ml_localised = data.get("MotherlodeType_Localised") or ml_internal
        if ml_internal:
            self._name_map[ml_internal] = ml_localised
            matched = False
            for m in materials:
                if m["name"] == ml_internal:
                    m["is_motherlode"] = True
                    matched = True
                    break
            if not matched:
                # Rare: motherlode not listed in Materials — append it
                materials.append({
                    "name":          ml_internal,
                    "localized_name": ml_localised,
                    "proportion":    0.0,
                    "is_motherlode": True,
                    "price":         prices.get(ml_internal, {}).get("avg_sell", 0),
                })

        content = data.get("Content_Localised") or data.get("Content", "")
        payload = {
            "event":     "ProspectedAsteroid",
            "materials": materials,
            "content":   content,
            "remaining": float(data.get("Remaining", 1.0)),
        }
        self._last_asteroid = {
            "materials": materials,
            "content":   content,
            "remaining": payload["remaining"],
        }
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
        # Type field may be "$lowtemperaturediamond_name;" — strip to internal name.
        internal  = self._strip_journal_key(data.get("Type", "") or "")
        localised = (data.get("Type_Localised")
                     or self._name_map.get(internal)
                     or internal)
        if internal:
            self._tracked_refined.add(internal)
            self._cargo_tally[internal] = self._cargo_tally.get(internal, 0) + 1
            self._name_map[internal]    = localised
        payload = {
            "event":          "MiningRefined",
            "type":           internal,
            "type_localised": localised,
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
            "event":             "LaunchDrone",
            "drone_type":        drone_type,
            "available_limpets": self._available_limpets,
        }
        self._save_state()
        return payload

    def _handle_loadout(self, data: dict) -> dict:
        self._cargo_capacity = float(data.get("CargoCapacity", 0.0))
        self._save_state()
        return {
            "event":          "Loadout",
            "ship":           data.get("Ship", ""),
            "ship_name":      data.get("ShipName", ""),
            "cargo_capacity": self._cargo_capacity,
            "hull_health":    float(data.get("HullHealth", 0.0)),
            "fuel_capacity":  data.get("FuelCapacity", {}),
        }

    def _handle_cargo(self, data: dict) -> dict | None:
        vessel = str(data.get("Vessel", "Ship"))
        if vessel.lower() not in ("ship", ""):
            return None

        inventory = data.get("Inventory")
        # inv_map keyed by internal name (plain lowercase, no $ wrapper in Cargo events)
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
                internal  = str(item.get("Name", "")).strip().lower()
                localised = item.get("Name_Localised") or internal
                if internal:
                    inv_map[internal] = count
                    self._name_map[internal] = localised
        elif "Count" in data:
            try:
                used = float(data.get("Count", used))
            except (TypeError, ValueError):
                pass
        self._last_status["cargo"] = float(used)

        if have_inventory:
            # Reconcile tally: keep only what is actually in the ship hold.
            for internal in list(self._tracked_refined):
                current = inv_map.get(internal, 0)
                if current <= 0:
                    self._cargo_tally.pop(internal, None)
                else:
                    self._cargo_tally[internal] = current

            limpet_count = self._extract_limpets(inv_map)
            if limpet_count is not None:
                self._available_limpets = limpet_count

        self._save_state()
        return {
            "event":               "Cargo",
            "cargo":               float(used),
            "available_limpets":   self._available_limpets,
            "refined_cargo_tally": dict(self._cargo_tally),
            "name_map":            dict(self._name_map),
            "inventory":           inventory if have_inventory else [],
        }

    def _handle_cargo_transfer(self, data: dict) -> dict | None:
        transfers = data.get("Transfers", [])
        if not isinstance(transfers, list) or not transfers:
            return None

        changed = False
        for t in transfers:
            if not isinstance(t, dict):
                continue
            # Type in CargoTransfer is plain lowercase (no $ wrapper)
            internal  = str(t.get("Type", "")).strip().lower()
            localised = t.get("Type_Localised") or self._name_map.get(internal) or internal
            direction = str(t.get("Direction", "")).lower()
            try:
                count = int(t.get("Count", 0))
            except (TypeError, ValueError):
                count = 0
            count = max(count, 0)
            if not internal or count == 0:
                continue

            if internal:
                self._name_map[internal] = localised

            is_limpet = "limpet" in internal or internal == "drones"
            if is_limpet:
                if direction == "tocarrier":
                    self._available_limpets = max(0, self._available_limpets - count)
                elif direction == "toship":
                    self._available_limpets += count
                changed = True
            elif internal in self._cargo_tally:
                if direction == "tocarrier":
                    new_count = max(0, self._cargo_tally.get(internal, 0) - count)
                    if new_count == 0:
                        self._cargo_tally.pop(internal, None)
                    else:
                        self._cargo_tally[internal] = new_count
                    changed = True

        if changed:
            self._save_state()

        return {
            "event":               "CargoTransfer",
            "refined_cargo_tally": dict(self._cargo_tally),
            "available_limpets":   self._available_limpets,
            "name_map":            dict(self._name_map),
        }

    def _handle_carrier_deposit_fuel(self, data: dict) -> dict | None:
        """Tritium deposited into fleet carrier fuel reserve."""
        try:
            amount = int(data.get("Amount", 0))
        except (TypeError, ValueError):
            amount = 0
        if amount <= 0:
            return None

        if "tritium" in self._cargo_tally:
            new_count = max(0, self._cargo_tally["tritium"] - amount)
            if new_count == 0:
                self._cargo_tally.pop("tritium", None)
            else:
                self._cargo_tally["tritium"] = new_count
            self._save_state()

        return {
            "event":               "CarrierDepositFuel",
            "amount":              amount,
            "refined_cargo_tally": dict(self._cargo_tally),
            "available_limpets":   self._available_limpets,
            "name_map":            dict(self._name_map),
        }

    def _handle_docked(self, data: dict) -> dict:
        self._last_asteroid = {
            "materials": [],
            "content":   "",
            "remaining": 1.0,
        }
        self._n_cracked     = 0
        self._n_collectors  = 0
        self._n_prospectors = 0
        self._save_state()
        return {
            "event":               "Docked",
            "station":             data.get("StationName", ""),
            "system":              data.get("StarSystem", ""),
            "refined_cargo_tally": dict(self._cargo_tally),
            "available_limpets":   self._available_limpets,
            "name_map":            dict(self._name_map),
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
            "event":             "BuyDrones",
            "count":             amount,
            "available_limpets": self._available_limpets,
            "cargo":             float(self._last_status.get("cargo", 0.0)),
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
            "event":             "SellDrones",
            "count":             amount,
            "available_limpets": self._available_limpets,
            "cargo":             float(self._last_status.get("cargo", 0.0)),
        }

    def _handle_eject_cargo(self, data: dict) -> dict | None:
        try:
            count = int(data.get("Count", 0))
        except (TypeError, ValueError):
            count = 0
        if count <= 0:
            return None

        raw_type = str(data.get("Type", "")).strip()
        # Type in EjectCargo may carry the "$..._name;" wrapper
        internal = self._strip_journal_key(raw_type)

        if "drone" in internal or internal == "drones":
            self._available_limpets = max(0, self._available_limpets - count)
        elif internal in self._cargo_tally:
            new_count = max(0, self._cargo_tally.get(internal, 0) - count)
            if new_count == 0:
                self._cargo_tally.pop(internal, None)
            else:
                self._cargo_tally[internal] = new_count

        self._last_status["cargo"] = max(
            0.0,
            float(self._last_status.get("cargo", 0.0)) - float(count),
        )
        self._save_state()
        log.info(
            "MiningRole: EjectCargo — internal=%r count=%d limpets=%d",
            internal, count, self._available_limpets,
        )
        return {
            "event":               "EjectCargo",
            "refined_cargo_tally": dict(self._cargo_tally),
            "available_limpets":   self._available_limpets,
            "cargo":               float(self._last_status.get("cargo", 0.0)),
            "name_map":            dict(self._name_map),
        }

    # ── Helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _strip_journal_key(raw: str) -> str:
        """Normalise a journal localisation key to its plain commodity name.

        Strips the ``$`` prefix and ``_name;`` / ``_name_plural;`` suffix so
        that e.g. ``"$lowtemperaturediamond_name;"`` → ``"lowtemperaturediamond"``.
        If ``raw`` has no wrapper the string is returned lowercased as-is.
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
