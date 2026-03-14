"""
ED Assist — Token Management
==============================
Generates and hashes pre-shared tokens used for client authentication.

Design
------
  • Raw tokens are 32-byte cryptographically random values, hex-encoded
    (64-character strings).  They are shown once to the agent operator
    and must be copied to the client's config file.
  • The agent stores only the SHA-256 hash of each token, never the raw
    value.  This prevents token leakage if the registry file is read by
    an attacker.
  • Token comparison is always performed with ``hmac.compare_digest`` to
    prevent timing-based side-channel attacks.

Dependencies
------------
  Standard library only: ``secrets``, ``hashlib``, ``hmac``.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets


def generate_token() -> str:
    """
    Generate a new cryptographically random token.

    Returns
    -------
    str
        A 64-character hex string (32 bytes of entropy).
    """
    return secrets.token_hex(32)


def hash_token(raw_token: str) -> str:
    """
    Return the SHA-256 hex digest of ``raw_token``.

    This is the value stored in the client registry.
    """
    return hashlib.sha256(raw_token.encode()).hexdigest()


def verify_token_against_hash(raw_token: str, stored_hash: str) -> bool:
    """
    Timing-safe check that ``hash(raw_token) == stored_hash``.

    Parameters
    ----------
    raw_token : str
        The token provided by a connecting client.
    stored_hash : str
        The SHA-256 hex digest stored in the client registry.
    """
    computed = hash_token(raw_token)
    return hmac.compare_digest(computed, stored_hash)
