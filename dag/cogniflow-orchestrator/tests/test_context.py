"""Tests for context assembly and input collection."""
import pytest
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from orchestrator.context import assemble_context, collect_inputs
from orchestrator.exceptions import MissingDependencyOutputError


def make_log():
    log = MagicMock()
    log.agent_inputs_collected = MagicMock()
    log.agent_context_ready = MagicMock()
    return log


class TestAssembleContext:
    def test_no_inputs(self, tmp_path):
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        (agent_dir / "01_system.md").write_text("You are helpful.", encoding="utf-8")
        (agent_dir / "02_prompt.md").write_text("Do something.", encoding="utf-8")
        (agent_dir / "03_inputs").mkdir()

        ctx = assemble_context("test", agent_dir, make_log())
        content = ctx.read_text(encoding="utf-8")
        assert "# Task" in content
        assert "Do something." in content
        # System prompt must NOT be in context (it goes via --system flag)
        assert "You are helpful." not in content

    def test_with_inputs(self, tmp_path):
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        (agent_dir / "01_system.md").write_text("System.", encoding="utf-8")
        (agent_dir / "02_prompt.md").write_text("Synthesise.", encoding="utf-8")
        inputs = agent_dir / "03_inputs"
        inputs.mkdir()
        (inputs / "from_001_researcher.md").write_text("Research A.", encoding="utf-8")
        (inputs / "from_002_researcher.md").write_text("Research B.", encoding="utf-8")

        ctx = assemble_context("synth", agent_dir, make_log())
        content = ctx.read_text(encoding="utf-8")
        assert "Synthesise." in content
        assert "Research A." in content
        assert "Research B." in content
        assert "Output from: 001_researcher" in content
        assert "Output from: 002_researcher" in content

    def test_system_excluded_from_context(self, tmp_path):
        """IMP-02: 01_system.md must NOT appear in 04_context.md."""
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        secret_system = "SECRET_ROLE_INSTRUCTIONS_DO_NOT_LEAK"
        (agent_dir / "01_system.md").write_text(secret_system, encoding="utf-8")
        (agent_dir / "02_prompt.md").write_text("Task.", encoding="utf-8")
        (agent_dir / "03_inputs").mkdir()

        ctx = assemble_context("a", agent_dir, make_log())
        assert secret_system not in ctx.read_text(encoding="utf-8")


class TestCollectInputs:
    def test_copies_outputs(self, tmp_path):
        agents_base = tmp_path / "agents"
        dep_dir = agents_base / "001_dep"
        dep_dir.mkdir(parents=True)
        (dep_dir / "05_output.md").write_text("dep output", encoding="utf-8")

        agent_dir = agents_base / "002_agent"
        agent_dir.mkdir()

        count = collect_inputs("002_agent", agent_dir, ["001_dep"],
                               agents_base, make_log(), "run001")
        assert count == 1
        result = (agent_dir / "03_inputs" / "from_001_dep.md").read_text()
        assert result == "dep output"

    def test_missing_dependency_raises(self, tmp_path):
        agents_base = tmp_path / "agents"
        agent_dir = agents_base / "002"
        agent_dir.mkdir(parents=True)
        (agents_base / "001").mkdir()  # exists but no output file

        with pytest.raises(MissingDependencyOutputError):
            collect_inputs("002", agent_dir, ["001"],
                           agents_base, make_log(), "run001")
