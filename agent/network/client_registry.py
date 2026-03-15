"""
ED Cockpit — Client Registry
==============================
Persists and manages the agent-side record of every known client:
its Client_ID, hashed token, assigned roles, and last-seen timestamp.

Responsibilities
----------------
  • Load/save the client registry from/to a JSON file on disk.
  • Generate new Client_IDs and tokens (delegated to security/tokens.py).
  • CRUD operations for client records.
  • Provide role assignment and lookup.
  • Thread-safe access (registry may be read from the WS server thread and
    written from the GUI thread simultaneously).

Storage format (clients.json)
------------------------------
  {
    "ed-client-7f3a": {
      "token_hash": "<sha256 hex of the raw token>",
      "roles": ["exobiology", "session_monitoring"],
      "label": "Tablet – Exobio",
      "last_seen": "2026-03-14T12:00:00Z"
    },
    ...
  }

The raw token is never stored — only its SHA-256 hash.  The raw token is
shown once to the operator when the client record is first created.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Optional

# TODO — Phase 2: implement full persistence and CRUD


class ClientRecord:
    """In-memory representation of one registered client."""

    def __init__(
        self,
        client_id:  str,
        token_hash: str,
        roles:      list[str],
        label:      str = "",
        last_seen:  str = "",
    ) -> None:
        self.client_id  = client_id
        self.token_hash = token_hash
        self.roles      = list(roles)
        self.label      = label
        self.last_seen  = last_seen


class ClientRegistry:
    """
    Thread-safe registry of all known clients.

    Stub — to be fully implemented in Phase 2.
    """

    def __init__(self, path: Path) -> None:
        self._path  = path
        self._lock  = threading.Lock()
        self._store: dict[str, ClientRecord] = {}
        self._load()

    # ── Persistence ────────────────────────────────────────────────────────

    def _load(self) -> None:
        if not self._path.exists():
            return
        with self._path.open() as fh:
            raw: dict = json.load(fh)
        for cid, rec in raw.items():
            self._store[cid] = ClientRecord(
                client_id  = cid,
                token_hash = rec.get("token_hash", ""),
                roles      = rec.get("roles", []),
                label      = rec.get("label", ""),
                last_seen  = rec.get("last_seen", ""),
            )

    def save(self) -> None:
        payload = {}
        with self._lock:
            for cid, rec in self._store.items():
                payload[cid] = {
                    "token_hash": rec.token_hash,
                    "roles":      rec.roles,
                    "label":      rec.label,
                    "last_seen":  rec.last_seen,
                }
        self._path.write_text(json.dumps(payload, indent=2))

    # ── Lookup ─────────────────────────────────────────────────────────────

    def get(self, client_id: str) -> Optional[ClientRecord]:
        with self._lock:
            return self._store.get(client_id)

    def all_records(self) -> list[ClientRecord]:
        with self._lock:
            return list(self._store.values())

    # ── Mutation ───────────────────────────────────────────────────────────

    def add(self, record: ClientRecord) -> None:
        with self._lock:
            self._store[record.client_id] = record
        self.save()

    def set_roles(self, client_id: str, roles: list[str]) -> bool:
        """Update roles for an existing client. Returns False if not found."""
        with self._lock:
            rec = self._store.get(client_id)
            if rec is None:
                return False
            rec.roles = list(roles)
        self.save()
        return True

    def set_label(self, client_id: str, label: str) -> bool:
        """Update the human-readable label for a client. Returns False if not found."""
        with self._lock:
            rec = self._store.get(client_id)
            if rec is None:
                return False
            rec.label = label
        self.save()
        return True

    def remove(self, client_id: str) -> bool:
        """Delete a client record. Returns False if the client did not exist."""
        with self._lock:
            if client_id not in self._store:
                return False
            del self._store[client_id]
        self.save()
        return True

    def update_last_seen(self, client_id: str, timestamp: str) -> None:
        with self._lock:
            rec = self._store.get(client_id)
            if rec:
                rec.last_seen = timestamp
