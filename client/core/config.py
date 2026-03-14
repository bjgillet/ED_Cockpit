"""
ED Assist — Client Configuration
====================================
Loads and persists the client's local configuration: Client_ID, raw token,
agent address, port, and TLS settings.

Storage
-------
  Configuration is stored in a JSON file at a platform-appropriate location:

  Linux / macOS : ~/.config/ed-assist/client.json
  Windows       : %APPDATA%\\ed-assist\\client.json

  The raw token IS stored here (this is the client's secret credential).
  The file should be protected by the OS user-permissions model
  (chmod 600 on Linux/macOS).

TLS fields
----------
  tls_enabled         : bool  — whether to use TLS (default True).
  cert_fingerprint    : str   — SHA-256 fingerprint of the agent's certificate,
                                colon-separated uppercase hex bytes.
                                Empty until the client connects and pins it.
  ca_cert_path        : str   — optional path to the agent's PEM certificate
                                on this machine.  When set, used for cert-file
                                pinning instead of fingerprint pinning.

First-run behaviour
-------------------
  If no config file exists, a default config is written with an
  auto-generated Client_ID (``ed-client-<4 hex chars>``).  The agent address
  defaults to ``localhost`` and TLS is enabled by default.
"""
from __future__ import annotations

import json
import os
import secrets
import sys
from pathlib import Path
from typing import Optional


def _default_config_path() -> Path:
    if sys.platform == "win32":
        base = Path.home() / "AppData" / "Roaming" / "ed-assist"
    else:
        base = Path.home() / ".config" / "ed-assist"
    base.mkdir(parents=True, exist_ok=True)
    return base / "client.json"


class ClientConfig:
    """
    Thin wrapper around the client's JSON config file.

    Attributes
    ----------
    client_id         : auto-generated ``ed-client-<hex>`` if not set
    token             : raw pre-shared token (empty until operator sets it)
    agent_host        : hostname or IP of the agent (default ``localhost``)
    agent_port        : WebSocket port of the agent (default 5759)
    tls_enabled       : use TLS for the connection (default True)
    cert_fingerprint  : pinned SHA-256 fingerprint of the agent certificate
                        (empty = not yet pinned; will be set on first connect)
    ca_cert_path      : optional path to agent.crt on this machine
    """

    _DEFAULTS: dict = {
        "client_id":        "",
        "token":            "",
        "agent_host":       "localhost",
        "agent_port":       5759,
        "tls_enabled":      True,
        "cert_fingerprint": "",
        "ca_cert_path":     "",
    }

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path              = path or _default_config_path()
        self.client_id:         str  = ""
        self.token:             str  = ""
        self.agent_host:        str  = "localhost"
        self.agent_port:        int  = 5759
        self.tls_enabled:       bool = True
        self.cert_fingerprint:  str  = ""
        self.ca_cert_path:      str  = ""
        self._load()

    # ── Persistence ────────────────────────────────────────────────────────

    def _load(self) -> None:
        if self._path.exists():
            try:
                with self._path.open() as fh:
                    data: dict = json.load(fh)
            except (json.JSONDecodeError, OSError):
                data = {}
        else:
            data = {}

        self.client_id        = data.get("client_id", "") or _generate_client_id()
        self.token            = data.get("token",            self._DEFAULTS["token"])
        self.agent_host       = data.get("agent_host",       self._DEFAULTS["agent_host"])
        self.agent_port       = data.get("agent_port",       self._DEFAULTS["agent_port"])
        self.tls_enabled      = data.get("tls_enabled",      self._DEFAULTS["tls_enabled"])
        self.cert_fingerprint = data.get("cert_fingerprint", self._DEFAULTS["cert_fingerprint"])
        self.ca_cert_path     = data.get("ca_cert_path",     self._DEFAULTS["ca_cert_path"])

        if not data.get("client_id"):
            self.save()

    def save(self) -> None:
        payload = {
            "client_id":        self.client_id,
            "token":            self.token,
            "agent_host":       self.agent_host,
            "agent_port":       self.agent_port,
            "tls_enabled":      self.tls_enabled,
            "cert_fingerprint": self.cert_fingerprint,
            "ca_cert_path":     self.ca_cert_path,
        }
        self._path.write_text(json.dumps(payload, indent=2))
        # Restrict permissions on the config file (contains raw token)
        if sys.platform != "win32":
            try:
                os.chmod(self._path, 0o600)
            except OSError:
                pass

    # ── Convenience ────────────────────────────────────────────────────────

    @property
    def is_configured(self) -> bool:
        """Return True if a token has been set (agent registration done)."""
        return bool(self.token)

    @property
    def has_pinned_cert(self) -> bool:
        """Return True if a TLS fingerprint or cert file path is set."""
        return bool(self.cert_fingerprint or self.ca_cert_path)

    def pin_fingerprint(self, fingerprint: str) -> None:
        """Store a fingerprint and persist immediately."""
        self.cert_fingerprint = fingerprint
        self.save()

    def resolved_ca_cert_path(self) -> Optional[Path]:
        """Return ``ca_cert_path`` as a Path, or None if not set."""
        if self.ca_cert_path:
            p = Path(self.ca_cert_path)
            return p if p.exists() else None
        return None


def _generate_client_id() -> str:
    return f"ed-client-{secrets.token_hex(2)}"
