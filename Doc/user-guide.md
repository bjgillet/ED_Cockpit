# ED Assist — User Guide

This guide covers everything you need to install, configure, and use ED Assist
as a player.  For architecture and development documentation see
[architecture.md](architecture.md).

---

## Table of contents

1. [Requirements](#requirements)
2. [Installation](#installation)
3. [Quick start](#quick-start)
4. [Client configuration reference](#client-configuration-reference)
5. [Key bindings](#key-bindings)
6. [Action buttons](#action-buttons)
7. [Platform notes](#platform-notes)
8. [Troubleshooting](#troubleshooting)

---

## Requirements

- **Python 3.10 or later** on every machine that runs the agent or client.
- **tkinter** — ships with the standard Python installer on Windows and macOS.
  On minimal Linux installs it may need to be added separately:

  ```bash
  # Fedora / RHEL
  sudo dnf install python3-tkinter

  # Debian / Ubuntu
  sudo apt install python3-tk
  ```

---

## Installation

### Agent machine (the PC running Elite Dangerous)

**Option A — install as a package (adds the `ed-agent` command):**

```bash
# Linux — X11 / Proton
pip install -e ".[agent-linux]"

# Windows
pip install -e ".[agent-windows]"
```

**Option B — install dependencies only (run with `python` directly):**

```bash
# All platforms
pip install psutil websockets cryptography

# Windows — add DirectInput key injection support
pip install pydirectinput

# Linux — add X11 key injection support
pip install python-xlib

# Linux — add Wayland / headless key injection support (needs /dev/uinput)
pip install evdev
sudo usermod -aG input $USER   # then log out and back in
```

`pydirectinput` is strongly recommended on Windows because Elite Dangerous uses
DirectInput.  If it is not installed the agent falls back to a pure-`ctypes`
SendInput implementation which may be less reliable.

### Client machine (any OS on your LAN)

**Option A — install as a package (adds the `ed-client` command):**

```bash
pip install -e ".[client]"
```

**Option B — install dependencies only:**

```bash
pip install websockets cryptography
```

---

## Quick start

### Step 1 — Generate a TLS certificate (once, on the agent machine)

```bash
python tools/gen_cert.py
```

The certificate and private key are written to:

| Platform | Location |
|---|---|
| Linux / macOS | `~/.config/ed-assist/` |
| Windows | `%APPDATA%\ed-assist\` |

The script also prints a **fingerprint** — keep it handy for the client setup.

### Step 2 — Run the agent

```bash
# If installed via pip:
ed-agent

# Or directly:
python agent/main.py          # Linux / macOS
python agent\main.py          # Windows
```

Two windows open: the **Status Monitor** (shows whether Elite Dangerous is
detected) and the **Client Manager** (manages connected clients).

Start Elite Dangerous — the agent detects it automatically within a few
seconds and begins reading its journal and status files.

### Step 3 — Create a client record

In the **Client Manager** window, click **+ Add Client**:

1. Give the client a label (e.g. `Tablet` or `Second screen`).
2. Tick the roles you want this client to see.
3. Click **⎘ Copy token + ID** — this puts both values on your clipboard.
4. Click **✔ Add Client** to save.

> The raw token is shown **once only**.  Copy it before closing the dialog.
> If you lose it, revoke the client and create a new one.

### Step 4 — Configure and run the client

On the client machine, create the config file at the path for your platform:

| Platform | Path |
|---|---|
| Linux / macOS | `~/.config/ed-assist/client.json` |
| Windows | `%APPDATA%\ed-assist\client.json` |

Paste in the values from the clipboard:

```json
{
  "client_id":        "ed-client-XXXX",
  "token":            "<token from Add Client dialog>",
  "agent_host":       "192.168.1.10",
  "agent_port":       5759,
  "tls_enabled":      true,
  "cert_fingerprint": "",
  "ca_cert_path":     ""
}
```

Replace `192.168.1.10` with the LAN IP of the agent machine
(`ipconfig` on Windows, `ip a` on Linux).

Leave `cert_fingerprint` empty — it is written automatically on first
connection (Trust On First Use).

Then launch the client:

```bash
# If installed via pip:
ed-client

# Or directly:
python client/main.py          # Linux / macOS
python client\main.py          # Windows
```

The window shows "Connecting…", then the panels for your assigned roles appear.
On the agent, the **Client Manager** table shows your client with a green
**●** Online dot.

---

## Client configuration reference

All fields in `client.json`:

| Field | Type | Default | Description |
|---|---|---|---|
| `client_id` | string | auto-generated | Unique identifier assigned by the agent. Must match the record in the agent's `clients.json`. |
| `token` | string | — | Pre-shared authentication token. Shown once in the Add Client dialog. |
| `agent_host` | string | `localhost` | Hostname or IP address of the agent machine. |
| `agent_port` | integer | `5759` | WebSocket port the agent listens on. |
| `tls_enabled` | bool | `true` | Whether to use TLS for the connection. Disable only for local testing. |
| `cert_fingerprint` | string | `""` | SHA-256 fingerprint of the agent's TLS certificate. Written automatically on first connect. Clear it if you regenerate the agent certificate. |
| `ca_cert_path` | string | `""` | Optional path to a copy of `agent.crt` on this machine. Use instead of fingerprint pinning if you prefer file-based verification. |

---

## Key bindings

On first run the agent writes `~/.config/ed-assist/bindings.json` (Linux) or
`%APPDATA%\ed-assist\bindings.json` (Windows) with the default key map.
Edit this file to customise which physical key each logical action triggers.

```json
{
  "hyperspace_jump":   "j",
  "boost":             "Tab",
  "next_firegroup":    "bracketright",
  "prev_firegroup":    "bracketleft",
  "landing_gear":      "l",
  "deploy_hardpoints": "u",
  "cargo_scoop":       "Home",
  "galaxy_map":        "m",
  "system_map":        "comma",
  "enter_fss":         "backslash",
  "recall_dismiss_ship": "Home"
}
```

Key name format:

| Platform | Format | Examples |
|---|---|---|
| Linux (X11) | X11 keysym name | `Tab`, `bracketright`, `KP_Add`, `j` |
| Windows | VK name for `pydirectinput` | `tab`, `]`, `[`, `j` |

The client sends **logical names** (e.g. `"boost"`); the agent resolves them
to physical keys locally.  The bindings file only needs to exist on the agent
machine.

To add a custom binding, add a new entry and restart the agent:

```json
{
  "my_custom_action": "F5"
}
```

---

## Action buttons

The **Navigation** and **Mining** panels include a **QUICK ACTIONS** strip that
sends signed key-press commands directly to the agent machine:

| Panel | Buttons |
|---|---|
| Navigation | Hyperspace Jump · Boost · Galaxy Map · System Map · Landing Gear · Recall/Dismiss Ship |
| Mining | Prev Firegroup · Next Firegroup · Deploy Hardpoints · Cargo Scoop · Enter FSS · Boost |

Each button click is authenticated with HMAC-SHA256 and a sequence number
(replay protection).  The agent's **Client Manager** window shows a live
**ACTION LOG** at the bottom listing the last 6 actions with timestamp,
client label, and key name.

---

## Platform notes

| Platform | Game process detection | Key injection |
|---|---|---|
| Windows | `EliteDangerous64.exe` by process name | `pydirectinput` (preferred) → `ctypes SendInput` with `KEYEVENTF_SCANCODE` fallback |
| Linux / Proton (X11) | Command-line scan for `EliteDangerous*.exe` | `python-xlib` XTEST extension |
| Linux / Proton (Wayland or headless) | Command-line scan for `EliteDangerous*.exe` | `evdev` UInput (`/dev/uinput` must be writable) |

The active key-injection backend is logged at agent startup:

```
INFO  ActionHandler: using backend _XlibBackend
```

If no backend is available the agent logs a warning and drops all key actions.
All other functionality (monitoring, streaming, panels) continues normally.

### Firewall (Windows)

The agent listens on TCP port **5759**.  Windows Defender Firewall will prompt
on first run — allow it on **Private networks**.  To add the rule manually:

```powershell
netsh advfirewall firewall add rule name="ED Assist Agent" ^
      dir=in action=allow protocol=TCP localport=5759
```

---

## Troubleshooting

### Client window stays on "Connecting…"

- Verify `agent_host` in `client.json` is the correct LAN IP of the agent
  machine (`ipconfig` on Windows, `ip a` on Linux).
- Confirm the agent is running and its **Status Monitor** window is open.
- Check the Windows Firewall allows port 5759 (see [Firewall](#firewall-windows)
  above).
- Try pinging the agent machine from the client machine.

### "Auth failed" message on the client

The `client_id` or `token` in `client.json` does not match the record stored
on the agent.

- Re-copy the values from the agent's `clients.json`
  (`%APPDATA%\ed-assist\clients.json` on Windows,
  `~/.config/ed-assist/clients.json` on Linux).
- If the token was lost, revoke the client in **Client Manager** and add it
  again to generate a new token.

### TLS fingerprint mismatch

The client pins the agent certificate on first connect.  If you regenerate the
agent's TLS certificate, the stored fingerprint becomes invalid.

Clear it from `client.json`:

```bash
# Linux
nano ~/.config/ed-assist/client.json   # remove or empty "cert_fingerprint"

# Windows
notepad %APPDATA%\ed-assist\client.json
```

The client will re-pin on the next successful connection.

### Client connects but receives no events

- Verify the client's assigned roles in the **Client Manager** window — the
  agent only forwards events to clients whose role list includes the matching
  role.
- Check that Elite Dangerous is detected: the **Status Monitor** window on the
  agent machine should show **COMPLETE** (green).

### Key actions received by agent but not delivered to the game

- **Linux X11**: confirm `DISPLAY` is set in the terminal running the agent
  (`echo $DISPLAY` should print `:0` or similar) and `python-xlib` is
  installed.
- **Linux Wayland / headless**: install `evdev` and ensure `/dev/uinput` is
  writable (`sudo usermod -aG input $USER`, then log out and back in).
- **Windows**: install `pydirectinput` (`pip install pydirectinput`).
- Check the agent log — if the line reads
  `ActionHandler: using backend _NullBackend`, no injection backend
  could be initialised.

### `bindings.json` key not recognised

If a button press logs `ActionHandler: unknown logical key 'my_action'`, add
the mapping to `bindings.json` on the agent machine and restart the agent.
