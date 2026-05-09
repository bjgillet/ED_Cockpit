"""
ED Cockpit — Commodity Price Fetcher
======================================
Fetches average and maximum sell prices for Elite Dangerous commodities.

Primary source: Ardent Insight API (https://api.ardent-insight.com)
  Open JSON REST API, no authentication, powered by EDDN community data.
  Docs / source: https://github.com/iaincollins/ardent-api

Fallback chain
--------------
  1. Memory cache     — avoids repeated I/O within one process run.
  2. File cache       — local JSON written after every successful fetch.
                        Considered fresh for CACHE_MAX_DAYS days.
  3. Ardent API fetch — live JSON from api.ardent-insight.com.
  4. Stale file cache — used if the live fetch fails.
  5. Bundled data     — static prices shipped with the agent
                        (agent/data/mining_commodity_prices.json).
                        Covers all common mining commodities.
                        Refresh with:  python check_inara.py --local

Cache file format
-----------------
  {
    "fetched_at": "<ISO 8601 UTC timestamp>",
    "prices": {
      "Painite":                  {"avg_sell": 57524,  "max_sell": 402344},
      "Low Temperature Diamonds": {"avg_sell": 129388, "max_sell": 647653},
      ...
    }
  }

Returned structure (from fetch_commodity_prices)
-------------------------------------------------
  {
    "Painite":                  {"avg_sell": 57524,  "max_sell": 402344},
    "Low Temperature Diamonds": {"avg_sell": 129388, "max_sell": 647653},
    ...
  }
  Keys are the localised display names used in MiningRefined journal events.
"""
from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# ── Configuration ──────────────────────────────────────────────────────────────

_ARDENT_URL     = "https://api.ardent-insight.com/v2/commodities"
_HTTP_TIMEOUT   = 20.0  # seconds per request
CACHE_MAX_DAYS  = 30    # refresh file cache if older than this many days

# Bundled static price list shipped with the agent — last resort fallback.
_BUNDLED_PRICES_PATH = Path(__file__).parent.parent / "data" / "mining_commodity_prices.json"

_HEADERS = {
    "User-Agent": "ED-Cockpit/1.0 (Commodity Price Cache)",
    "Accept":     "application/json",
}

# ── Internal-name → display-name mapping ──────────────────────────────────────
# The Ardent API uses lowercase journal internal names (e.g. "lowtemperaturediamond").
# The panel and cargo tally use localised display names ("Low Temperature Diamonds").
# This mapping covers all common mining commodities and the most traded metals.
_INTERNAL_TO_DISPLAY: dict[str, str] = {
    "alexandrite":                   "Alexandrite",
    "benitoite":                     "Benitoite",
    "bertrandite":                   "Bertrandite",
    "bromellite":                    "Bromellite",
    "coltan":                        "Coltan",
    "gold":                          "Gold",
    "grandidierite":                 "Grandidierite",
    "jadeite":                       "Jadeite",
    "lowtemperaturediamond":         "Low Temperature Diamonds",
    "methanolmonohydratecrystals":   "Methanol Monohydrate Crystals",
    "monazite":                      "Monazite",
    "musgravite":                    "Musgravite",
    "osmium":                        "Osmium",
    "opal":                          "Void Opal",
    "painite":                       "Painite",
    "palladium":                     "Palladium",
    "platinum":                      "Platinum",
    "praseodymium":                  "Praseodymium",
    "rhodplumsite":                  "Rhodplumsite",
    "samarium":                      "Samarium",
    "serendibite":                   "Serendibite",
    "silver":                        "Silver",
    "taaffeite":                     "Taaffeite",
    "titanium":                      "Titanium",
    "tritium":                       "Tritium",
    # common metals mined in laser / core mining
    "bauxite":                       "Bauxite",
    "beryllium":                     "Beryllium",
    "cobalt":                        "Cobalt",
    "copper":                        "Copper",
    "gallite":                       "Gallite",
    "gallium":                       "Gallium",
    "indite":                        "Indite",
    "indium":                        "Indium",
    "lepidolite":                    "Lepidolite",
    "lithium":                       "Lithium",
    "methane clathrate":             "Methane Clathrate",
    "moissanite":                    "Moissanite",
    "pyrophyllite":                  "Pyrophyllite",
    "rutile":                        "Rutile",
    "uraninite":                     "Uraninite",
    "void opal":                     "Void Opal",
}

# ── In-memory layer (process-lifetime cache) ───────────────────────────────────

_MEM_CACHE: Optional[dict[str, dict]] = None


# ── Public API ─────────────────────────────────────────────────────────────────

def fetch_commodity_prices(
    cache_path: Optional[Path] = None,
    *,
    force_refresh: bool = False,
) -> dict[str, dict]:
    """
    Return a dict mapping commodity display name → {avg_sell, max_sell}.

    Lookup order
    ------------
    1. In-memory cache  — hit if already populated this process run.
    2. File cache       — read ``cache_path`` if fresh (≤ CACHE_MAX_DAYS).
    3. Ardent API fetch — live JSON from api.ardent-insight.com.
    4. Stale file cache — used if the live fetch fails.
    5. Bundled data     — static prices shipped with the agent.

    Never raises — returns an empty dict on total failure so the caller
    can degrade gracefully.

    Parameters
    ----------
    cache_path : Path, optional
        Path to the JSON cache file.
    force_refresh : bool
        Bypass all caches and always fetch fresh data.
    """
    global _MEM_CACHE

    if _MEM_CACHE is not None and not force_refresh:
        return _MEM_CACHE

    # ── File cache ──────────────────────────────────────────────────────────
    if cache_path is not None and not force_refresh:
        cached = _load_file_cache(cache_path)
        if cached is not None:
            _MEM_CACHE = cached
            log.info(
                "Commodity prices: loaded %d entries from file cache (%s)",
                len(cached), cache_path,
            )
            return _MEM_CACHE

    # ── Live fetch (Ardent API) ─────────────────────────────────────────────
    try:
        prices = _fetch_ardent()
    except Exception as exc:
        log.warning("Commodity prices: Ardent API fetch failed: %s", exc)
        prices = {}

    if prices:
        log.info(
            "Commodity prices: fetched %d entries from Ardent API", len(prices)
        )
        if cache_path is not None:
            _save_file_cache(cache_path, prices)
        _MEM_CACHE = prices
        return _MEM_CACHE

    log.warning("Commodity prices: live fetch returned 0 entries")

    # ── Stale file cache ────────────────────────────────────────────────────
    if cache_path is not None:
        stale = _load_file_cache(cache_path, ignore_age=True)
        if stale:
            log.warning(
                "Commodity prices: using stale file cache (%d entries)", len(stale)
            )
            _MEM_CACHE = stale
            return _MEM_CACHE

    # ── Bundled static prices ───────────────────────────────────────────────
    bundled = _load_bundled_prices()
    if bundled:
        log.warning(
            "Commodity prices: using bundled static prices (%d entries). "
            "Run 'python check_inara.py' to attempt a live refresh.",
            len(bundled),
        )
        _MEM_CACHE = bundled
        return _MEM_CACHE

    return {}


def invalidate_memory_cache() -> None:
    """Clear the in-memory cache. Next call re-reads the file or fetches."""
    global _MEM_CACHE
    _MEM_CACHE = None


# ── Ardent API ─────────────────────────────────────────────────────────────────

def _fetch_ardent() -> dict[str, dict]:
    """
    Fetch all commodities from the Ardent Insight API and return a
    display-name-keyed price dict.

    The API returns records for every known commodity; we filter to those
    that appear in _INTERNAL_TO_DISPLAY and store both the display name
    key (for the panel) and the internal name key (for journal lookups).
    """
    req = urllib.request.Request(_ARDENT_URL, headers=_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            raw = resp.read()
            encoding = resp.headers.get("Content-Encoding", "")
            if encoding.lower() == "gzip":
                import gzip
                raw = gzip.decompress(raw)
            data = json.loads(raw.decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(
            f"HTTP {exc.code} fetching Ardent commodity list: {exc.reason}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"URL error fetching Ardent commodity list: {exc.reason}"
        ) from exc

    if not isinstance(data, list):
        raise RuntimeError(
            f"Unexpected Ardent response type: {type(data).__name__}"
        )

    prices: dict[str, dict] = {}
    for item in data:
        if not isinstance(item, dict):
            continue
        internal = str(item.get("commodityName", "")).strip().lower()
        avg_sell  = item.get("avgSellPrice")
        max_sell  = item.get("maxSellPrice")

        if avg_sell is None or max_sell is None:
            continue

        try:
            entry = {
                "avg_sell": int(avg_sell),
                "max_sell": int(max_sell),
            }
        except (TypeError, ValueError):
            continue

        # Store under internal name so the mining role's _name_map lookups work.
        prices[internal] = entry

        # Also store under the localised display name so the client panel can
        # look up by the string it receives from MiningRefined events.
        display = _INTERNAL_TO_DISPLAY.get(internal)
        if display:
            prices[display] = entry

    return prices


# ── File cache helpers ─────────────────────────────────────────────────────────

def _load_file_cache(
    path: Path,
    *,
    ignore_age: bool = False,
) -> Optional[dict[str, dict]]:
    try:
        raw  = json.loads(path.read_text(encoding="utf-8"))
        ts   = datetime.fromisoformat(raw["fetched_at"])
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age  = datetime.now(timezone.utc) - ts
        if not ignore_age and age > timedelta(days=CACHE_MAX_DAYS):
            log.info(
                "Commodity prices: cache is %d days old (> %d) — will refresh",
                age.days, CACHE_MAX_DAYS,
            )
            return None
        prices = raw.get("prices", {})
        if not isinstance(prices, dict):
            return None
        return {str(k): dict(v) for k, v in prices.items()}
    except FileNotFoundError:
        log.debug("Commodity prices: no cache file at %s", path)
        return None
    except Exception as exc:
        log.warning("Commodity prices: could not read cache %s: %s", path, exc)
        return None


def _save_file_cache(path: Path, prices: dict[str, dict]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "prices":     prices,
        }
        path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        log.info("Commodity prices: cache written to %s", path)
    except Exception as exc:
        log.warning("Commodity prices: could not write cache %s: %s", path, exc)


def _load_bundled_prices() -> Optional[dict[str, dict]]:
    """Load the static price list bundled with the agent."""
    try:
        raw = json.loads(_BUNDLED_PRICES_PATH.read_text(encoding="utf-8"))
        prices = raw.get("prices", {})
        if not isinstance(prices, dict) or not prices:
            return None
        return {str(k): dict(v) for k, v in prices.items()}
    except Exception as exc:
        log.debug("Commodity prices: could not load bundled prices: %s", exc)
        return None


# ── Legacy Inara scraping (kept as reference, not called by default) ───────────

_INARA_URL = "https://inara.cz/elite/commodities-list/"

_INARA_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Referer":         "https://inara.cz/elite/",
}

_ROW_RE   = re.compile(
    r'href="/elite/commodity/\d+/"[^>]*>\s*([^<]+?)\s*</a>(.*?)</tr>',
    re.DOTALL | re.IGNORECASE,
)
_PRICE_RE = re.compile(r'([\d,]+)\s*Cr', re.IGNORECASE)


def _fetch_html(url: str) -> str:
    """Download a page and return it decoded (used by check_inara.py --local)."""
    req = urllib.request.Request(url, headers=_INARA_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            raw      = resp.read()
            encoding = resp.headers.get("Content-Encoding", "")
            if encoding.lower() == "gzip":
                import gzip
                raw = gzip.decompress(raw)
            charset = _charset_from_headers(resp.headers) or "utf-8"
            return raw.decode(charset, errors="replace")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(
            f"HTTP {exc.code} fetching Inara commodity list: {exc.reason}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"URL error fetching Inara commodity list: {exc.reason}"
        ) from exc


def _charset_from_headers(headers) -> str:
    ct = headers.get("Content-Type", "")
    m  = re.search(r"charset=([^\s;]+)", ct, re.IGNORECASE)
    return m.group(1).strip('"\'') if m else "utf-8"


def _parse_commodity_table(html: str) -> dict[str, dict]:
    """
    Parse the Inara commodity list HTML (used by check_inara.py --local).

    Table columns: 0=avg_sell 1=avg_buy 2=avg_profit 3=max_sell 4=min_buy 5=max_profit
    """
    results: dict[str, dict] = {}
    for m in _ROW_RE.finditer(html):
        name     = m.group(1).strip()
        row_tail = m.group(2)
        prices   = [
            int(raw.replace(",", ""))
            for raw in _PRICE_RE.findall(row_tail)
        ]
        if not name or len(prices) < 4:
            continue
        results[name] = {
            "avg_sell": prices[0],
            "max_sell": prices[3],
        }
    return results
