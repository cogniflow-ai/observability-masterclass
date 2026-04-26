"""
Cogniflow Orchestrator — Pipeline validation (IMP-03).

validate_pipeline() is the first call in run_pipeline().
It reports ALL problems at once so the operator can fix
everything before re-running — not one error per run.
"""

from __future__ import annotations
import json
from pathlib import Path
from typing import Any

from .exceptions import PipelineValidationError
from .dag import build_graph, assert_no_cycle, compute_layers_fallback

try:
    import networkx as nx
    _HAS_NX = True
except ImportError:
    _HAS_NX = False


REQUIRED_AGENT_FILES = ("01_system.md", "02_prompt.md")
VALID_BUDGET_STRATEGIES = ("hard_fail", "auto_summarise", "select_top_n")


def validate_pipeline(pipeline_path: Path, agents_base: Path) -> dict[str, Any]:
    """
    Validate pipeline.json exhaustively.

    Returns the parsed pipeline dict on success.
    Raises PipelineValidationError (with all problems listed) on failure.
    """
    problems: list[str] = []

    # ── 1. JSON parse ─────────────────────────────────────────────────────
    try:
        data = json.loads(pipeline_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise PipelineValidationError([f"pipeline.json is not valid JSON: {e}"])
    except FileNotFoundError:
        raise PipelineValidationError([f"pipeline.json not found at {pipeline_path}"])

    # ── 2. Top-level schema ───────────────────────────────────────────────
    if "name" not in data:
        problems.append("Missing required field 'name'")
    if "agents" not in data or not isinstance(data.get("agents"), list):
        problems.append("Missing or invalid field 'agents' (must be a list)")
        raise PipelineValidationError(problems)  # can't continue without agents
    if len(data["agents"]) == 0:
        problems.append("'agents' list is empty — nothing to run")
        raise PipelineValidationError(problems)

    # ── 3. Agent entries ──────────────────────────────────────────────────
    seen_ids: set[str] = set()
    all_ids: set[str]  = {a.get("id", "") for a in data["agents"]}

    for i, agent in enumerate(data["agents"]):
        prefix = f"Agent[{i}]"

        # Required: id
        aid = agent.get("id", "")
        if not aid:
            problems.append(f"{prefix}: missing or empty 'id'")
            continue
        prefix = f"Agent '{aid}'"

        # Duplicate id
        if aid in seen_ids:
            problems.append(f"{prefix}: duplicate agent ID")
        seen_ids.add(aid)

        # depends_on references must exist
        for dep in agent.get("depends_on", []):
            if dep not in all_ids:
                problems.append(f"{prefix}: depends_on '{dep}' which is not in the pipeline")

        # Required instruction files must exist on disk
        agent_dir = agents_base / aid
        for slot in REQUIRED_AGENT_FILES:
            fpath = agent_dir / slot
            if not fpath.exists():
                problems.append(f"{prefix}: missing file {fpath}")

        # Router validation
        router = agent.get("router")
        if router is not None:
            if "routes" not in router:
                problems.append(f"{prefix}: router block missing 'routes'")
            else:
                for decision, targets in router.get("routes", {}).items():
                    if not isinstance(targets, list):
                        problems.append(
                            f"{prefix}: router route '{decision}' must be a list of agent IDs"
                        )
                    for t in (targets if isinstance(targets, list) else []):
                        if t not in all_ids:
                            problems.append(
                                f"{prefix}: router route '{decision}' references unknown agent '{t}'"
                            )

        # Per-agent config: budget strategy and static_inputs
        cfg_path = agent_dir / "00_config.json"
        if cfg_path.exists():
            try:
                cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
                strat = cfg.get("budget_strategy", "hard_fail")
                if strat not in VALID_BUDGET_STRATEGIES:
                    problems.append(
                        f"{prefix}: unknown budget_strategy '{strat}'. "
                        f"Valid: {VALID_BUDGET_STRATEGIES}"
                    )
                # Retry policy (IMP-09) — both fields are optional and
                # supersede the env-driven defaults when present.
                if "max_retries" in cfg:
                    mr = cfg["max_retries"]
                    if not isinstance(mr, int) or mr < 0:
                        problems.append(
                            f"{prefix}: 'max_retries' must be a non-negative int, got {mr!r}"
                        )
                if "retry_delays_s" in cfg:
                    rd = cfg["retry_delays_s"]
                    if not isinstance(rd, list) or not all(
                        isinstance(x, int) and x >= 0 for x in rd
                    ):
                        problems.append(
                            f"{prefix}: 'retry_delays_s' must be a list of non-negative ints, got {rd!r}"
                        )

                static_inputs = cfg.get("static_inputs", [])
                if not isinstance(static_inputs, list):
                    problems.append(
                        f"{prefix}: 'static_inputs' must be a list of paths"
                    )
                else:
                    pipeline_dir = agents_base.parent
                    for rel in static_inputs:
                        if not isinstance(rel, str):
                            problems.append(
                                f"{prefix}: static_inputs entry must be a string, got {type(rel).__name__}"
                            )
                            continue
                        resolved = (pipeline_dir / rel).resolve()
                        if not resolved.exists() or not resolved.is_file():
                            problems.append(
                                f"{prefix}: static_inputs file not found: {rel} "
                                f"(resolved: {resolved})"
                            )
            except json.JSONDecodeError:
                problems.append(f"{prefix}: 00_config.json is not valid JSON")

    # ── 4. Cycle detection ────────────────────────────────────────────────
    if not problems:   # only meaningful if IDs and deps are valid
        try:
            if _HAS_NX:
                g = build_graph(data["agents"])
                assert_no_cycle(g)
            else:
                compute_layers_fallback(data["agents"])
        except Exception as e:
            problems.append(str(e))

    if problems:
        raise PipelineValidationError(problems)

    return data


def load_agent_config(agent_dir: Path) -> dict[str, Any]:
    """Load and return 00_config.json for an agent, or defaults if absent."""
    cfg_path = agent_dir / "00_config.json"
    defaults: dict[str, Any] = {
        "budget_strategy":   "hard_fail",
        "requires_approval": False,
        "output_schema":     None,
    }
    if cfg_path.exists():
        loaded = json.loads(cfg_path.read_text(encoding="utf-8"))
        defaults.update(loaded)
    return defaults
