"""Cogniflow Configurator — pipeline validation engine.

Mirrors the Orchestrator's validate_pipeline() logic so the Configurator
guarantees that everything it produces is executable.
"""
from __future__ import annotations
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

AGENT_ID_RE = re.compile(r"^\d{3}_[a-z0-9_]+$")
VALID_TYPES = {
    "orchestrator", "worker", "reviewer", "synthesizer",
    "router", "classifier", "validator", "summarizer",
}
VALID_BUDGET = {"hard_fail", "auto_summarise", "select_top_n"}
VALID_GRAPH_MODES = {"dag", "cyclic"}
VALID_EDGE_TYPES = {"feedback", "peer"}

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
        from config import settings as _settings
    except Exception:
        return []
    if kind == "task":
        return list(_settings.default_taglines_task)
    return list(_settings.default_taglines_system)


def global_default_taglines(kind: str) -> list[str]:
    """Expose the global defaults (read-only snapshot) for the UI helper
    text that shows users what they'd inherit if they leave a list empty."""
    try:
        from config import settings as _settings
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
                         f"Agent id '{aid}' must match NNN_name (e.g. 001_researcher)")
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

    return result


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
