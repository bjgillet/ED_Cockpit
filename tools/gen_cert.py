"""
ED Assist — Certificate Generator (one-time setup tool)
=========================================================
Run this once on the agent machine to generate the self-signed TLS
certificate and private key used by the WebSocket server.

Usage
-----
    python tools/gen_cert.py
    python tools/gen_cert.py --out /path/to/certs/ --cn my-agent --days 3650

Output files
------------
  agent.crt — PEM certificate   (share with clients, or share its fingerprint)
  agent.key — PEM private key   (keep secret — never share)

After generation
----------------
  The fingerprint is printed to stdout.  Share it with every client user
  so they can verify the agent's identity on first connect (TOFU).

  Alternatively, copy agent.crt to the client machine and set ``ca_cert_path``
  in the client's config file to the copied path.

Dependencies
------------
    pip install cryptography
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from agent.security.tls_setup import generate_self_signed_cert, cert_fingerprint


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate ED Assist agent TLS certificate",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--out",  default="",          metavar="DIR",
                        help="Output directory (default: ~/.config/ed-assist/)")
    parser.add_argument("--cn",   default="ed-agent",  metavar="NAME",
                        help="Certificate common name (default: ed-agent)")
    parser.add_argument("--days", default=3650, type=int, metavar="N",
                        help="Certificate validity in days (default: 3650 = 10 years)")
    parser.add_argument("--force", action="store_true",
                        help="Overwrite existing certificate without prompting")
    args = parser.parse_args()

    # ── Determine output directory ─────────────────────────────────────────
    if args.out:
        out_dir = Path(args.out)
    else:
        if sys.platform == "win32":
            out_dir = Path.home() / "AppData" / "Roaming" / "ed-assist"
        else:
            out_dir = Path.home() / ".config" / "ed-assist"

    out_dir.mkdir(parents=True, exist_ok=True)
    cert_path = out_dir / "agent.crt"
    key_path  = out_dir / "agent.key"

    # ── Safety check ──────────────────────────────────────────────────────
    if cert_path.exists() and not args.force:
        existing_fp = cert_fingerprint(cert_path)
        print(f"Certificate already exists: {cert_path}")
        print(f"Existing fingerprint: {existing_fp}")
        answer = input("Overwrite? [y/N] ").strip().lower()
        if answer != "y":
            print("Aborted.")
            sys.exit(0)

    # ── Generate ──────────────────────────────────────────────────────────
    print()
    print("Generating self-signed RSA-2048 certificate …")
    print(f"  Common name : {args.cn}")
    print(f"  Validity    : {args.days} days")
    print(f"  Certificate : {cert_path}")
    print(f"  Private key : {key_path}")
    print()

    fingerprint = generate_self_signed_cert(cert_path, key_path, args.cn, args.days)

    # ── Print fingerprint prominently ─────────────────────────────────────
    print("=" * 70)
    print("  CERTIFICATE FINGERPRINT (SHA-256)")
    print()
    print(f"  {fingerprint}")
    print()
    print("  Share this fingerprint with all client users so they can verify")
    print("  the agent's identity on first connect (Trust On First Use).")
    print()
    print("  Alternatively, copy agent.crt to each client machine and set")
    print("  'ca_cert_path' in the client's config file.")
    print("=" * 70)
    print()
    print("Done.")


if __name__ == "__main__":
    main()
