"""Tests for GAP-2 secrets hygiene."""
from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.secrets import (
    generate_gitignore,
    scan_for_secrets,
    apply_substitutions,
)


class FakeLog:
    """Minimal EventLog stand-in that records emissions."""

    def __init__(self) -> None:
        self.warnings: list[tuple[str, str]] = []
        self.substitution_warnings: list[tuple[str, str]] = []

    def secret_warning(self, agent: str, pattern: str) -> None:
        self.warnings.append((agent, pattern))

    def secret_substitution_warning(self, agent: str, var: str) -> None:
        self.substitution_warnings.append((agent, var))


# ── generate_gitignore ────────────────────────────────────────────────────────

def test_generate_gitignore_creates_file(tmp_path):
    result = generate_gitignore(tmp_path)
    assert result == tmp_path / ".gitignore"
    assert result.exists()
    content = result.read_text(encoding="utf-8")
    assert ".state/" in content


def test_generate_gitignore_appends_when_missing_rule(tmp_path):
    existing = tmp_path / ".gitignore"
    existing.write_text("*.log\n", encoding="utf-8")
    generate_gitignore(tmp_path)
    content = existing.read_text(encoding="utf-8")
    assert "*.log" in content  # original kept
    assert ".state/" in content


def test_generate_gitignore_no_op_if_rule_present(tmp_path):
    # v4: generate_gitignore now also ensures pipelines/secrets.db is
    # excluded. If the existing .gitignore already has the .state rule but
    # not the vault rule, the function appends the missing vault lines.
    existing = tmp_path / ".gitignore"
    existing.write_text(
        "**/.state/\n**/pipelines/secrets.db\npipelines/secrets.db\n",
        encoding="utf-8",
    )
    before = existing.read_text(encoding="utf-8")
    generate_gitignore(tmp_path)
    after = existing.read_text(encoding="utf-8")
    assert before == after


def test_generate_gitignore_appends_vault_rule_when_missing(tmp_path):
    existing = tmp_path / ".gitignore"
    existing.write_text("**/.state/\n", encoding="utf-8")
    generate_gitignore(tmp_path)
    content = existing.read_text(encoding="utf-8")
    assert "**/pipelines/secrets.db" in content
    assert "pipelines/secrets.db" in content
    # .state rule preserved
    assert "**/.state/" in content


# ── scan_for_secrets ──────────────────────────────────────────────────────────

def test_scan_flags_aws_key(tmp_path):
    (tmp_path / "02_prompt.md").write_text(
        "Use key AKIAIOSFODNN7EXAMPLE to query bucket",
        encoding="utf-8",
    )
    log = FakeLog()
    findings = scan_for_secrets("agent1", tmp_path, log)
    assert len(findings) >= 1
    assert any(f["pattern"] == "AWS access key" for f in findings)
    assert ("agent1", "AWS access key") in log.warnings


def test_scan_flags_openai_key(tmp_path):
    (tmp_path / "01_system.md").write_text(
        "export OPENAI_API_KEY=sk-abcdefghij1234567890ABCDEFGHIJ1234",
        encoding="utf-8",
    )
    log = FakeLog()
    findings = scan_for_secrets("agent1", tmp_path, log)
    assert findings  # at least one pattern matched


def test_scan_is_silent_on_clean_prompts(tmp_path):
    (tmp_path / "02_prompt.md").write_text("Just a boring prompt", encoding="utf-8")
    log = FakeLog()
    findings = scan_for_secrets("agent1", tmp_path, log)
    assert findings == []
    assert log.warnings == []


def test_scan_skips_missing_files(tmp_path):
    # Only one of the two canonical files exists — scan should not raise
    (tmp_path / "01_system.md").write_text("clean", encoding="utf-8")
    log = FakeLog()
    findings = scan_for_secrets("agent1", tmp_path, log)
    assert findings == []


# ── apply_substitutions ───────────────────────────────────────────────────────

def test_substitutions_replace_known_vars():
    log = FakeLog()
    out = apply_substitutions(
        "Prepare briefing for {{CLIENT}} using API at {{API_DOMAIN}}",
        {"CLIENT": "Acme Corp", "API_DOMAIN": "api.example.com"},
        "a",
        log,
    )
    assert out == "Prepare briefing for Acme Corp using API at api.example.com"
    assert log.substitution_warnings == []


def test_substitutions_leave_missing_placeholders_unchanged_and_warn():
    log = FakeLog()
    out = apply_substitutions(
        "Hello {{CLIENT}} and {{MISSING_VAR}}",
        {"CLIENT": "Acme Corp"},
        "a",
        log,
    )
    assert out == "Hello Acme Corp and {{MISSING_VAR}}"
    assert log.substitution_warnings == [("a", "MISSING_VAR")]


def test_substitutions_noop_when_no_placeholders():
    log = FakeLog()
    out = apply_substitutions("No placeholders here", {"X": "y"}, "a", log)
    assert out == "No placeholders here"


def test_substitutions_noop_with_empty_dict():
    log = FakeLog()
    out = apply_substitutions("Hello {{CLIENT}}", {}, "a", log)
    assert out == "Hello {{CLIENT}}"
    assert log.substitution_warnings == [("a", "CLIENT")]
