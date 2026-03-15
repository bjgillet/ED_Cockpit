"""
ED Cockpit — Role Registry
===========================
Central registration point for all agent-side roles.

Import the registry dict to look up a role by name, or call
``get_role(name)`` for a safe lookup with a clear error message.

To register a new role, import its class here and add it to ``_REGISTRY``.
"""
from __future__ import annotations

from agent.roles.base_role import BaseRole
from agent.roles.exobiology import ExobiologyRole
from agent.roles.mining import MiningRole
from agent.roles.session import SessionRole
from agent.roles.navigation import NavigationRole

_REGISTRY: dict[str, type[BaseRole]] = {
    ExobiologyRole.name: ExobiologyRole,
    MiningRole.name:     MiningRole,
    SessionRole.name:    SessionRole,
    NavigationRole.name: NavigationRole,
}


def get_role(name: str) -> BaseRole:
    """
    Instantiate and return the role handler for ``name``.

    Raises
    ------
    KeyError
        If no role with the given name is registered.
    """
    cls = _REGISTRY.get(name)
    if cls is None:
        raise KeyError(f"Unknown role: {name!r}. Registered: {list(_REGISTRY)}")
    return cls()


def all_role_names() -> list[str]:
    """Return an ordered list of all registered role names."""
    return list(_REGISTRY.keys())
