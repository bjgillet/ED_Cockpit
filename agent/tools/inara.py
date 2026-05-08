"""
ED Cockpit — Inara Commodity Price Fetcher
==========================================
Fetches average and maximum sell prices for Elite Dangerous commodities
from the Inara public commodity list page, and caches the result in a
local JSON file so Inara is contacted at most once per month.

No external dependencies — uses only the Python standard library
(urllib + re + json), consistent with the rest of the agent tools.

Data source
-----------
  https://inara.cz/elite/commodities-list/

  Inara aggregates real-time market data from EDDN (contributed by players
  running EDMC, EDDiscovery, EDDI, etc.).  The commodity list shows:
    • Avg sell price   — community average sell price across all markets
    • Max sell price   — highest known sell price currently on record

Cache strategy
--------------
  Three layers (tried in order):

  1. Memory cache — populated from layers below on the first call.
                   Avoids repeated disk/network I/O within one process run.

  2. File cache   — JSON file at a configurable path (typically
                   ~/.config/ed-cockpit/commodity_prices.json or
                   %APPDATA%\\ed-cockpit\\commodity_prices.json on Windows).
                   Written on every successful Inara fetch.
                   Considered fresh for CACHE_MAX_DAYS days.

  3. Live fetch   — HTTP request to Inara.  Result is written to the file
                   cache and stored in memory.

  4. Bundled data — If layers 2 and 3 both fail (e.g. Inara is down or the
                   machine is behind a firewall), the static price list
                   bundled with the agent (agent/data/mining_commodity_prices.json)
                   is used as a last resort.  Prices may be slightly outdated
                   but are always better than nothing.  Update the bundled
                   file by running:  python check_inara.py

Cache file format
-----------------
  {
    "fetched_at": "<ISO 8601 UTC timestamp>",
    "prices": {
      "Painite":   {"avg_sell": 57672,  "max_sell": 391116},
      "Void Opal": {"avg_sell": 150532, "max_sell": 552666},
      ...
    }
  }

Returned structure (from fetch_commodity_prices)
-------------------------------------------------
  {
    "Painite":      {"avg_sell": 57672,  "max_sell": 391116},
    "Void Opal":    {"avg_sell": 150532, "max_sell": 552666},
    "Tritium":      {"avg_sell": 53422,  "max_sell": 61894},
    ...
  }
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

_INARA_URL      = "https://inara.cz/elite/commodities-list/"
_HTTP_TIMEOUT   = 15.0   # seconds per request
CACHE_MAX_DAYS  = 30     # refresh file cache if older than this many days

# Bundled static price list shipped with the agent — used as last resort when
# Inara is unreachable (firewall, bot detection, site down, etc.).
_BUNDLED_PRICES_PATH = Path(__file__).parent.parent / "data" / "mining_commodity_prices.json"

# Browser-like headers to avoid bot-detection (Cloudflare, etc.).
# Inara's commodity list is a public page but some IPs / plain urllib
# User-Agents are challenged.  Using a realistic browser signature avoids
# most soft blocks without requiring any credentials.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer":         "https://inara.cz/elite/",
    "Connection":      "keep-alive",
    "DNT":             "1",
}

# Match one commodity table row: capture the name from the anchor, then
# everything up to the closing </tr> for price extraction.
_ROW_RE = re.compile(
    r'href="/elite/commodity/\d+/"[^>]*>\s*([^<]+?)\s*</a>(.*?)</tr>',
    re.DOTALL | re.IGNORECASE,
)

# Match price values like "57,672 Cr"
_PRICE_RE = re.compile(r'([\d,]+)\s*Cr', re.IGNORECASE)

# ── In-memory layer (process-lifetime cache) ───────────────────────────────────

_MEM_CACHE: Optional[dict[str, dict]] = None


# ── Public API ─────────────────────────────────────────────────────────────────

def fetch_commodity_prices(
    cache_path: Optional[Path] = None,
    *,
    force_refresh: bool = False,
) -> dict[str, dict]:
    """
    Return a dict mapping commodity name → {avg_sell, max_sell}.

    Lookup order
    ------------
    1. In-memory cache    — hit if already populated this process run.
    2. File cache         — read ``cache_path`` if fresh (≤ CACHE_MAX_DAYS).
    3. Inara HTTP fetch   — done only when the file is absent / stale.
       Result is written back to ``cache_path`` and stored in memory.

    Pass ``force_refresh=True`` to skip both caches and always fetch.

    Never raises — returns an empty dict on any error so the caller
    can degrade gracefully.

    Blocking — call inside a daemon thread or asyncio executor.

    Parameters
    ----------
    cache_path : Path, optional
        Path to the JSON cache file.  When *None* only the in-memory
        layer is used (no disk I/O).
    force_refresh : bool
        Bypass both caches and fetch unconditionally.
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
                "Inara: loaded %d commodity prices from file cache (%s)",
                len(cached), cache_path,
            )
            return _MEM_CACHE

    # ── Live fetch ──────────────────────────────────────────────────────────
    try:
        html   = _fetch_html(_INARA_URL)
        prices = _parse_commodity_table(html)
    except Exception as exc:
        log.warning("Inara: commodity price fetch failed: %s", exc)
        prices = {}

    if prices:
        log.info("Inara: fetched %d commodity prices from Inara", len(prices))
        if cache_path is not None:
            _save_file_cache(cache_path, prices)
        _MEM_CACHE = prices
        return _MEM_CACHE

    log.warning(
        "Inara: live fetch returned 0 commodities "
        "(bot detection / page structure change / network error)"
    )

    # ── Stale file cache ────────────────────────────────────────────────────
    if cache_path is not None:
        stale = _load_file_cache(cache_path, ignore_age=True)
        if stale:
            log.warning(
                "Inara: using stale file cache as fallback (%d commodities)", len(stale)
            )
            _MEM_CACHE = stale
            return _MEM_CACHE

    # ── Bundled static prices ───────────────────────────────────────────────
    bundled = _load_bundled_prices()
    if bundled:
        log.warning(
            "Inara: using bundled static prices as last resort (%d commodities). "
            "Run 'python check_inara.py' from the agent machine to refresh.",
            len(bundled),
        )
        _MEM_CACHE = bundled
        return _MEM_CACHE

    return {}


def invalidate_memory_cache() -> None:
    """Clear the in-memory cache.  Next call re-reads the file (or fetches)."""
    global _MEM_CACHE
    _MEM_CACHE = None


# ── File cache helpers ─────────────────────────────────────────────────────────

def _load_file_cache(
    path: Path,
    *,
    ignore_age: bool = False,
) -> Optional[dict[str, dict]]:
    """
    Load prices from the JSON cache file.

    Returns the price dict if the file exists and (unless *ignore_age*)
    is not older than CACHE_MAX_DAYS.  Returns None otherwise.
    """
    try:
        raw  = json.loads(path.read_text(encoding="utf-8"))
        ts   = datetime.fromisoformat(raw["fetched_at"])
        # Make offset-naive timestamps comparable.
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age  = datetime.now(timezone.utc) - ts
        if not ignore_age and age > timedelta(days=CACHE_MAX_DAYS):
            log.info(
                "Inara: cache file is %d days old (> %d) — will refresh",
                age.days, CACHE_MAX_DAYS,
            )
            return None
        prices = raw.get("prices", {})
        if not isinstance(prices, dict):
            return None
        return {str(k): dict(v) for k, v in prices.items()}
    except FileNotFoundError:
        log.debug("Inara: no cache file at %s — will fetch", path)
        return None
    except Exception as exc:
        log.warning("Inara: could not read cache file %s: %s", path, exc)
        return None


def _load_bundled_prices() -> Optional[dict[str, dict]]:
    """Load the static price list bundled with the agent (agent/data/mining_commodity_prices.json).

    Returns the price dict or None if the file cannot be read.
    """
    try:
        raw = json.loads(_BUNDLED_PRICES_PATH.read_text(encoding="utf-8"))
        prices = raw.get("prices", {})
        if not isinstance(prices, dict) or not prices:
            return None
        return {str(k): dict(v) for k, v in prices.items()}
    except Exception as exc:
        log.debug("Inara: could not load bundled prices: %s", exc)
        return None


def _save_file_cache(path: Path, prices: dict[str, dict]) -> None:
    """Write prices and a UTC timestamp to the JSON cache file."""
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
        log.info("Inara: price cache written to %s", path)
    except Exception as exc:
        log.warning("Inara: could not write cache file %s: %s", path, exc)


# ── HTTP + HTML parsing ────────────────────────────────────────────────────────

def _fetch_html(url: str) -> str:
    """Download the commodity list page and return it decoded."""
    req = urllib.request.Request(url, headers=_HEADERS)
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
    Parse the Inara commodity list HTML into a price map.

    Table columns (in order):
      0 avg_sell  1 avg_buy  2 avg_profit  3 max_sell  4 min_buy  5 max_profit

    We store avg_sell (index 0) and max_sell (index 3).
    Rows with fewer than 4 price values are skipped (headers / empty rows).
    """
    results: dict[str, dict] = {}
    for m in _ROW_RE.finditer(html):
        name     = m.group(1).strip()
        row_tail = m.group(2)

        prices = [
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
