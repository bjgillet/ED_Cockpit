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
  Two layers:

  1. File cache  — JSON file at a configurable path (typically
                   ~/.config/ed-cockpit/commodity_prices.json).
                   Written on every successful Inara fetch.
                   Considered fresh for CACHE_MAX_DAYS days.

  2. Memory cache — populated from the file cache (or a fresh fetch)
                   on the first call within a process run.
                   Avoids repeated disk reads when get_snapshot() is
                   called multiple times per session.

  At agent startup the file is read.  If it is ≤ CACHE_MAX_DAYS old the
  stored prices are used without any network request.  Only when the file
  is absent or older than CACHE_MAX_DAYS is a fresh Inara request made.

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

_HEADERS = {
    "User-Agent":      "ED-Cockpit/1.0 (Commodity Price Cache; contact via github)",
    "Accept":          "text/html,application/xhtml+xml",
    "Accept-Encoding": "gzip, deflate",
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
        # Fall back to a stale file cache rather than returning nothing.
        if cache_path is not None:
            stale = _load_file_cache(cache_path, ignore_age=True)
            if stale:
                log.warning("Inara: using stale file cache as fallback")
                _MEM_CACHE = stale
                return _MEM_CACHE
        return {}

    if not prices:
        log.warning(
            "Inara: parsed 0 commodities — page structure may have changed"
        )
        return {}

    log.info("Inara: fetched %d commodity prices from Inara", len(prices))

    if cache_path is not None:
        _save_file_cache(cache_path, prices)

    _MEM_CACHE = prices
    return _MEM_CACHE


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
