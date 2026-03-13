# ED Assist

A Python companion tool for **Elite Dangerous** that detects the running game
instance, locates its journal and status files, and provides a modular GUI
built with tkinter/ttk.

## Features

- **Automatic game detection** — finds the Elite Dangerous process on Windows
  (native) and Linux (Wine / Steam Proton) without any manual configuration.
- **Journal & Status file discovery** — locates the active `Journal.*.log` and
  `Status.json` files by inspecting the game process's open file handles.
- **Decoupled architecture** — backend services run independently of any GUI
  window; multiple windows can observe the same live state simultaneously.
- **Cross-platform** — works on Windows and Linux.

## Project structure

```
ED_Assist/
├── main.py                  # Application entry point
├── ed_app.py                # Central app core — owns all backend services
├── ed_process_watcher.py    # Threaded ED process & file detector
└── GUI/
    ├── ed_status_monitor.py # Process / file detection status window
    ├── activity_bar.py      # In-game activity selector widget
    ├── bioscan_table.py      # Exobiology scan table widget
    └── icons_b64.py         # Embedded base64 icons
```

## Requirements

- Python 3.10+
- [psutil](https://pypi.org/project/psutil/)

```bash
pip install psutil
```

## Running

```bash
python main.py
```

The status monitor window opens automatically and begins searching for a
running Elite Dangerous instance.

## Architecture overview

```
EDApp  (ed_app.py)
 └── EDProcessWatcher  (ed_process_watcher.py)
      ├── Thread: ED-ProcessScan  — detects the game process (every 5 s)
      └── Thread: ED-FileScan    — locates journal/status files (every 5 s)

GUI windows subscribe to EDApp via a thread-safe queue.Queue.
Closing a window unsubscribes it; the backend keeps running.
```

## Platform notes

| Platform | Process detection | Journal path |
|---|---|---|
| Windows | `EliteDangerous64.exe` by name | `%USERPROFILE%\Saved Games\Frontier Developments\Elite Dangerous\` |
| Linux (Proton) | cmdline scan for `EliteDangerous*.exe` | Wine prefix inside the Steam compatdata directory |
