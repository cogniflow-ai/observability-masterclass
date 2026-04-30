"""
Bundled entry point for the standalone cogniflow-orchestrator .exe.

Behavior:
  * Default invocation runs launcher.main() with --root ./pipelines
    (resolved relative to the .exe directory).
  * When the first argv is the literal "__cli__", the rest of argv is
    forwarded to cli.main(). The launcher uses this to spawn pipeline
    subprocesses against the same .exe instead of an external python.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_FROZEN = getattr(sys, "frozen", False)
_DISPATCH_TOKEN = "__cli__"


def _exe_dir() -> Path:
    if _FROZEN:
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _dispatch_cli() -> int:
    sys.argv = [sys.argv[0]] + sys.argv[2:]
    from cli import main as cli_main
    cli_main()
    return 0


def _patch_launcher_start() -> None:
    """Re-route ProcessTracker.start() to spawn `<exe> __cli__ run <dir>`
    instead of `<python> cli.py run <dir>`. The frozen exe contains both
    launcher and CLI code, so we re-invoke ourselves."""
    import logging
    import launcher

    log = logging.getLogger("cogniflow.launcher")

    def start(self, pipeline_dir, config):
        cmd = [sys.executable, _DISPATCH_TOKEN, "run", str(pipeline_dir)]
        log.info("Starting pipeline: %s", pipeline_dir.name)
        log.debug("Command: %s", " ".join(cmd))
        proc = subprocess.Popen(
            cmd,
            cwd=str(_exe_dir()),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
            if sys.platform == "win32" else 0,
        )
        self._procs[pipeline_dir] = proc
        log.info("Pipeline '%s' started (PID %d)", pipeline_dir.name, proc.pid)
        return proc

    launcher.ProcessTracker.start = start


def _has_flag(argv: list[str], flag: str) -> bool:
    return any(a == flag or a.startswith(flag + "=") for a in argv)


def _run_launcher() -> int:
    if not _has_flag(sys.argv[1:], "--root"):
        sys.argv += ["--root", "./pipelines"]

    if _FROZEN:
        # config.validate() requires cli_path to exist on disk; the patched
        # start() ignores it. Point it at the .exe itself so validation passes.
        if not _has_flag(sys.argv[1:], "--cli"):
            sys.argv += ["--cli", str(Path(sys.executable))]
        _patch_launcher_start()

    from launcher import main as launcher_main
    launcher_main()
    return 0


def main() -> int:
    if len(sys.argv) >= 2 and sys.argv[1] == _DISPATCH_TOKEN:
        return _dispatch_cli()
    return _run_launcher()


if __name__ == "__main__":
    sys.exit(main())
