# ED Assist

A Python companion tool for **Elite Dangerous** that monitors the running
game, extracts events and data from its journal and status files, and streams
them over a secure WebSocket connection to one or more client programs that
can run on the same machine or any other platform.

---

## Architecture

The project is split into two distinct programs:

```
┌──────────────────────────────────────────────────────────────────┐
│                     ED Agent  (agent/)                           │
│  Runs on the same machine as Elite Dangerous                     │
│                                                                  │
│  EDProcessWatcher ──► JournalReader ──► Role filters ──►         │
│  StatusReader                          WebSocket server          │
│                        ▲                     │                   │
│                        │ key simulation       │ TLS + PSK auth   │
│              ActionHandler ◄─────────────────┘                   │
└──────────────────────────────────────────────────────────────────┘
              │                          │
        (localhost)               (LAN / network)
              │                          │
┌─────────────▼────────┐    ┌────────────▼──────────────┐
│  Local Client GUI    │    │  Remote Client (any OS)   │
│  client/             │    │  client/                  │
│  tkinter panels      │    │  tkinter panels           │
└──────────────────────┘    └───────────────────────────┘
```

The **agent** watches the game, filters events by role, and pushes them to
subscribed clients. Clients can also send key-press actions back to the agent.

The **client** is a single program that works both locally and remotely.
At startup it connects to the agent and receives its assigned role list;
only the matching panels are shown.

---

## Project structure

```
ED_Assist/
├── agent/                        # ED Agent — runs on the ED machine
│   ├── main.py                   # Agent entry point
│   ├── core/
│   │   ├── ed_app.py             # Agent core — owns all backend services
│   │   ├── ed_process_watcher.py # Threaded ED process & file detector
│   │   ├── journal_reader.py     # Journal file tailer
│   │   ├── status_reader.py      # Status.json poller
│   │   └── action_handler.py     # Key-press simulator (OS-specific)
│   ├── network/
│   │   ├── ws_server.py          # Asyncio WebSocket server
│   │   ├── auth.py               # Token & HMAC verification
│   │   └── client_registry.py    # Persistent client → roles store
│   ├── roles/
│   │   ├── base_role.py          # Abstract role (plugin contract)
│   │   ├── exobiology.py         # Exobiology event filter
│   │   ├── mining.py             # Mining event filter
│   │   ├── session.py            # Session monitoring filter
│   │   └── navigation.py         # Planet navigation filter
│   ├── security/
│   │   ├── tls_setup.py          # Self-signed TLS cert generator
│   │   └── tokens.py             # Token generation & hashing
│   └── GUI/
│       ├── ed_status_monitor.py  # Process / file detection window
│       ├── client_manager.py     # Connected clients & role assignment
│       ├── activity_bar.py       # Activity selector widget
│       └── icons_b64.py          # Embedded base64 icons
│
├── client/                       # ED Client — runs anywhere
│   ├── main.py                   # Client entry point
│   ├── core/
│   │   ├── ed_client.py          # Client core — WebSocket + dispatch
│   │   └── config.py             # Client_ID, token, agent address
│   ├── network/
│   │   └── ws_connection.py      # Asyncio WebSocket connection manager
│   ├── roles/
│   │   ├── base_panel.py         # Abstract panel (plugin contract)
│   │   ├── exobiology_panel.py   # Exobiology panel (BioScan table)
│   │   ├── mining_panel.py       # Mining panel
│   │   ├── session_panel.py      # Session monitoring panel
│   │   └── navigation_panel.py   # Planet navigation panel
│   └── GUI/
│       ├── main_window.py        # Root window — ActivityBar + panels
│       ├── activity_bar.py       # Activity selector widget
│       └── icons_b64.py          # Embedded base64 icons
│
├── shared/                       # Code shared by agent and client
│   ├── messages.py               # Message envelope dataclasses
│   ├── roles_def.py              # Canonical role name constants
│   └── version.py                # Protocol version
│
├── tools/
│   └── gen_cert.py               # One-time TLS cert generator
│
├── requirements-agent.txt        # Agent pip dependencies
├── requirements-client.txt       # Client pip dependencies
└── README.md
```

---

## Requirements

- Python 3.10+

**Agent machine:**

```bash
pip install -r requirements-agent.txt
# psutil, websockets, cryptography (always required)
```

**Key injection — Linux (install at least one):**

```bash
# Preferred — X11 / Wine / Proton with DISPLAY set:
pip install python-xlib

# Alternative — Wayland or headless (needs /dev/uinput access):
pip install evdev
sudo usermod -aG input $USER   # then log out and back in
```

**Key injection — Windows:**

```bash
# Preferred — DirectInput-compatible (required for ED):
pip install pydirectinput

# If pydirectinput is not installed, a zero-dependency ctypes fallback
# using KEYEVENTF_SCANCODE is used automatically.
```

**Client machine:**

```bash
pip install -r requirements-client.txt
# websockets, cryptography
```

---

## Quick start

### 1 — Generate a TLS certificate (once, on the agent machine)

```bash
python tools/gen_cert.py
```

Certificate and key are written to `~/.config/ed-assist/` (Linux) or
`%APPDATA%\ed-assist\` (Windows).

### 2 — Run the agent

```bash
python agent/main.py
```

The status monitor window opens. Start Elite Dangerous — the agent detects
it automatically.

### 3 — Create a client record on the agent

In the agent's Client Manager window, click **Add Client**.  A Client_ID and
one-time token are generated and displayed.

### 4 — Run the client

Copy the token to the client machine's `~/.config/ed-assist/client.json`,
then:

```bash
python client/main.py
```

The client connects, receives its role assignment, and opens the matching
panels.

---

## Key bindings

On first run the agent writes `~/.config/ed-assist/bindings.json` (Linux) or
`%APPDATA%\ed-assist\bindings.json` (Windows) with the default key map.
Edit this file to customise which physical key each logical action triggers.

```json
{
  "boost":             "Tab",
  "next_firegroup":    "bracketright",
  "prev_firegroup":    "bracketleft",
  "landing_gear":      "l",
  "deploy_hardpoints": "u",
  "hyperspace_jump":   "j",
  "galaxy_map":        "m",
  "system_map":        "comma",
  "enter_fss":         "backslash",
  "...":               "..."
}
```

Key values on Linux are X11 keysym names (e.g. `Tab`, `bracketright`,
`KP_Add`).  On Windows they are Virtual-Key names accepted by
`pydirectinput` (e.g. `tab`, `]`, `[`).

The client sends the **logical name** (`"boost"`, `"landing_gear"`, …);
the agent resolves it to the physical key locally, so the bindings file
only needs to exist on the agent machine.

---

## Action buttons

The **Navigation** and **Mining** client panels include a **QUICK ACTIONS**
strip with one-click buttons that send a signed key-press to the agent:

| Panel | Buttons |
|---|---|
| Navigation | Hyperspace Jump · Boost · Galaxy Map · System Map · Landing Gear · Recall/Dismiss Ship |
| Mining | Prev/Next Firegroup · Deploy Hardpoints · Cargo Scoop · Enter FSS · Boost |

Each click is authenticated with HMAC-SHA256 and a monotonically increasing
sequence number (replay protection).  The agent's **Client Manager** window
shows a live **ACTION LOG** at the bottom listing the last 6 actions received,
with timestamp, client label, and key name.

To add a button for a key that is not in the default list:

1. Add the mapping to `bindings.json` on the agent machine:
   ```json
   { "my_action": "F5" }
   ```
2. In the relevant client panel, add a button:
   ```python
   tk.Button(row, text="My Action",
             command=lambda: self.send_action("key_press", "my_action"),
             **_BTN).pack(side="left", padx=3)
   ```

---

## Security

| Layer | Mechanism |
|---|---|
| Transport | TLS (self-signed cert, TOFU pinning) |
| Client authentication | Pre-shared token (HMAC-SHA256 comparison) |
| Action integrity | HMAC-SHA256 + sequence number (replay protection) |

---

## Adding a new role

**Agent side** — create `agent/roles/<name>.py`:

```python
from agent.roles.base_role import BaseRole
from shared.roles_def import Role

class MyRole(BaseRole):
    name = "my_role"
    journal_events = frozenset({"SomeJournalEvent"})

    def filter(self, event_name: str, data: dict) -> dict | None:
        return data   # or None to drop the event
```

Register it in `agent/roles/__init__.py` — no other changes needed.

**Client side** — create `client/roles/<name>_panel.py`:

```python
from client.roles.base_panel import BasePanel

class MyRolePanel(BasePanel):
    role_name = "my_role"

    def _build_ui(self) -> None: ...
    def on_event(self, event: str, data: dict) -> None: ...
```

Register it in `client/roles/__init__.py`.

Then add the constant to `shared/roles_def.py`.

---

## Platform notes

| Platform | Process detection | Key simulation |
|---|---|---|
| Windows | `EliteDangerous64.exe` by name | `pydirectinput` (preferred) → `ctypes SendInput` (KEYEVENTF_SCANCODE fallback) |
| Linux / Proton (X11) | cmdline scan for `EliteDangerous*.exe` | `python-xlib` XTEST (preferred) |
| Linux / Proton (Wayland / headless) | cmdline scan for `EliteDangerous*.exe` | `evdev` UInput (`/dev/uinput` must be writable) |

The active backend is logged at agent startup:

```
INFO  ActionHandler: using backend _XlibBackend
```

If no backend is available a warning is printed and key actions are dropped
(all other functionality — monitoring, event streaming, panels — continues
to work normally).

---

## Installation (optional)

To install both programs as proper Python packages with `ed-agent` /
`ed-client` entry-point scripts:

```bash
# From the repo root — installs in editable mode
pip install -e ".[agent-linux]"   # Linux (X11 + evdev)
pip install -e ".[agent-windows]" # Windows
pip install -e ".[client]"        # Client machine

# Then launch with:
ed-agent
ed-client
```

Without installation, the programs can always be run directly:

```bash
python agent/main.py
python client/main.py
```

---

## Troubleshooting

### Key actions are received by the agent but not delivered to the game

- **Linux X11**: make sure `DISPLAY` is set in the terminal where the agent
  runs (`echo $DISPLAY` should print `:0` or similar) and that
  `python-xlib` is installed.
- **Linux Wayland / headless**: install `evdev` and ensure `/dev/uinput` is
  writable (`sudo usermod -aG input $USER`, then log out and back in).
- **Windows**: install `pydirectinput` (`pip install pydirectinput`).  The
  ctypes fallback works for most games but pydirectinput is more reliable
  for DirectInput titles like Elite Dangerous.
- Check the agent log for the line `ActionHandler: using backend …` — if it
  says `_NullBackend`, no injection backend could be initialised.

### Client cannot connect — TLS fingerprint mismatch

The client pins the agent's certificate fingerprint on first connect (TOFU).
If you regenerated the agent's TLS certificate, delete the pinned fingerprint
from the client config:

```bash
# Linux
nano ~/.config/ed-assist/client.json   # remove the "cert_fingerprint" line
# Windows
notepad %APPDATA%\ed-assist\client.json
```

The client will re-pin on the next successful connection.

### Client connects but receives no events

- Verify the client's assigned roles in the **Client Manager** window (the
  agent only forwards events to clients whose role list includes the matching
  role).
- Check that Elite Dangerous is actually running and detected (the
  **Status Monitor** window on the agent shows the detection state).

### `bindings.json` not found / key not recognised

The agent writes `bindings.json` on first run.  If a button press produces
a log line like `ActionHandler: unknown logical key 'my_action'`, add the
mapping to `bindings.json` and restart the agent.
