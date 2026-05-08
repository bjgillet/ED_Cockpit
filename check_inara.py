"""
ED Cockpit — Inara Diagnostic Script
=====================================
Run this directly on the agent machine (Windows or Linux) to diagnose
why commodity prices are not being fetched / cached.

Usage (from the ED_Cockpit directory):
    python check_inara.py

What it does:
  1. Shows the expected cache file path on this OS.
  2. Checks whether the cache file already exists and its age.
  3. Attempts a live Inara fetch and shows how many commodities were parsed.
  4. Writes the result to the cache file if the fetch succeeded.
  5. Shows the bundled static-price fallback status.
"""
import json
import logging
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.DEBUG,
    format="%(levelname)-8s %(name)s — %(message)s",
)

# ── Resolve config dir (mirrors MiningRole._resolve_config_dir) ────────────
if sys.platform == "win32":
    config_dir = Path.home() / "AppData" / "Roaming" / "ed-cockpit"
else:
    config_dir = Path.home() / ".config" / "ed-cockpit"

cache_path = config_dir / "commodity_prices.json"
project_root = Path(__file__).parent

print("=" * 60)
print("ED Cockpit — Inara Price Diagnostic")
print("=" * 60)
print(f"\nPlatform      : {sys.platform}")
print(f"Config dir    : {config_dir}")
print(f"Cache file    : {cache_path}")
print(f"File exists   : {cache_path.exists()}")

if cache_path.exists():
    try:
        raw = json.loads(cache_path.read_text(encoding="utf-8"))
        fetched_at = raw.get("fetched_at", "unknown")
        n = len(raw.get("prices", {}))
        print(f"Fetched at    : {fetched_at}")
        print(f"Cached prices : {n} commodities")
    except Exception as e:
        print(f"Cache read err: {e}")
else:
    print("Cache file    : NOT FOUND — will attempt live fetch\n")

# ── Add project root to path so agent.tools.inara can be imported ──────────
sys.path.insert(0, str(project_root))

print("\n" + "-" * 60)
print("Attempting live fetch from https://inara.cz/elite/commodities-list/")
print("-" * 60)

try:
    from agent.tools.inara import (
        fetch_commodity_prices,
        _fetch_html,
        _parse_commodity_table,
        _INARA_URL,
    )
except ImportError as e:
    print(f"\nImport error: {e}")
    print("Run this script from the ED_Cockpit root directory:")
    print("  cd path\\to\\ED_Cockpit")
    print("  python check_inara.py")
    sys.exit(1)

# Step 1: raw HTTP fetch
print("\n[1/3] Fetching HTML...")
fetch_ok = False
try:
    html = _fetch_html(_INARA_URL)
    print(f"      OK — received {len(html):,} bytes")
    fetch_ok = True
except Exception as e:
    print(f"      FAILED: {e}")
    print("\nPossible causes:")
    print("  • No internet access / firewall blocking outbound HTTPS")
    print("  • Cloudflare bot detection blocking this IP / User-Agent")
    print("  • Inara is down or returning an error page")

if fetch_ok:
    # Step 2: parse
    print("\n[2/3] Parsing commodity table...")
    prices = _parse_commodity_table(html)
    print(f"      Parsed {len(prices)} commodities")

    if not prices:
        print("\n  WARNING: 0 commodities parsed!")
        print("  Inara is probably returning a bot-check / Cloudflare page.")
        debug_file = Path("inara_debug.html")
        debug_file.write_text(html[:20000], encoding="utf-8")
        print(f"  Saved first 20 000 chars to {debug_file.resolve()}")
        print("  Inspect that file — if it says 'Please verify you are human'")
        print("  the page is Cloudflare-blocked on this machine.")
    else:
        # Step 3: show sample and write cache
        print("\n[3/3] Sample prices (first 10):")
        for name, data in list(prices.items())[:10]:
            print(f"      {name:<35} avg_sell={data['avg_sell']:>8,} Cr  "
                  f"max_sell={data['max_sell']:>8,} Cr")

        print("\n[+] Writing cache file...")
        prices_full = fetch_commodity_prices(cache_path, force_refresh=True)
        print(f"    Written to: {cache_path}")
        print(f"    Total entries: {len(prices_full)}")
        print("\n✓ Inara fetch is working. Restart the agent to pick up fresh prices.")

# ── Bundled fallback status (always shown) ─────────────────────────────────
print("\n" + "-" * 60)
bundled_path = project_root / "agent" / "data" / "mining_commodity_prices.json"
print(f"Bundled static prices : {bundled_path}")
if bundled_path.exists():
    try:
        raw = json.loads(bundled_path.read_text(encoding="utf-8"))
        n = len(raw.get("prices", {}))
        print(f"Bundled entries       : {n} commodities")
        print(f"Bundled date          : {raw.get('fetched_at', 'unknown')}")
        print("  → This is used as last resort when Inara is unreachable.")
        print("    Re-run this script after fixing network access to refresh it.")
    except Exception as e:
        print(f"Bundled read error    : {e}")
else:
    print("WARNING: bundled file not found!")
    print("  Expected: agent/data/mining_commodity_prices.json")
