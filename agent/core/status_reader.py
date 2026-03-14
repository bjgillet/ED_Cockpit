"""
ED Assist — Status Reader
===========================
Polls the Elite Dangerous ``Status.json`` file and emits parsed state to a
registered callback whenever the content changes.

Design
------
  Status.json is written and immediately closed by ED on every game tick
  (~1 second).  Unlike the journal it is not a streaming log — it always
  reflects the current game state as a single JSON object.

  The reader runs in a background thread and:
    1. Reads the file at ``POLL_INTERVAL`` seconds.
    2. Hashes the raw content (SHA-256) and compares with the previous read.
    3. On change, parses the JSON and calls ``on_status(data)``.
    4. If the file path changes (new game session), it switches seamlessly.
    5. If the file is unavailable, it waits and retries silently.

  Thread safety: ``on_status`` is called from the reader thread.  Callers
  must dispatch to GUI or asyncio themselves.

Dependencies
------------
  Standard library only.
"""
from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from typing import Callable, Optional

POLL_INTERVAL: float = 1.0   # seconds between status polls


class StatusReader:
    """
    Background Status.json poller.

    Parameters
    ----------
    on_status : callable(data: dict)
        Called whenever Status.json content changes.
        Invoked from the reader thread — callers are responsible for
        thread-safe dispatch to GUI or asyncio code.
    """

    def __init__(
        self,
        on_status: Callable[[dict], None],
    ) -> None:
        self._on_status  = on_status
        self._stop       = threading.Event()
        self._path_lock  = threading.Lock()
        self._path: Optional[str] = None
        self._last_hash  = ""
        self._thread     = threading.Thread(
            target=self._poll_loop,
            name="ED-StatusReader",
            daemon=True,
        )

    # ── Public ─────────────────────────────────────────────────────────────

    def set_path(self, path: Optional[str]) -> None:
        """
        Set or update the Status.json path.

        Safe to call from any thread.  The next poll cycle will use the
        new path and will treat any content as changed (fresh baseline).
        """
        with self._path_lock:
            if path != self._path:
                self._path      = path
                self._last_hash = ""   # force emit on next read

    def start(self) -> None:
        """Start the background poller thread."""
        self._thread.start()

    def stop(self) -> None:
        """Signal the poller thread to stop.  Returns immediately."""
        self._stop.set()

    # ── Internal ───────────────────────────────────────────────────────────

    def _poll_loop(self) -> None:
        while not self._stop.is_set():
            with self._path_lock:
                path = self._path

            if path is not None:
                try:
                    raw = Path(path).read_text(encoding="utf-8", errors="replace")
                    content_hash = hashlib.sha256(raw.encode()).hexdigest()

                    if content_hash != self._last_hash:
                        self._last_hash = content_hash
                        try:
                            data: dict = json.loads(raw)
                            self._on_status(data)
                        except json.JSONDecodeError:
                            pass   # truncated write in progress — skip cycle
                except OSError:
                    pass   # file not available yet — retry next cycle

            self._stop.wait(POLL_INTERVAL)
