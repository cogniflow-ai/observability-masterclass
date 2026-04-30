"""
Cogniflow Orchestrator v3.0 — Token budget strategies.

Unchanged from v2.1.0.  Provides estimate_tokens() which is reused
by v3.0 memory modules (REQ-MEM-006, REQ-ARTIFACT-003).

Three strategies:
  hard_fail      — abort the pipeline if context exceeds the budget
  auto_summarise — call claude.exe to compress the largest input files
  select_top_n   — drop the smallest input files until under budget
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from .exceptions import TokenBudgetError

if TYPE_CHECKING:
    from .config import OrchestratorConfig
    from .events import EventLog


CHARS_PER_TOKEN = 4


def estimate_tokens(path: Path) -> int:
    """Rough token estimate: file chars / CHARS_PER_TOKEN."""
    try:
        return max(1, path.stat().st_size // CHARS_PER_TOKEN)
    except OSError:
        return 0


def estimate_tokens_str(text: str) -> int:
    """Rough token estimate for an in-memory string."""
    return max(1, len(text) // CHARS_PER_TOKEN)


def apply_budget(
    agent_id: str,
    agent_dir: Path,
    config: "OrchestratorConfig",
    log: "EventLog",
) -> None:
    """
    Apply the token budget strategy declared in 00_config.json.
    Reads token_budget and token_strategy from the agent config.
    No-ops if neither field is present.
    """
    cfg_path = agent_dir / "00_config.json"
    if not cfg_path.exists():
        return

    import json
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    budget   = cfg.get("token_budget")
    strategy = cfg.get("token_strategy", "hard_fail")

    if budget is None:
        return

    input_files = sorted((agent_dir / "03_inputs").glob("*.md")) if \
                  (agent_dir / "03_inputs").exists() else []
    prompt_files = [agent_dir / "02_prompt.md"]
    all_files    = [f for f in prompt_files + input_files if f.exists()]
    total_est    = sum(estimate_tokens(f) for f in all_files)

    from .debug import get_logger
    dlog = get_logger()

    if total_est <= budget:
        dlog.debug(f"[budget:{agent_id}] under budget: ~{total_est:,} / {budget:,} tokens")
        return

    log.budget_strategy(agent_id, strategy)
    dlog.debug(f"[budget:{agent_id}] OVER budget: ~{total_est:,} / {budget:,} tokens "
               f"— applying strategy '{strategy}'")

    if strategy == "hard_fail":
        raise TokenBudgetError(agent_id, total_est, budget)

    elif strategy == "auto_summarise":
        _apply_auto_summarise(input_files, budget, agent_id, config)
        new_total = sum(estimate_tokens(f) for f in all_files if f.exists())
        log.budget_applied(agent_id, strategy, total_est, new_total)
        dlog.debug(f"[budget:{agent_id}] auto_summarise done: "
                   f"~{total_est:,} → ~{new_total:,} tokens")

    elif strategy == "select_top_n":
        _apply_select_top_n(input_files, budget)
        new_total = sum(estimate_tokens(f) for f in all_files if f.exists())
        log.budget_applied(agent_id, strategy, total_est, new_total)
        dlog.debug(f"[budget:{agent_id}] select_top_n done: "
                   f"~{total_est:,} → ~{new_total:,} tokens")


def _apply_select_top_n(input_files: list[Path], budget: int) -> None:
    files_by_size = sorted(input_files, key=lambda f: f.stat().st_size)
    remaining = sum(estimate_tokens(f) for f in input_files)
    for f in files_by_size:
        if remaining <= budget:
            break
        tokens = estimate_tokens(f)
        f.rename(f.with_suffix(".excluded"))
        remaining -= tokens


def _apply_auto_summarise(
    input_files: list[Path],
    budget: int,
    agent_id: str,
    config: "OrchestratorConfig",
) -> None:
    n_files  = max(len(input_files), 1)
    per_file = budget // n_files

    for f in input_files:
        if estimate_tokens(f) <= per_file:
            continue
        summary = _summarise_file(f, per_file, config)
        if summary:
            f.write_text(
                f"[Auto-summarised from {estimate_tokens(f)} tokens to fit budget]\n\n"
                + summary,
                encoding="utf-8",
            )


def _summarise_file(path: Path, target_tokens: int,
                    config: "OrchestratorConfig") -> str:
    target_words = int(target_tokens * CHARS_PER_TOKEN / 5)
    system = "You are a precise summariser. Produce a dense, factual summary. Preserve all key findings."
    prompt = (
        f"Summarise the following text in at most {target_words} words. "
        "Preserve all key facts, findings, and conclusions.\n\n"
        + path.read_text(encoding="utf-8")
    )
    try:
        args = [config.claude_bin, "--system-prompt", system,
                "-p"] + config.summary_model_args()
        result = subprocess.run(
            args,
            input=prompt.encode("utf-8"),
            capture_output=True,
            timeout=120,
        )
        if result.returncode == 0:
            return result.stdout.decode("utf-8", errors="replace").strip()
    except Exception:
        pass
    return ""
