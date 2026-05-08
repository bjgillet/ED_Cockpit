"""
ED Cockpit — Inara Diagnostic / Price Updater
===============================================
Run this directly on the agent machine (Windows or Linux) to diagnose
why commodity prices are not being fetched / cached, and to refresh
the bundled static price list.

Usage
-----
  # Auto-fetch mode (may be blocked by Cloudflare — 503):
  python check_inara.py

  # Local HTML mode — use this when the auto-fetch is blocked:
  1. Open https://inara.cz/elite/commodities-list/ in your browser.
  2. Press Ctrl+S (or Cmd+S) and save as "Web Page, Complete" or
     "Web Page, HTML only" — call the file  inara_commodities.html
     and put it in the same directory as this script.
  3. Run:  python check_inara.py --local

  The script will parse the saved HTML and write a fresh
  agent/data/mining_commodity_prices.json + the user cache file.
"""
import json
import logging
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.DEBUG,
    format="%(levelname)-8s %(name)s — %(message)s",
)

LOCAL_HTML_FILE = Path(__file__).parent / "inara_commodities.html"
project_root    = Path(__file__).parent

# ── Resolve config dir ─────────────────────────────────────────────────────
if sys.platform == "win32":
    config_dir = Path.home() / "AppData" / "Roaming" / "ed-cockpit"
else:
    config_dir = Path.home() / ".config" / "ed-cockpit"

cache_path = config_dir / "commodity_prices.json"

print("=" * 60)
print("ED Cockpit — Inara Price Diagnostic / Updater")
print("=" * 60)
print(f"\nPlatform   : {sys.platform}")
print(f"Cache file : {cache_path}")
print(f"Exists     : {cache_path.exists()}")

if cache_path.exists():
    try:
        raw = json.loads(cache_path.read_text(encoding="utf-8"))
        print(f"Fetched at : {raw.get('fetched_at', 'unknown')}")
        print(f"Prices     : {len(raw.get('prices', {}))} commodities")
    except Exception as e:
        print(f"Read error : {e}")

# ── Import inara tools ─────────────────────────────────────────────────────
sys.path.insert(0, str(project_root))

try:
    from agent.tools.inara import (
        fetch_commodity_prices,
        _fetch_ardent,
        _fetch_html,
        _parse_commodity_table,
        _save_file_cache,
        _INARA_URL,
        _ARDENT_URL,
    )
except ImportError as e:
    print(f"\nImport error: {e}")
    print("Run from the ED_Cockpit root directory:")
    print("  python check_inara.py")
    sys.exit(1)

# ── Try Ardent API first (primary source) ─────────────────────────────────
print("\n" + "-" * 60)
print(f"[PRIMARY] Ardent Insight API: {_ARDENT_URL}")
print("-" * 60)
ardent_ok = False
try:
    ardent_prices = _fetch_ardent()
    print(f"  OK — {len(ardent_prices)} entries returned")
    for name in ["Void Opal", "Low Temperature Diamonds", "Painite", "Monazite", "Tritium"]:
        p = ardent_prices.get(name)
        if p:
            print(f"    {name:<38} avg={p['avg_sell']:>8,} Cr  max={p['max_sell']:>8,} Cr")
    print(f"\n[+] Writing cache → {cache_path}")
    _save_file_cache(cache_path, ardent_prices)
    print("    Done. Restart the agent to use fresh prices.")
    ardent_ok = True
except Exception as e:
    print(f"  FAILED: {e}")

# ── Choose source for Inara local-HTML fallback ────────────────────────────
use_local = "--local" in sys.argv or LOCAL_HTML_FILE.exists()

if use_local:
    if not LOCAL_HTML_FILE.exists():
        print(f"\nERROR: local HTML file not found: {LOCAL_HTML_FILE}")
        print("\nTo use local mode:")
        print("  1. Open https://inara.cz/elite/commodities-list/ in your browser")
        print("  2. Press Ctrl+S → save as 'inara_commodities.html' next to this script")
        print("  3. Run: python check_inara.py --local")
        sys.exit(1)

    print(f"\n[LOCAL MODE] Reading {LOCAL_HTML_FILE.name} ...")
    try:
        html = LOCAL_HTML_FILE.read_text(encoding="utf-8", errors="replace")
        print(f"             {len(html):,} bytes read")
    except Exception as e:
        print(f"ERROR reading file: {e}")
        sys.exit(1)

else:
    print("\n" + "-" * 60)
    print("Attempting live fetch from Inara...")
    print("-" * 60)
    print("(If this fails with 503, use --local mode — see usage above)")

    try:
        html = _fetch_html(_INARA_URL)
        print(f"\n[1/3] Fetched OK — {len(html):,} bytes")
    except Exception as e:
        print(f"\n[1/3] FAILED: {e}")
        print("\nInara is blocking automated requests (Cloudflare 503).")
        print("Use --local mode instead:")
        print("  1. Open https://inara.cz/elite/commodities-list/ in your browser")
        print(f"  2. Ctrl+S → save as:  {LOCAL_HTML_FILE}")
        print("  3. Run:  python check_inara.py --local")
        html = None

# ── Parse ──────────────────────────────────────────────────────────────────
prices = {}
if html:
    print("\n[2/3] Parsing commodity table...")
    prices = _parse_commodity_table(html)
    print(f"      Parsed {len(prices)} commodities")

    if not prices:
        debug_out = project_root / "inara_debug.html"
        debug_out.write_text(html[:20000], encoding="utf-8")
        print(f"\nWARNING: 0 commodities parsed.")
        print(f"  Saved first 20 000 chars to {debug_out}")
        if use_local:
            print("  Make sure you saved the *full* page (Ctrl+S in browser).")
            print("  The page must contain the commodity table, not just a redirect.")
        else:
            print("  The page returned was a bot-check page, not the real table.")

# ── Write results ──────────────────────────────────────────────────────────
if prices:
    print("\n[3/3] Sample prices (first 10 entries):")
    for name, data in list(prices.items())[:10]:
        print(f"      {name:<38} avg={data['avg_sell']:>8,} Cr  "
              f"max={data['max_sell']:>8,} Cr")

    # Write user cache file
    print(f"\n[+] Writing user cache → {cache_path}")
    _save_file_cache(cache_path, prices)
    print(f"    Done ({len(prices)} commodities)")

    # Update bundled static price file
    bundled_path = project_root / "agent" / "data" / "mining_commodity_prices.json"
    try:
        from datetime import datetime, timezone
        mining_keys = {
            "Alexandrite", "Benitoite", "Bromellite", "Coltan", "Gold",
            "Grandidierite", "Jadeite", "Low Temperature Diamonds",
            "Methanol Monohydrate Crystals", "Monazite", "Musgravite",
            "Osmium", "Painite", "Palladium", "Platinum", "Praseodymium",
            "Rhodplumsite", "Samarium", "Serendibite", "Taaffeite",
            "Tritium", "Void Opal",
        }
        mining_prices = {k: v for k, v in prices.items() if k in mining_keys}
        # Also pick up any new mining commodities not in the known set
        # by keeping everything if the known set yielded few results
        if len(mining_prices) < 5:
            mining_prices = prices

        bundled = {
            "_comment": (
                "Static fallback for Inara commodity prices. "
                "Refresh by running: python check_inara.py --local"
            ),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "prices": mining_prices,
        }
        bundled_path.write_text(
            json.dumps(bundled, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"\n[+] Updated bundled prices → {bundled_path}")
        print(f"    {len(mining_prices)} mining commodities stored")
    except Exception as e:
        print(f"\nWARNING: could not update bundled prices: {e}")

    print("\n✓ Done. Restart the ED Cockpit agent to pick up fresh prices.")

# ── Bundled fallback status (always shown) ─────────────────────────────────
bundled_path = project_root / "agent" / "data" / "mining_commodity_prices.json"
print("\n" + "-" * 60)
print(f"Bundled static prices : {bundled_path}")
if bundled_path.exists():
    try:
        raw = json.loads(bundled_path.read_text(encoding="utf-8"))
        n = len(raw.get("prices", {}))
        print(f"Bundled entries       : {n} commodities")
        print(f"Bundled date          : {raw.get('fetched_at', 'unknown')}")
        print("  → Active when live fetch is blocked (current situation).")
        if not prices:
            print("  → To refresh: save the Inara page from your browser and run")
            print("    python check_inara.py --local")
    except Exception as e:
        print(f"Bundled read error    : {e}")
else:
    print("WARNING: bundled file not found!")
    print("  Expected: agent/data/mining_commodity_prices.json")
