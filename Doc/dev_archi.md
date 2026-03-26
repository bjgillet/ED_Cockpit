# ED Cockpit — Architecture & Developer Guide

<p align="center">
  <img src="banner.svg" alt="Elite Dangerous Cockpit" width="900"/>
</p>

This document covers the internal design, module structure, security model,
message protocol, and extension points of ED Cockpit.
For installation and usage see [user-guide.md](user-guide.md).

---

## Table of contents

1. [System overview](#system-overview)
2. [Thread model](#thread-model)
3. [Project structure](#project-structure)
4. [Security model](#security-model)
5. [Message protocol](#message-protocol)
6. [Role system](#role-system)
7. [How to add a new role](#how-to-add-a-new-role)
8. [How to add a new action button](#how-to-add-a-new-action-button)
9. [Observer / subscriber APIs](#observer--subscriber-apis)

---

## System overview

ED Cockpit is split into two independent programs that communicate over a
secure WebSocket connection:

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

**Agent responsibilities:**
- Watch for the Elite Dangerous process and locate its journal / status files.
- Tail the journal file and push events through per-role filters.
- Poll `Status.json` every second and broadcast live ship/position data.
- Authenticate connecting clients and maintain their role assignments.
- Receive signed `ActionMessage` packets from clients and inject the
  corresponding key-press into the OS.

**Client responsibilities:**
- Connect to the agent over TLS WebSocket, authenticate, and receive the
  assigned role list.
- Display only the panels matching the assigned roles.
- Send signed key-press requests to the agent when the operator clicks an
  action button.

---

## Thread model

```
Agent side
──────────
Main thread        tkinter mainloop + all GUI windows
ED-ProcessScan     EDProcessWatcher — polls process list
ED-FileScan        EDProcessWatcher — watches journal directory
ED-JournalReader   JournalReader — tails the active journal file
ED-StatusReader    StatusReader — polls Status.json every second
ED-AsyncioLoop     asyncio event loop — runs WSServer

Client side
───────────
Main thread        tkinter mainloop + MainWindow
ED-ClientLoop      asyncio event loop — runs WSConnection
```

Cross-thread communication follows two patterns:

- **Background → tkinter**: data is put onto a `queue.Queue`; the GUI
  window drains it with `widget.after(N, poll_fn)`.
- **Background → asyncio**: `loop.call_soon_threadsafe()` or
  `asyncio.run_coroutine_threadsafe()`.

No tkinter widget is ever touched from outside the main thread.

---

## Project structure

```
ED_Cockpit/
├── agent/                        # ED Agent — runs on the ED machine
│   ├── main.py                   # Entry point; creates EDApp + GUI windows
│   ├── core/
│   │   ├── ed_app.py             # Central application object
│   │   ├── ed_process_watcher.py # Threaded process & file detector
│   │   ├── journal_reader.py     # Journal file tailer
│   │   ├── status_reader.py      # Status.json poller
│   │   └── action_handler.py     # OS-level key injection (multi-backend)
│   ├── network/
│   │   ├── ws_server.py          # Asyncio WebSocket server
│   │   ├── auth.py               # HMAC verification helpers
│   │   └── client_registry.py    # Persistent client → roles store (JSON)
│   ├── roles/
│   │   ├── base_role.py          # Abstract BaseRole (plugin contract)
│   │   ├── exobiology.py         # Exobiology event filter
│   │   ├── mining.py             # Mining event filter
│   │   ├── session.py            # Session monitoring filter
│   │   └── navigation.py         # Planet navigation filter
│   ├── security/
│   │   ├── tls_setup.py          # Self-signed TLS cert generator + helpers
│   │   └── tokens.py             # Token generation & hashing
│   └── GUI/
│       ├── ed_status_monitor.py  # Process / file detection window
│       ├── client_manager.py     # Client table, Add/Edit/Revoke dialogs,
│       │                         # ACTION LOG strip
│       ├── activity_bar.py       # Activity selector widget
│       └── icons_b64.py          # Embedded base64 PNG icons
│
├── client/                       # ED Client — runs anywhere
│   ├── main.py                   # Entry point; creates EDClient + MainWindow
│   ├── core/
│   │   ├── ed_client.py          # Central client object; owns WSConnection
│   │   └── config.py             # client.json loader / saver
│   ├── network/
│   │   └── ws_connection.py      # Asyncio WebSocket connection + reconnect
│   ├── roles/
│   │   ├── base_panel.py         # Abstract BasePanel (plugin contract)
│   │   ├── exobiology_panel.py   # BioScan table panel
│   │   ├── mining_panel.py       # Asteroid / refined cargo panel
│   │   ├── session_panel.py      # Session monitor + fuel/hull/shield bars
│   │   └── navigation_panel.py   # Surface nav + DSS signals panel
│   └── GUI/
│       ├── main_window.py        # Root window — ActivityBar + panel switcher
│       ├── activity_bar.py       # Role icon bar
│       └── icons_b64.py          # Embedded base64 PNG icons
│
├── shared/                       # Code imported by both agent and client
│   ├── messages.py               # Message dataclasses + HMAC helpers
│   ├── roles_def.py              # Canonical role name constants
│   └── version.py                # Protocol version string
│
├── tools/
│   └── gen_cert.py               # One-time TLS certificate generator
│
├── Doc/
│   ├── user-guide.md             # Installation, configuration, usage
│   └── architecture.md           # This file
│
├── pyproject.toml                # Package metadata + entry points
├── requirements-agent.txt        # Agent pip dependencies (quick reference)
├── requirements-client.txt       # Client pip dependencies (quick reference)
└── README.md                     # Landing page
```

---

## Security model

| Layer | Mechanism |
|---|---|
| Transport | TLS 1.2+ with a self-signed certificate generated on first run |
| Certificate trust | Trust On First Use (TOFU) — fingerprint pinned in `client.json` after first successful connect |
| Client authentication | Pre-shared token (256-bit random) stored as a bcrypt/SHA-256 hash in `clients.json` on the agent |
| Action integrity | HMAC-SHA256 over `client_id:seq:action:key` using the raw token as the key |
| Replay protection | Monotonically increasing per-client sequence number — the agent rejects any `seq` that is not strictly greater than the last accepted value |

### Handshake flow

```
Client                              Agent
  │                                   │
  │── TLS connect ──────────────────► │
  │── RegisterMessage ───────────────► │  {client_id, token, proposed_roles}
  │                                   │  verify token hash
  │◄── WelcomeMessage ────────────── │  {assigned_roles, protocol_version}
  │                                   │
  │  (bidirectional message flow)     │
  │◄── EventMessage ───────────────── │  journal / status events
  │── ActionMessage ────────────────► │  {action, key, seq, hmac}
```

### Action HMAC computation

The HMAC payload is the UTF-8 string:

```
client_id:seq:action:key
```

The key is the **raw pre-shared token** (not the hash stored on the agent).
Both sides compute the HMAC independently; `hmac.compare_digest` is used for
timing-safe comparison.

The helper is in `shared/messages.py` so the client can sign messages without
importing any agent-side package:

```python
from shared.messages import compute_action_hmac

hmac_val = compute_action_hmac(client_id, seq, "key_press", "boost", token)
```

---

## Message protocol

All messages are JSON objects with a `type` discriminator field.
Defined in `shared/messages.py`.

### Agent → Client

| Type | Class | Description |
|---|---|---|
| `welcome` | `WelcomeMessage` | Sent after successful authentication. Contains `assigned_roles` and `protocol_version`. |
| `event` | `EventMessage` | Carries a single game event. Contains `role`, `event`, `timestamp`, `data`. |
| `roles_updated` | `RolesUpdatedMessage` | Sent when the operator changes a client's roles at runtime. |
| `error` | `ErrorMessage` | Protocol error. `fatal=true` means the agent closes the connection. |

### Client → Agent

| Type | Class | Description |
|---|---|---|
| `register` | `RegisterMessage` | First message after TLS connect. Contains `client_id`, `token`, `proposed_roles`. |
| `action` | `ActionMessage` | Key-press request. Contains `action`, `key`, `seq`, `hmac`. |

### Example round-trip

```python
from shared.messages import EventMessage, message_from_json

msg = EventMessage(
    role="exobiology",
    event="ScanOrganic",
    timestamp="2026-03-14T12:00:00Z",
    data={"species": "Bacterium Nebulus"},
)
raw = msg.to_json()          # → JSON string
received = message_from_json(raw)   # → EventMessage instance
```

---

## Role system

A **role** is a named filter that decides which journal / status events are
relevant and what data to include in the `EventMessage` sent to subscribed
clients.

### Agent side — `BaseRole`

Located in `agent/roles/base_role.py`:

```python
class BaseRole(ABC):
    name: str = ""
    journal_events: frozenset[str] = frozenset()

    @abstractmethod
    def filter(self, event_name: str, data: dict) -> dict | None:
        """Return filtered data dict, or None to drop the event."""

    def filter_status(self, status: dict) -> dict | None:
        """Return filtered status data, or None to suppress."""
        return None
```

Roles are registered in `agent/roles/__init__.py` and instantiated once by
`EDApp`.  No other changes are needed anywhere else.

### Client side — `BasePanel`

Located in `client/roles/base_panel.py`:

```python
class BasePanel(ttk.Frame, ABC):
    role_name: str = ""

    @abstractmethod
    def _build_ui(self) -> None: ...

    @abstractmethod
    def on_event(self, event: str, data: dict) -> None: ...

    def send_action(self, action: str, key: str) -> None:
        """Send a key-press to the agent (if action_callback is set)."""
        if self._action_cb:
            self._action_cb(action, key)
```

Panels are registered in `client/roles/__init__.py`.

The `action_callback` is injected by `MainWindow` as `client.send_action`,
which signs the message with HMAC and puts it on the outbound queue.

---

## How to add a new role

### 1 — Define the role name constant

In `shared/roles_def.py`, add to the `Role` namespace and `ALL_ROLES` tuple:

```python
class Role:
    EXOBIOLOGY:  str = "exobiology"
    # ...
    MY_ROLE:     str = "my_role"       # add this

ALL_ROLES: tuple[str, ...] = (
    Role.EXOBIOLOGY,
    # ...
    Role.MY_ROLE,                      # add this
)
```

### 2 — Create the agent-side filter

Create `agent/roles/my_role.py`:

```python
from agent.roles.base_role import BaseRole
from shared.roles_def import Role

class MyRole(BaseRole):
    name = Role.MY_ROLE
    journal_events = frozenset({"SomeJournalEvent", "AnotherEvent"})

    def filter(self, event_name: str, data: dict) -> dict | None:
        # Return a dict to forward, or None to drop
        return {"key": data.get("Key")}

    def filter_status(self, status: dict) -> dict | None:
        # Optional — return status fields relevant to this role
        return None
```

Register it in `agent/roles/__init__.py`:

```python
from agent.roles.my_role import MyRole

_ROLES = {
    # existing roles ...
    MyRole.name: MyRole(),
}
```

### 3 — Create the client-side panel

Create `client/roles/my_role_panel.py`:

```python
import tkinter as tk
from client.roles.base_panel import BasePanel
from shared.roles_def import Role

class MyRolePanel(BasePanel):
    role_name = Role.MY_ROLE

    def _build_ui(self) -> None:
        # Build tkinter widgets here
        ...

    def on_event(self, event: str, data: dict) -> None:
        # Update widgets from incoming event data
        ...
```

Register it in `client/roles/__init__.py`:

```python
from client.roles.my_role_panel import MyRolePanel

_PANELS = {
    # existing panels ...
    MyRolePanel.role_name: MyRolePanel,
}
```

### 4 — Add an icon (optional)

Add a 32×32 PNG base64 string to both `agent/GUI/icons_b64.py` and
`client/GUI/icons_b64.py`, then add the attribute name to `_ROLE_ICON_ATTR`
in `client/GUI/main_window.py`.

That is all — no changes to `EDApp`, `WSServer`, `EDClient`, or `MainWindow`
are required.

---

## How to add a new action button

### 1 — Add the key binding (agent machine)

In `~/.config/ed-cockpit/bindings.json` (Linux) or
`%APPDATA%\ed-cockpit\bindings.json` (Windows):

```json
{
  "my_action": "F5"
}
```

Restart the agent to pick up the change.

### 2 — Add the button to the panel

In the relevant `client/roles/<role>_panel.py`, inside `_build_ui()`:

```python
_BTN = dict(bg="#1a1a3a", fg=ACCENT, activebackground="#2a2a5a",
            activeforeground=TEXT_FG, relief="flat", bd=0,
            font=FONT_BODY, cursor="hand2", padx=8, pady=4)

row = tk.Frame(acts, bg=PANEL_BG)
row.pack(fill="x", padx=4, pady=(0, 4))

tk.Button(row, text="My Action",
          command=lambda: self.send_action("key_press", "my_action"),
          **_BTN).pack(side="left", padx=3)
```

`send_action("key_press", "my_action")` is inherited from `BasePanel`.  It
signs the message with HMAC + sequence number and sends it to the agent via
the existing WebSocket connection.

---

## Observer / subscriber APIs

### `EDApp` — watcher state (agent GUI)

```python
q = app.subscribe()         # returns queue.Queue
# q receives a state dict on every EDProcessWatcher update
app.unsubscribe(q)
```

### `EDApp` — action observer (agent GUI)

```python
def on_action(client_id: str, action: str, key: str) -> None:
    my_queue.put_nowait((client_id, action, key))

app.subscribe_actions(on_action)    # called from asyncio thread — use a queue
app.unsubscribe_actions(on_action)
```

### `EDClient` — role events (client panels)

```python
q = client.subscribe_role("exobiology")
# q receives {"event": "<name>", "data": {...}} dicts
client.unsubscribe_role("exobiology", q)
```

### `EDClient` — connection status (client GUI)

```python
q = client.subscribe_status()
# q receives status dicts:
#   {"status": "connecting"}
#   {"status": "connected",  "roles": [...]}
#   {"status": "disconnected"}
#   {"status": "auth_failed", "message": "..."}
#   {"status": "roles_updated", "roles": [...]}
#   {"status": "cert_pinned",  "fingerprint": "..."}
client.unsubscribe_status(q)
```
