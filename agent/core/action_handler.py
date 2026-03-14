"""
ED Assist — Action Handler
============================
Executes hardware-level actions (key presses) on the machine running the
agent, on behalf of authenticated client requests.

Design
------
  The action handler is a thin OS-abstraction layer.  It receives a logical
  key name (e.g. ``"next_firegroup"``) and translates it to a platform-specific
  keypress event.

  Platform support
  ----------------
  Windows:
    Elite Dangerous uses DirectInput, so ``pyautogui`` alone is not reliable.
    The handler uses ``pydirectinput`` or a raw ``SendInput`` call via
    ``ctypes`` to guarantee DirectInput delivery.

  Linux (Wine/Proton):
    The handler uses ``python-xlib`` or ``evdev`` (for /dev/input injection)
    to send key events to the Wine window.

  The correct backend is selected automatically at import time based on
  ``sys.platform``.

Key map
-------
  Logical names map to game bindings which the user can configure in the
  ED options menu.  A separate ``bindings.json`` file (future work) will
  allow the mapping to be customised without code changes.

Dependencies
------------
  Windows: pip install pydirectinput
  Linux:   pip install python-xlib   OR   (root) evdev

TODO — Phase 5: implement platform backends.
"""
from __future__ import annotations

import sys


class ActionHandler:
    """
    Key-press action handler — stub, to be implemented in Phase 5.

    Parameters
    ----------
    key_map : dict[str, str], optional
        Override the default logical-name → platform-key mapping.
    """

    _DEFAULT_KEY_MAP: dict[str, str] = {
        "next_firegroup":    "bracketright",
        "prev_firegroup":    "bracketleft",
        "landing_gear":      "l",
        "deploy_hardpoints": "u",
        "boost":             "tab",
        "hyperspace_jump":   "j",
    }

    def __init__(self, key_map: dict[str, str] | None = None) -> None:
        self._key_map = {**self._DEFAULT_KEY_MAP, **(key_map or {})}

    def execute(self, action: str, key: str) -> bool:
        """
        Execute an action.

        Parameters
        ----------
        action : str
            Action type — currently only ``"key_press"`` is supported.
        key : str
            Logical key name from the key map.

        Returns
        -------
        bool
            True if the action was dispatched, False if the key is unknown
            or the action type is unsupported.
        """
        if action != "key_press":
            return False
        platform_key = self._key_map.get(key)
        if platform_key is None:
            return False
        # TODO — Phase 5: dispatch to platform-specific backend
        return False

    @staticmethod
    def supported_platform() -> bool:
        """Return True if key simulation is supported on the current OS."""
        return sys.platform in ("win32", "linux")
