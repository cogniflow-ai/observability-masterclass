"""
Cogniflow Orchestrator — Token budget management (IMP-06).

Estimates token counts before assembling 04_context.md for a fan-in
agent. If the total would exceed the configured budget, applies
the agent's configured strategy rather than silently truncating.

Strategies:
  hard_fail      — raise TokenBudgetError immediately
  auto_summarise — call claude to summarise each oversized input
  select_top_n   — keep only the N largest inputs (by char count)
"""

from __future__ import annotations
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from .exceptions import TokenBudgetError

if TYPE_CHECKING:
    from .config import OrchestratorConfig
    from .events import EventLog


# Characters-per-token approximation (conservative for multi-language content)
CHARS_PER_TOKEN = 3.5


def estimate_tokens(path: Path) -> int:
    """Rough token estimate: file size in chars / CHARS_PER_TOKEN."""
    try:
        return int(len(path.read_text(encoding="utf-8")) / CHARS_PER_TOKEN)
    except FileNotFoundError:
        return 0


def check_and_prepare_inputs(
    agent_id: str,
    agent_dir: Path,
    strategy: str,
    config: "OrchestratorConfig",
    log: "EventLog",
) -> None:
    """
    Estimate total context size for agent_id.
    If within budget: do nothing (assemble_context() proceeds normally).
    If over budget:   apply strategy, then return.
    Raises TokenBudgetError if strategy is 'hard_fail'.
    """
    inputs_dir = agent_dir / "03_inputs"
    budget     = config.input_token_budget

    total  = estimate_tokens(agent_dir / "01_system.md")
    total += estimate_tokens(agent_dir / "02_prompt.md")
    input_files = sorted(inputs_dir.glob("*.md")) if inputs_dir.exists() else []
    for f in input_files:
        total += estimate_tokens(f)

    log.agent_budget_estimated(
        agent_id,
        bytes_=int(total * CHARS_PER_TOKEN),
        tokens_est=total
    )

    if total <= budget:
        return   # within budget — nothing to do

    log.agent_budget_exceeded(agent_id, total, budget, strategy)

    if strategy == "hard_fail":
        raise TokenBudgetError(agent_id, total, budget)

    elif strategy == "select_top_n":
        _apply_select_top_n(input_files, budget, total, agent_id, agent_dir)

    elif strategy == "auto_summarise":
        _apply_auto_summarise(input_files, budget, agent_id, config)


# ── Strategies ────────────────────────────────────────────────────────────────

def _apply_select_top_n(
    input_files: list[Path],
    budget: int,
    total_est: int,
    agent_id: str,
    agent_dir: Path,
) -> None:
    """
    Remove the smallest input files until the budget is met.
    Preserves the most content by dropping the shortest inputs first.
    Renames dropped files to .excluded so they are still inspectable.
    """
    files_by_size = sorted(input_files, key=lambda f: f.stat().st_size)
    remaining = total_est
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
    """
    For each input file that is above a per-file threshold, call claude
    to produce a summary and replace the file in place.
    """
    n_files   = max(len(input_files), 1)
    per_file  = budget // n_files

    for f in input_files:
        if estimate_tokens(f) <= per_file:
            continue
        summary = _summarise_file(f, per_file, config)
        if summary:
            f.write_text(
                f"[Auto-summarised from {estimate_tokens(f)} tokens to fit budget]\n\n{summary}",
                encoding="utf-8"
            )


def _summarise_file(path: Path, target_tokens: int, config: "OrchestratorConfig") -> str:
    """Call claude to produce a concise summary of one input file."""
    target_words = int(target_tokens * CHARS_PER_TOKEN / 5)  # rough words estimate
    system = "You are a precise summariser. Produce a dense, factual summary. Preserve all key findings."
    prompt = (
        f"Summarise the following text in at most {target_words} words. "
        f"Preserve all key facts, findings, and conclusions.\n\n"
        + path.read_text(encoding="utf-8")
    )
    try:
        result = subprocess.run(
            [config.claude_bin,
             "--system-prompt", system,
             "-p", prompt],          # -p required for non-interactive mode
            capture_output=True,
            timeout=120,
        )
        if result.returncode == 0:
            return result.stdout.decode("utf-8", errors="replace").strip()
    except Exception:
        pass
    return ""
