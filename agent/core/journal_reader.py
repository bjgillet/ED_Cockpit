"""
ED Assist — Journal Reader
============================
Tails the active Elite Dangerous journal file and emits parsed JSON events
to a registered callback as they are appended.

Design
------
  The journal is a plain-text file where each line is a JSON object with at
  least the fields ``{"timestamp": "...", "event": "..."}``.  ED appends lines
  continuously while the game is running and creates a new file on each game
  start.

  The reader runs in a background thread and:
    1. Opens the journal file at the path set via ``set_path()``.
    2. Seeks to the end so historical entries are skipped on the first open.
    3. Polls for new lines at ``POLL_INTERVAL`` seconds.
    4. Parses each new line as JSON and calls ``on_event(event_name, data)``.
    5. If the journal path changes (new game session), it transparently
       re-opens the new file and seeks to its end.
    6. If the file becomes unavailable (game closed mid-session), it waits
       and retries silently.

  Thread safety: ``on_event`` is called from the reader thread.  Callers
  must dispatch to the GUI or asyncio layer themselves (via queue + after()
  or loop.call_soon_threadsafe()).

Dependencies
------------
  Standard library only.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Callable, Optional

POLL_INTERVAL: float = 1.0   # seconds between tail polls


class JournalReader:
    """
    Background journal file tailer.

    Parameters
    ----------
    on_event : callable(event_name: str, data: dict)
        Called for each new journal line parsed successfully.
        Invoked from the reader thread — callers are responsible for
        thread-safe dispatch to GUI or asyncio code.
    """

    def __init__(
        self,
        on_event: Callable[[str, dict], None],
    ) -> None:
        self._on_event  = on_event
        self._stop      = threading.Event()
        self._path_lock = threading.Lock()
        self._path: Optional[str] = None
        self._thread    = threading.Thread(
            target=self._tail_loop,
            name="ED-JournalReader",
            daemon=True,
        )

    # ── Public ─────────────────────────────────────────────────────────────

    def set_path(self, path: Optional[str]) -> None:
        """
        Set or update the journal file path.

        Safe to call from any thread at any time.  On the next poll cycle
        the reader will switch to the new path and seek to its end.
        """
        with self._path_lock:
            self._path = path

    def start(self) -> None:
        """Start the background reader thread."""
        self._thread.start()

    def stop(self) -> None:
        """Signal the reader thread to stop.  Returns immediately."""
        self._stop.set()

    # ── Internal ───────────────────────────────────────────────────────────

    def _tail_loop(self) -> None:
        current_path: Optional[str] = None
        fh = None

        while not self._stop.is_set():
            with self._path_lock:
                new_path = self._path

            # ── Path changed or first open ─────────────────────────────────
            if new_path != current_path:
                if fh is not None:
                    try:
                        fh.close()
                    except OSError:
                        pass
                    fh = None
                current_path = new_path

            # ── No path yet — wait ────────────────────────────────────────
            if current_path is None:
                self._stop.wait(POLL_INTERVAL)
                continue

            # ── Open file if needed ───────────────────────────────────────
            if fh is None:
                try:
                    fh = open(current_path, "r", encoding="utf-8", errors="replace")
                    fh.seek(0, 2)   # seek to end — skip historical entries
                except OSError:
                    self._stop.wait(POLL_INTERVAL)
                    continue

            # ── Read any new lines ────────────────────────────────────────
            try:
                while True:
                    line = fh.readline()
                    if not line:
                        break
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data: dict = json.loads(line)
                        event_name: str = data.get("event", "")
                        if event_name:
                            self._on_event(event_name, data)
                    except (json.JSONDecodeError, Exception):
                        pass   # malformed line — skip silently
            except OSError:
                # File became unavailable — close and retry next cycle
                try:
                    fh.close()
                except OSError:
                    pass
                fh = None

            self._stop.wait(POLL_INTERVAL)

        # ── Cleanup ────────────────────────────────────────────────────────
        if fh is not None:
            try:
                fh.close()
            except OSError:
                pass
