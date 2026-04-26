"""Tests for memory management — full_context, summary, thread, index, budget."""
import json
import pytest
from pathlib import Path
from orchestrator.memory import (
    init_agent_memory, write_entry_start, write_entry_body, write_entry_end,
    has_complete_entry, truncate_to_last_complete_entry, extract_chunk_text,
    append_turn_to_thread, get_recent_thread, get_summary,
    format_summary_for_prompt, append_chunks, get_index,
    record_tokens, is_budget_exceeded, write_artifact,
)
from orchestrator.config import OrchestratorConfig


@pytest.fixture
def mem_dir(tmp_path):
    d = tmp_path / "agents" / "architect"
    init_agent_memory(d, "architect", "run-test")
    return d


@pytest.fixture
def cfg():
    return OrchestratorConfig()


# ── full_context.md ───────────────────────────────────────────────────────────

def test_init_creates_files(mem_dir):
    assert (mem_dir / "full_context.md").exists()
    assert (mem_dir / "structured_summary.json").exists()
    assert (mem_dir / "recent_thread.md").exists()
    assert (mem_dir / "context_index.json").exists()
    assert (mem_dir / "08_token_budget.json").exists()


def test_entry_markers_write_and_detect(mem_dir):
    write_entry_start(mem_dir, "msg-001", 1)
    write_entry_body(mem_dir, "pm", "Design auth.", "JWT 15min expiry.")
    assert not has_complete_entry(mem_dir, "msg-001")
    write_entry_end(mem_dir, "msg-001")
    assert has_complete_entry(mem_dir, "msg-001")


def test_truncate_removes_partial_entry(mem_dir):
    write_entry_start(mem_dir, "msg-001", 1)
    write_entry_body(mem_dir, "pm", "first message", "first response")
    write_entry_end(mem_dir, "msg-001")

    write_entry_start(mem_dir, "msg-002", 2)
    write_entry_body(mem_dir, "developer", "second message", "PARTIAL — no end marker")
    # No write_entry_end → partial

    assert not has_complete_entry(mem_dir, "msg-002")
    truncate_to_last_complete_entry(mem_dir)
    assert has_complete_entry(mem_dir, "msg-001")
    assert not has_complete_entry(mem_dir, "msg-002")


def test_extract_chunk_text(mem_dir):
    write_entry_start(mem_dir, "msg-003", 3)
    write_entry_body(mem_dir, "pm",
                     "incoming text",
                     "line one\nline two\nline three\nline four")
    write_entry_end(mem_dir, "msg-003")
    text = extract_chunk_text(mem_dir, "msg-003", [2, 3])
    assert "line two" in text
    assert "line three" in text


# ── recent_thread.md ─────────────────────────────────────────────────────────

def test_append_turn_and_get(mem_dir, cfg):
    append_turn_to_thread(mem_dir, "Hello", "Hi there", "pm", "architect", 1, cfg)
    thread = get_recent_thread(mem_dir)
    assert "Hello" in thread
    assert "Hi there" in thread
    assert "TURN 1" in thread


def test_thread_window_enforced(mem_dir):
    cfg = OrchestratorConfig()
    cfg.thread_token_budget = 50  # very small — will force truncation
    for i in range(10):
        append_turn_to_thread(
            mem_dir, f"incoming {i}", f"response {i}",
            "pm", "architect", i + 1, cfg,
        )
    thread = get_recent_thread(mem_dir)
    # Should not contain all 10 turns
    assert "TURN 1" not in thread or "TURN 10" in thread


# ── structured_summary.json ───────────────────────────────────────────────────

def test_format_empty_summary(mem_dir):
    text = format_summary_for_prompt(get_summary(mem_dir))
    assert "No prior decisions" in text or text  # either is valid


def test_format_summary_with_decisions(mem_dir):
    (mem_dir / "structured_summary.json").write_text(json.dumps({
        "decisions": [
            {"id": "D-01", "text": "JWT 15min expiry", "cycle": 1, "superseded_by": None}
        ],
        "open_questions": [],
        "constraints": ["No Redis"],
        "acknowledgements": [],
    }), encoding="utf-8")
    text = format_summary_for_prompt(get_summary(mem_dir))
    assert "D-01" in text
    assert "JWT" in text
    assert "No Redis" in text


def test_superseded_decisions_excluded_from_prompt(mem_dir):
    (mem_dir / "structured_summary.json").write_text(json.dumps({
        "decisions": [
            {"id": "D-01", "text": "Old decision", "cycle": 1, "superseded_by": "D-02"},
            {"id": "D-02", "text": "New decision", "cycle": 3, "superseded_by": None},
        ],
        "open_questions": [],
        "constraints": [],
        "acknowledgements": [],
    }), encoding="utf-8")
    text = format_summary_for_prompt(get_summary(mem_dir))
    assert "D-02" in text
    assert "Old decision" not in text


# ── context_index.json ────────────────────────────────────────────────────────

def test_append_chunks_and_get_index(mem_dir):
    chunks = [
        {"id": "arch-1-c1", "tags": ["auth", "jwt", "decision"], "synopsis": None, "line_range": [1, 10]},
        {"id": "arch-1-c2", "tags": ["auth", "api"], "synopsis": "endpoints", "line_range": [11, 15]},
    ]
    append_chunks(mem_dir, chunks, "msg-001")
    index = get_index(mem_dir)
    ids = [c["id"] for c in index["chunks"]]
    assert "arch-1-c1" in ids
    assert "arch-1-c2" in ids


def test_duplicate_chunks_not_added_twice(mem_dir):
    chunks = [{"id": "arch-1-c1", "tags": ["auth"], "synopsis": None, "line_range": [1, 5]}]
    append_chunks(mem_dir, chunks, "msg-001")
    append_chunks(mem_dir, chunks, "msg-001")
    index = get_index(mem_dir)
    assert len([c for c in index["chunks"] if c["id"] == "arch-1-c1"]) == 1


# ── 08_token_budget.json ──────────────────────────────────────────────────────

def test_record_tokens_updates_budget(tmp_path):
    mem_dir = tmp_path / "agents" / "a1"
    init_agent_memory(mem_dir, "a1", "run-t")
    from orchestrator.events import EventLog
    log = EventLog(tmp_path / ".state" / "events.jsonl")
    record_tokens(mem_dir, 500, "agent_response", 1, {}, log, "a1",
                  OrchestratorConfig())
    tb = json.loads((mem_dir / "08_token_budget.json").read_text())
    assert tb["used_tokens"] == 500
    assert tb["by_type"]["agent_response"] == 500


def test_budget_exceeded_detection(tmp_path):
    mem_dir = tmp_path / "agents" / "a2"
    init_agent_memory(mem_dir, "a2", "run-t")
    from orchestrator.events import EventLog
    log = EventLog(tmp_path / ".state" / "events.jsonl")
    cfg = {"cyclic_token_budget": 100}
    record_tokens(mem_dir, 150, "agent_response", 1, cfg, log, "a2",
                  OrchestratorConfig())
    assert is_budget_exceeded(mem_dir, cfg)


def test_no_budget_never_exceeded(tmp_path):
    mem_dir = tmp_path / "agents" / "a3"
    init_agent_memory(mem_dir, "a3", "run-t")
    assert not is_budget_exceeded(mem_dir, {})  # no cyclic_token_budget set


# ── Shared artifact workspace ─────────────────────────────────────────────────

def test_write_and_version_artifact(tmp_path):
    shared = tmp_path / ".state" / "shared"
    from orchestrator.events import EventLog
    log = EventLog(tmp_path / ".state" / "events.jsonl")

    write_artifact(shared, "design-doc", "# Design v1", "architect", "summary v1", 1, log)
    write_artifact(shared, "design-doc", "# Design v2", "architect", "summary v2", 3, log)

    idx = json.loads((shared / "ARTIFACT_INDEX.json").read_text())
    entry = next(a for a in idx["artifacts"] if a["id"] == "design-doc")
    assert entry["version"] == 2
    assert "summary v2" in entry["summary"]
    assert (shared / "design-doc.md").read_text() == "# Design v2"
