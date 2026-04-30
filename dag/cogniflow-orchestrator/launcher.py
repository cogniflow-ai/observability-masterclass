#!/usr/bin/env python3
"""
Cogniflow Launcher — v3.5.0

Watches PIPELINES_ROOT for .command.json files and spawns/kills
Cogniflow Orchestrator subprocesses in response.

Usage:
    python launcher.py [--root PATH] [--poll N] [--cli PATH]

The launcher is a standalone companion to the Cogniflow Orchestrator and
the Observation GUI. It requires no orchestrator imports and makes no
network calls. All communication is through files on the filesystem.

Command file: <pipeline_dir>/.command.json
    {"action": "start", "issued_at": "2026-03-17T11:04:00Z"}
    {"action": "stop",  "issued_at": "2026-03-17T11:08:00Z"}

The launcher deletes the command file after acting on it.

Environment variables:
    PIPELINES_ROOT      Root directory to scan (default: .)
    LAUNCHER_POLL_S     Poll interval in seconds (default: 1)
    COGNIFLOW_CLI       Path to cli.py (default: auto-detect)
    COGNIFLOW_PYTHON    Python executable to use (default: sys.executable)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("cogniflow.launcher")


# ── Configuration ─────────────────────────────────────────────────────────────

def _find_cli(start: Path) -> Optional[Path]:
    """
    Locate cli.py by searching upward from start directory, then
    checking the directory of this launcher script itself.
    """
    # 1. Env var override
    env = os.environ.get("COGNIFLOW_CLI")
    if env:
        p = Path(env)
        if p.exists():
            return p

    # 2. Upward search from start
    candidate = start
    for _ in range(6):
        p = candidate / "cli.py"
        if p.exists():
            return p
        if candidate.parent == candidate:
            break
        candidate = candidate.parent

    # 3. Same directory as this launcher script
    here = Path(__file__).parent / "cli.py"
    if here.exists():
        return here

    return None


class LauncherConfig:
    def __init__(
        self,
        pipelines_root: Path,
        poll_s: float,
        cli_path: Optional[Path],
        python_exe: str,
    ):
        self.pipelines_root = pipelines_root.resolve()
        self.poll_s         = poll_s
        self.cli_path       = cli_path or _find_cli(pipelines_root)
        self.python_exe     = python_exe

    @classmethod
    def from_env_and_args(cls, args: argparse.Namespace) -> "LauncherConfig":
        root = Path(
            args.root or os.environ.get("PIPELINES_ROOT", ".")
        )
        poll = float(
            args.poll or os.environ.get("LAUNCHER_POLL_S", "1")
        )
        cli  = Path(args.cli) if args.cli else None
        py   = os.environ.get("COGNIFLOW_PYTHON", sys.executable)
        return cls(root, poll, cli, py)

    def validate(self) -> None:
        if not self.pipelines_root.exists():
            raise SystemExit(
                f"PIPELINES_ROOT does not exist: {self.pipelines_root}"
            )
        if self.cli_path is None or not self.cli_path.exists():
            raise SystemExit(
                f"Cannot find cli.py. Set COGNIFLOW_CLI or pass --cli. "
                f"Searched from: {self.pipelines_root}"
            )


# ── State file helpers ────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# Writer settle window: if the command file's mtime is newer than this,
# assume the GUI is still writing and defer reading until the next poll.
_COMMAND_SETTLE_S = 0.2


def _read_command(pipeline_dir: Path) -> tuple[Optional[dict], bool]:
    """
    Read the command file.

    Returns (payload, is_malformed):
      - (dict, False): valid command, ready to act on
      - (None, False): absent, unreadable, or still being written (retry next tick)
      - (None, True):  stable on disk but un-parseable — caller should delete it
    """
    cmd_file = pipeline_dir / ".command.json"
    try:
        st = cmd_file.stat()
    except FileNotFoundError:
        return None, False
    except OSError:
        return None, False

    # If the file was touched very recently, the GUI may still be writing it.
    # Defer — we'll pick it up on the next poll once it has settled.
    if time.time() - st.st_mtime < _COMMAND_SETTLE_S:
        return None, False

    try:
        data = json.loads(cmd_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        # Stable on disk but still un-parseable → genuinely malformed.
        return None, True

    if not isinstance(data, dict):
        return None, True
    return data, False


def _delete_command(pipeline_dir: Path) -> None:
    """Delete .command.json — silently ignore if already gone."""
    try:
        (pipeline_dir / ".command.json").unlink()
    except FileNotFoundError:
        pass


def _mark_running_agents_cancelled(pipeline_dir: Path) -> int:
    """When a pipeline is force-stopped, the subprocess dies before the
    orchestrator can flip any in-flight agent's status from 'running' to a
    terminal state. This sweeps agents/<id>/06_status.json and rewrites any
    status=='running' entry to 'cancelled' so the GUI does not stay stuck.
    Returns the number of agents patched."""
    agents_dir = pipeline_dir / "agents"
    if not agents_dir.is_dir():
        return 0
    patched = 0
    now_iso = _now_iso()
    for ad in agents_dir.iterdir():
        if not ad.is_dir():
            continue
        status_f = ad / "06_status.json"
        if not status_f.exists():
            continue
        try:
            st = json.loads(status_f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if st.get("status") != "running":
            continue
        st["status"]   = "cancelled"
        st["ended_at"] = now_iso
        started = st.get("started_at")
        if started and "duration_s" not in st:
            try:
                dt = datetime.fromisoformat(started.replace("Z", "+00:00"))
                st["duration_s"] = round(
                    (datetime.now(timezone.utc) - dt).total_seconds(), 1
                )
            except ValueError:
                pass
        try:
            tmp = status_f.with_suffix(".tmp")
            tmp.write_text(json.dumps(st, indent=2), encoding="utf-8")
            tmp.replace(status_f)
            patched += 1
        except OSError as e:
            log.warning("Could not mark %s cancelled: %s", status_f, e)
    return patched


def _write_launcher_event(pipeline_dir: Path, event: str, **kwargs) -> None:
    """
    Append a launcher event to .state/events.jsonl.
    This uses the same format as the orchestrator's event log so the GUI
    can display launcher events in the event stream.
    The write is best-effort — failure is logged but never fatal.
    """
    state_dir = pipeline_dir / ".state"
    events_file = state_dir / "events.jsonl"
    if not events_file.exists():
        return   # no events file yet — orchestrator hasn't started
    record = json.dumps({
        "ts":     _now_iso(),
        "event":  event,
        "source": "launcher",
        **kwargs,
    }, ensure_ascii=False)
    try:
        with open(events_file, "a", encoding="utf-8") as f:
            f.write(record + "\n")
    except OSError as e:
        log.debug("Could not write launcher event to %s: %s", events_file, e)


def _is_pipeline_dir(path: Path) -> bool:
    """Return True if this directory contains a valid pipeline.json."""
    return (path / "pipeline.json").exists()


# ── Process tracker ───────────────────────────────────────────────────────────

class ProcessTracker:
    """
    Tracks one subprocess per pipeline directory.
    All methods are synchronous and safe to call from the poll loop.
    """

    def __init__(self) -> None:
        # Maps pipeline_dir (Path) → subprocess.Popen
        self._procs: dict[Path, subprocess.Popen] = {}

    def is_running(self, pipeline_dir: Path) -> bool:
        proc = self._procs.get(pipeline_dir)
        if proc is None:
            return False
        if proc.poll() is not None:
            # Process has exited — clean up
            del self._procs[pipeline_dir]
            return False
        return True

    def start(
        self,
        pipeline_dir: Path,
        config: LauncherConfig,
    ) -> subprocess.Popen:
        """Spawn cli.py run <pipeline_dir> as a detached subprocess."""
        cmd = [config.python_exe, str(config.cli_path), "run", str(pipeline_dir)]
        log.info("Starting pipeline: %s", pipeline_dir.name)
        log.debug("Command: %s", " ".join(cmd))

        # Orchestrator v3.5 reads all config from <pipeline_dir>/config.json;
        # the launcher only needs to inherit the platform environment so
        # LOCALAPPDATA/APPDATA/PATH resolve claude.exe on Windows.
        # Discard child stdout/stderr rather than piping: the orchestrator
        # owns its own logging via .state/events.jsonl, and an un-drained
        # PIPE will deadlock once the OS pipe buffer fills (~64KB Linux, ~4KB Win).
        proc = subprocess.Popen(
            cmd,
            cwd=str(config.cli_path.parent),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            # On Windows, create a new process group so CTRL+C doesn't kill children
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
            if sys.platform == "win32" else 0,
        )
        self._procs[pipeline_dir] = proc
        log.info("Pipeline '%s' started (PID %d)", pipeline_dir.name, proc.pid)
        return proc

    def stop(self, pipeline_dir: Path) -> None:
        """Send SIGTERM (Unix) or CTRL_BREAK_EVENT (Windows) to the subprocess."""
        proc = self._procs.get(pipeline_dir)
        if proc is None or proc.poll() is not None:
            log.warning("Stop requested but no active process for: %s", pipeline_dir.name)
            return

        log.info("Stopping pipeline: %s (PID %d)", pipeline_dir.name, proc.pid)
        try:
            if sys.platform == "win32":
                proc.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                proc.terminate()   # SIGTERM — lets the orchestrator clean up

            # Give it 5 seconds to exit cleanly before force-killing
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                log.warning("Process %d did not exit cleanly — killing", proc.pid)
                proc.kill()
                proc.wait()
        except OSError as e:
            log.warning("Could not stop PID %d: %s", proc.pid, e)
        finally:
            if pipeline_dir in self._procs:
                del self._procs[pipeline_dir]

        log.info("Pipeline '%s' stopped", pipeline_dir.name)

    def reap_finished(self) -> list[Path]:
        """
        Check all tracked processes. Remove and return dirs for those that exited.
        """
        finished = []
        for pipeline_dir, proc in list(self._procs.items()):
            if proc.poll() is not None:
                exit_code = proc.returncode
                log.info(
                    "Pipeline '%s' finished (exit %d)",
                    pipeline_dir.name, exit_code,
                )
                del self._procs[pipeline_dir]
                finished.append(pipeline_dir)
        return finished

    def active_count(self) -> int:
        return len(self._procs)

    def active_pipelines(self) -> list[Path]:
        return list(self._procs.keys())


# ── Main poll loop ────────────────────────────────────────────────────────────

def poll_once(
    config: LauncherConfig,
    tracker: ProcessTracker,
) -> None:
    """Single poll iteration — called every config.poll_s seconds."""

    # Reap finished subprocesses once per tick (not per pipeline entry).
    tracker.reap_finished()

    # Collect all valid pipeline directories
    try:
        entries = list(config.pipelines_root.iterdir())
    except OSError as e:
        log.error("Cannot scan PIPELINES_ROOT: %s", e)
        return

    for entry in sorted(entries):
        if not entry.is_dir():
            continue
        if not _is_pipeline_dir(entry):
            continue

        # ── Read command file ───────────────────────────────────────────
        cmd, malformed = _read_command(entry)
        if malformed:
            log.warning(
                "Malformed .command.json in '%s' — discarding",
                entry.name,
            )
            _delete_command(entry)
            continue
        if cmd is None:
            continue

        action = cmd.get("action", "").strip().lower()

        if action == "start":
            if tracker.is_running(entry):
                log.info(
                    "Start requested for '%s' but it is already running — ignored",
                    entry.name,
                )
            else:
                proc = tracker.start(entry, config)
                _write_launcher_event(
                    entry, "pipeline_launch_requested",
                    pipeline=entry.name,
                    pid=proc.pid,
                )
            _delete_command(entry)

        elif action == "stop":
            if tracker.is_running(entry):
                tracker.stop(entry)
                patched = _mark_running_agents_cancelled(entry)
                _write_launcher_event(
                    entry, "pipeline_stop_requested",
                    pipeline=entry.name,
                    cancelled_agents=patched,
                )
            else:
                # Even when no subprocess is tracked, a prior crash may have
                # left stale 'running' statuses on disk. Sweep them too.
                _mark_running_agents_cancelled(entry)
                log.info(
                    "Stop requested for '%s' but it is not running — ignored",
                    entry.name,
                )
            _delete_command(entry)

        else:
            log.warning(
                "Unknown action '%s' in .command.json for '%s' — discarding",
                action, entry.name,
            )
            _delete_command(entry)


def run_launcher(config: LauncherConfig) -> None:
    """Main entry point — runs the poll loop until interrupted."""
    config.validate()

    tracker = ProcessTracker()

    log.info("=" * 56)
    log.info("  Cogniflow Launcher  v3.5.0")
    log.info("=" * 56)
    log.info("  PIPELINES_ROOT : %s", config.pipelines_root)
    log.info("  cli.py         : %s", config.cli_path)
    log.info("  Python         : %s", config.python_exe)
    log.info("  Poll interval  : %ss", config.poll_s)
    log.info("=" * 56)
    log.info("  Watching for .command.json files...  (Ctrl+C to stop)")

    def _handle_sigterm(signum, frame):
        log.info("SIGTERM received — stopping active pipelines and exiting")
        for pipeline_dir in tracker.active_pipelines():
            tracker.stop(pipeline_dir)
        sys.exit(0)

    signal.signal(signal.SIGTERM, _handle_sigterm)
    if sys.platform != "win32":
        signal.signal(signal.SIGHUP, _handle_sigterm)

    try:
        while True:
            poll_once(config, tracker)
            time.sleep(config.poll_s)
    except KeyboardInterrupt:
        log.info("Interrupted — stopping active pipelines")
        for pipeline_dir in tracker.active_pipelines():
            tracker.stop(pipeline_dir)
        log.info("Launcher stopped cleanly")


# ── CLI ───────────────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="launcher",
        description=(
            "Cogniflow Launcher — watches PIPELINES_ROOT for .command.json "
            "files and manages Orchestrator subprocesses."
        ),
    )
    parser.add_argument(
        "--root", metavar="PATH",
        help="Root directory containing pipeline dirs "
             "(default: PIPELINES_ROOT env var or current dir)",
    )
    parser.add_argument(
        "--poll", metavar="SECONDS", type=float,
        help="Poll interval in seconds (default: LAUNCHER_POLL_S env var or 1)",
    )
    parser.add_argument(
        "--cli", metavar="PATH",
        help="Path to cli.py (default: COGNIFLOW_CLI env var or auto-detect)",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable DEBUG-level logging",
    )
    parser.add_argument(
        "--version", action="version", version="cogniflow-launcher 3.5.0",
    )
    return parser


def main() -> None:
    parser = _build_parser()
    args   = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    config = LauncherConfig.from_env_and_args(args)
    run_launcher(config)


if __name__ == "__main__":
    main()
