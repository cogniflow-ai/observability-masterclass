"""
Cogniflow Orchestrator v3.0 — Standalone event writer.

DESIGN CONSTRAINT: This module has NO imports from any other Cogniflow
module.  It is imported by hook scripts that run inside a claude.exe
subprocess session and must have zero coupling to the rest of the package.

Usage::

    from orchestrator.event_writer import append_event

    append_event(
        events_path="/path/to/.state/events.jsonl",
        event="cli_bash_call",
        session_id="sess-001",
        command="ls -la",
        exit_code=0,
        duration_ms=42,
    )
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Optional filelock — same fallback strategy as events.py
try:
    from filelock import FileLock as _FileLock
    _HAS_FILELOCK = True
except ImportError:
    _HAS_FILELOCK = False

_thread_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def append_event(events_path: str, event: str, **kwargs: Any) -> None:
    """
    Append one JSONL event record to *events_path*.

    Thread-safe (filelock if available, threading.Lock otherwise).
    Creates the file and parent directories if they do not exist.
    Never raises — all errors are silently swallowed so hook scripts
    cannot crash a claude.exe session.
    """
    try:
        path = Path(events_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        record = json.dumps({"ts": _now(), "event": event, **kwargs})
        lock_path = str(path) + ".lock"

        if _HAS_FILELOCK:
            with _FileLock(lock_path, timeout=5):
                with open(path, "a", encoding="utf-8") as fh:
                    fh.write(record + "\n")
        else:
            with _thread_lock:
                with open(path, "a", encoding="utf-8") as fh:
                    fh.write(record + "\n")
    except Exception:
        pass  # never crash a hook script


def find_events_file() -> str | None:
    """
    Attempt to locate events.jsonl by walking up from CWD.
    Hook scripts run with CWD set to the pipeline directory.
    Returns the absolute path string, or None if not found.
    """
    cwd = Path(os.getcwd())
    for directory in [cwd, *cwd.parents]:
        candidate = directory / ".state" / "events.jsonl"
        if candidate.exists():
            return str(candidate)
        # also check if .state exists (file may not yet)
        if (directory / ".state").is_dir():
            return str(directory / ".state" / "events.jsonl")
    return None
