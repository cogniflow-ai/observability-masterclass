"""
Cogniflow Orchestrator v3.5 — Runtime configuration.

Configuration is loaded from ``<pipeline_dir>/config.json``.

**No Cogniflow env vars are read.** The only OS env vars consulted are
``LOCALAPPDATA`` and ``APPDATA`` — used solely as Windows platform
conventions to locate the Claude binary when no path is configured.
Set ``claude.binary`` in config.json to bypass that detection.

Missing config.json → built-in defaults.
Missing sections / keys → defaults for just those values.
Unknown keys → silently ignored (forward compatibility).
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


_DEFAULTS: dict[str, Any] = {
    "claude": {
        "binary":          None,
        "default_model":   None,
        "summary_model":   None,
        "retrieval_model": None,
    },
    "execution": {
        "agent_timeout_s":     300,
        "max_parallel_agents": 8,
        "verbose":             True,
        "max_retries":         3,
        "retry_delays_s":      [3, 3, 10],
    },
    "budget": {
        "model_context_limit":   180000,
        "input_budget_fraction": 0.66,
    },
    "output": {
        "keep_versions": True,
    },
    "cyclic": {
        "loop_poll_s":                 0.5,
        "thread_window":               6,
        "thread_token_budget":         1500,
        "summary_max_tokens":          1000,
        "index_compression_threshold": 80,
        "artifact_max_inject_tokens":  800,
    },
    "approval": {
        "approver":         "operator",
        "poll_interval_s":  10,
        "timeout_s":        3600,
    },
    "secrets": {
        "rehydrate_outputs": True,
        "vault_db_path":     None,  # None → resolve_vault_path() picks the default
    },
    "debug": {
        "enabled": False,
        "logfile": ".state/runlog.log",
    },
    "substitutions": {},
}


@dataclass
class OrchestratorConfig:
    # Flat attribute names so the rest of the package keeps its existing
    # attribute-access patterns (config.agent_timeout, config.verbose, ...).

    # ── Claude binary and models ──────────────────────────────────────────────
    claude_bin:      str           = "claude"
    default_model:   Optional[str] = None
    summary_model:   Optional[str] = None
    retrieval_model: Optional[str] = None

    # ── Execution ─────────────────────────────────────────────────────────────
    agent_timeout:       int  = 300
    max_parallel_agents: int  = 8
    verbose:             bool = True

    # ── Retry policy (IMP-09) ─────────────────────────────────────────────────
    max_retries:     int       = 3
    retry_delays_s:  list[int] = field(default_factory=lambda: [3, 3, 10])

    # ── Budget ────────────────────────────────────────────────────────────────
    model_context_limit:   int   = 180000
    input_budget_fraction: float = 0.66

    # ── Output ────────────────────────────────────────────────────────────────
    keep_output_versions: bool = True

    # ── Cyclic event loop ─────────────────────────────────────────────────────
    loop_poll_s:                 float = 0.5
    thread_window:               int   = 6
    thread_token_budget:         int   = 1500
    summary_max_tokens:          int   = 1000
    index_compression_threshold: int   = 80
    artifact_max_inject_tokens:  int   = 800

    # ── Approval (GAP-3) ──────────────────────────────────────────────────────
    approver:                 str = "operator"
    approval_poll_interval_s: int = 10
    approval_timeout_s:       int = 3600

    # ── Secrets vault (v4) ────────────────────────────────────────────────────
    rehydrate_outputs: bool           = True
    vault_db_path:     Optional[str]  = None

    # ── Debug ─────────────────────────────────────────────────────────────────
    debug_enabled: bool = False
    debug_logfile: str  = ".state/runlog.log"

    # ── Substitutions (GAP-2) ─────────────────────────────────────────────────
    substitutions: dict[str, str] = field(default_factory=dict)

    # ── Metadata ──────────────────────────────────────────────────────────────
    _source: str = ""  # path of config.json loaded, or "" for built-in defaults

    # ── Derived values ────────────────────────────────────────────────────────
    @property
    def input_token_budget(self) -> int:
        return int(self.model_context_limit * self.input_budget_fraction)

    # ── Claude --model arg helpers ────────────────────────────────────────────
    def model_args(self, model_override: Optional[str] = None) -> list[str]:
        m = model_override or self.default_model
        return ["--model", m] if m else []

    def summary_model_args(self) -> list[str]:
        m = self.summary_model or self.default_model
        return ["--model", m] if m else []

    def retrieval_model_args(self) -> list[str]:
        m = self.retrieval_model or self.default_model
        return ["--model", m] if m else []

    # ── Loader ────────────────────────────────────────────────────────────────
    @classmethod
    def from_pipeline_dir(cls, pipeline_dir: Path) -> "OrchestratorConfig":
        """
        Load config from ``<pipeline_dir>/config.json``.

        File absent → all defaults.
        File invalid JSON → raises ValueError with a clear message.
        """
        cfg_path = pipeline_dir / "config.json"
        loaded: dict[str, Any] = {}
        source = ""
        if cfg_path.exists():
            try:
                loaded = json.loads(cfg_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"config.json in {pipeline_dir} is not valid JSON: {exc}"
                ) from exc
            source = str(cfg_path)

        def merged(section: str) -> dict[str, Any]:
            d = dict(_DEFAULTS[section])
            override = loaded.get(section, {}) or {}
            d.update({k: v for k, v in override.items() if not k.startswith("_")})
            return d

        claude_s   = merged("claude")
        exec_s     = merged("execution")
        budget_s   = merged("budget")
        output_s   = merged("output")
        cyclic_s   = merged("cyclic")
        approval_s = merged("approval")
        secrets_s  = merged("secrets")
        debug_s    = merged("debug")

        subs_raw = loaded.get("substitutions", {}) or {}
        # Strip any metadata keys (e.g. _warning, _comment)
        subs = {k: str(v) for k, v in subs_raw.items() if not k.startswith("_")}

        claude_bin = claude_s.get("binary") or _detect_claude()
        claude_bin = _ensure_windows_launchable(claude_bin)

        return cls(
            claude_bin                  = claude_bin,
            default_model               = claude_s.get("default_model"),
            summary_model               = claude_s.get("summary_model"),
            retrieval_model             = claude_s.get("retrieval_model"),
            agent_timeout               = int(exec_s["agent_timeout_s"]),
            max_parallel_agents         = int(exec_s["max_parallel_agents"]),
            verbose                     = bool(exec_s["verbose"]),
            max_retries                 = max(0, int(exec_s.get("max_retries", 3))),
            retry_delays_s              = [
                max(0, int(x)) for x in
                (exec_s.get("retry_delays_s") or [3, 3, 10])
                if isinstance(x, (int, float))
            ] or [3, 3, 10],
            model_context_limit         = int(budget_s["model_context_limit"]),
            input_budget_fraction       = float(budget_s["input_budget_fraction"]),
            keep_output_versions        = bool(output_s["keep_versions"]),
            loop_poll_s                 = float(cyclic_s["loop_poll_s"]),
            thread_window               = int(cyclic_s["thread_window"]),
            thread_token_budget         = int(cyclic_s["thread_token_budget"]),
            summary_max_tokens          = int(cyclic_s["summary_max_tokens"]),
            index_compression_threshold = int(cyclic_s["index_compression_threshold"]),
            artifact_max_inject_tokens  = int(cyclic_s["artifact_max_inject_tokens"]),
            approver                    = str(approval_s["approver"]),
            approval_poll_interval_s    = int(approval_s["poll_interval_s"]),
            approval_timeout_s          = int(approval_s["timeout_s"]),
            rehydrate_outputs           = bool(secrets_s["rehydrate_outputs"]),
            vault_db_path               = (
                str(secrets_s["vault_db_path"])
                if secrets_s.get("vault_db_path") else None
            ),
            debug_enabled               = bool(debug_s["enabled"]),
            debug_logfile               = str(debug_s["logfile"]),
            substitutions               = subs,
            _source                     = source,
        )


def _detect_claude() -> str:
    """
    Locate the claude binary. Search order:

      1. `claude.exe` / `claude.cmd` / `claude` in PATH (``shutil.which``).
         On Windows, extensionless shims are skipped because CreateProcess
         cannot execute them (OSError 8, "%1 is not a valid Win32 application").
      2. Common Windows install locations (uses ``LOCALAPPDATA`` / ``APPDATA``
         — these are Windows platform env vars, not Cogniflow config knobs)
      3. Common Unix/macOS install locations

    Set ``claude.binary`` in ``config.json`` to skip detection entirely.
    """
    if sys.platform == "win32":
        names = ("claude.exe", "claude.cmd", "claude.bat")
    else:
        names = ("claude", "claude.exe")
    for name in names:
        found = shutil.which(name)
        if found:
            return found

    if sys.platform == "win32":
        candidates = [
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "claude" / "claude.exe",
            Path(os.environ.get("APPDATA", ""))      / "npm"      / "claude.cmd",
            Path("C:/Program Files/Anthropic/Claude/claude.exe"),
        ]
        for c in candidates:
            if c.exists():
                return str(c)

    for p in (
        "/usr/local/bin/claude",
        "/opt/homebrew/bin/claude",
        str(Path.home() / ".local"      / "bin" / "claude"),
        str(Path.home() / ".npm-global" / "bin" / "claude"),
    ):
        if Path(p).exists():
            return p

    return "claude"  # will fail at runtime with a clear error if unresolved


def _ensure_windows_launchable(path: str) -> str:
    """
    On Windows, if the resolved claude binary is extensionless (an npm shell
    shim, not a real PE executable), swap it for a sibling `.exe` / `.cmd` /
    `.bat`. subprocess on Windows calls CreateProcess, which cannot execute
    extensionless shell scripts — attempting to do so raises OSError(8,
    "%1 is not a valid Win32 application"). No-op on non-Windows platforms.
    """
    if sys.platform != "win32":
        return path
    p = Path(path)
    if p.suffix:
        return path
    for ext in (".exe", ".cmd", ".bat"):
        candidate = p.with_suffix(ext)
        if candidate.exists():
            return str(candidate)
    return path


# All-defaults instance. Useful for unit tests and fallback callers that
# don't have a pipeline_dir. Production callers should use
# OrchestratorConfig.from_pipeline_dir(pipeline_dir).
DEFAULT_CONFIG = OrchestratorConfig()
