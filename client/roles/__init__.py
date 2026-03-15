"""
ED Cockpit — Client Role Panel Registry
========================================
Maps canonical role names to their panel classes.

To register a new panel, import its class and add it to ``_REGISTRY``.
The MainWindow uses this registry to dynamically instantiate only the panels
matching the roles assigned to this client by the agent.
"""
from __future__ import annotations

import queue
import tkinter as tk
from typing import Callable, Optional

from client.roles.base_panel import BasePanel
from client.roles.exobiology_panel import ExobiologyPanel
from client.roles.mining_panel import MiningPanel
from client.roles.session_panel import SessionPanel
from client.roles.navigation_panel import NavigationPanel

_REGISTRY: dict[str, type[BasePanel]] = {
    ExobiologyPanel.role_name: ExobiologyPanel,
    MiningPanel.role_name:     MiningPanel,
    SessionPanel.role_name:    SessionPanel,
    NavigationPanel.role_name: NavigationPanel,
}


def create_panel(
    role_name:       str,
    parent:          tk.Misc,
    event_queue:     queue.Queue,
    action_callback: Optional[Callable[[str, str], None]] = None,
) -> BasePanel:
    """
    Instantiate and return the panel for ``role_name``.

    Raises
    ------
    KeyError
        If no panel is registered for the given role name.
    """
    cls = _REGISTRY.get(role_name)
    if cls is None:
        raise KeyError(f"No panel registered for role: {role_name!r}")
    return cls(parent, event_queue, action_callback)


def all_panel_role_names() -> list[str]:
    return list(_REGISTRY.keys())
