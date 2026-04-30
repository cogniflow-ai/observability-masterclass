"""Tests for EventLog and event_writer."""
import json
import threading
from pathlib import Path
import pytest
from orchestrator.events import EventLog
from orchestrator.event_writer import append_event


@pytest.fixture
def log(tmp_path):
    return EventLog(tmp_path / ".state" / "events.jsonl")


def _read_events(log: EventLog):
    return [json.loads(l) for l in log.path.read_text().splitlines() if l.strip()]


def test_emit_writes_record(log):
    log.emit("test_event", foo="bar")
    events = _read_events(log)
    assert len(events) == 1
    assert events[0]["event"] == "test_event"
    assert events[0]["foo"] == "bar"
    assert "ts" in events[0]


def test_pipeline_start_event(log):
    log.pipeline_start("my-pipeline", "run-001")
    events = _read_events(log)
    assert events[0]["event"] == "pipeline_start"
    assert events[0]["pipeline"] == "my-pipeline"


def test_agent_done_includes_optional_fields(log):
    log.agent_done("architect", 42.5, 0, invocation_n=3, thread_id="t-001")
    events = _read_events(log)
    ev = events[0]
    assert ev["invocation_n"] == 3
    assert ev["thread_id"] == "t-001"


def test_agent_done_omits_thread_when_none(log):
    log.agent_done("architect", 10.0, 0)
    events = _read_events(log)
    assert "thread_id" not in events[0]


def test_thread_safety(log):
    """Multiple threads must not corrupt events.jsonl."""
    errors = []
    def worker():
        try:
            for _ in range(20):
                log.emit("thread_test", worker=threading.current_thread().name)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    events = _read_events(log)
    assert len(events) == 100  # 5 threads × 20 events


def test_all_cyclic_events(log):
    log.message_sent("a", "b", "msg-001", "t-001", 1)
    log.message_received("b", "msg-001", "t-001", 1)
    log.agent_activated("b", "t-001", 2, 3)
    log.agent_waiting("b", ["a"], "t-001")
    log.feedback_loop_tick(["a", "b"], 2, "t-001", 500)
    log.cycle_guard_triggered("a", "b", 11, "escalate_pm")
    log.conversation_thread_start("t-001", ["a", "b"], "feedback")
    log.conversation_thread_close("t-001", 5, "resolved")
    log.context_retrieval_request("b", "query", ["tag"], "t-001")
    log.context_retrieval_result("b", ["chunk-1"], "high", 1)
    log.context_retrieval_miss("b", "query", "no matches")
    log.summary_updated("b", 2, 3, 1)
    log.summary_overflow("b", 2, 5)
    log.budget_warning("b", 4000, 5000, 4000)
    log.hard_budget_exceeded("b", 5100, 5000, "finalise_now")
    log.deadlock_detected(["a", "b"], {"a": ["b"], "b": ["a"]})
    log.malformed_output("b", 1, "missing JSON block", "t-001")
    log.routing_violation("b", "unknown_agent", "no edge")
    log.artifact_written("b", "design-doc", 1, 3)
    log.pipeline_convergence("run-001", ["a", "b"], 10, 3)
    log.pipeline_timeout("run-001", 3601.0, ["b"])

    events = _read_events(log)
    event_names = {e["event"] for e in events}
    assert "message_sent" in event_names
    assert "pipeline_convergence" in event_names
    assert "deadlock_detected" in event_names


def test_event_writer_standalone(tmp_path):
    """event_writer must work without importing any other orchestrator module."""
    events_file = str(tmp_path / "events.jsonl")
    append_event(events_file, "cli_bash_call",
                 session_id="s1", command="ls", exit_code=0, duration_ms=10)
    records = [json.loads(l) for l in Path(events_file).read_text().splitlines()]
    assert records[0]["event"] == "cli_bash_call"
    assert records[0]["command"] == "ls"


def test_event_writer_never_raises(tmp_path):
    """event_writer must not raise even with a bad path."""
    # Should not raise:
    append_event("/nonexistent/path/that/cannot/be/created/events.jsonl",
                 "test", data="x")
