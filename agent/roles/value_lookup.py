"""
ED Cockpit — Exobiology Value Lookup
======================================
Resolves Vista Genomics redemption values (CR) for scanned species using a
three-tier priority chain:

  1. Journal ``SurveyData.Value``   — exact, provided by the game on the
                                      Analyse (3rd) scan step.  The caller
                                      passes this in via ``update()``.

  2. Local seed / user cache        — ``agent/data/exobiology_values.json``
                                      ships with the app and covers all known
                                      Odyssey species.  Values learned from
                                      the journal are merged into a separate
                                      user-writable cache file so new species
                                      are remembered across sessions.

  3. Remote API fallback            — triggered asynchronously when a species
                                      is not found in the local data.  The
                                      result is saved to the user cache.
                                      Only fires once per unknown species.

Usage
-----
::

    lookup = ValueLookup(cache_dir=Path("~/.config/ed-cockpit").expanduser())
    value  = lookup.get("Tussock Stigmasis")   # → 19010800 (from seed)

    # After the journal supplies the exact value:
    lookup.update("Tussock Stigmasis", 19010800)

    # For an unknown future species (fires API fetch in background):
    value = lookup.get("Futurum Novum")        # → 0 (unknown for now)
    # … asynchronously, the value is fetched and cached.

API fallback
------------
The default implementation queries the Spansh ``/api/codex/entries`` endpoint.
If the response is unavailable or the species is not found, ``0`` is returned
and a warning is logged.

You can replace ``ValueLookup.api_url`` with any endpoint that accepts a
``?name=<species>`` query parameter and returns JSON with a ``"value"`` field.
"""
from __future__ import annotations

import asyncio
import json
import logging
import urllib.parse
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)

# Path to the bundled seed file (relative to this module's directory)
_SEED_PATH = Path(__file__).parent.parent / "data" / "exobiology_values.json"

# User cache filename (written to the agent config dir)
_CACHE_FILENAME = "exobiology_values_cache.json"

# Spansh codex entries endpoint — returns a list of entries matching the name
_DEFAULT_API_URL = "https://spansh.co.uk/api/codex/entries"


class ValueLookup:
    """
    Thread-safe, asyncio-friendly species value resolver.

    Parameters
    ----------
    cache_dir:
        Directory where the user-writable cache file is stored
        (typically ``~/.config/ed-cockpit``).
    api_url:
        Base URL for the remote fallback.  ``None`` disables the API.
    """

    def __init__(
        self,
        cache_dir: Path,
        api_url: str | None = _DEFAULT_API_URL,
    ) -> None:
        self._cache_path = cache_dir / _CACHE_FILENAME
        self.api_url = api_url

        # Normalised (lowercase) name → value
        self._data:    dict[str, int] = {}
        # Species whose API fetch is in-flight or already attempted
        self._pending: set[str] = set()

        self._load_seed()
        self._load_cache()

    # ── Public API ──────────────────────────────────────────────────────────

    def get(self, species: str) -> int:
        """
        Return the best-known value for *species* (0 if unknown).

        If the species is not in the local data, a one-shot async API fetch
        is scheduled on the running event loop (fire-and-forget).
        """
        key = self._norm(species)
        value = self._data.get(key, 0)

        if not value and key not in self._pending:
            self._pending.add(key)
            self._schedule_api_fetch(species)

        return value

    def update(self, species: str, value: int, *, save: bool = True) -> None:
        """
        Record a value for *species* (typically from the journal).

        If *value* differs from what is already stored, the cache file is
        updated on disk.
        """
        if not value:
            return
        key = self._norm(species)
        if self._data.get(key) == value:
            return
        self._data[key] = value
        self._pending.discard(key)   # no need for API fetch any more
        if save:
            self._save_cache()
        log.debug("ValueLookup: cached %r = %d CR", species, value)

    # ── Load / save ─────────────────────────────────────────────────────────

    def _load_seed(self) -> None:
        """Load the bundled seed file."""
        try:
            raw = json.loads(_SEED_PATH.read_text(encoding="utf-8"))
            count = 0
            for name, value in raw.items():
                if name.startswith("_"):
                    continue    # metadata keys
                self._data[self._norm(name)] = int(value)
                count += 1
            log.info("ValueLookup: loaded %d entries from seed", count)
        except Exception as exc:
            log.warning("ValueLookup: could not load seed file: %s", exc)

    def _load_cache(self) -> None:
        """Merge the user cache (may contain journal-learned or API values)."""
        try:
            raw = json.loads(self._cache_path.read_text(encoding="utf-8"))
            count = 0
            for name, value in raw.items():
                self._data[self._norm(name)] = int(value)
                count += 1
            if count:
                log.info("ValueLookup: merged %d entries from user cache", count)
        except FileNotFoundError:
            pass    # normal on first run
        except Exception as exc:
            log.warning("ValueLookup: could not load user cache: %s", exc)

    def _save_cache(self) -> None:
        """Persist the user cache.  Only stores entries not in the seed."""
        try:
            seed_keys: set[str] = set()
            try:
                seed_raw = json.loads(_SEED_PATH.read_text(encoding="utf-8"))
                seed_keys = {self._norm(k) for k in seed_raw if not k.startswith("_")}
            except Exception:
                pass

            # Only persist values that are new / different from the seed
            cache_out: dict[str, int] = {
                k: v for k, v in self._data.items()
                if k not in seed_keys
            }
            if not cache_out:
                return

            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._cache_path.write_text(
                json.dumps(cache_out, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as exc:
            log.warning("ValueLookup: could not save user cache: %s", exc)

    # ── API fallback ────────────────────────────────────────────────────────

    def _schedule_api_fetch(self, species: str) -> None:
        """Schedule a fire-and-forget API fetch on the running event loop."""
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._fetch_async(species))
        except RuntimeError:
            # No running loop (e.g. during unit tests) — skip silently
            pass

    async def _fetch_async(self, species: str) -> None:
        """Async wrapper: runs the blocking HTTP call in a thread-pool."""
        if not self.api_url:
            return
        try:
            loop = asyncio.get_running_loop()
            value = await loop.run_in_executor(None, self._fetch_blocking, species)
            if value:
                self.update(species, value, save=True)
                log.info("ValueLookup: API returned %d CR for %r", value, species)
            else:
                log.debug("ValueLookup: API returned nothing for %r", species)
        except Exception as exc:
            log.warning("ValueLookup: API fetch failed for %r: %s", species, exc)

    def _fetch_blocking(self, species: str) -> int:
        """
        Synchronous HTTP fetch (runs in executor thread).

        Tries the Spansh codex entries endpoint.  Returns 0 on any error or
        if the species is not found.
        """
        try:
            params = urllib.parse.urlencode({"name": species})
            url = f"{self.api_url}?{params}"
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "ED-Cockpit/1.0 exobiology-value-lookup"},
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                payload = json.loads(resp.read().decode("utf-8"))

            # Spansh returns {"results": [...], ...}
            # Each result may have a "value" or similar field
            results = payload.get("results", [])
            for entry in results:
                # Try common field names from Spansh / EDSM responses
                for field in ("value", "Value", "baseValue", "base_value"):
                    v = entry.get(field)
                    if v:
                        return int(v)
        except Exception as exc:
            log.debug("ValueLookup: _fetch_blocking error: %s", exc)
        return 0

    # ── Helpers ─────────────────────────────────────────────────────────────

    @staticmethod
    def _norm(name: str) -> str:
        """Normalise a species name for dictionary lookup."""
        return name.strip().lower()
