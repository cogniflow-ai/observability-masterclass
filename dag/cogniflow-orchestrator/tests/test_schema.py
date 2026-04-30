"""Tests for GAP-1 output schema validation."""
from __future__ import annotations

import json
import pytest

from orchestrator.schema import (
    validate_output_schema,
    schema_from_agent_config,
    VALID_MODES,
)
from orchestrator.exceptions import SchemaViolationError


# ── Passthrough / empty ───────────────────────────────────────────────────────

def test_empty_schema_passes():
    # Empty schema → no validation, no exception
    validate_output_schema("a", "anything", {})


def test_none_schema_passes():
    validate_output_schema("a", "anything", None)  # type: ignore[arg-type]


# ── min_words / max_words ─────────────────────────────────────────────────────

def test_min_words_passes():
    text = " ".join(["word"] * 100)
    validate_output_schema("a", text, {"mode": "min_words", "min_words": 50})


def test_min_words_fails():
    with pytest.raises(SchemaViolationError) as exc:
        validate_output_schema("a", "three words only", {"mode": "min_words", "min_words": 50})
    assert any("minimum is 50" in v for v in exc.value.violations)


def test_max_words_fails():
    text = " ".join(["word"] * 200)
    with pytest.raises(SchemaViolationError):
        validate_output_schema("a", text, {"mode": "max_words", "max_words": 50})


# ── contains / not_contains ───────────────────────────────────────────────────

def test_contains_passes_case_insensitive():
    validate_output_schema("a", "The EXECUTIVE SUMMARY is here",
                           {"mode": "contains", "contains": ["executive summary"]})


def test_contains_fails():
    with pytest.raises(SchemaViolationError):
        validate_output_schema("a", "no such section",
                               {"mode": "contains", "contains": ["## Summary"]})


def test_not_contains_flags_forbidden():
    with pytest.raises(SchemaViolationError) as exc:
        validate_output_schema("a", "there's a TODO here",
                               {"mode": "not_contains", "not_contains": ["TODO"]})
    assert any("TODO" in v for v in exc.value.violations)


def test_case_sensitive_contains():
    # Lowercase haystack does not satisfy uppercase required substring
    with pytest.raises(SchemaViolationError):
        validate_output_schema("a", "foo bar",
                               {"mode": "contains",
                                "contains": ["FOO"],
                                "case_sensitive": True})


# ── regex ─────────────────────────────────────────────────────────────────────

def test_regex_passes():
    validate_output_schema("a", "# Heading\n\nBody",
                           {"mode": "regex", "regex": [r"^#\s"]})


def test_regex_fails():
    with pytest.raises(SchemaViolationError):
        validate_output_schema("a", "no heading",
                               {"mode": "regex", "regex": [r"^#\s"]})


# ── starts_with / ends_with ───────────────────────────────────────────────────

def test_starts_with_passes():
    validate_output_schema("a", "  # Title\n",
                           {"mode": "starts_with", "starts_with": "# Title"})


def test_ends_with_fails():
    with pytest.raises(SchemaViolationError):
        validate_output_schema("a", "no period",
                               {"mode": "ends_with", "ends_with": "."})


# ── json ──────────────────────────────────────────────────────────────────────

def test_json_passes_valid_object():
    validate_output_schema("a", '{"decision": "yes", "reason": "ok"}',
                           {"mode": "json"})


def test_json_fails_invalid_syntax():
    with pytest.raises(SchemaViolationError):
        validate_output_schema("a", "{ not json",
                               {"mode": "json"})


def test_json_strips_markdown_fence():
    fenced = '```json\n{"x": 1}\n```'
    validate_output_schema("a", fenced, {"mode": "json"})


def test_json_schema_required_fallback():
    # When jsonschema package is unavailable, manual required-field check runs
    out = '{"decision": "yes"}'
    with pytest.raises(SchemaViolationError) as exc:
        validate_output_schema("a", out, {
            "mode": "json",
            "json_schema": {
                "type": "object",
                "required": ["decision", "reason"],
            },
        })
    assert any("reason" in v for v in exc.value.violations)


# ── Combined modes ────────────────────────────────────────────────────────────

def test_multiple_modes_all_must_pass():
    text = "# Section\nrequired"
    validate_output_schema("a", text, {
        "mode": ["contains", "starts_with"],
        "contains":    ["required"],
        "starts_with": "#",
    })


def test_multiple_modes_collects_all_violations():
    with pytest.raises(SchemaViolationError) as exc:
        validate_output_schema("a", "bad output", {
            "mode": ["min_words", "contains"],
            "min_words": 100,
            "contains": ["## Summary"],
        })
    assert len(exc.value.violations) == 2


def test_unknown_mode_is_a_violation():
    with pytest.raises(SchemaViolationError) as exc:
        validate_output_schema("a", "x", {"mode": "teleport"})
    assert any("Unknown schema mode" in v for v in exc.value.violations)


# ── Config discovery ──────────────────────────────────────────────────────────

def test_schema_from_agent_config_reads_file(tmp_path):
    (tmp_path / "00_config.json").write_text(
        json.dumps({"output_schema": {"mode": "min_words", "min_words": 10}}),
        encoding="utf-8",
    )
    out = schema_from_agent_config(tmp_path)
    assert out is not None
    assert out["min_words"] == 10


def test_schema_from_agent_config_returns_none_when_absent(tmp_path):
    assert schema_from_agent_config(tmp_path) is None


def test_valid_modes_constant():
    # VALID_MODES is the source of truth used by validate.py for V-CYC/GAP-1
    # Changing this requires updating validate_pipeline() in parallel.
    assert "json" in VALID_MODES
    assert "min_words" in VALID_MODES
    # v4 added "has_sections" so the same mode whitelist can be reused by
    # the new input_schema block (see schema.validate_input_schema).
    assert "has_sections" in VALID_MODES
    assert len(VALID_MODES) == 9
