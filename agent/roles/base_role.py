"""
ED Assist — Base Role
======================
Abstract base class that every agent-side role module must implement.

A role is responsible for:
  1. Declaring which Elite Dangerous journal event names it cares about
     (``journal_events``).
  2. Filtering and optionally enriching raw event data before it is
     forwarded to subscribed clients (``filter()``).

Adding a new role
-----------------
  1. Create ``agent/roles/<role_name>.py`` with a class inheriting BaseRole.
  2. Set ``name`` and ``journal_events``.
  3. Override ``filter()``.
  4. Register the class in ``agent/roles/__init__.py``.

No changes to the agent core are required.

Example
-------
    from agent.roles.base_role import BaseRole

    class ExobiologyRole(BaseRole):
        name = "exobiology"
        journal_events = frozenset({"ScanOrganic", "SellOrganicData"})

        def filter(self, event_name: str, data: dict) -> dict | None:
            if event_name == "ScanOrganic":
                return {"species": data.get("Species_Localised", "")}
            return None
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class BaseRole(ABC):
    """Abstract base for all agent-side role handlers."""

    name: str = ""
    journal_events: frozenset[str] = frozenset()

    @abstractmethod
    def filter(self, event_name: str, data: dict) -> dict | None:
        """
        Process a journal event and return filtered data to forward.

        Parameters
        ----------
        event_name : str
            The value of the ``event`` field from the ED journal line.
        data : dict
            The full parsed journal line (includes ``event``, ``timestamp``,
            and all event-specific fields).

        Returns
        -------
        dict | None
            The data payload to include in the ``EventMessage.data`` field,
            or ``None`` to drop the event entirely.
        """
