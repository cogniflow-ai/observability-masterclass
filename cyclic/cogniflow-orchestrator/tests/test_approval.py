"""Tests for GAP-3 human-in-the-loop approval."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from orchestrator.approval import (
    request_approval, wait_for_approval,
    write_approval, get_approval_status,
)
from orchestrator.config import OrchestratorConfig
from orchestrator.exceptions import ApprovalRejectedError, ApprovalTimeoutError


class FakeLog:
    def __init__(self) -> None:
        self.events: list[tuple[str, tuple]] = []

    def agent_approval_required(self, agent: str) -> None:
        self.events.append(("required", (agent,)))

    def agent_approved(self, agent: str, approved_by: str) -> None:
        self.events.append(("approved", (agent, approved_by)))

    def agent_rejected(self, agent: str, approved_by: str, note: str = "") -> None:
        self.events.append(("rejected", (agent, approved_by, note)))


# ── request_approval ─────────────────────────────────────────────────────────

def test_request_approval_writes_request_file(tmp_path):
    log = FakeLog()
    request_approval("writer", tmp_path, "run-1", log)
    req = tmp_path / "07_approval_request.json"
    assert req.exists()
    data = json.loads(req.read_text(encoding="utf-8"))
    assert data["agent_id"] == "writer"
    assert data["run_id"]   == "run-1"
    assert data["status"]   == "pending"
    assert "instructions" in data
    assert log.events and log.events[0][0] == "required"


# ── write_approval ────────────────────────────────────────────────────────────

def test_write_approval_writes_decision_file(tmp_path):
    path = write_approval(tmp_path, "writer", "giuseppe", approved=True, note="LGTM")
    assert path == tmp_path / "07_approval.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["status"]      == "approved"
    assert data["approved_by"] == "giuseppe"
    assert data["note"]        == "LGTM"


def test_write_approval_rejection(tmp_path):
    write_approval(tmp_path, "writer", "giuseppe", approved=False, note="rewrite")
    data = json.loads((tmp_path / "07_approval.json").read_text(encoding="utf-8"))
    assert data["status"] == "rejected"
    assert data["note"]   == "rewrite"


# ── get_approval_status ──────────────────────────────────────────────────────

def test_get_approval_status_none(tmp_path):
    assert get_approval_status(tmp_path) == "none"


def test_get_approval_status_awaiting(tmp_path):
    (tmp_path / "07_approval_request.json").write_text("{}", encoding="utf-8")
    assert get_approval_status(tmp_path) == "awaiting_approval"


def test_get_approval_status_approved(tmp_path):
    write_approval(tmp_path, "a", "op", approved=True)
    assert get_approval_status(tmp_path) == "approved"


# ── wait_for_approval ────────────────────────────────────────────────────────

def _fast_config(**overrides) -> OrchestratorConfig:
    cfg = OrchestratorConfig(
        approval_poll_interval_s=1,
        approval_timeout_s=5,
        verbose=False,
    )
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def test_wait_returns_on_approval(tmp_path):
    cfg = _fast_config()
    log = FakeLog()

    # Approve from another thread after a short delay
    def approver():
        time.sleep(0.3)
        write_approval(tmp_path, "writer", "op", approved=True)

    t = threading.Thread(target=approver)
    t.start()

    wait_for_approval("writer", tmp_path, cfg, log)
    t.join()

    assert any(e[0] == "approved" for e in log.events)


def test_wait_raises_rejected(tmp_path):
    cfg = _fast_config()
    log = FakeLog()

    def rejector():
        time.sleep(0.3)
        write_approval(tmp_path, "writer", "op", approved=False, note="nope")

    t = threading.Thread(target=rejector)
    t.start()

    with pytest.raises(ApprovalRejectedError, match="nope"):
        wait_for_approval("writer", tmp_path, cfg, log)
    t.join()


def test_wait_raises_timeout(tmp_path):
    cfg = _fast_config(approval_timeout_s=1)
    log = FakeLog()

    with pytest.raises(ApprovalTimeoutError):
        wait_for_approval("writer", tmp_path, cfg, log)


def test_wait_ignores_incomplete_json_until_valid(tmp_path):
    # Simulate a partial write: first a malformed file, then a valid one
    cfg = _fast_config()
    log = FakeLog()

    def writer():
        p = tmp_path / "07_approval.json"
        p.write_text("{ broken", encoding="utf-8")  # unparseable
        time.sleep(0.5)
        # Replace with a valid decision
        write_approval(tmp_path, "writer", "op", approved=True)

    t = threading.Thread(target=writer)
    t.start()

    wait_for_approval("writer", tmp_path, cfg, log)
    t.join()

    assert any(e[0] == "approved" for e in log.events)
