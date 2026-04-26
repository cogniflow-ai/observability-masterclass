"""
Cogniflow Orchestrator — Runtime configuration.

All tunable values come from environment variables so pipelines
can be reconfigured without editing any file.
"""

from __future__ import annotations
import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class OrchestratorConfig:
    # ── Claude CLI ────────────────────────────────────────────────────────
    # Path to the claude executable.  Auto-detected if not set.
    claude_bin: str = field(default_factory=lambda: _find_claude())

    # Seconds before a single claude call is killed (IMP-04)
    agent_timeout: int = field(
        default_factory=lambda: int(os.getenv("AGENT_TIMEOUT", "300"))
    )

    # ── Token budget (IMP-06) ─────────────────────────────────────────────
    # Approximate context window of the model
    context_limit: int = field(
        default_factory=lambda: int(os.getenv("MODEL_CONTEXT_LIMIT", "180000"))
    )
    # Fraction of context_limit reserved for input (rest is for output)
    input_budget_fraction: float = field(
        default_factory=lambda: float(os.getenv("INPUT_BUDGET_FRACTION", "0.66"))
    )

    @property
    def input_token_budget(self) -> int:
        return int(self.context_limit * self.input_budget_fraction)

    # ── Output versioning (IMP-05) ────────────────────────────────────────
    keep_output_versions: bool = field(
        default_factory=lambda: os.getenv("KEEP_OUTPUT_VERSIONS", "1") != "0"
    )

    # ── Parallelism ───────────────────────────────────────────────────────
    max_parallel_agents: int = field(
        default_factory=lambda: int(os.getenv("MAX_PARALLEL_AGENTS", "8"))
    )

    # ── Retry policy (IMP-09) ─────────────────────────────────────────────
    # Number of retries after the first failed call. 3 retries → 4 attempts
    # in total. Set to 0 to disable retries entirely.
    max_retries: int = field(
        default_factory=lambda: int(os.getenv("AGENT_MAX_RETRIES", "3"))
    )
    # Comma-separated list of seconds to sleep between attempts.
    # Index i = wait between attempt (i+1) and attempt (i+2). The list is
    # padded with its last value if max_retries exceeds its length, so a
    # short list still works at higher retry counts.
    retry_delays_s: list[int] = field(
        default_factory=lambda: _parse_int_csv(
            os.getenv("AGENT_RETRY_DELAYS_S", "3,3,10")
        )
    )

    # ── Observability ─────────────────────────────────────────────────────
    # Print a status line for every agent event when True
    verbose: bool = field(
        default_factory=lambda: os.getenv("VERBOSE", "1") != "0"
    )


def _parse_int_csv(raw: str) -> list[int]:
    """Parse '3,3,10' → [3, 3, 10]. Empty / malformed entries are skipped."""
    out: list[int] = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            out.append(int(chunk))
        except ValueError:
            continue
    return out or [3, 3, 10]   # never return empty — fall back to default


def _find_claude() -> str:
    """
    Locate the claude CLI executable.
    Search order:
      1. CLAUDE_BIN env var
      2. 'claude' in PATH
      3. 'claude.exe' in PATH (Windows)
      4. Common install locations
    """
    env_bin = os.getenv("CLAUDE_BIN")
    if env_bin:
        return env_bin

    # On Windows, prefer the .cmd/.exe wrappers over the extensionless bash
    # script that npm also installs — subprocess.run uses CreateProcess, which
    # cannot execute shell scripts and fails with WinError 193.
    if sys.platform == "win32":
        search_names = ("claude.cmd", "claude.exe", "claude.bat", "claude")
    else:
        search_names = ("claude",)
    for name in search_names:
        found = shutil.which(name)
        if found:
            return found

    # Common Windows install paths
    if sys.platform == "win32":
        candidates = [
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "claude" / "claude.exe",
            Path(os.environ.get("APPDATA", ""))      / "npm"     / "claude.cmd",
            Path("C:/Program Files/Anthropic/Claude/claude.exe"),
        ]
        for c in candidates:
            if c.exists():
                return str(c)

    # Common Unix/macOS paths
    unix_paths = [
        "/usr/local/bin/claude",
        "/opt/homebrew/bin/claude",
        str(Path.home() / ".local" / "bin" / "claude"),
        str(Path.home() / ".npm-global" / "bin" / "claude"),
    ]
    for p in unix_paths:
        if Path(p).exists():
            return p

    # Fall back — will fail at runtime with a clear error
    return "claude"


# Singleton used throughout the package
DEFAULT_CONFIG = OrchestratorConfig()
