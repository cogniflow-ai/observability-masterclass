"""Cogniflow Configurator — pipeline validation engine.

Mirrors the Orchestrator's validate_pipeline() logic so the Configurator
guarantees that everything it produces is executable.

When the orchestrator library is importable (see orchestrator_bridge), every
save also runs orchestrator.validate.validate_pipeline() and surfaces its
errors[] inline alongside the Configurator's own structural checks. This is
the v3.5 → v4 alignment point: the runtime's word is final, and authoring
catches every error the runtime would raise.
"""
from __future__ import annotations
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import orchestrator_bridge as ob

AGENT_ID_RE = re.compile(r"^(?:\d{3}_[a-z0-9_]+|[a-z][a-z0-9_]*)$")
VALID_TYPES = {
    "orchestrator", "worker", "reviewer", "synthesizer",
    "router", "classifier", "validator", "summarizer",
}
VALID_BUDGET = {"hard_fail", "auto_summarise", "select_top_n"}
VALID_GRAPH_MODES = {"dag", "cyclic"}
VALID_EDGE_TYPES = {"feedback", "peer"}
VALID_APPROVAL_INCLUDE = {"output", "note", "full_context"}
VALID_APPROVAL_ROUTE_MODE = {"feedback", "task"}

_TAGLINE_RE = re.compile(r"<(/?)([A-Za-z_][\w-]*)>")


def validate_prompt_taglines(text: str, required: list[str]) -> str | None:
    """Check XML-like taglines in a prompt file. Mirrors the Observer's rules:

      1. Every name listed in `required` must appear as both <name> and </name>.
      2. Every tagline found in the text must have a matching partner — an
         open <x> with no </x>, or a </x> with no <x>, is rejected.
      3. Taglines cannot be nested: pairs must be strictly sequential.

    Only the no-space form <name> / </name> is considered — anything with
    attributes or whitespace is ignored, matching the editor's highlighter.

    Returns None on success, or a human-readable error message.
    """
    matches = list(_TAGLINE_RE.finditer(text))

    open_names = {m.group(2) for m in matches if not m.group(1)}
    close_names = {m.group(2) for m in matches if m.group(1)}
    missing: list[str] = []
    for name in required:
        if name not in open_names:
            missing.append(f"<{name}>")
        if name not in close_names:
            missing.append(f"</{name}>")
    if missing:
        return ("Missing required tag(s): " + ", ".join(missing) +
                ". Add the tag pair(s) to the prompt — required taglines are "
                "configured in the pipeline's Graph tab.")

    stack: list[tuple[str, int]] = []
    for m in matches:
        slash, name = m.group(1), m.group(2)
        line = text.count("\n", 0, m.start()) + 1
        if not slash:
            if stack:
                prev_name, prev_line = stack[-1]
                return (f"Nested tag on line {line}: <{name}> opens while "
                        f"<{prev_name}> (line {prev_line}) is still open. "
                        f"To fix: close </{prev_name}> before opening "
                        f"<{name}>, or move <{name}> outside <{prev_name}>. "
                        f"Taglines cannot be nested.")
            stack.append((name, line))
        else:
            if not stack:
                return (f"Stray closing tag </{name}> on line {line}: no "
                        f"matching <{name}> was opened before it. "
                        f"To fix: add an opening <{name}> earlier, or remove "
                        f"this </{name}>.")
            top_name, top_line = stack[-1]
            if top_name != name:
                return (f"Mismatched closing tag on line {line}: found "
                        f"</{name}>, but the currently open tag is "
                        f"<{top_name}> (line {top_line}). "
                        f"To fix: change </{name}> to </{top_name}>, or add "
                        f"a matching </{top_name}> before line {line}.")
            stack.pop()

    if stack:
        name, line = stack[-1]
        return (f"Unclosed tag <{name}> on line {line} is never closed. "
                f"To fix: add </{name}> somewhere after line {line}.")

    return None


def _required_taglines(data: dict, kind: str = "system") -> list[str]:
    """Return the list of required taglines for `kind` ∈ {"system", "task"}.

    pipeline.json stores two independent lists:

        "validated_taglines_system": ["role", "guardrails"]
        "validated_taglines_task":   ["goals", "input", "output"]

    Inheritance rule: when a pipeline's list is empty/missing but
    `validate_taglines` is on, fall back to the configurator's global
    defaults (`config.default_taglines_system` / `_task`). The moment the
    user adds at least one tag to a list, that list becomes the authority
    for that pipeline; if they later remove all tags, the list falls back
    to global again.

    Legacy fallback: the single-list field `validated_taglines` is still
    honoured ahead of globals if the split fields are absent.
    """
    if not data.get("validate_taglines"):
        return []
    if kind == "task":
        raw = data.get("validated_taglines_task")
    else:
        raw = data.get("validated_taglines_system")
    if raw is None:
        raw = data.get("validated_taglines")
    if raw is None:
        parsed: list[str] = []
    elif isinstance(raw, list):
        parsed = [str(t).strip() for t in raw if str(t).strip()]
    else:
        parsed = [t.strip() for t in str(raw).split(",") if t.strip()]
    if parsed:
        return parsed
    # Inherit globals.
    try:
        from .config import settings as _settings
    except Exception:
        return []
    if kind == "task":
        return list(_settings.default_taglines_task)
    return list(_settings.default_taglines_system)


def global_default_taglines(kind: str) -> list[str]:
    """Expose the global defaults (read-only snapshot) for the UI helper
    text that shows users what they'd inherit if they leave a list empty."""
    try:
        from .config import settings as _settings
    except Exception:
        return []
    if kind == "task":
        return list(_settings.default_taglines_task)
    return list(_settings.default_taglines_system)


def _kind_for_filename(filename: str) -> str:
    """Map '01_system.md' → 'system', '02_prompt.md' → 'task'."""
    return "task" if filename.endswith("02_prompt.md") else "system"


@dataclass
class Issue:
    level: str          # "error" | "warning"
    section: str        # "graph" | "agents" | "prompts" | "files"
    field: str          # JSON-path style, e.g. "agents[0].type"
    message: str

    def as_dict(self) -> dict:
        return {"level": self.level, "section": self.section,
                "field": self.field, "message": self.message}


@dataclass
class ValidationResult:
    issues: list[Issue] = field(default_factory=list)

    def error(self, section: str, field: str, message: str):
        self.issues.append(Issue("error", section, field, message))

    def warning(self, section: str, field: str, message: str):
        self.issues.append(Issue("warning", section, field, message))

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.level == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.level == "warning"]

    @property
    def status(self) -> str:
        if self.errors:
            return "errors"
        if self.warnings:
            return "warnings"
        return "valid"

    @property
    def badge_color(self) -> str:
        return {"errors": "red", "warnings": "amber", "valid": "green"}[self.status]

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "badge_color": self.badge_color,
            "errors": [i.as_dict() for i in self.errors],
            "warnings": [i.as_dict() for i in self.warnings],
        }


def validate_pipeline(pipeline_dir: Path, data: dict | None = None) -> ValidationResult:
    """Validate a pipeline. If data is provided use it, otherwise read pipeline.json."""
    result = ValidationResult()
    pipeline_json = pipeline_dir / "pipeline.json"

    # --- JSON syntax ---
    if data is None:
        if not pipeline_json.exists():
            result.error("graph", "pipeline.json", "pipeline.json not found")
            return result
        try:
            data = json.loads(pipeline_json.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            result.error("graph", "pipeline.json", f"JSON syntax error: {e}")
            return result

    # --- Required top-level fields ---
    for f in ("name", "agents"):
        if f not in data:
            result.error("graph", f, f"Required field '{f}' is missing")

    if "agents" not in data or not isinstance(data["agents"], list):
        result.error("graph", "agents", "agents must be a list")
        return result

    graph_mode = data.get("graph_mode", "dag")
    if graph_mode not in VALID_GRAPH_MODES:
        result.error("graph", "graph_mode",
                     f"graph_mode must be one of {sorted(VALID_GRAPH_MODES)}, got '{graph_mode}'")
        graph_mode = "dag"

    agents = data["agents"]
    agent_ids: set[str] = set()

    # --- Agent-level checks ---
    for idx, agent in enumerate(agents):
        prefix = f"agents[{idx}]"
        if not isinstance(agent, dict):
            result.error("agents", prefix, "Each agent must be a JSON object")
            continue

        # id
        aid = agent.get("id", "")
        if not aid:
            result.error("agents", f"{prefix}.id", "Agent id is missing")
        elif not AGENT_ID_RE.match(aid):
            result.error("agents", f"{prefix}.id",
                         f"Agent id '{aid}' must match NNN_name (e.g. 001_researcher) "
                         f"or be a plain lowercase identifier (e.g. 'pm')")
        elif aid in agent_ids:
            result.error("agents", f"{prefix}.id", f"Duplicate agent id '{aid}'")
        else:
            agent_ids.add(aid)

        # depends_on must be present
        if "depends_on" not in agent:
            result.error("agents", f"{prefix}.depends_on",
                         f"Agent '{aid}' is missing required field depends_on")

        # type
        atype = agent.get("type")
        if atype and atype not in VALID_TYPES:
            result.error("agents", f"{prefix}.type",
                         f"Agent type '{atype}' is not valid. Must be one of {sorted(VALID_TYPES)}")

        # budget_strategy
        bs = agent.get("budget_strategy")
        if bs and bs not in VALID_BUDGET:
            result.error("agents", f"{prefix}.budget_strategy",
                         f"budget_strategy '{bs}' must be one of {sorted(VALID_BUDGET)}")

    # --- Edge integrity: depends_on references ---
    for idx, agent in enumerate(agents):
        if not isinstance(agent, dict):
            continue
        aid = agent.get("id", f"[{idx}]")
        for dep in agent.get("depends_on", []):
            if dep not in agent_ids:
                result.error("graph", f"agents[{idx}].depends_on",
                             f"Agent '{aid}' depends_on '{dep}' which does not exist")

    # --- DAG cycle detection ---
    if graph_mode == "dag":
        cycle = _detect_cycle(agents)
        if cycle:
            result.error("graph", "depends_on",
                         f"Cycle detected in DAG: {' → '.join(cycle)}")

    # --- Cyclic edge validation ---
    edges = data.get("edges", [])
    if edges and graph_mode == "dag":
        result.error("graph", "edges",
                     "edges array with feedback/peer types is not allowed in dag mode")

    if graph_mode == "cyclic":
        seen_edges: set[tuple] = set()
        for i, edge in enumerate(edges):
            if not isinstance(edge, dict):
                continue
            ef, et, etype = edge.get("from"), edge.get("to"), edge.get("type")
            if ef not in agent_ids:
                result.error("graph", f"edges[{i}].from",
                             f"Edge from '{ef}' references non-existent agent")
            if et not in agent_ids:
                result.error("graph", f"edges[{i}].to",
                             f"Edge to '{et}' references non-existent agent")
            if etype not in VALID_EDGE_TYPES:
                result.error("graph", f"edges[{i}].type",
                             f"Edge type '{etype}' must be feedback or peer")
            key = (ef, et, etype)
            if key in seen_edges:
                result.error("graph", f"edges[{i}]",
                             f"Duplicate edge {ef} → {et} ({etype})")
            seen_edges.add(key)

        # Termination condition
        term = data.get("termination", {})
        if not term.get("max_cycles") and not term.get("convergence_agent"):
            result.warning("graph", "termination",
                           "Cyclic pipeline has no termination condition defined "
                           "(termination.max_cycles or termination.convergence_agent). "
                           "Pipeline cannot be safely launched.")

    # --- Prompt files ---
    required_sys  = _required_taglines(data, "system")
    required_task = _required_taglines(data, "task")
    for idx, agent in enumerate(agents):
        if not isinstance(agent, dict):
            continue
        aid = agent.get("id", f"[{idx}]")
        agent_dir = pipeline_dir / "agents" / aid
        for prompt in ("01_system.md", "02_prompt.md"):
            pf = agent_dir / prompt
            if not pf.exists():
                result.error("prompts", f"agents/{aid}/{prompt}",
                             f"Missing prompt file: agents/{aid}/{prompt}")
                continue
            required_tags = (required_task if prompt == "02_prompt.md"
                             else required_sys)
            if required_tags or pf.stat().st_size > 0:
                try:
                    body = pf.read_text(encoding="utf-8")
                except OSError:
                    continue
                tag_err = validate_prompt_taglines(body, required_tags)
                if tag_err:
                    result.error("prompts", f"agents/{aid}/{prompt}", tag_err)

        sys_prompt = agent_dir / "01_system.md"
        if sys_prompt.exists() and sys_prompt.stat().st_size == 0:
            result.warning("prompts", f"agents/{aid}/01_system.md",
                           f"Agent '{aid}' has an empty system prompt")

    # --- Per-agent 00_config.json (input_schema, output_schema, approval_routes)
    _validate_agent_configs(pipeline_dir, agents, agent_ids, graph_mode, result)

    # --- Pipeline-level config.json (approval, secrets) ---
    _validate_pipeline_config(pipeline_dir, result)

    # --- Library-call to orchestrator.validate (if available) ---------------
    # The orchestrator's checks are authoritative; surface any errors it
    # raises that we missed locally. We collect them under their own section
    # so the UI can render them next to the offending agent (V-CYC-005,
    # V-APPROVE-001, etc. all carry an agent id in the message).
    orch_errors = _run_orchestrator_validate(pipeline_dir)
    for msg in orch_errors:
        section, field_anchor = _classify_orchestrator_error(msg, agent_ids)
        # Skip duplicate noise: if the local result already has an error at
        # the same field with the same wording, don't add a second copy.
        if any(i.message == msg and i.field == field_anchor
               for i in result.issues):
            continue
        result.error(section, field_anchor, msg)

    return result


# ── New v4 sub-validators ────────────────────────────────────────────────────

def _validate_agent_configs(pipeline_dir: Path, agents: list[dict],
                            agent_ids: set[str], graph_mode: str,
                            result: "ValidationResult") -> None:
    """Shape-check each agent's 00_config.json (when present) for the v4
    blocks the Configurator now writes: input_schema, output_schema,
    approval_routes. This is a *shape* check; the orchestrator's
    validate_pipeline call is the authority on rule-level errors."""
    has_cyclic_edges = graph_mode == "cyclic"
    for agent in agents:
        if not isinstance(agent, dict):
            continue
        aid = agent.get("id", "")
        if not aid:
            continue
        cfg_path = pipeline_dir / "agents" / aid / "00_config.json"
        if not cfg_path.exists():
            continue
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            result.error("agents", f"agents/{aid}/00_config.json",
                         f"00_config.json is not valid JSON: {e}")
            continue
        if not isinstance(cfg, dict):
            continue

        for schema_key in ("input_schema", "output_schema"):
            sch = cfg.get(schema_key)
            if sch is None:
                continue
            if not isinstance(sch, dict):
                result.error("agents", f"agents/{aid}/{schema_key}",
                             f"'{schema_key}' must be an object")
                continue
            modes = sch.get("mode", [])
            if isinstance(modes, str):
                modes = [modes]
            if not isinstance(modes, list):
                result.error("agents", f"agents/{aid}/{schema_key}.mode",
                             f"{schema_key}.mode must be a string or list of strings")
                continue
            for m in modes:
                if m not in ob.VALID_SCHEMA_MODES:
                    result.error("agents", f"agents/{aid}/{schema_key}.mode",
                                 f"Unknown {schema_key} mode '{m}'. "
                                 f"Valid: {sorted(ob.VALID_SCHEMA_MODES)}")
            sections = sch.get("sections")
            if sections is not None and not (
                isinstance(sections, list)
                and all(isinstance(x, str) for x in sections)
            ):
                result.error("agents", f"agents/{aid}/{schema_key}.sections",
                             f"{schema_key}.sections must be a list of strings")
            contains = sch.get("contains")
            if contains is not None and not (
                isinstance(contains, list)
                and all(isinstance(x, str) for x in contains)
            ):
                result.error("agents", f"agents/{aid}/{schema_key}.contains",
                             f"{schema_key}.contains must be a list of strings")
            if schema_key == "input_schema":
                req_up = sch.get("require_upstream")
                if req_up is not None and not (
                    isinstance(req_up, list)
                    and all(isinstance(x, str) for x in req_up)
                ):
                    result.error(
                        "agents", f"agents/{aid}/input_schema.require_upstream",
                        "require_upstream must be a list of agent IDs")
                elif isinstance(req_up, list):
                    declared = set(agent.get("depends_on", []) or [])
                    for up in req_up:
                        if up not in agent_ids:
                            result.error(
                                "agents", f"agents/{aid}/input_schema.require_upstream",
                                f"require_upstream references unknown agent '{up}'")
                        elif declared and up not in declared:
                            result.error(
                                "agents", f"agents/{aid}/input_schema.require_upstream",
                                f"require_upstream '{up}' is not in depends_on")
                si_req = sch.get("static_inputs_required")
                if si_req is not None and not isinstance(si_req, bool):
                    result.error(
                        "agents", f"agents/{aid}/input_schema.static_inputs_required",
                        "static_inputs_required must be true or false")

        # ── approval_routes (V-APPROVE-001 shape) ────────────────────────────
        routes = cfg.get("approval_routes")
        if routes is not None:
            if not cfg.get("requires_approval"):
                result.error(
                    "agents", f"agents/{aid}/approval_routes",
                    "approval_routes declared but requires_approval is not true")
            if not isinstance(routes, dict):
                result.error("agents", f"agents/{aid}/approval_routes",
                             "approval_routes must be an object")
            else:
                for rkey in ("on_reject", "on_approve"):
                    route = routes.get(rkey)
                    if route is None:
                        continue
                    if not isinstance(route, dict):
                        result.error(
                            "agents", f"agents/{aid}/approval_routes.{rkey}",
                            f"approval_routes.{rkey} must be an object")
                        continue
                    target = route.get("target")
                    if target is not None:
                        if not isinstance(target, str):
                            result.error(
                                "agents",
                                f"agents/{aid}/approval_routes.{rkey}.target",
                                "target must be a string")
                        elif target == aid:
                            result.error(
                                "agents",
                                f"agents/{aid}/approval_routes.{rkey}.target",
                                "[V-APPROVE-001] target must not equal the gate "
                                "agent itself")
                        elif target not in agent_ids:
                            result.error(
                                "agents",
                                f"agents/{aid}/approval_routes.{rkey}.target",
                                f"[V-APPROVE-001] target references unknown "
                                f"agent '{target}'")
                    include = route.get("include", ["output"])
                    if not isinstance(include, list) or not all(
                        isinstance(x, str) for x in include
                    ):
                        result.error(
                            "agents",
                            f"agents/{aid}/approval_routes.{rkey}.include",
                            "include must be a list of strings")
                    else:
                        for part in include:
                            if part not in VALID_APPROVAL_INCLUDE:
                                result.error(
                                    "agents",
                                    f"agents/{aid}/approval_routes.{rkey}.include",
                                    f"Unknown include entry '{part}'. "
                                    f"Valid: {sorted(VALID_APPROVAL_INCLUDE)}")
                    mode_val = route.get("mode", "feedback")
                    if mode_val not in VALID_APPROVAL_ROUTE_MODE:
                        result.error(
                            "agents",
                            f"agents/{aid}/approval_routes.{rkey}.mode",
                            f"mode '{mode_val}' is invalid. "
                            f"Valid: {sorted(VALID_APPROVAL_ROUTE_MODE)}")
                if not has_cyclic_edges:
                    result.error(
                        "agents", f"agents/{aid}/approval_routes",
                        "approval_routes is only supported on cyclic pipelines. "
                        "In DAG mode, rejection stops the run.")


def _validate_pipeline_config(pipeline_dir: Path,
                              result: "ValidationResult") -> None:
    """Shape-check <pipeline>/config.json — the file the Configurator now
    writes for approval and secrets settings. The orchestrator already
    tolerates unknown keys, so we only flag malformed values."""
    cfg_path = pipeline_dir / "config.json"
    if not cfg_path.exists():
        return
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        result.error("graph", "config.json", f"config.json is not valid JSON: {e}")
        return
    if not isinstance(cfg, dict):
        return
    appr = cfg.get("approval", {})
    if isinstance(appr, dict):
        for k in ("poll_interval_s", "timeout_s"):
            if k in appr:
                v = appr[k]
                if not isinstance(v, int) or v < 0:
                    result.error("graph", f"config.json.approval.{k}",
                                 f"approval.{k} must be a non-negative int, got {v!r}")
        approver = appr.get("approver")
        if approver is not None and not isinstance(approver, str):
            result.error("graph", "config.json.approval.approver",
                         "approval.approver must be a string")
    sec = cfg.get("secrets", {})
    if isinstance(sec, dict):
        ro = sec.get("rehydrate_outputs")
        if ro is not None and not isinstance(ro, bool):
            result.error("graph", "config.json.secrets.rehydrate_outputs",
                         "secrets.rehydrate_outputs must be true or false")


def _run_orchestrator_validate(pipeline_dir: Path) -> list[str]:
    """Call orchestrator.validate.validate_pipeline as a library and return
    the errors[] list it would raise. Returns [] on success or when the
    orchestrator library cannot be imported."""
    if not ob.is_available():
        return []
    try:
        ob.validate_pipeline(pipeline_dir)
    except ob.PipelineValidationError as e:
        errs = getattr(e, "errors", None)
        if isinstance(errs, list):
            return [str(x) for x in errs]
        return [str(e)]
    except Exception as e:  # pragma: no cover — defensive
        return [f"orchestrator.validate raised {type(e).__name__}: {e}"]
    return []


_ERR_AGENT_RX = re.compile(r"Agent\s+'([^']+)'")


def _classify_orchestrator_error(msg: str,
                                 agent_ids: set[str]) -> tuple[str, str]:
    """Map an orchestrator error string to (section, field_anchor) so the UI
    can render it next to the right agent / panel.

    - 'Agent <id>: ...'           → ('agents', 'agents/<id>/...')
    - '[V-CYC-...]'                → ('graph', 'pipeline.json')
    - everything else              → ('graph', 'pipeline.json')
    """
    if msg.startswith("[V-CYC"):
        return ("graph", "pipeline.json")
    m = _ERR_AGENT_RX.search(msg)
    if m and m.group(1) in agent_ids:
        return ("agents", f"agents/{m.group(1)}/")
    return ("graph", "pipeline.json")


def _detect_cycle(agents: list[dict]) -> list[str] | None:
    """Return a cycle path if one exists, else None."""
    graph: dict[str, list[str]] = {}
    for agent in agents:
        if isinstance(agent, dict):
            graph[agent.get("id", "")] = agent.get("depends_on", [])

    visited: set[str] = set()
    rec_stack: set[str] = set()
    path: list[str] = []

    def dfs(node: str) -> bool:
        visited.add(node)
        rec_stack.add(node)
        path.append(node)
        for nb in graph.get(node, []):
            if nb not in visited:
                if dfs(nb):
                    return True
            elif nb in rec_stack:
                path.append(nb)
                return True
        path.pop()
        rec_stack.discard(node)
        return False

    for node in list(graph):
        if node not in visited:
            path.clear()
            if dfs(node):
                # trim path to the cycle
                start = path[-1]
                cycle = path[path.index(start):]
                return cycle
    return None


def check_running(pipeline_dir: Path) -> bool:
    """Return True if the pipeline appears to be running."""
    status_file = pipeline_dir / ".state" / "pipeline_status.json"
    if not status_file.exists():
        return False
    try:
        data = json.loads(status_file.read_text(encoding="utf-8"))
        return data.get("status") in ("running", "approved")
    except Exception:
        return False
