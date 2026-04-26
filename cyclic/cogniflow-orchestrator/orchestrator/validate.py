"""
Cogniflow Orchestrator v3.0 — Pipeline validation.

validate_pipeline() collects ALL errors before raising, so the user sees
the complete list in one shot.

v3.0 adds cyclic-graph checks V-CYC-001 through V-CYC-010.
All existing checks are preserved unchanged.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .exceptions import PipelineValidationError
from .schema import VALID_MODES as VALID_SCHEMA_MODES

VALID_STRATEGIES         = {"all_done", "coordinator_signal", "timeout_only"}
VALID_ON_CYCLE_LIMIT     = {"escalate_pm", "force_done", "halt"}
VALID_ON_DEADLOCK        = {"escalate_pm", "force_unblock_oldest", "halt"}
VALID_EDGE_TYPES         = {"task", "feedback", "peer"}
VALID_APPROVAL_INCLUDE   = {"output", "note", "full_context"}
VALID_APPROVAL_ROUTE_MODE = {"feedback", "task"}


def validate_pipeline(pipeline_dir: Path) -> dict[str, Any]:
    """
    Validate the pipeline at *pipeline_dir*.  Returns the parsed spec on
    success.  Raises PipelineValidationError with all collected errors on
    failure.  Prints warnings (V-CYC-010) without raising.
    """
    errors: list[str]   = []
    warnings: list[str] = []

    # ── Load pipeline.json ────────────────────────────────────────────────────
    pj = pipeline_dir / "pipeline.json"
    if not pj.exists():
        raise PipelineValidationError([f"pipeline.json not found in {pipeline_dir}"])

    try:
        spec = json.loads(pj.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PipelineValidationError([f"pipeline.json is not valid JSON: {exc}"])

    # Validate config.json if present (raises ValueError we surface as error)
    cfg_path = pipeline_dir / "config.json"
    if cfg_path.exists():
        try:
            json.loads(cfg_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"config.json is not valid JSON: {exc}")

    agent_ids = {a["id"] for a in spec.get("agents", [])}
    edges     = spec.get("edges", [])
    has_cyclic_edges = any(e.get("type") in ("feedback", "peer") for e in edges)

    # ── Existing checks (v2.1.0) ──────────────────────────────────────────────
    if not spec.get("agents"):
        errors.append("pipeline.json must define at least one agent")

    for agent in spec.get("agents", []):
        aid = agent.get("id", "")
        if not aid:
            errors.append("Each agent must have an 'id' field")
            continue
        adir_rel = agent.get("dir")
        if adir_rel:
            adir = pipeline_dir / adir_rel
        else:
            # v1 convention: agents/<id>/ ; v3.5 implicit: <id>/
            v1_dir = pipeline_dir / "agents" / aid
            adir = v1_dir if v1_dir.exists() else pipeline_dir / aid
        if not adir.exists():
            errors.append(f"Agent directory not found: {adir_rel or aid}")
            continue
        # System prompt required for cyclic agents (also good practice for all)
        sys_md = adir / "01_system.md"
        if has_cyclic_edges and not sys_md.exists():
            errors.append(f"[V-CYC-005] Cyclic agent missing system prompt: {aid}")
        # Prompt required for acyclic agents
        prompt_md = adir / "02_prompt.md"
        if not has_cyclic_edges and not prompt_md.exists() and not sys_md.exists():
            errors.append(f"Agent '{aid}' missing both 01_system.md and 02_prompt.md")
        # Config file optional but validate if present
        cfg_path = adir / "00_config.json"
        if cfg_path.exists():
            try:
                cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
                strategy = cfg.get("token_strategy")
                if strategy and strategy not in {"hard_fail", "auto_summarise", "select_top_n"}:
                    errors.append(
                        f"Agent '{aid}' has invalid token_strategy: '{strategy}'"
                    )
                # GAP-1 — validate output_schema modes
                schema = cfg.get("output_schema")
                if schema:
                    modes = schema.get("mode", [])
                    if isinstance(modes, str):
                        modes = [modes]
                    for m in modes:
                        if m not in VALID_SCHEMA_MODES:
                            errors.append(
                                f"Agent '{aid}' has unknown output_schema mode: '{m}'. "
                                f"Valid: {sorted(VALID_SCHEMA_MODES)}"
                            )
                # v4 — validate input_schema modes (same vocabulary).
                in_schema = cfg.get("input_schema")
                if in_schema:
                    if not isinstance(in_schema, dict):
                        errors.append(
                            f"Agent '{aid}': 'input_schema' must be an object"
                        )
                    else:
                        in_modes = in_schema.get("mode", [])
                        if isinstance(in_modes, str):
                            in_modes = [in_modes]
                        for m in in_modes:
                            if m not in VALID_SCHEMA_MODES:
                                errors.append(
                                    f"Agent '{aid}' has unknown input_schema mode: "
                                    f"'{m}'. Valid: {sorted(VALID_SCHEMA_MODES)}"
                                )
                        req_up = in_schema.get("require_upstream")
                        if req_up is not None and not (
                            isinstance(req_up, list)
                            and all(isinstance(x, str) for x in req_up)
                        ):
                            errors.append(
                                f"Agent '{aid}': input_schema.require_upstream "
                                "must be a list of agent IDs"
                            )
                        elif isinstance(req_up, list):
                            declared_deps = set(agent.get("depends_on", []))
                            for up in req_up:
                                if up not in agent_ids:
                                    errors.append(
                                        f"Agent '{aid}': input_schema.require_upstream "
                                        f"references unknown agent '{up}'"
                                    )
                                elif declared_deps and up not in declared_deps:
                                    errors.append(
                                        f"Agent '{aid}': input_schema.require_upstream "
                                        f"'{up}' is not in depends_on"
                                    )
                        si_req = in_schema.get("static_inputs_required")
                        if si_req is not None and not isinstance(si_req, bool):
                            errors.append(
                                f"Agent '{aid}': input_schema.static_inputs_required "
                                f"must be true/false, got {si_req!r}"
                            )
                # v4 — validate approval_routes (V-APPROVE-001).
                appr_routes = cfg.get("approval_routes")
                if appr_routes is not None:
                    if not cfg.get("requires_approval"):
                        errors.append(
                            f"Agent '{aid}': approval_routes declared but "
                            "requires_approval is not true"
                        )
                    if not isinstance(appr_routes, dict):
                        errors.append(
                            f"Agent '{aid}': approval_routes must be an object"
                        )
                    else:
                        for route_key in ("on_reject", "on_approve"):
                            route = appr_routes.get(route_key)
                            if route is None:
                                continue
                            if not isinstance(route, dict):
                                errors.append(
                                    f"Agent '{aid}': approval_routes.{route_key} "
                                    "must be an object"
                                )
                                continue
                            target = route.get("target")
                            if target is not None:
                                if not isinstance(target, str):
                                    errors.append(
                                        f"Agent '{aid}': approval_routes."
                                        f"{route_key}.target must be a string"
                                    )
                                elif target == aid:
                                    errors.append(
                                        f"[V-APPROVE-001] Agent '{aid}': "
                                        f"approval_routes.{route_key}.target "
                                        "must not equal the gate agent itself"
                                    )
                                elif target not in agent_ids:
                                    errors.append(
                                        f"[V-APPROVE-001] Agent '{aid}': "
                                        f"approval_routes.{route_key}.target "
                                        f"references unknown agent '{target}'"
                                    )
                            include = route.get("include", ["output"])
                            if not isinstance(include, list) or not all(
                                isinstance(x, str) for x in include
                            ):
                                errors.append(
                                    f"Agent '{aid}': approval_routes."
                                    f"{route_key}.include must be a list of strings"
                                )
                            else:
                                for part in include:
                                    if part not in VALID_APPROVAL_INCLUDE:
                                        errors.append(
                                            f"Agent '{aid}': approval_routes."
                                            f"{route_key}.include has unknown "
                                            f"entry '{part}'. "
                                            f"Valid: {sorted(VALID_APPROVAL_INCLUDE)}"
                                        )
                            mode_val = route.get("mode", "feedback")
                            if mode_val not in VALID_APPROVAL_ROUTE_MODE:
                                errors.append(
                                    f"Agent '{aid}': approval_routes."
                                    f"{route_key}.mode='{mode_val}' invalid. "
                                    f"Valid: {sorted(VALID_APPROVAL_ROUTE_MODE)}"
                                )
                        # approval_routes only meaningful on cyclic pipelines.
                        if not has_cyclic_edges:
                            errors.append(
                                f"Agent '{aid}': approval_routes is only "
                                "supported on cyclic pipelines. In DAG mode, "
                                "rejection stops the run."
                            )
                # GAP-3 — requires_approval must be boolean
                ra = cfg.get("requires_approval")
                if ra is not None and not isinstance(ra, bool):
                    errors.append(
                        f"Agent '{aid}': 'requires_approval' must be true/false, got {ra!r}"
                    )
                # IMP-09 — retry fields (optional, override config.json defaults)
                if "max_retries" in cfg:
                    mr = cfg["max_retries"]
                    if not isinstance(mr, int) or mr < 0:
                        errors.append(
                            f"Agent '{aid}': 'max_retries' must be a non-negative int, got {mr!r}"
                        )
                if "retry_delays_s" in cfg:
                    rd = cfg["retry_delays_s"]
                    if not isinstance(rd, list) or not all(
                        isinstance(x, int) and x >= 0 for x in rd
                    ):
                        errors.append(
                            f"Agent '{aid}': 'retry_delays_s' must be a list of non-negative ints, got {rd!r}"
                        )
                # Static inputs — optional list of paths relative to pipeline dir
                static_inputs = cfg.get("static_inputs", [])
                if not isinstance(static_inputs, list):
                    errors.append(
                        f"Agent '{aid}': 'static_inputs' must be a list of paths"
                    )
                else:
                    for rel in static_inputs:
                        if not isinstance(rel, str):
                            errors.append(
                                f"Agent '{aid}': static_inputs entry must be a string, got {type(rel).__name__}"
                            )
                            continue
                        resolved = (pipeline_dir / rel).resolve()
                        if not resolved.exists() or not resolved.is_file():
                            errors.append(
                                f"Agent '{aid}': static_inputs file not found: {rel} "
                                f"(resolved: {resolved})"
                            )
                # Router (IMP-08): routes must point at known agents
                router = cfg.get("router")
                if router is not None:
                    if not isinstance(router, dict):
                        errors.append(
                            f"Agent '{aid}': router must be an object"
                        )
                    else:
                        routes = router.get("routes", {})
                        if not isinstance(routes, dict):
                            errors.append(
                                f"Agent '{aid}': router.routes must be an object"
                            )
                        else:
                            for decision, targets in routes.items():
                                if not isinstance(targets, list):
                                    errors.append(
                                        f"Agent '{aid}': router route '{decision}' "
                                        "must be a list of agent IDs"
                                    )
                                    continue
                                for t in targets:
                                    if t not in agent_ids:
                                        errors.append(
                                            f"Agent '{aid}': router route '{decision}' "
                                            f"references unknown agent '{t}'"
                                        )
            except json.JSONDecodeError:
                errors.append(f"Agent '{aid}': 00_config.json is not valid JSON")

    # ── depends_on graph checks (acyclic path) ────────────────────────────────
    for agent in spec.get("agents", []):
        for dep in agent.get("depends_on", []):
            if dep not in agent_ids:
                errors.append(
                    f"Agent '{agent['id']}' depends_on unknown agent: '{dep}'"
                )

    # ── Cyclic-mode checks (v3.0) ─────────────────────────────────────────────
    if has_cyclic_edges:
        _validate_cyclic(spec, pipeline_dir, agent_ids, edges, errors, warnings)

    # ── Report ────────────────────────────────────────────────────────────────
    for w in warnings:
        print(f"  WARNING: {w}")

    if errors:
        raise PipelineValidationError(errors)

    return spec


def _validate_cyclic(
    spec: dict,
    pipeline_dir: Path,
    agent_ids: set[str],
    edges: list[dict],
    errors: list[str],
    warnings: list[str],
) -> None:
    """All V-CYC-XXX checks."""
    term = spec.get("termination")

    # V-CYC-001
    if not term:
        errors.append("[V-CYC-001] Cyclic pipeline requires termination block")
    else:
        # V-CYC-002
        strategy = term.get("strategy")
        if strategy not in VALID_STRATEGIES:
            errors.append(
                f"[V-CYC-002] Invalid termination strategy: '{strategy}'. "
                f"Must be one of: {', '.join(sorted(VALID_STRATEGIES))}"
            )
        # V-CYC-003
        max_cycles = term.get("max_cycles", 0)
        if not isinstance(max_cycles, int) or max_cycles < 2:
            errors.append("[V-CYC-003] max_cycles must be >= 2")
        # on_cycle_limit
        ocl = term.get("on_cycle_limit")
        if ocl and ocl not in VALID_ON_CYCLE_LIMIT:
            errors.append(
                f"[V-CYC-] Invalid on_cycle_limit: '{ocl}'. "
                f"Must be one of: {', '.join(sorted(VALID_ON_CYCLE_LIMIT))}"
            )
        # on_deadlock
        odl = term.get("on_deadlock", "escalate_pm")
        if odl not in VALID_ON_DEADLOCK:
            errors.append(
                f"[V-CYC-] Invalid on_deadlock: '{odl}'. "
                f"Must be one of: {', '.join(sorted(VALID_ON_DEADLOCK))}"
            )

    # V-CYC-004 — edges reference known agent IDs
    for edge in edges:
        for field in ("from", "to"):
            aid = edge.get(field)
            if aid and aid not in agent_ids:
                errors.append(f"[V-CYC-004] Edge references unknown agent: '{aid}'")

    # V-CYC-006 — feedback/peer edges must have directed:false
    for edge in edges:
        if edge.get("type") in ("feedback", "peer") and edge.get("directed", False):
            errors.append(
                f"[V-CYC-006] {edge.get('type')} edge from "
                f"'{edge.get('from')}' to '{edge.get('to')}' must have directed:false"
            )

    # V-CYC-007 — tags.domain required
    tags = spec.get("tags", {})
    if not tags.get("domain"):
        errors.append("[V-CYC-007] Cyclic pipeline must define tags.domain")

    # V-CYC-008 — every agent has at least one outbound edge
    # An agent is considered to have an outbound edge if:
    #   - it appears as "from" in any edge, OR
    #   - it appears as "to" in a bidirectional (directed:false) edge
    can_send: set[str] = set()
    for edge in edges:
        can_send.add(edge.get("from", ""))
        if not edge.get("directed", True):   # bidirectional — "to" can also send
            can_send.add(edge.get("to", ""))
    for agent in spec.get("agents", []):
        aid = agent["id"]
        has_depends_on = bool(agent.get("depends_on"))
        if aid not in can_send and not has_depends_on:
            # Only flag if the agent actually appears in the graph at all
            in_any_edge = any(
                e.get("from") == aid or e.get("to") == aid for e in edges
            )
            if in_any_edge:
                errors.append(f"[V-CYC-008] Isolated agent with no outbound edges: '{aid}'")

    # V-CYC-009 — pm agent required for escalation
    if "pm" not in agent_ids:
        errors.append("[V-CYC-009] Cyclic pipeline requires a pm agent for escalation")

    # V-CYC-010 — .claude directory present (warning only)
    if not (pipeline_dir / ".claude").exists():
        warnings.append(
            f"[V-CYC-010] .claude/ directory absent. "
            f"Run: python cli.py hooks install {pipeline_dir}"
        )
