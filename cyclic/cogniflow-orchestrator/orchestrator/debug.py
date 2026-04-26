"""
Cogniflow Orchestrator v3.5 — Debug logging.

When ``debug.enabled`` is true in config.json (or ``--debug`` is passed
on the ``run`` command), a ``cogniflow`` logger is configured with two
handlers:

  1. ``stderr`` — human-readable console output
  2. ``<pipeline_dir>/<debug.logfile>`` — append-only log file
     (default: ``.state/runlog.log``)

All orchestrator modules share the logger via::

    from .debug import get_logger
    log = get_logger()
    log.debug("whatever")

When debug is off, the logger's level is WARNING and every ``log.debug(...)``
call is a near-free no-op.

## Categories of events emitted (at DEBUG level)

  engine        — mode detection, layer composition, layer transitions
  agent         — per-agent lifecycle (start, resume, finish, fail)
  claude        — subprocess invocations (argv with large payloads elided,
                  exit code, duration, stderr snippet)
  context       — 02_prompt.md read, 03_inputs/ collected, 04_context.md
                  written with byte/token count and head excerpt
  budget        — strategy applied, token estimates before/after
  secret        — substitution counts (no values), credential scan hits
  schema        — validation attempts and pass/fail with violation list
  approval      — request written, wait loop entered, decision seen
  memory        — cyclic memory updates (when cyclic path is active)

Sensitive data handling: substituted VALUES are never logged — only the
name of the substituted variable. System prompts and assembled contexts
are logged as their first 300 characters + the byte count.
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path


LOGGER_NAME = "cogniflow"

# How many characters of a prompt / context / output to show in debug logs.
DEBUG_SNIPPET_CHARS = 300


def setup_logging(
    pipeline_dir: Path,
    debug_enabled: bool,
    logfile_relpath: str = ".state/runlog.log",
) -> logging.Logger:
    """
    Initialise the cogniflow logger.

    Clears any previous handlers (safe for in-process re-runs such as tests).
    Returns the configured logger. Also accessible via
    ``logging.getLogger("cogniflow")`` from anywhere in the package.
    """
    logger = logging.getLogger(LOGGER_NAME)
    logger.handlers.clear()
    logger.propagate = False

    if not debug_enabled:
        logger.setLevel(logging.WARNING)
        return logger

    logger.setLevel(logging.DEBUG)

    fmt = logging.Formatter(
        fmt="%(asctime)s %(levelname)-5s %(name)s.%(module)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # Console handler — stderr, so it doesn't pollute captured stdout
    console = logging.StreamHandler(stream=sys.stderr)
    console.setLevel(logging.DEBUG)
    console.setFormatter(fmt)
    logger.addHandler(console)

    # File handler — append mode (history across runs is useful for a course)
    log_path = pipeline_dir / logfile_relpath
    log_path.parent.mkdir(parents=True, exist_ok=True)
    fileh = logging.FileHandler(log_path, mode="a", encoding="utf-8")
    fileh.setLevel(logging.DEBUG)
    fileh.setFormatter(fmt)
    logger.addHandler(fileh)

    # Run header — a clear visual anchor so runs are easy to separate
    # when tailing the log.
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    logger.debug("═" * 60)
    logger.debug(f"═══ Cogniflow debug run · {now}")
    logger.debug(f"═══ pipeline: {pipeline_dir}")
    logger.debug(f"═══ logfile:  {log_path}")
    logger.debug("═" * 60)

    return logger


def get_logger() -> logging.Logger:
    """Return the shared cogniflow logger. Safe to call before setup_logging()."""
    return logging.getLogger(LOGGER_NAME)


def snippet(text: str, n: int = DEBUG_SNIPPET_CHARS) -> str:
    """Collapse whitespace and truncate — safe for one-line log display."""
    flat = " ".join(text.split())
    if len(flat) <= n:
        return flat
    return flat[:n] + f"… [+{len(flat) - n} chars]"


def elided_argv(argv: list[str], max_arg_chars: int = 80) -> str:
    """
    Render a subprocess argv for logging, eliding large payloads.
    Useful for the Claude invocation, where --system-prompt is the full
    system prompt (thousands of characters). The user prompt is piped via
    stdin, not argv, so it is logged separately by the caller.
    """
    parts: list[str] = []
    skip_next = False
    for i, a in enumerate(argv):
        if skip_next:
            parts.append(f"<{len(a)} chars>")
            skip_next = False
            continue
        if a == "--system-prompt":
            parts.append(a)
            skip_next = True
            continue
        if len(a) > max_arg_chars:
            parts.append(f"<{len(a)} chars>")
        else:
            parts.append(a)
    return " ".join(parts)
