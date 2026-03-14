"""
ED Assist — Canonical Role Definitions
========================================
Single source of truth for role names shared between the agent and client.

Both sides import these constants to avoid hard-coded strings and to make
adding a new role a one-place change.

Usage
-----
    from shared.roles_def import Role, ALL_ROLES

    if role_name in ALL_ROLES:
        ...
"""
from __future__ import annotations


class Role:
    """Namespace of canonical role name constants."""

    EXOBIOLOGY:         str = "exobiology"
    MINING:             str = "mining"
    SESSION_MONITORING: str = "session_monitoring"
    NAVIGATION:         str = "navigation"


# Ordered list of all defined roles — used for validation and UI ordering.
ALL_ROLES: tuple[str, ...] = (
    Role.EXOBIOLOGY,
    Role.MINING,
    Role.SESSION_MONITORING,
    Role.NAVIGATION,
)
