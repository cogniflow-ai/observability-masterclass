"""
Tests for OrchestratorConfig — file-based loader (no env vars).
"""
from __future__ import annotations

import json
import pytest

from orchestrator.config import OrchestratorConfig


# ── Defaults ──────────────────────────────────────────────────────────────────

def test_defaults_when_file_absent(tmp_path):
    cfg = OrchestratorConfig.from_pipeline_dir(tmp_path)
    assert cfg.agent_timeout == 300
    assert cfg.max_parallel_agents == 8
    assert cfg.verbose is True
    assert cfg.model_context_limit == 180000
    assert cfg.loop_poll_s == 0.5
    assert cfg.thread_window == 6
    assert cfg.thread_token_budget == 1500
    assert cfg.summary_max_tokens == 1000
    assert cfg.index_compression_threshold == 80
    assert cfg.artifact_max_inject_tokens == 800
    assert cfg.keep_output_versions is True
    assert cfg.approver == "operator"
    assert cfg.approval_poll_interval_s == 10
    assert cfg.approval_timeout_s == 3600
    assert cfg.substitutions == {}
    assert cfg._source == ""


def test_all_defaults_instance():
    cfg = OrchestratorConfig()
    assert cfg.agent_timeout == 300
    assert cfg.substitutions == {}


# ── Loading ───────────────────────────────────────────────────────────────────

def _write(tmp_path, blob):
    (tmp_path / "config.json").write_text(json.dumps(blob), encoding="utf-8")
    return tmp_path


def test_loads_execution_block(tmp_path):
    _write(tmp_path, {"execution": {"agent_timeout_s": 900, "verbose": False}})
    cfg = OrchestratorConfig.from_pipeline_dir(tmp_path)
    assert cfg.agent_timeout == 900
    assert cfg.verbose is False
    # Unspecified values keep defaults
    assert cfg.max_parallel_agents == 8


def test_loads_claude_models(tmp_path):
    _write(tmp_path, {
        "claude": {
            "default_model":   "claude-opus-4-7",
            "summary_model":   "claude-haiku-4-5-20251001",
            "retrieval_model": None
        }
    })
    cfg = OrchestratorConfig.from_pipeline_dir(tmp_path)
    assert cfg.default_model   == "claude-opus-4-7"
    assert cfg.summary_model   == "claude-haiku-4-5-20251001"
    assert cfg.retrieval_model is None


def test_loads_cyclic_block(tmp_path):
    _write(tmp_path, {
        "cyclic": {
            "loop_poll_s":        1.0,
            "thread_window":      10,
            "summary_max_tokens": 2000
        }
    })
    cfg = OrchestratorConfig.from_pipeline_dir(tmp_path)
    assert cfg.loop_poll_s == 1.0
    assert cfg.thread_window == 10
    assert cfg.summary_max_tokens == 2000
    # Unspecified cyclic keys keep defaults
    assert cfg.thread_token_budget == 1500


def test_loads_approval_block(tmp_path):
    _write(tmp_path, {
        "approval": {"approver": "giuseppe", "poll_interval_s": 5, "timeout_s": 120}
    })
    cfg = OrchestratorConfig.from_pipeline_dir(tmp_path)
    assert cfg.approver == "giuseppe"
    assert cfg.approval_poll_interval_s == 5
    assert cfg.approval_timeout_s == 120


def test_loads_substitutions_and_strips_metadata(tmp_path):
    _write(tmp_path, {
        "substitutions": {
            "_warning":   "non-production",
            "_comment":   "just a reminder",
            "CLIENT":     "Acme Corp",
            "API_DOMAIN": "api.example.com"
        }
    })
    cfg = OrchestratorConfig.from_pipeline_dir(tmp_path)
    assert cfg.substitutions == {"CLIENT": "Acme Corp", "API_DOMAIN": "api.example.com"}
    # Metadata keys are stripped
    assert "_warning" not in cfg.substitutions
    assert "_comment" not in cfg.substitutions


def test_source_path_recorded(tmp_path):
    _write(tmp_path, {})
    cfg = OrchestratorConfig.from_pipeline_dir(tmp_path)
    assert cfg._source.endswith("config.json")


# ── Errors ────────────────────────────────────────────────────────────────────

def test_malformed_json_raises(tmp_path):
    (tmp_path / "config.json").write_text("{ not json", encoding="utf-8")
    with pytest.raises(ValueError, match="not valid JSON"):
        OrchestratorConfig.from_pipeline_dir(tmp_path)


def test_unknown_keys_silently_ignored(tmp_path):
    # Forward compatibility — unknown top-level keys must not break loading
    _write(tmp_path, {"execution": {"agent_timeout_s": 10}, "future_feature": {"x": 1}})
    cfg = OrchestratorConfig.from_pipeline_dir(tmp_path)
    assert cfg.agent_timeout == 10


# ── Model args ────────────────────────────────────────────────────────────────

def test_model_args_empty_when_no_model():
    cfg = OrchestratorConfig(default_model=None)
    assert cfg.model_args() == []


def test_model_args_returns_flag():
    cfg = OrchestratorConfig(default_model="claude-opus-4-7")
    assert cfg.model_args() == ["--model", "claude-opus-4-7"]


def test_model_override_takes_precedence():
    cfg = OrchestratorConfig(default_model="default-model")
    assert cfg.model_args("override-model") == ["--model", "override-model"]


def test_summary_model_falls_back_to_default():
    cfg = OrchestratorConfig(default_model="claude-haiku-4-5-20251001", summary_model=None)
    assert cfg.summary_model_args() == ["--model", "claude-haiku-4-5-20251001"]


def test_retrieval_model_falls_back_to_default():
    cfg = OrchestratorConfig(default_model="claude-opus-4-7", retrieval_model=None)
    assert cfg.retrieval_model_args() == ["--model", "claude-opus-4-7"]


def test_input_token_budget_property():
    cfg = OrchestratorConfig(model_context_limit=200000, input_budget_fraction=0.5)
    assert cfg.input_token_budget == 100000
