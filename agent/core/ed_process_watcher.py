"""
ED Process Watcher
==================
Detects a running Elite Dangerous instance via psutil, then locates the
active Journal and Status.json files the game has open.

Two daemon threads are managed internally:
  • ED-ProcessScan  – scans running processes every SCAN_INTERVAL seconds
  • ED-FileScan     – once ED is found, checks its open files every SCAN_INTERVAL s

When both files are found the search pauses; paths are printed and stored.
If the process later disappears the scan automatically resumes from scratch.

Platform support
----------------
  • Windows  – detects EliteDangerous64.exe by process name
  • Linux    – detects Wine / Proton wrapper by scanning cmdlines for the .exe
               and resolves journal paths through /proc/<pid>/fd as a fallback

Dependencies
------------
  pip install psutil

Usage
-----
    from ed_process_watcher import EDProcessWatcher

    def on_update(state: dict):
        print(state)

    watcher = EDProcessWatcher(on_update=on_update)
    watcher.start()
    ...
    watcher.stop()
"""
from __future__ import annotations

import os
import re
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

try:
    import psutil
    _PSUTIL_OK = True
except ImportError:  # pragma: no cover
    _PSUTIL_OK = False

# ── Process detection ─────────────────────────────────────────────────────────

_ED_NAMES: frozenset[str] = frozenset({
    "EliteDangerous64.exe",
    "EliteDangerous32.exe",
    "EliteDangerous.exe",
})

_ED_CMDLINE_RE = re.compile(r"EliteDangerous\d*\.exe", re.IGNORECASE)

# ── File detection ────────────────────────────────────────────────────────────

# Journal.2024-01-15T120000.01.log
_JOURNAL_RE = re.compile(
    r"^Journal\.\d{4}-\d{2}-\d{2}T\d{6}\.\d+\.log$",
    re.IGNORECASE,
)
_STATUS_NAME = "status.json"

SCAN_INTERVAL: float = 5.0  # seconds between each poll


# ── State ─────────────────────────────────────────────────────────────────────

class EDWatcherState:
    """Thread-safe state container."""

    # Phase values
    SEARCHING_PROCESS = "searching_process"
    SEARCHING_FILES   = "searching_files"
    COMPLETE          = "complete"
    STOPPED           = "stopped"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.phase:         str            = self.SEARCHING_PROCESS
        self.process_found: bool           = False
        self.process_pid:   Optional[int]  = None
        self.process_name:  Optional[str]  = None
        self.journal_path:  Optional[str]  = None
        self.status_path:   Optional[str]  = None
        self.files_found:   bool           = False
        self.error:         Optional[str]  = None

    # ------------------------------------------------------------------
    def snapshot(self) -> Dict:
        with self._lock:
            return {
                "phase":         self.phase,
                "process_found": self.process_found,
                "process_pid":   self.process_pid,
                "process_name":  self.process_name,
                "journal_path":  self.journal_path,
                "status_path":   self.status_path,
                "files_found":   self.files_found,
                "error":         self.error,
            }

    def update(self, **kwargs) -> None:
        with self._lock:
            for k, v in kwargs.items():
                if hasattr(self, k):
                    setattr(self, k, v)

    def reset_process(self) -> None:
        with self._lock:
            self.phase         = self.SEARCHING_PROCESS
            self.process_found = False
            self.process_pid   = None
            self.process_name  = None
            self.journal_path  = None
            self.status_path   = None
            self.files_found   = False

    def reset_files(self) -> None:
        with self._lock:
            self.phase        = self.SEARCHING_FILES
            self.journal_path = None
            self.status_path  = None
            self.files_found  = False


# ── Watcher ───────────────────────────────────────────────────────────────────

class EDProcessWatcher:
    """
    Two-phase watcher for Elite Dangerous.

    Phase 1 — process scan:
        Polls running processes every SCAN_INTERVAL seconds until the ED
        executable is detected.  On Windows it matches by process name; on
        Linux it also scans command-line arguments to catch Wine / Proton
        wrappers.

    Phase 2 — file scan:
        Polls the found process (and its children) for open files matching the
        Journal and Status.json patterns.  On Linux a /proc/<pid>/fd fallback
        is used when psutil cannot read open files due to permissions.

    When both files are found the scan pauses.  If the process later exits
    the watcher automatically restarts Phase 1.

    Parameters
    ----------
    on_update : callable(dict), optional
        Called from the watcher thread whenever the state changes.
    """

    def __init__(
        self,
        on_update: Optional[Callable[[Dict], None]] = None,
    ) -> None:
        if not _PSUTIL_OK:
            raise RuntimeError(
                "psutil is required.  Install it with:  pip install psutil"
            )

        self._on_update = on_update
        self.state      = EDWatcherState()

        self._stop        = threading.Event()
        self._proc_active = threading.Event()   # set → process-scan thread runs
        self._file_active = threading.Event()   # set → file-scan thread runs

        self._proc_active.set()   # start in process-scan phase

        self._proc_thread = threading.Thread(
            target=self._process_scan_loop,
            name="ED-ProcessScan",
            daemon=True,
        )
        self._file_thread = threading.Thread(
            target=self._file_scan_loop,
            name="ED-FileScan",
            daemon=True,
        )

    # ── Public ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start both watcher threads."""
        self._proc_thread.start()
        self._file_thread.start()

    def stop(self) -> None:
        """Request both threads to stop (they are daemon threads)."""
        self._stop.set()
        self._proc_active.set()
        self._file_active.set()
        self.state.update(phase=EDWatcherState.STOPPED)

    def snapshot(self) -> Dict:
        """Return a thread-safe snapshot of the current state."""
        return self.state.snapshot()

    def rescan(self) -> None:
        """
        Force a fresh scan from Phase 1 regardless of current phase.
        Useful when the game is restarted after files were already found.
        """
        self._file_active.clear()
        self.state.reset_process()
        self._notify()
        self._proc_active.set()

    # ── Notification ───────────────────────────────────────────────────────

    def _notify(self) -> None:
        if self._on_update:
            try:
                self._on_update(self.snapshot())
            except Exception:
                pass

    # ── Process detection ──────────────────────────────────────────────────

    def _find_ed_process(self) -> "Optional[psutil.Process]":
        """Return the ED psutil.Process if running, else None."""
        for proc in psutil.process_iter(["name", "pid"]):
            try:
                name = (proc.info.get("name") or "").strip()

                # Direct name match (Windows, some Wine configurations)
                if name in _ED_NAMES:
                    return proc

                # Cmdline match (Linux + Wine / Proton)
                try:
                    cmdline = " ".join(proc.cmdline())
                    if _ED_CMDLINE_RE.search(cmdline):
                        return proc
                except (psutil.AccessDenied, psutil.ZombieProcess,
                        psutil.NoSuchProcess):
                    pass

            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        return None

    # ── File detection ─────────────────────────────────────────────────────

    def _collect_procs(
        self,
        root: "psutil.Process",
    ) -> "List[psutil.Process]":
        """
        Return root plus all descendant processes.
        On Linux the game files may be held by a child wine/wineserver process.
        """
        procs: list["psutil.Process"] = [root]
        try:
            procs.extend(root.children(recursive=True))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        return procs

    def _open_files_psutil(
        self,
        proc: "psutil.Process",
    ) -> List[str]:
        try:
            return [f.path for f in proc.open_files()]
        except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
            return []

    def _open_files_proc_fs(self, pid: int) -> List[str]:
        """
        Linux fallback: resolve symlinks under /proc/<pid>/fd to get open paths.
        Useful when psutil raises AccessDenied.
        """
        paths: list[str] = []
        if sys.platform == "win32":
            return paths
        fd_dir = Path(f"/proc/{pid}/fd")
        if not fd_dir.exists():
            return paths
        try:
            for link in fd_dir.iterdir():
                try:
                    target = str(link.resolve())
                    paths.append(target)
                except OSError:
                    pass
        except PermissionError:
            pass
        return paths

    def _find_journal_and_status(
        self,
        proc: "psutil.Process",
    ) -> tuple[Optional[str], Optional[str]]:
        """
        Locate the active Journal log and Status.json for the given process.

        Strategy
        --------
        Journal files are kept open continuously by ED (append-only log), so
        they reliably appear in the process's open file handles.

        Status.json, however, is written and *immediately closed* on every
        game tick (~1 s).  It is almost never open at polling time, so
        scanning open handles will not find it.  Instead, once the journal
        directory is known, Status.json is looked up by path on disk.

        Returns (journal_path, status_path); each may be None if not yet found.
        """
        journal: Optional[str] = None
        status:  Optional[str] = None

        # ── Phase A: find the journal via open file handles ──────────────
        for p in self._collect_procs(proc):
            file_paths = self._open_files_psutil(p)
            if not file_paths:
                file_paths = self._open_files_proc_fs(p.pid)

            for path_str in file_paths:
                fname = Path(path_str).name
                if journal is None and _JOURNAL_RE.match(fname):
                    journal = path_str
                # Keep an opportunistic check in case the file happens to
                # be open at this exact moment.
                if status is None and fname.lower() == _STATUS_NAME:
                    status = path_str
                if journal and status:
                    return journal, status

        # ── Phase B: derive Status.json from the journal directory ───────
        # ED writes Status.json to the same folder as the journal, but closes
        # it immediately after each write, so it will not appear in open
        # handles.  Checking for its existence on disk is sufficient.
        if journal and status is None:
            candidate = Path(journal).parent / "Status.json"
            if candidate.is_file():
                status = str(candidate)

        return journal, status

    # ── Thread: Phase 1 — process scan ────────────────────────────────────

    def _process_scan_loop(self) -> None:
        while not self._stop.is_set():
            self._proc_active.wait()
            if self._stop.is_set():
                break

            proc = self._find_ed_process()

            if proc is not None:
                try:
                    proc_name = proc.name()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    proc_name = "EliteDangerous"

                self.state.update(
                    phase         = EDWatcherState.SEARCHING_FILES,
                    process_found = True,
                    process_pid   = proc.pid,
                    process_name  = proc_name,
                )
                print(
                    f"[ED Watcher] Elite Dangerous detected — "
                    f"PID={proc.pid}  name={proc_name}"
                )
                self._notify()

                # Hand off to file-scan phase
                self._proc_active.clear()
                self._file_active.set()

            self._stop.wait(SCAN_INTERVAL)

    # ── Thread: Phase 2 — file scan ───────────────────────────────────────

    def _file_scan_loop(self) -> None:
        while not self._stop.is_set():
            self._file_active.wait()
            if self._stop.is_set():
                break

            pid = self.snapshot().get("process_pid")
            if pid is None:
                self._stop.wait(SCAN_INTERVAL)
                continue

            # Verify the process is still alive
            try:
                proc = psutil.Process(pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                print(
                    "[ED Watcher] Elite Dangerous process ended — "
                    "resuming process search."
                )
                self.state.reset_process()
                self._notify()
                self._file_active.clear()
                self._proc_active.set()
                self._stop.wait(SCAN_INTERVAL)
                continue

            journal, status = self._find_journal_and_status(proc)

            snap    = self.snapshot()
            changed = False

            if journal and journal != snap.get("journal_path"):
                self.state.update(journal_path=journal)
                changed = True
                print(f"[ED Watcher] Journal : {journal}")

            if status and status != snap.get("status_path"):
                self.state.update(status_path=status)
                changed = True
                print(f"[ED Watcher] Status  : {status}")

            # Re-read after potential update
            snap = self.snapshot()
            if snap["journal_path"] and snap["status_path"] and not snap["files_found"]:
                self.state.update(
                    files_found = True,
                    phase       = EDWatcherState.COMPLETE,
                )
                changed = True
                print("[ED Watcher] ✓ Both files located — search paused.")
                print(f"             Journal : {snap['journal_path']}")
                print(f"             Status  : {snap['status_path']}")
                self._file_active.clear()

            if changed:
                self._notify()

            self._stop.wait(SCAN_INTERVAL)


# ── CLI entry-point ───────────────────────────────────────────────────────────

def _main() -> None:
    print("ED Process Watcher — standalone test")
    print(f"Scanning every {SCAN_INTERVAL} s.  Press Ctrl-C to quit.\n")

    def on_update(state: Dict) -> None:
        phase = state["phase"]
        if phase == EDWatcherState.SEARCHING_PROCESS:
            print("[state] Searching for Elite Dangerous process …")
        elif phase == EDWatcherState.SEARCHING_FILES:
            print(
                f"[state] ED found (PID={state['process_pid']}) — "
                "looking for journal/status files …"
            )
        elif phase == EDWatcherState.COMPLETE:
            print(
                f"[state] COMPLETE\n"
                f"        Journal : {state['journal_path']}\n"
                f"        Status  : {state['status_path']}"
            )
        elif phase == EDWatcherState.STOPPED:
            print("[state] Watcher stopped.")

    watcher = EDProcessWatcher(on_update=on_update)
    watcher.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        watcher.stop()
        print("\nStopped.")


if __name__ == "__main__":
    _main()
