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
# psutil, websockets, cryptography
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
| Windows | `EliteDangerous64.exe` by name | `pydirectinput` / `SendInput` (DirectInput) |
| Linux (Proton) | cmdline scan for `EliteDangerous*.exe` | `python-xlib` or `evdev` |
