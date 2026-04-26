"""
Cogniflow Orchestrator v3.5 — Output schema validation (GAP-1, restored).

When an agent's 00_config.json declares an ``output_schema``, the orchestrator
validates the agent's output immediately after a successful invocation.
A violation is treated as a hard failure — the agent is re-runnable, and
the violation details are written to 06_status.json and the event log.

Supported modes (combine any subset — all must pass):

  ``json``          output must be valid JSON, optionally matching a JSON Schema
  ``regex``         output must match every listed regular expression
  ``contains``      output must contain every listed substring
  ``not_contains``  output must NOT contain any listed substring
  ``min_words``     at least N words
  ``max_words``     at most N words
  ``starts_with``   text (stripped) starts with a prefix
  ``ends_with``     text (stripped) ends with a suffix

Example 00_config.json::

    {
      "output_schema": {
        "mode": ["min_words", "not_contains"],
        "min_words": 400,
        "not_contains": ["[PLACEHOLDER]", "TODO"]
      }
    }
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Union

from .exceptions import SchemaViolationError


VALID_MODES = {
    "json", "regex", "contains", "not_contains",
    "min_words", "max_words", "starts_with", "ends_with",
    "has_sections",
}


def validate_output_schema(
    agent_id: str,
    output: Union[str, Path],
    schema: dict[str, Any],
) -> None:
    """
    Validate *output* against *schema*.

    ``output`` may be a string (the text itself) or a Path to a file.
    Raises ``SchemaViolationError`` listing every violation found; passes
    silently when schema is None/empty.
    """
    if not schema:
        return

    text = _read(output)
    modes = schema.get("mode", [])
    if isinstance(modes, str):
        modes = [modes]

    violations: list[str] = []
    for mode in modes:
        checker = _CHECKERS.get(mode)
        if checker is None:
            violations.append(f"Unknown schema mode '{mode}'")
            continue
        violations.extend(checker(text, schema))

    if violations:
        raise SchemaViolationError(agent_id, violations)


def schema_from_agent_config(agent_dir: Path) -> dict[str, Any] | None:
    """Return the ``output_schema`` block from 00_config.json, or None if absent."""
    cfg_path = agent_dir / "00_config.json"
    if not cfg_path.exists():
        return None
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return cfg.get("output_schema") or None


def input_schema_from_agent_config(agent_dir: Path) -> dict[str, Any] | None:
    """Return the ``input_schema`` block from 00_config.json, or None if absent."""
    cfg_path = agent_dir / "00_config.json"
    if not cfg_path.exists():
        return None
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return cfg.get("input_schema") or None


def validate_input_schema(
    agent_id: str,
    schema: dict[str, Any],
    upstream_outputs: dict[str, str],
    static_inputs: dict[str, str] | None = None,
) -> None:
    """
    Validate this agent's inputs against its declared ``input_schema``.

    *upstream_outputs* maps ``upstream_agent_id → 05_output.md text``.
    *static_inputs* maps ``filename → file contents`` (optional).

    The same mode vocabulary as ``output_schema`` is used; additionally
    the following fields shape the check:

      ``require_upstream`` — list of upstream agent IDs whose outputs
                             must pass the schema. Defaults to every key
                             in *upstream_outputs*.
      ``static_inputs_required`` — when true, every static input file
                             must be present AND non-empty.

    Violations across upstreams are prefixed with ``[from <agent_id>]``
    so the operator can tell which upstream failed.

    Raises ``SchemaViolationError(phase="input")`` on any violation.
    """
    if not schema:
        return

    modes = schema.get("mode", [])
    if isinstance(modes, str):
        modes = [modes]

    require = schema.get("require_upstream")
    if require is None:
        require = list(upstream_outputs.keys())
    elif isinstance(require, str):
        require = [require]

    violations: list[str] = []

    # Per-upstream schema check: reuse the output-schema mode checkers
    # by treating each upstream's text as if it were "the output".
    for up_id in require:
        text = upstream_outputs.get(up_id)
        if text is None:
            violations.append(f"upstream '{up_id}' produced no output")
            continue
        for mode in modes:
            checker = _CHECKERS.get(mode)
            if checker is None:
                violations.append(f"Unknown schema mode '{mode}'")
                continue
            for v in checker(text, schema):
                violations.append(f"[from {up_id}] {v}")

    # Static inputs presence/non-empty check.
    if schema.get("static_inputs_required"):
        si = static_inputs or {}
        if not si:
            violations.append(
                "static_inputs_required=true but no static inputs were provided"
            )
        else:
            for fname, content in si.items():
                if not (content or "").strip():
                    violations.append(f"static input '{fname}' is empty")

    if violations:
        raise SchemaViolationError(agent_id, violations, phase="input")


# ── Internal ──────────────────────────────────────────────────────────────────

def _read(output: Union[str, Path]) -> str:
    if isinstance(output, Path):
        return output.read_text(encoding="utf-8")
    return output


def _check_json(text: str, schema: dict[str, Any]) -> list[str]:
    """Output must be valid JSON; optionally conform to a JSON Schema."""
    payload = text.strip()
    # Strip markdown code fences if present
    if payload.startswith("```"):
        lines = payload.split("\n")
        if lines[-1].strip().startswith("```"):
            payload = "\n".join(lines[1:-1]).strip()
        else:
            payload = "\n".join(lines[1:]).strip()

    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as exc:
        return [f"Output is not valid JSON: {exc}"]

    json_schema = schema.get("json_schema")
    if not json_schema:
        return []

    try:
        import jsonschema  # optional dependency
        try:
            jsonschema.validate(instance=parsed, schema=json_schema)
        except jsonschema.ValidationError as exc:
            return [f"JSON Schema validation failed: {exc.message}"]
        except jsonschema.SchemaError as exc:
            return [f"Output schema definition is invalid: {exc.message}"]
    except ImportError:
        # Fallback: manual required-field check for object schemas
        if json_schema.get("type") == "object":
            required = json_schema.get("required", [])
            if not isinstance(parsed, dict):
                return ["Expected a JSON object at the top level"]
            missing = [k for k in required if k not in parsed]
            if missing:
                return [f"JSON output missing required fields: {missing}"]

    return []


def _check_regex(text: str, schema: dict[str, Any]) -> list[str]:
    patterns = schema.get("regex", [])
    if isinstance(patterns, str):
        patterns = [patterns]
    errs: list[str] = []
    for pat in patterns:
        try:
            if not re.search(pat, text, re.MULTILINE | re.DOTALL):
                errs.append(f"Output does not match required pattern: {pat!r}")
        except re.error as exc:
            errs.append(f"Invalid regex pattern {pat!r}: {exc}")
    return errs


def _check_contains(text: str, schema: dict[str, Any]) -> list[str]:
    required = schema.get("contains", [])
    case_sensitive = bool(schema.get("case_sensitive", False))
    haystack = text if case_sensitive else text.lower()
    return [
        f"Output missing required substring: {s!r}"
        for s in required
        if (s if case_sensitive else s.lower()) not in haystack
    ]


def _check_not_contains(text: str, schema: dict[str, Any]) -> list[str]:
    forbidden = schema.get("not_contains", [])
    case_sensitive = bool(schema.get("case_sensitive", False))
    haystack = text if case_sensitive else text.lower()
    return [
        f"Output contains forbidden substring: {s!r}"
        for s in forbidden
        if (s if case_sensitive else s.lower()) in haystack
    ]


def _check_min_words(text: str, schema: dict[str, Any]) -> list[str]:
    minimum = int(schema.get("min_words", 0))
    count   = len(text.split())
    return [f"Output has {count} words, minimum is {minimum}"] if count < minimum else []


def _check_max_words(text: str, schema: dict[str, Any]) -> list[str]:
    maximum = int(schema.get("max_words", 0))
    count   = len(text.split())
    return [f"Output has {count} words, maximum is {maximum}"] if count > maximum else []


def _check_starts_with(text: str, schema: dict[str, Any]) -> list[str]:
    prefix = str(schema.get("starts_with", ""))
    return [f"Output does not start with {prefix!r}"] \
        if not text.strip().startswith(prefix) else []


def _check_ends_with(text: str, schema: dict[str, Any]) -> list[str]:
    suffix = str(schema.get("ends_with", ""))
    return [f"Output does not end with {suffix!r}"] \
        if not text.strip().endswith(suffix) else []


def _check_has_sections(text: str, schema: dict[str, Any]) -> list[str]:
    """Every listed section title must appear as a markdown heading (any level)."""
    required = schema.get("sections", [])
    if isinstance(required, str):
        required = [required]
    headings: set[str] = set()
    for line in text.splitlines():
        m = re.match(r"^\s*#{1,6}\s+(.+?)\s*$", line)
        if m:
            headings.add(m.group(1).strip())
    return [
        f"Missing required section heading: {sec!r}"
        for sec in required
        if sec not in headings
    ]


_CHECKERS = {
    "json":         _check_json,
    "regex":        _check_regex,
    "contains":     _check_contains,
    "not_contains": _check_not_contains,
    "min_words":    _check_min_words,
    "max_words":    _check_max_words,
    "starts_with":  _check_starts_with,
    "ends_with":    _check_ends_with,
    "has_sections": _check_has_sections,
}
