"""
ED Assist — Action Handler
============================
Executes hardware-level key-press actions on the machine running the agent,
on behalf of authenticated client requests.

Design
------
  The action handler is a thin OS-abstraction layer.  It receives a logical
  key name (e.g. ``"next_firegroup"``) and translates it to a platform-specific
  key-press event using one of three backends selected automatically at
  instantiation time:

  ┌──────────────────────────────────────────────────────────────────┐
  │  Platform   │  Backend                │  Library               │
  ├──────────────────────────────────────────────────────────────────┤
  │  Linux X11  │  _XlibBackend           │  python-xlib (XTEST)   │
  │  Linux any  │  _EvdevBackend          │  evdev + uinput        │
  │  Windows    │  _WinBackend            │  pydirectinput OR      │
  │             │                         │  ctypes SendInput      │
  │  Fallback   │  _NullBackend           │  none (logs warning)   │
  └──────────────────────────────────────────────────────────────────┘

  Selection order on Linux:
    1. _XlibBackend  — if DISPLAY is set and python-xlib is installed.
    2. _EvdevBackend — if /dev/uinput exists and evdev is installed.
    3. _NullBackend  — logs a warning.

  Selection order on Windows:
    1. _WinBackend with pydirectinput — if the library is installed.
    2. _WinBackend with ctypes SendInput — zero-dependency fallback.

Key map
-------
  Logical names (e.g. ``"boost"``) map to X11 keysym names (Linux) or
  Virtual-Key names (Windows).  The mapping is loaded from
  ``<config_dir>/bindings.json`` if that file exists; otherwise the
  built-in default table is used.

  The default table covers the most commonly remote-triggered bindings.
  Users can add or override entries by editing ``bindings.json``.

bindings.json format
--------------------
  {
    "next_firegroup":    "bracketright",
    "prev_firegroup":    "bracketleft",
    "boost":             "Tab",
    ...
  }

  Key values on Linux are X11 keysym names (as accepted by
  ``Xlib.XK.string_to_keysym``).  On Windows they are Virtual-Key names
  (as accepted by ``pydirectinput.press`` / ``ctypes VkKeyScanW``).

Thread safety
-------------
  ``execute()`` may be called from any thread.  The Xlib and evdev backends
  serialise writes internally.  The Windows backend is GIL-protected.

Dependencies (optional — gracefully degraded if absent)
---------------------------------------------------------
  Linux:   pip install python-xlib evdev
  Windows: pip install pydirectinput
"""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# ── Default logical → platform-key map ──────────────────────────────────────
#
# Key values are X11 keysym names on Linux and VK names on Windows.
# The list covers the standard ED keyboard bindings most users keep at
# default; extend via bindings.json.

_DEFAULT_KEY_MAP: dict[str, str] = {
    # ── Navigation ────────────────────────────────────────────────
    "hyperspace_jump":          "j",
    "supercruise":              "j",        # same key, different context
    "boost":                    "Tab",
    "next_system_in_route":     "BackSpace",

    # ── Combat / weapons ──────────────────────────────────────────
    "next_firegroup":           "bracketright",
    "prev_firegroup":           "bracketleft",
    "deploy_hardpoints":        "u",
    "fire_primary":             "space",
    "fire_secondary":           "Return",

    # ── Ship systems ──────────────────────────────────────────────
    "landing_gear":             "l",
    "cargo_scoop":              "Home",
    "flight_assist_toggle":     "z",
    "silent_running":           "Delete",
    "ship_lights":              "Insert",
    "night_vision":             "i",

    # ── Panels ────────────────────────────────────────────────────
    "target_panel":             "1",
    "comms_panel":              "2",
    "quick_comms":              "Return",
    "role_panel":               "3",
    "systems_panel":            "4",

    # ── Galaxy / System map ───────────────────────────────────────
    "galaxy_map":               "m",
    "system_map":               "comma",

    # ── SRV / on-foot ─────────────────────────────────────────────
    "recall_dismiss_ship":      "Home",
    "SRV_handbrake":            "Home",

    # ── FSS / DSS ─────────────────────────────────────────────────
    "enter_fss":                "backslash",
    "fss_zoom_in":              "KP_Add",
    "fss_zoom_out":             "KP_Subtract",
    "dss_fire":                 "space",

    # ── Function keys (custom user bindings) ─────────────────────
    "custom_f1":                "F1",
    "custom_f2":                "F2",
    "custom_f3":                "F3",
    "custom_f4":                "F4",
}

# Hold duration (seconds) for a single key tap
_PRESS_DURATION: float = 0.05


# ── Abstract backend ──────────────────────────────────────────────────────────

class _Backend(ABC):
    """Abstract key-press backend."""

    @abstractmethod
    def send_key(self, key: str) -> bool:
        """
        Inject a key press+release for the given platform key name.

        Returns True on success, False if the key was not recognised or
        the injection failed.
        """

    @abstractmethod
    def available(self) -> bool:
        """Return True if this backend is ready to inject keys."""


# ── Linux / X11 backend (python-xlib XTEST) ──────────────────────────────────

class _XlibBackend(_Backend):
    """
    Injects key events into the X11 server via the XTEST extension.

    Works for Elite Dangerous running under Wine / Proton on X11.
    The XTEST fake_input events are delivered at the X-server level, so
    all X11 windows — including Wine ones — receive them regardless of
    focus.

    Keysym names follow the X11 convention (e.g. ``"Tab"``, ``"bracketright"``,
    ``"KP_Add"``).  See ``/usr/include/X11/keysymdef.h`` or the python-xlib
    XK module for the full list.
    """

    def __init__(self) -> None:
        self._lock  = threading.Lock()
        self._display = None
        self._ok    = False
        self._init()

    def _init(self) -> None:
        try:
            from Xlib import display as xdisplay, X, XK
            from Xlib.ext import xtest as _xtest
            d = xdisplay.Display()
            if not d.query_extension("XTEST").present:
                log.warning("XlibBackend: XTEST extension not available.")
                return
            self._display = d
            self._X   = X
            self._XK  = XK
            self._xtest = _xtest
            self._ok  = True
            log.info("ActionHandler: XlibBackend ready (DISPLAY=%s)",
                     os.environ.get("DISPLAY", "?"))
        except Exception as exc:
            log.warning("ActionHandler: XlibBackend init failed: %s", exc)

    def available(self) -> bool:
        return self._ok

    def send_key(self, key: str) -> bool:
        if not self._ok or self._display is None:
            return False
        with self._lock:
            return self._inject(key)

    def _inject(self, key: str) -> bool:
        try:
            keysym = self._XK.string_to_keysym(key)
            if keysym == 0:
                # Try capitalised variant (e.g. "tab" → "Tab")
                keysym = self._XK.string_to_keysym(key.capitalize())
            if keysym == 0:
                log.warning("XlibBackend: unknown keysym %r", key)
                return False

            keycode = self._display.keysym_to_keycode(keysym)
            if keycode == 0:
                log.warning("XlibBackend: no keycode for keysym %r (%d)",
                            key, keysym)
                return False

            X = self._X
            self._xtest.fake_input(self._display, X.KeyPress,   keycode)
            self._display.sync()
            time.sleep(_PRESS_DURATION)
            self._xtest.fake_input(self._display, X.KeyRelease, keycode)
            self._display.sync()
            log.debug("XlibBackend: sent key %r (keycode=%d)", key, keycode)
            return True
        except Exception as exc:
            log.error("XlibBackend: inject failed for %r: %s", key, exc)
            return False


# ── Linux / evdev + uinput backend ───────────────────────────────────────────

class _EvdevBackend(_Backend):
    """
    Injects key events via ``/dev/uinput`` using the ``evdev`` library.

    Useful when DISPLAY is not set (headless / Wayland) or as an alternative
    to the Xlib backend.  Requires:
      • ``/dev/uinput`` to exist and be writable by the current user, OR
      • Running as root.
    Add the user to the ``input`` group or create a udev rule:
        KERNEL=="uinput", MODE="0660", GROUP="input"

    Keysym names are mapped to Linux KEY_* codes via a local lookup table.
    """

    # Logical keysym → evdev KEY_* name
    _KEY_TABLE: dict[str, str] = {
        "Tab":          "KEY_TAB",
        "Return":       "KEY_ENTER",
        "Escape":       "KEY_ESC",
        "BackSpace":    "KEY_BACKSPACE",
        "Delete":       "KEY_DELETE",
        "Insert":       "KEY_INSERT",
        "Home":         "KEY_HOME",
        "End":          "KEY_END",
        "Page_Up":      "KEY_PAGEUP",
        "Page_Down":    "KEY_PAGEDOWN",
        "Up":           "KEY_UP",
        "Down":         "KEY_DOWN",
        "Left":         "KEY_LEFT",
        "Right":        "KEY_RIGHT",
        "space":        "KEY_SPACE",
        "minus":        "KEY_MINUS",
        "equal":        "KEY_EQUAL",
        "bracketleft":  "KEY_LEFTBRACE",
        "bracketright": "KEY_RIGHTBRACE",
        "backslash":    "KEY_BACKSLASH",
        "semicolon":    "KEY_SEMICOLON",
        "apostrophe":   "KEY_APOSTROPHE",
        "grave":        "KEY_GRAVE",
        "comma":        "KEY_COMMA",
        "period":       "KEY_DOT",
        "slash":        "KEY_SLASH",
        "F1":  "KEY_F1",  "F2":  "KEY_F2",  "F3":  "KEY_F3",
        "F4":  "KEY_F4",  "F5":  "KEY_F5",  "F6":  "KEY_F6",
        "F7":  "KEY_F7",  "F8":  "KEY_F8",  "F9":  "KEY_F9",
        "F10": "KEY_F10", "F11": "KEY_F11", "F12": "KEY_F12",
        "1": "KEY_1", "2": "KEY_2", "3": "KEY_3", "4": "KEY_4",
        "5": "KEY_5", "6": "KEY_6", "7": "KEY_7", "8": "KEY_8",
        "9": "KEY_9", "0": "KEY_0",
        "a": "KEY_A", "b": "KEY_B", "c": "KEY_C", "d": "KEY_D",
        "e": "KEY_E", "f": "KEY_F", "g": "KEY_G", "h": "KEY_H",
        "i": "KEY_I", "j": "KEY_J", "k": "KEY_K", "l": "KEY_L",
        "m": "KEY_M", "n": "KEY_N", "o": "KEY_O", "p": "KEY_P",
        "q": "KEY_Q", "r": "KEY_R", "s": "KEY_S", "t": "KEY_T",
        "u": "KEY_U", "v": "KEY_V", "w": "KEY_W", "x": "KEY_X",
        "y": "KEY_Y", "z": "KEY_Z",
        "KP_0": "KEY_KP0", "KP_1": "KEY_KP1", "KP_2": "KEY_KP2",
        "KP_3": "KEY_KP3", "KP_4": "KEY_KP4", "KP_5": "KEY_KP5",
        "KP_6": "KEY_KP6", "KP_7": "KEY_KP7", "KP_8": "KEY_KP8",
        "KP_9": "KEY_KP9",
        "KP_Add":      "KEY_KPPLUS",
        "KP_Subtract": "KEY_KPMINUS",
        "KP_Multiply": "KEY_KPASTERISK",
        "KP_Divide":   "KEY_KPSLASH",
        "KP_Enter":    "KEY_KPENTER",
        "KP_Decimal":  "KEY_KPDOT",
        "Shift_L":   "KEY_LEFTSHIFT",
        "Shift_R":   "KEY_RIGHTSHIFT",
        "Control_L": "KEY_LEFTCTRL",
        "Control_R": "KEY_RIGHTCTRL",
        "Alt_L":     "KEY_LEFTALT",
        "Alt_R":     "KEY_RIGHTALT",
    }

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ui   = None
        self._ok   = False
        self._ecodes = None
        self._init()

    def _init(self) -> None:
        try:
            import evdev
            from evdev import UInput, ecodes
            self._ecodes = ecodes
            # Build the set of all KEY_* codes we'll ever emit
            key_codes = [
                ecodes.ecodes[name]
                for name in self._KEY_TABLE.values()
                if name in ecodes.ecodes
            ]
            self._ui = UInput(
                {ecodes.EV_KEY: key_codes},
                name="ed-assist-virtual-keyboard",
                version=0x3,
            )
            self._ok = True
            log.info("ActionHandler: EvdevBackend ready (/dev/uinput)")
        except PermissionError:
            log.warning(
                "ActionHandler: EvdevBackend requires /dev/uinput write access. "
                "Add user to 'input' group or create a udev rule.")
        except FileNotFoundError:
            log.warning("ActionHandler: /dev/uinput not found.")
        except Exception as exc:
            log.warning("ActionHandler: EvdevBackend init failed: %s", exc)

    def available(self) -> bool:
        return self._ok

    def send_key(self, key: str) -> bool:
        if not self._ok or self._ui is None:
            return False
        with self._lock:
            return self._inject(key)

    def _inject(self, key: str) -> bool:
        try:
            ev_name = self._KEY_TABLE.get(key) or self._KEY_TABLE.get(key.lower())
            if ev_name is None:
                log.warning("EvdevBackend: no mapping for key %r", key)
                return False
            code = self._ecodes.ecodes.get(ev_name)
            if code is None:
                log.warning("EvdevBackend: unknown evdev code %r", ev_name)
                return False
            EV_KEY = self._ecodes.EV_KEY
            self._ui.write(EV_KEY, code, 1)   # key down
            self._ui.syn()
            time.sleep(_PRESS_DURATION)
            self._ui.write(EV_KEY, code, 0)   # key up
            self._ui.syn()
            log.debug("EvdevBackend: sent key %r (%s=%d)", key, ev_name, code)
            return True
        except Exception as exc:
            log.error("EvdevBackend: inject failed for %r: %s", key, exc)
            return False

    def close(self) -> None:
        if self._ui:
            try:
                self._ui.close()
            except Exception:
                pass


# ── Windows backend (pydirectinput / ctypes SendInput) ───────────────────────

class _WinBackend(_Backend):
    """
    Injects DirectInput-compatible key events on Windows.

    Two sub-strategies, tried in order:
      1. ``pydirectinput`` — if installed, provides a clean API.
      2. Pure ``ctypes`` SendInput — zero-dependency fallback using
         KEYEVENTF_SCANCODE so the events reach DirectInput games.

    Key names are Virtual-Key or scan-code names accepted by pydirectinput,
    which follow the same convention as pyautogui (lowercase for letters,
    e.g. ``"j"``, ``"tab"``, ``"f1"``).
    """

    def __init__(self) -> None:
        self._lock  = threading.Lock()
        self._ok    = False
        self._mode  = "none"
        self._pdi   = None
        self._init()

    def _init(self) -> None:
        if sys.platform != "win32":
            return
        # Try pydirectinput first
        try:
            import pydirectinput as pdi
            pdi.PAUSE = _PRESS_DURATION
            self._pdi  = pdi
            self._mode = "pydirectinput"
            self._ok   = True
            log.info("ActionHandler: WinBackend ready (pydirectinput)")
            return
        except ImportError:
            pass
        # Fall back to raw ctypes SendInput
        try:
            import ctypes
            ctypes.windll.user32   # sanity-check that it exists
            self._ctypes = ctypes
            self._mode   = "ctypes"
            self._ok     = True
            log.info("ActionHandler: WinBackend ready (ctypes SendInput)")
        except Exception as exc:
            log.warning("ActionHandler: WinBackend init failed: %s", exc)

    def available(self) -> bool:
        return self._ok

    def send_key(self, key: str) -> bool:
        if not self._ok:
            return False
        with self._lock:
            if self._mode == "pydirectinput":
                return self._send_pdi(key)
            return self._send_ctypes(key)

    def _send_pdi(self, key: str) -> bool:
        try:
            self._pdi.press(key)
            log.debug("WinBackend(pdi): sent key %r", key)
            return True
        except Exception as exc:
            log.error("WinBackend(pdi): failed for %r: %s", key, exc)
            return False

    def _send_ctypes(self, key: str) -> bool:
        """
        Send a key via SendInput using scan codes (KEYEVENTF_SCANCODE).
        Scan-code delivery is required for DirectInput games.
        """
        try:
            import ctypes
            from ctypes import wintypes

            # Map key name to Virtual-Key code via VkKeyScanW
            vk = ctypes.windll.user32.VkKeyScanW(ord(key[0])) & 0xFF
            if vk == 0xFF:
                log.warning("WinBackend(ctypes): no VK for %r", key)
                return False

            # MapVirtualKey: VK → scan code
            scan = ctypes.windll.user32.MapVirtualKeyW(vk, 0)

            KEYEVENTF_SCANCODE = 0x0008
            KEYEVENTF_KEYUP    = 0x0002

            class KEYBDINPUT(ctypes.Structure):
                _fields_ = [
                    ("wVk",         wintypes.WORD),
                    ("wScan",       wintypes.WORD),
                    ("dwFlags",     wintypes.DWORD),
                    ("time",        wintypes.DWORD),
                    ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
                ]

            class INPUT(ctypes.Structure):
                class _INPUT(ctypes.Union):
                    _fields_ = [("ki", KEYBDINPUT)]
                _anonymous_ = ("_input",)
                _fields_    = [("type", wintypes.DWORD), ("_input", _INPUT)]

            INPUT_KEYBOARD = 1

            def _make_input(flags: int) -> INPUT:
                inp = INPUT()
                inp.type    = INPUT_KEYBOARD
                inp.ki.wVk  = 0
                inp.ki.wScan = scan
                inp.ki.dwFlags = flags
                return inp

            down = _make_input(KEYEVENTF_SCANCODE)
            up   = _make_input(KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP)

            ctypes.windll.user32.SendInput(1, ctypes.byref(down), ctypes.sizeof(INPUT))
            time.sleep(_PRESS_DURATION)
            ctypes.windll.user32.SendInput(1, ctypes.byref(up),   ctypes.sizeof(INPUT))
            log.debug("WinBackend(ctypes): sent key %r (vk=%d scan=%d)",
                      key, vk, scan)
            return True
        except Exception as exc:
            log.error("WinBackend(ctypes): failed for %r: %s", key, exc)
            return False


# ── Null backend (no-op fallback) ─────────────────────────────────────────────

class _NullBackend(_Backend):
    """
    No-op backend used when no functional platform backend is available.
    Logs a warning on first use so the operator knows action delivery is
    disabled.
    """

    def __init__(self) -> None:
        self._warned = False

    def available(self) -> bool:
        return False

    def send_key(self, key: str) -> bool:
        if not self._warned:
            log.warning(
                "ActionHandler: no usable key-injection backend is available "
                "on this platform/configuration.  Key actions will be ignored.\n"
                "  Linux X11:  pip install python-xlib  (DISPLAY must be set)\n"
                "  Linux any:  pip install evdev  (/dev/uinput must be writable)\n"
                "  Windows:    pip install pydirectinput")
            self._warned = True
        log.debug("NullBackend: dropped key %r", key)
        return False


# ── Public ActionHandler ──────────────────────────────────────────────────────

class ActionHandler:
    """
    OS-agnostic key-press dispatcher for the ED Agent.

    Instantiation probes for an available backend and selects it
    automatically.  Once constructed, call ``execute()`` from any thread.

    Parameters
    ----------
    config_dir : Path, optional
        Directory that contains (or will contain) ``bindings.json``.
        Defaults to ``~/.config/ed-assist/`` on Linux / macOS and
        ``%APPDATA%\\ed-assist\\`` on Windows.
    key_map : dict, optional
        Fully overrides the key map (bindings.json and defaults are ignored).
        Intended for unit tests.
    force_backend : _Backend, optional
        Inject a specific backend instance (testing only).
    """

    def __init__(
        self,
        config_dir:     Optional[Path] = None,
        key_map:        Optional[dict[str, str]] = None,
        force_backend:  Optional[_Backend] = None,
    ) -> None:
        self._key_map = self._load_key_map(config_dir, key_map)
        self._backend = force_backend or self._select_backend()

    # ── Public API ─────────────────────────────────────────────────────────

    def execute(self, action: str, key: str) -> bool:
        """
        Execute an action sent by an authenticated client.

        Parameters
        ----------
        action : str
            Action type — only ``"key_press"`` is currently supported.
        key : str
            Logical key name from the key map (e.g. ``"boost"``).

        Returns
        -------
        bool
            True if the key event was dispatched to the OS.
        """
        if action != "key_press":
            log.warning("ActionHandler: unsupported action type %r", action)
            return False

        platform_key = self._key_map.get(key)
        if platform_key is None:
            log.warning("ActionHandler: unknown logical key %r", key)
            return False

        log.info("ActionHandler: %s(%s) → %r", action, key, platform_key)
        return self._backend.send_key(platform_key)

    @property
    def backend_name(self) -> str:
        return type(self._backend).__name__

    @property
    def is_functional(self) -> bool:
        return self._backend.available()

    def key_map(self) -> dict[str, str]:
        """Return a copy of the current logical → platform key map."""
        return dict(self._key_map)

    @staticmethod
    def supported_platform() -> bool:
        """Return True if key simulation is supported on the current OS."""
        return sys.platform in ("win32", "linux")

    # ── Key-map loading ────────────────────────────────────────────────────

    @staticmethod
    def _load_key_map(
        config_dir: Optional[Path],
        override: Optional[dict[str, str]],
    ) -> dict[str, str]:
        """
        Build the key map by merging (in priority order):
          1. Hard-coded _DEFAULT_KEY_MAP
          2. bindings.json from config_dir (if it exists)
          3. ``override`` dict (if provided)
        """
        if override is not None:
            return dict(override)

        result = dict(_DEFAULT_KEY_MAP)

        if config_dir is not None:
            bindings_path = config_dir / "bindings.json"
            if bindings_path.exists():
                try:
                    custom: dict = json.loads(bindings_path.read_text())
                    if isinstance(custom, dict):
                        result.update(custom)
                        log.info("ActionHandler: loaded %d binding(s) from %s",
                                 len(custom), bindings_path)
                except Exception as exc:
                    log.warning("ActionHandler: could not read bindings.json: %s",
                                exc)

        return result

    @staticmethod
    def write_default_bindings(config_dir: Path) -> Path:
        """
        Write the default bindings to ``<config_dir>/bindings.json`` if the
        file does not already exist.

        Returns the path to the file.
        """
        path = config_dir / "bindings.json"
        if not path.exists():
            config_dir.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(_DEFAULT_KEY_MAP, indent=2))
            log.info("ActionHandler: wrote default bindings to %s", path)
        return path

    # ── Backend selection ──────────────────────────────────────────────────

    @staticmethod
    def _select_backend() -> _Backend:
        if sys.platform == "linux":
            return _ActionHandler__select_linux_backend()
        if sys.platform == "win32":
            b = _WinBackend()
            if b.available():
                return b
        return _NullBackend()


def _ActionHandler__select_linux_backend() -> _Backend:
    """
    Module-level helper (name-mangled style) to keep _select_backend static
    while allowing it to instantiate backends that need import-time probing.
    """
    # Prefer Xlib (XTEST) when a display is available
    if os.environ.get("DISPLAY"):
        b = _XlibBackend()
        if b.available():
            return b

    # Fall back to evdev / uinput
    b = _EvdevBackend()
    if b.available():
        return b

    return _NullBackend()
