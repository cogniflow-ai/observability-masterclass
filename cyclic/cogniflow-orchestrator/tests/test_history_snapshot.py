"""Tests for the pipeline-level end-of-run history snapshot (ported from v1)."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from orchestrator.config import OrchestratorConfig
from orchestrator.core import (
    _AGENT_SNAPSHOT_FILES,
    _next_history_version,
    _snapshot_run,
)


def _build_pipeline(tmp_path: Path) -> tuple[Path, Path, dict[str, Path]]:
    """Lay out a minimal pipeline directory with two agents.
    Returns (pipeline_dir, agents_base, agent_dirs)."""
    pipeline_dir = tmp_path / "pipe"
    pipeline_dir.mkdir()

    (pipeline_dir / "pipeline.json").write_text(
        json.dumps({
            "id":   "p",
            "name": "test",
            "agents": [
                {"id": "001_writer", "depends_on": []},
                {"id": "002_critic", "depends_on": ["001_writer"]},
            ],
        }),
        encoding="utf-8",
    )

    agents_base = pipeline_dir / "agents"
    agent_dirs: dict[str, Path] = {}
    for aid in ("001_writer", "002_critic"):
        ad = agents_base / aid
        ad.mkdir(parents=True)
        (ad / "01_system.md").write_text(f"sys for {aid}", encoding="utf-8")
        (ad / "02_prompt.md").write_text(f"task for {aid}", encoding="utf-8")
        (ad / "03_inputs").mkdir()
        (ad / "04_context.md").write_text(f"ctx for {aid}", encoding="utf-8")
        (ad / "05_output.md").write_text(f"output of {aid}", encoding="utf-8")
        (ad / "05_usage.json").write_text(
            json.dumps({"input_tokens": 10, "output_tokens": 20}), encoding="utf-8",
        )
        (ad / "06_status.json").write_text(
            json.dumps({"agent_id": aid, "status": "done"}), encoding="utf-8",
        )
        agent_dirs[aid] = ad

    # 002_critic reads from 001_writer, so emulate the input-collection step.
    (agents_base / "002_critic" / "03_inputs" / "from_001_writer.md").write_text(
        "output of 001_writer", encoding="utf-8",
    )
    return pipeline_dir, agents_base, agent_dirs


def test_next_history_version_empty(tmp_path):
    assert _next_history_version(tmp_path / "history") == 1


def test_next_history_version_increments(tmp_path):
    h = tmp_path / "history"
    h.mkdir()
    (h / "v1_20260101-000000").mkdir()
    (h / "v2_20260102-000000").mkdir()
    (h / "v10_20260103-000000").mkdir()   # numeric, not lex
    (h / "not-a-version").mkdir()
    assert _next_history_version(h) == 11


def test_snapshot_captures_all_agent_files(tmp_path):
    pipeline_dir, agents_base, agent_dirs = _build_pipeline(tmp_path)

    # Fake .state/events.jsonl with one prior run plus this run's events.
    state_dir = pipeline_dir / ".state"
    state_dir.mkdir()
    events_path = state_dir / "events.jsonl"
    events_path.write_bytes(b'{"event":"prior"}\n')
    events_offset = events_path.stat().st_size
    events_path.write_bytes(
        b'{"event":"prior"}\n'
        b'{"event":"pipeline_start","run_id":"R"}\n'
        b'{"event":"pipeline_done","run_id":"R"}\n'
    )

    summary = {
        "run_id": "R", "status": "done", "duration_s": 1.0,
        "layers": 2, "agents_run": 2, "agents_skipped": 0,
        "tokens": {"agents_counted": 2},
    }

    agents_def = json.loads((pipeline_dir / "pipeline.json").read_text())["agents"]
    cfg = OrchestratorConfig()

    snap = _snapshot_run(
        pipeline_dir, agent_dirs, agents_def,
        "R", summary, cfg, events_path, events_offset,
    )

    assert snap.exists()
    assert snap.name == "v1_R"
    assert snap.parent.name == "history"

    # pipeline.json snapshot
    assert (snap / "pipeline.json").exists()

    # Each agent's files copied.
    for aid in ("001_writer", "002_critic"):
        agent_snap = snap / "agents" / aid
        assert agent_snap.is_dir()
        for fname in ("01_system.md", "02_prompt.md", "04_context.md",
                      "05_output.md", "05_usage.json", "06_status.json"):
            assert (agent_snap / fname).exists(), f"{aid}/{fname} missing"
        assert (agent_snap / "03_inputs").is_dir()

    # 002_critic's upstream input is captured.
    critic_input = snap / "agents" / "002_critic" / "03_inputs" / "from_001_writer.md"
    assert critic_input.read_text() == "output of 001_writer"

    # events.jsonl slice — only THIS run's lines.
    sliced = (snap / "events.jsonl").read_text(encoding="utf-8")
    assert "pipeline_start" in sliced
    assert "pipeline_done" in sliced
    assert '"event":"prior"' not in sliced

    # summary.json round-trips.
    assert json.loads((snap / "summary.json").read_text()) == summary

    # env.snapshot.json has config fields, no secrets.
    env = json.loads((snap / "env.snapshot.json").read_text())
    for key in ("claude_bin", "agent_timeout", "max_retries", "retry_delays_s",
                "context_limit", "max_parallel_agents"):
        assert key in env


def test_snapshot_tolerates_missing_optional_files(tmp_path):
    """Agents that never ran (e.g. bypassed) may be missing most files."""
    pipeline_dir = tmp_path / "pipe"
    pipeline_dir.mkdir()
    (pipeline_dir / "pipeline.json").write_text(
        json.dumps({"id": "p", "name": "t",
                    "agents": [{"id": "001_a", "depends_on": []}]}),
        encoding="utf-8",
    )
    agents_base = pipeline_dir / "agents"
    agent_a = agents_base / "001_a"
    agent_a.mkdir(parents=True)
    # Only a status file, nothing else.
    (agent_a / "06_status.json").write_text(
        json.dumps({"status": "bypassed"}), encoding="utf-8",
    )

    state_dir = pipeline_dir / ".state"
    state_dir.mkdir()
    events_path = state_dir / "events.jsonl"
    events_path.touch()

    agents_def = [{"id": "001_a", "depends_on": []}]
    summary = {"run_id": "R", "status": "done"}
    snap = _snapshot_run(
        pipeline_dir, {"001_a": agent_a}, agents_def,
        "R", summary, OrchestratorConfig(), events_path, 0,
    )

    assert (snap / "agents" / "001_a" / "06_status.json").exists()
    # Missing files should just be absent, not raise.
    assert not (snap / "agents" / "001_a" / "01_system.md").exists()


def test_snapshot_files_constant_covers_expected_set():
    """Guard against accidentally dropping a file type from the snapshot."""
    expected = {
        "00_config.json", "01_system.md", "02_prompt.md",
        "04_context.md",  "05_output.md", "05_usage.json",
        "06_status.json", "routing.json",
    }
    assert set(_AGENT_SNAPSHOT_FILES) == expected


# ── Integration: pause/resume + history snapshot via run_pipeline() ──────────

def _build_two_layer_pipeline(pipeline_dir: Path) -> None:
    """Pipeline with two sequential agents: 001_first → 002_second."""
    pipeline_dir.mkdir()
    (pipeline_dir / "pipeline.json").write_text(
        json.dumps({
            "id": "p", "name": "stub",
            "agents": [
                {"id": "001_first",  "depends_on": []},
                {"id": "002_second", "depends_on": ["001_first"]},
            ],
        }),
        encoding="utf-8",
    )
    for aid in ("001_first", "002_second"):
        ad = pipeline_dir / "agents" / aid
        ad.mkdir(parents=True)
        (ad / "01_system.md").write_text("sys", encoding="utf-8")
        (ad / "02_prompt.md").write_text("task", encoding="utf-8")


def _stub_exec_agent():
    """Returns (fake_exec, ran_list). Writes the artefacts a successful
    agent would leave behind and returns "done"."""
    ran: list[str] = []

    def fake(aid, agent_dir, deps, agent_dirs, cfg, log, run_id):
        ad = agent_dir
        (ad / "03_inputs").mkdir(exist_ok=True)
        (ad / "04_context.md").write_text("ctx", encoding="utf-8")
        (ad / "05_output.md").write_text(f"out {aid}", encoding="utf-8")
        (ad / "06_status.json").write_text(
            json.dumps({"agent_id": aid, "status": "done"}),
            encoding="utf-8",
        )
        ran.append(aid)
        return "done"

    return fake, ran


def test_pause_and_resume_sentinels_are_consumed_and_run_completes(
    tmp_path, monkeypatch,
):
    """
    When both .state/pause and .state/resume are dropped during a layer's
    execution, after that layer completes the orchestrator must:
      • consume (remove) both sentinel files,
      • emit pipeline_paused AND pipeline_resumed,
      • continue into the next layer and finish normally,
      • snapshot the run as usual.
    """
    from orchestrator import core as core_mod
    from orchestrator.core import (
        run_pipeline, PAUSE_SENTINEL, RESUME_SENTINEL,
    )

    pipeline_dir = tmp_path / "pipe"
    _build_two_layer_pipeline(pipeline_dir)
    state_dir = pipeline_dir / ".state"

    fake_exec, ran = _stub_exec_agent()

    def exec_and_signal(aid, agent_dir, deps, agent_dirs, cfg, log, run_id):
        result = fake_exec(aid, agent_dir, deps, agent_dirs, cfg, log, run_id)
        if aid == "001_first":
            state_dir.mkdir(exist_ok=True)
            (state_dir / PAUSE_SENTINEL).touch()
            (state_dir / RESUME_SENTINEL).touch()
        return result

    monkeypatch.setattr(core_mod, "exec_agent", exec_and_signal)

    summary = run_pipeline(pipeline_dir)

    assert ran == ["001_first", "002_second"]
    assert summary["status"] == "done"
    assert summary["agents_run"] == 2

    assert not (state_dir / PAUSE_SENTINEL).exists()
    assert not (state_dir / RESUME_SENTINEL).exists()

    snap_dirs = list((pipeline_dir / "history").iterdir())
    assert len(snap_dirs) == 1
    events_text = (snap_dirs[0] / "events.jsonl").read_text(encoding="utf-8")
    assert "pipeline_paused"  in events_text
    assert "pipeline_resumed" in events_text
    assert "pipeline_done"    in events_text


def test_pause_blocks_until_resume_sentinel_appears(tmp_path, monkeypatch):
    """Pause alone blocks until a resume sentinel appears."""
    import threading
    from orchestrator import core as core_mod
    from orchestrator.core import (
        run_pipeline, PAUSE_SENTINEL, RESUME_SENTINEL,
    )

    monkeypatch.setattr(core_mod, "PAUSE_POLL_SECONDS", 0.02)

    pipeline_dir = tmp_path / "pipe"
    _build_two_layer_pipeline(pipeline_dir)
    state_dir = pipeline_dir / ".state"

    fake_exec, ran = _stub_exec_agent()

    def exec_and_signal(aid, agent_dir, deps, agent_dirs, cfg, log, run_id):
        result = fake_exec(aid, agent_dir, deps, agent_dirs, cfg, log, run_id)
        if aid == "001_first":
            state_dir.mkdir(exist_ok=True)
            (state_dir / PAUSE_SENTINEL).touch()

            def drop_resume_later():
                time.sleep(0.1)
                (state_dir / RESUME_SENTINEL).touch()
            threading.Thread(target=drop_resume_later, daemon=True).start()
        return result

    monkeypatch.setattr(core_mod, "exec_agent", exec_and_signal)

    summary = run_pipeline(pipeline_dir)

    assert ran == ["001_first", "002_second"]
    assert summary["status"] == "done"
    assert not (state_dir / PAUSE_SENTINEL).exists()
    assert not (state_dir / RESUME_SENTINEL).exists()


def test_pause_event_emitted_strictly_after_current_layer_done(
    tmp_path, monkeypatch,
):
    """pipeline_paused must come AFTER the current layer's layer_done."""
    from orchestrator import core as core_mod
    from orchestrator.core import (
        run_pipeline, PAUSE_SENTINEL, RESUME_SENTINEL,
    )

    pipeline_dir = tmp_path / "pipe"
    _build_two_layer_pipeline(pipeline_dir)
    state_dir = pipeline_dir / ".state"
    state_dir.mkdir()
    (state_dir / PAUSE_SENTINEL).touch()
    (state_dir / RESUME_SENTINEL).touch()

    fake_exec, ran = _stub_exec_agent()
    monkeypatch.setattr(core_mod, "exec_agent", fake_exec)

    run_pipeline(pipeline_dir)

    snap_dirs = list((pipeline_dir / "history").iterdir())
    events = [
        json.loads(line)
        for line in (snap_dirs[0] / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    event_names = [e["event"] for e in events]

    first_layer_done_idx = next(
        i for i, e in enumerate(events)
        if e["event"] == "layer_done" and e.get("layer") == 0
    )
    pause_idx  = event_names.index("pipeline_paused")
    resume_idx = event_names.index("pipeline_resumed")

    assert first_layer_done_idx < pause_idx, (
        "pipeline_paused must be emitted AFTER layer_done(0), not before"
    )
    assert pause_idx < resume_idx

    layer_1_start_idx = next(
        i for i, e in enumerate(events)
        if e["event"] == "layer_start" and e.get("layer") == 1
    )
    assert resume_idx < layer_1_start_idx
    assert events[pause_idx]["next_layer"] == 1


def test_pause_dropped_during_last_layer_is_ignored(
    tmp_path, monkeypatch,
):
    """Pausing at pipeline termination has no effect."""
    from orchestrator import core as core_mod
    from orchestrator.core import run_pipeline, PAUSE_SENTINEL

    pipeline_dir = tmp_path / "pipe"
    _build_two_layer_pipeline(pipeline_dir)
    state_dir = pipeline_dir / ".state"

    fake_exec, ran = _stub_exec_agent()

    def exec_and_signal(aid, agent_dir, deps, agent_dirs, cfg, log, run_id):
        result = fake_exec(aid, agent_dir, deps, agent_dirs, cfg, log, run_id)
        if aid == "002_second":
            state_dir.mkdir(exist_ok=True)
            (state_dir / PAUSE_SENTINEL).touch()
        return result

    monkeypatch.setattr(core_mod, "exec_agent", exec_and_signal)

    summary = run_pipeline(pipeline_dir)

    assert ran == ["001_first", "002_second"]
    assert summary["status"] == "done"

    assert (state_dir / PAUSE_SENTINEL).exists()

    snap_dirs = list((pipeline_dir / "history").iterdir())
    events_text = (snap_dirs[0] / "events.jsonl").read_text(encoding="utf-8")
    assert "pipeline_paused" not in events_text


def test_no_sentinel_present_is_a_no_op(tmp_path, monkeypatch):
    """Baseline: without any sentinel files, pause logic is invisible."""
    from orchestrator import core as core_mod
    from orchestrator.core import run_pipeline

    pipeline_dir = tmp_path / "pipe"
    _build_two_layer_pipeline(pipeline_dir)

    fake_exec, ran = _stub_exec_agent()
    monkeypatch.setattr(core_mod, "exec_agent", fake_exec)

    summary = run_pipeline(pipeline_dir)

    assert ran == ["001_first", "002_second"]
    assert summary["status"] == "done"

    snap_dirs = list((pipeline_dir / "history").iterdir())
    events_text = (snap_dirs[0] / "events.jsonl").read_text(encoding="utf-8")
    assert "pipeline_paused"  not in events_text
    assert "pipeline_resumed" not in events_text


def test_run_pipeline_emits_empty_label_and_note_in_summary(tmp_path, monkeypatch):
    """Empty label/note land in summary and the on-disk snapshot."""
    from orchestrator import core as core_mod
    from orchestrator.core import run_pipeline

    pipeline_dir = tmp_path / "pipe"
    pipeline_dir.mkdir()
    (pipeline_dir / "pipeline.json").write_text(
        json.dumps({
            "id":   "p",
            "name": "stub",
            "agents": [{"id": "001_stub", "depends_on": []}],
        }),
        encoding="utf-8",
    )
    agent_dir = pipeline_dir / "agents" / "001_stub"
    agent_dir.mkdir(parents=True)
    (agent_dir / "01_system.md").write_text("sys", encoding="utf-8")
    (agent_dir / "02_prompt.md").write_text("task", encoding="utf-8")

    def fake_exec_agent(aid, ad, deps, agent_dirs, cfg, log, run_id):
        (ad / "03_inputs").mkdir(exist_ok=True)
        (ad / "04_context.md").write_text("ctx", encoding="utf-8")
        (ad / "05_output.md").write_text("out", encoding="utf-8")
        (ad / "06_status.json").write_text(
            json.dumps({"agent_id": aid, "status": "done"}),
            encoding="utf-8",
        )
        return "done"

    monkeypatch.setattr(core_mod, "exec_agent", fake_exec_agent)

    summary = run_pipeline(pipeline_dir)

    assert summary["label"] == ""
    assert summary["note"]  == ""
    assert summary["status"] == "done"

    snap_dirs = list((pipeline_dir / "history").iterdir())
    assert len(snap_dirs) == 1
    snap_summary = json.loads((snap_dirs[0] / "summary.json").read_text())
    assert snap_summary["label"] == ""
    assert snap_summary["note"]  == ""
