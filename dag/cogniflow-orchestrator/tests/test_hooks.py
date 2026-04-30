"""Tests for hook installation and CLAUDE.md generation."""
import json
import pytest
from pathlib import Path
from orchestrator.hooks import generate_claude_md, install_hooks


_SPEC = {
    "name": "auth-pipeline",
    "description": "Build an auth module.",
    "agents": [
        {"id": "pm",          "dir": "00_pm",         "description": "Project Manager"},
        {"id": "architect",   "dir": "01_architect",  "description": "Software Architect"},
        {"id": "developer_1", "dir": "02_developer_1","description": "Developer"},
    ],
    "tags": {"domain": ["auth", "jwt", "api"]},
}


def test_generate_claude_md(tmp_path):
    generate_claude_md(tmp_path, _SPEC)
    claude_md = tmp_path / "CLAUDE.md"
    assert claude_md.exists()
    text = claude_md.read_text()
    assert "auth-pipeline" in text
    assert "pm" in text
    assert "auth" in text
    assert "decision" in text  # structural tags
    assert ".state/shared/" in text


def test_claude_md_does_not_include_protocol_block(tmp_path):
    """CLAUDE.md must not contain the routing JSON schema — that goes in system prompts."""
    generate_claude_md(tmp_path, _SPEC)
    text = (tmp_path / "CLAUDE.md").read_text()
    assert '"send_to"' not in text


def test_install_hooks_creates_settings_json(tmp_path):
    # Create minimal pipeline.json
    (tmp_path / "pipeline.json").write_text(
        json.dumps({"name": "test", "agents": []}), encoding="utf-8"
    )
    install_hooks(tmp_path)

    settings = tmp_path / ".claude" / "settings.json"
    assert settings.exists()
    data = json.loads(settings.read_text())
    assert "PostToolUse" in data["hooks"]
    assert "Stop" in data["hooks"]
    assert "StopFailure" in data["hooks"]


def test_install_hooks_creates_hook_scripts(tmp_path):
    (tmp_path / "pipeline.json").write_text(
        json.dumps({"name": "test", "agents": []}), encoding="utf-8"
    )
    install_hooks(tmp_path)
    hooks_dir = tmp_path / ".claude" / "hooks"
    assert (hooks_dir / "post_tool_event.py").exists()
    assert (hooks_dir / "agent_stop_event.py").exists()
    assert (hooks_dir / "agent_stop_failure_event.py").exists()


def test_settings_json_references_correct_scripts(tmp_path):
    (tmp_path / "pipeline.json").write_text(
        json.dumps({"name": "test", "agents": []}), encoding="utf-8"
    )
    install_hooks(tmp_path)
    data = json.loads((tmp_path / ".claude" / "settings.json").read_text())
    post_cmd = data["hooks"]["PostToolUse"][0]["hooks"][0]["command"]
    assert "post_tool_event.py" in post_cmd
    stop_cmd = data["hooks"]["Stop"][0]["hooks"][0]["command"]
    assert "agent_stop_event.py" in stop_cmd
