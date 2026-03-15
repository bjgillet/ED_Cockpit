"""
ED Cockpit — Agent Authentication
==================================
Validates incoming client connections using the pre-shared token scheme
and verifies HMAC integrity on ActionMessages.

Responsibilities
----------------
  • Token lookup: given a client_id, retrieve its stored token from the
    ClientRegistry and perform a timing-safe comparison.
  • HMAC + sequence verification: delegates to ``shared.messages`` helpers
    which are also used by the client side.
  • Sequence tracking: the last accepted sequence number per client is
    maintained in ``WSServer._ConnectedClient.last_seq``.

All comparisons use ``hmac.compare_digest`` to prevent timing attacks.

Dependencies
------------
  Standard library only: ``hmac``, ``hashlib``.
"""
from __future__ import annotations

import hashlib
import hmac

from agent.security.tokens import verify_token_against_hash
from shared.messages import compute_action_hmac, verify_action_hmac

__all__ = [
    "verify_token",
    "compute_action_hmac",
    "verify_action_hmac",
    "verify_token_against_hash",
]


def verify_token(stored_token: str, provided_token: str) -> bool:
    """Timing-safe comparison of two raw token strings."""
    return hmac.compare_digest(
        stored_token.encode(),
        provided_token.encode(),
    )
