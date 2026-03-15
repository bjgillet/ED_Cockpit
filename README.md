# ED Assist

**ED Assist** is a Python companion tool for **Elite Dangerous**.
It runs on the same machine as the game, reads its journal and status files in
real time, and streams live data over a secure WebSocket connection to one or
more client windows — on the same machine or anywhere on your local network.

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

**Key features:**

- **Live data panels** — fuel, hull, shields, cargo fill, lat/lon position,
  asteroid composition, refined ore tally, session timeline, and more,
  updated every second from `Status.json` and the ED journal.
- **One-click action buttons** — send signed key-press commands from any
  client machine directly to the game running on the agent machine.
- **Secure by design** — TLS transport, TOFU certificate pinning, HMAC-SHA256
  token authentication, and sequence-number replay protection.

---

## Documentation

| Document | Contents |
|---|---|
| [Doc/user-guide.md](Doc/user-guide.md) | Requirements, installation, quick start, configuration, key bindings, action buttons, troubleshooting |
| [Doc/architecture.md](Doc/architecture.md) | System design, thread model, project structure, security model, message protocol, extending the project |

---

## Quick launch

**Requirements:** Python 3.10+

```bash
# Agent machine (the PC running Elite Dangerous)
python agent/main.py

# Client machine (any OS on your LAN)
python client/main.py
```

See [Doc/user-guide.md](Doc/user-guide.md) for the full setup walkthrough.
