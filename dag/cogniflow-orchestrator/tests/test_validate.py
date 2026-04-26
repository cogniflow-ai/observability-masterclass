"""Tests for pipeline validation."""
import json
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from orchestrator.validate import validate_pipeline
from orchestrator.exceptions import PipelineValidationError


def write_pipeline(tmp_path, data):
    p = tmp_path / "pipeline.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def make_agent_files(agents_base, agent_id):
    d = agents_base / agent_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "01_system.md").write_text("system", encoding="utf-8")
    (d / "02_prompt.md").write_text("prompt", encoding="utf-8")


class TestValidatePipeline:
    def test_valid_pipeline(self, tmp_path):
        agents_base = tmp_path / "agents"
        for aid in ("a", "b"):
            make_agent_files(agents_base, aid)
        data = {"name": "test", "agents": [
            {"id": "a", "depends_on": []},
            {"id": "b", "depends_on": ["a"]},
        ]}
        p = write_pipeline(tmp_path, data)
        result = validate_pipeline(p, agents_base)
        assert result["name"] == "test"

    def test_missing_pipeline_file(self, tmp_path):
        with pytest.raises(PipelineValidationError, match="not found"):
            validate_pipeline(tmp_path / "pipeline.json", tmp_path / "agents")

    def test_invalid_json(self, tmp_path):
        p = tmp_path / "pipeline.json"
        p.write_text("{bad json", encoding="utf-8")
        with pytest.raises(PipelineValidationError, match="not valid JSON"):
            validate_pipeline(p, tmp_path / "agents")

    def test_missing_name(self, tmp_path):
        agents_base = tmp_path / "agents"
        make_agent_files(agents_base, "a")
        data = {"agents": [{"id": "a", "depends_on": []}]}
        p = write_pipeline(tmp_path, data)
        with pytest.raises(PipelineValidationError, match="name"):
            validate_pipeline(p, agents_base)

    def test_duplicate_ids(self, tmp_path):
        agents_base = tmp_path / "agents"
        make_agent_files(agents_base, "a")
        data = {"name": "t", "agents": [
            {"id": "a", "depends_on": []},
            {"id": "a", "depends_on": []},
        ]}
        p = write_pipeline(tmp_path, data)
        with pytest.raises(PipelineValidationError, match="duplicate"):
            validate_pipeline(p, agents_base)

    def test_unknown_dependency(self, tmp_path):
        agents_base = tmp_path / "agents"
        make_agent_files(agents_base, "a")
        data = {"name": "t", "agents": [
            {"id": "a", "depends_on": ["ghost"]},
        ]}
        p = write_pipeline(tmp_path, data)
        with pytest.raises(PipelineValidationError, match="ghost"):
            validate_pipeline(p, agents_base)

    def test_missing_system_file(self, tmp_path):
        agents_base = tmp_path / "agents"
        d = agents_base / "a"
        d.mkdir(parents=True, exist_ok=True)
        (d / "02_prompt.md").write_text("prompt", encoding="utf-8")
        # 01_system.md deliberately absent
        data = {"name": "t", "agents": [{"id": "a", "depends_on": []}]}
        p = write_pipeline(tmp_path, data)
        with pytest.raises(PipelineValidationError, match="01_system.md"):
            validate_pipeline(p, agents_base)

    def test_cycle_detected(self, tmp_path):
        agents_base = tmp_path / "agents"
        for aid in ("a", "b"):
            make_agent_files(agents_base, aid)
        data = {"name": "t", "agents": [
            {"id": "a", "depends_on": ["b"]},
            {"id": "b", "depends_on": ["a"]},
        ]}
        p = write_pipeline(tmp_path, data)
        with pytest.raises(PipelineValidationError):
            validate_pipeline(p, agents_base)

    def test_reports_all_errors_at_once(self, tmp_path):
        """Validation should collect ALL problems, not just the first."""
        agents_base = tmp_path / "agents"
        # No agent files created — both will be missing
        data = {"name": "t", "agents": [
            {"id": "x", "depends_on": ["missing_dep"]},
            {"id": "y", "depends_on": ["also_missing"]},
        ]}
        p = write_pipeline(tmp_path, data)
        try:
            validate_pipeline(p, agents_base)
            assert False, "Should have raised"
        except PipelineValidationError as e:
            # Should report multiple problems
            assert len(e.problems) > 1
