"""Tests for validate_pipeline() including all V-CYC checks."""
import json
import pytest
from pathlib import Path
from orchestrator.validate import validate_pipeline
from orchestrator.exceptions import PipelineValidationError


def _make_pipeline(tmp_path, spec, agents=None):
    """Write pipeline.json and agent directories to tmp_path."""
    pj = tmp_path / "pipeline.json"
    pj.write_text(json.dumps(spec), encoding="utf-8")
    for a in (agents or spec.get("agents", [])):
        aid = a["id"]
        adir_rel = a.get("dir", aid)
        adir = tmp_path / adir_rel
        adir.mkdir(parents=True, exist_ok=True)
        (adir / "01_system.md").write_text(f"# {aid} system", encoding="utf-8")
        (adir / "02_prompt.md").write_text(f"# {aid} prompt", encoding="utf-8")
    return tmp_path


_VALID_CYCLIC = {
    "name": "test",
    "agents": [
        {"id": "pm", "dir": "00_pm"},
        {"id": "architect", "dir": "01_architect"},
        {"id": "developer_1", "dir": "02_dev"},
    ],
    "edges": [
        {"from": "pm", "to": "architect", "type": "task", "directed": True},
        {"from": "architect", "to": "developer_1", "type": "feedback", "directed": False},
    ],
    "termination": {
        "strategy": "all_done",
        "max_cycles": 5,
        "timeout_s": 3600,
        "on_cycle_limit": "escalate_pm",
    },
    "tags": {"domain": ["auth", "api"]},
}


def test_valid_cyclic_pipeline(tmp_path):
    _make_pipeline(tmp_path, _VALID_CYCLIC)
    spec = validate_pipeline(tmp_path)
    assert spec["name"] == "test"


def test_vcyc_001_missing_termination(tmp_path):
    spec = {**_VALID_CYCLIC}
    del spec["termination"]
    _make_pipeline(tmp_path, spec)
    with pytest.raises(PipelineValidationError) as exc_info:
        validate_pipeline(tmp_path)
    assert any("V-CYC-001" in e for e in exc_info.value.errors)


def test_vcyc_002_bad_strategy(tmp_path):
    spec = {**_VALID_CYCLIC,
            "termination": {**_VALID_CYCLIC["termination"], "strategy": "invalid"}}
    _make_pipeline(tmp_path, spec)
    with pytest.raises(PipelineValidationError) as exc_info:
        validate_pipeline(tmp_path)
    assert any("V-CYC-002" in e for e in exc_info.value.errors)


def test_vcyc_003_max_cycles_too_low(tmp_path):
    spec = {**_VALID_CYCLIC,
            "termination": {**_VALID_CYCLIC["termination"], "max_cycles": 1}}
    _make_pipeline(tmp_path, spec)
    with pytest.raises(PipelineValidationError) as exc_info:
        validate_pipeline(tmp_path)
    assert any("V-CYC-003" in e for e in exc_info.value.errors)


def test_vcyc_004_unknown_agent_in_edge(tmp_path):
    spec = {**_VALID_CYCLIC, "edges": [
        {"from": "pm", "to": "nonexistent", "type": "task", "directed": True},
        {"from": "architect", "to": "developer_1", "type": "feedback", "directed": False},
    ]}
    _make_pipeline(tmp_path, spec)
    with pytest.raises(PipelineValidationError) as exc_info:
        validate_pipeline(tmp_path)
    assert any("V-CYC-004" in e for e in exc_info.value.errors)


def test_vcyc_006_feedback_directed_true(tmp_path):
    spec = {**_VALID_CYCLIC, "edges": [
        {"from": "pm", "to": "architect", "type": "task", "directed": True},
        {"from": "architect", "to": "developer_1", "type": "feedback", "directed": True},
    ]}
    _make_pipeline(tmp_path, spec)
    with pytest.raises(PipelineValidationError) as exc_info:
        validate_pipeline(tmp_path)
    assert any("V-CYC-006" in e for e in exc_info.value.errors)


def test_vcyc_007_missing_domain_tags(tmp_path):
    spec = {**_VALID_CYCLIC}
    del spec["tags"]
    _make_pipeline(tmp_path, spec)
    with pytest.raises(PipelineValidationError) as exc_info:
        validate_pipeline(tmp_path)
    assert any("V-CYC-007" in e for e in exc_info.value.errors)


def test_vcyc_009_no_pm_agent(tmp_path):
    spec = {
        "name": "no-pm",
        "agents": [
            {"id": "architect", "dir": "01_arch"},
            {"id": "developer_1", "dir": "02_dev"},
        ],
        "edges": [
            {"from": "architect", "to": "developer_1", "type": "feedback", "directed": False},
        ],
        "termination": _VALID_CYCLIC["termination"],
        "tags": {"domain": ["auth"]},
    }
    _make_pipeline(tmp_path, spec)
    with pytest.raises(PipelineValidationError) as exc_info:
        validate_pipeline(tmp_path)
    assert any("V-CYC-009" in e for e in exc_info.value.errors)


def test_multiple_errors_collected_at_once(tmp_path):
    """All errors should be reported together, not one at a time."""
    spec = {
        "name": "multi-error",
        "agents": [
            {"id": "architect", "dir": "01_arch"},
            {"id": "developer_1", "dir": "02_dev"},
        ],
        "edges": [
            {"from": "architect", "to": "developer_1", "type": "feedback", "directed": False},
        ],
        # Missing: termination, tags.domain, pm agent
    }
    _make_pipeline(tmp_path, spec)
    with pytest.raises(PipelineValidationError) as exc_info:
        validate_pipeline(tmp_path)
    # Should have at least 3 errors (V-CYC-001, V-CYC-007, V-CYC-009)
    assert len(exc_info.value.errors) >= 3


def test_valid_dag_pipeline(tmp_path):
    spec = {
        "name": "simple-dag",
        "agents": [
            {"id": "researcher", "dir": "001_researcher", "depends_on": []},
            {"id": "writer",     "dir": "002_writer",     "depends_on": ["researcher"]},
        ],
    }
    _make_pipeline(tmp_path, spec)
    result = validate_pipeline(tmp_path)
    assert result["name"] == "simple-dag"
