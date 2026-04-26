"""
Cogniflow Orchestrator — Pipeline runner.

run_pipeline() is the single entry point.  It:
  1. validate_pipeline()          — IMP-03: fail before spending credits
  2. compute_layers()             — IMP-07: networkx topological sort
  3. For each layer → run_layer() — parallel or sequential
  4. Emits all lifecycle events   — structured JSONL for observability
"""

from __future__ import annotations
import json
import re
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import OrchestratorConfig, DEFAULT_CONFIG
from .events import EventLog
from .validate import validate_pipeline
from .dag import build_graph, compute_layers, compute_layers_fallback, get_dependencies
from .agent import exec_agent, is_done, read_status, STATUS_BYPASSED
from .exceptions import PipelineError

try:
    import networkx as nx
    _HAS_NX = True
except ImportError:
    _HAS_NX = False


def _run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


_EMPTY_TOKEN_TOTALS: dict[str, Any] = {
    "total_input":          0,
    "total_output":         0,
    "total_cache_creation": 0,
    "total_cache_read":     0,
    "total_cost_usd":       0.0,
    "agents_counted":       0,
}

# Observer-controlled pause/resume sentinels (consumed by the orchestrator).
#
# The observer instructs the running orchestrator by dropping files into
# pipeline_dir/.state/:
#   • PAUSE_SENTINEL  — detected between layers → orchestrator removes it,
#                       emits pipeline_paused, then blocks until...
#   • RESUME_SENTINEL — orchestrator removes it, emits pipeline_resumed,
#                       and proceeds into the next layer.
#
# Files are consumed (removed) by the orchestrator so their presence
# always reflects a pending instruction. No file present ⇒ nothing
# pending. This keeps the observer fully in control: it's the only
# party that creates these files; the orchestrator is purely reactive.
#
# Pause is transient within a run — the final status of a run that was
# paused and then resumed is "done" (or "failed"), not "paused".
PAUSE_SENTINEL  = "pause"
RESUME_SENTINEL = "resume"
PAUSE_POLL_SECONDS = 1.0


# Per-agent files captured in each history snapshot. 03_inputs/ is handled
# separately (it's a directory, copied with copytree).
_AGENT_SNAPSHOT_FILES: tuple[str, ...] = (
    "00_config.json",
    "01_system.md",
    "02_prompt.md",
    "04_context.md",
    "05_output.md",
    "05_usage.json",
    "06_status.json",
    "routing.json",
)


def run_pipeline(
    pipeline_dir: Path,
    config: OrchestratorConfig | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """
    Execute the pipeline defined in pipeline_dir/pipeline.json.

    Directory layout expected:
      pipeline_dir/
        pipeline.json
        agents/
          <agent_id>/
            01_system.md
            02_prompt.md
            00_config.json   (optional)

    State is written to pipeline_dir/.state/ at runtime.

    Returns a summary dict: {run_id, status, duration_s, layers, agents_run, agents_skipped}
    """
    cfg    = config or DEFAULT_CONFIG
    run_id = run_id or _run_id()

    pipeline_json = pipeline_dir / "pipeline.json"
    agents_base   = pipeline_dir / "agents"
    state_dir     = pipeline_dir / ".state"
    state_dir.mkdir(parents=True, exist_ok=True)

    events_path = state_dir / "events.jsonl"
    log = EventLog(events_path)
    # Capture the end of prior runs' events so we can slice out THIS run's
    # events for the history snapshot at end of run.
    events_offset = events_path.stat().st_size if events_path.exists() else 0

    # ── 1. Validate before spending a single credit (IMP-03) ──────────────
    print("\n" + "═" * 50)
    print("  Cogniflow Multi-Agent Orchestrator")
    print("═" * 50)
    print(f"  Pipeline dir : {pipeline_dir}")
    print(f"  Run ID       : {run_id}")
    print(f"  Claude bin   : {cfg.claude_bin}")
    print(f"  Timeout      : {cfg.agent_timeout}s per agent")
    print("═" * 50 + "\n")

    print("  Validating pipeline…", end=" ", flush=True)
    try:
        data = validate_pipeline(pipeline_json, agents_base)
        print("✓")
    except Exception as e:
        print("✗")
        log.pipeline_error("validation_failed", str(e))
        raise

    name = data.get("name", "unnamed")
    agents_def: list[dict] = data["agents"]
    total = len(agents_def)

    log.pipeline_start(name, run_id, total)
    print(f"\n  Pipeline : {name}")
    print(f"  Agents   : {total}\n")

    # ── 2-3. Execute, with an end-of-run snapshot regardless of outcome ──
    t_pipeline_start = time.monotonic()
    agents_run     = 0
    agents_skipped = 0
    summary_status = "done"
    layers: list[list[str]] = []
    totals: dict[str, Any] = _EMPTY_TOKEN_TOTALS.copy()

    pause_path  = state_dir / PAUSE_SENTINEL
    resume_path = state_dir / RESUME_SENTINEL

    try:
        # Build graph and compute layers
        if _HAS_NX:
            g      = build_graph(agents_def)
            layers = compute_layers(g)
        else:
            g      = None
            layers = compute_layers_fallback(agents_def)

        # Execute layers
        last_layer_idx = len(layers) - 1
        for layer_num, layer_agents in enumerate(layers):
            # Skip agents that were bypassed by a router in a previous layer
            active_agents = [
                aid for aid in layer_agents
                if read_status(agents_base / aid).get("status") != STATUS_BYPASSED
            ]

            if active_agents:
                parallel    = len(active_agents) > 1
                layer_label = f"parallel ×{len(active_agents)}" if parallel else "sequential"
                print(f"── Layer {layer_num} [{layer_label}]: {', '.join(active_agents)}")

                t_layer = time.monotonic()
                log.layer_start(layer_num, active_agents, parallel)

                try:
                    ran, skipped = _run_layer(
                        active_agents, agents_base, agents_def, g, cfg, log, run_id, parallel
                    )
                    agents_run     += ran
                    agents_skipped += skipped
                except PipelineError as exc:
                    failed = [a for a in active_agents]
                    log.layer_fail(layer_num, failed)
                    log.pipeline_error("layer_failed", str(exc))
                    summary_status = "failed"
                    print(f"\n  ✗ Pipeline halted at layer {layer_num}: {exc}\n")
                    raise

                log.layer_done(layer_num, time.monotonic() - t_layer)
                print()

            # ── Observer pause/resume handshake (AFTER layer completes) ──
            # Emitted only once the current layer has fully finished, so
            # the observer UI can render "Pausing…" while the layer
            # drains and flip to "Paused" the moment pipeline_paused
            # arrives. Skipped on the last layer: pausing before pipeline
            # termination would have no effect.
            if layer_num < last_layer_idx:
                _check_pause(
                    pause_path, resume_path,
                    next_layer=layer_num + 1,
                    log=log, run_id=run_id, config=cfg,
                )

        # ── Token rollup (IMP-07) ─────────────────────────────────────────
        # Sum the per-agent usage blocks written into 06_status.json by
        # exec_agent(). Emit pipeline_tokens *before* pipeline_done so the
        # final two events bracket the totals cleanly.
        duration = time.monotonic() - t_pipeline_start
        totals   = _sum_pipeline_tokens(agents_base, agents_def)
        if totals["agents_counted"] > 0:
            log.pipeline_tokens(
                run_id,
                total_input=totals["total_input"],
                total_output=totals["total_output"],
                total_cache_creation=totals["total_cache_creation"],
                total_cache_read=totals["total_cache_read"],
                total_cost_usd=totals["total_cost_usd"],
                agents_counted=totals["agents_counted"],
            )
        log.pipeline_done(run_id, len(layers), duration)

        print("═" * 50)
        print(f"  Done  ·  {len(layers)} layers  ·  {duration:.0f}s  ·  "
              f"{agents_run} run, {agents_skipped} skipped")
        if totals["agents_counted"] > 0:
            print(f"  Tokens · in {totals['total_input']:,} · "
                  f"out {totals['total_output']:,} · "
                  f"cache_read {totals['total_cache_read']:,} · "
                  f"${totals['total_cost_usd']:.4f}  "
                  f"({totals['agents_counted']}/{len(agents_def)} agents reported)")
        print("═" * 50 + "\n")
    finally:
        # Whatever happened (success, PipelineError, KeyboardInterrupt),
        # snapshot the pipeline state for this run before returning/raising.
        final_duration = time.monotonic() - t_pipeline_start
        if summary_status != "done" and not totals["agents_counted"]:
            # Salvage any partial token data before we snapshot.
            try:
                totals = _sum_pipeline_tokens(agents_base, agents_def)
            except Exception:
                totals = _EMPTY_TOKEN_TOTALS.copy()

        # label and note are intentionally blank at run time. They live in
        # summary.json so a reviewer can annotate a run after the fact
        # (e.g. label="cot-critic", note="tries chain-of-thought for the
        # critic agent"). Placed first so they're the first thing an
        # editor sees when opening summary.json.
        summary = {
            "label":          "",
            "note":           "",
            "run_id":         run_id,
            "status":         summary_status,
            "duration_s":     round(final_duration, 1),
            "layers":         len(layers),
            "agents_run":     agents_run,
            "agents_skipped": agents_skipped,
            "tokens":         totals,
        }

        try:
            snap_dir = _snapshot_run(
                pipeline_dir, agents_base, agents_def,
                run_id, summary, cfg, events_path, events_offset,
            )
            if cfg.verbose:
                print(f"  History  : history/{snap_dir.name}\n")
        except Exception as snap_exc:
            # Snapshot failures must not mask the real run outcome.
            print(f"  ⚠  History snapshot failed: {snap_exc}\n")

    return summary


def _check_pause(
    pause_path:  Path,
    resume_path: Path,
    next_layer:  int,
    log:         "EventLog",
    run_id:      str,
    config:      OrchestratorConfig,
) -> None:
    """
    Between-layer pause/resume handshake driven by the observer.

    If `pause_path` exists:
      1. Consume it (unlink), emit pipeline_paused.
      2. Poll every PAUSE_POLL_SECONDS until `resume_path` appears.
      3. Consume the resume file, emit pipeline_resumed, return.

    If neither file is present this is a no-op.

    No-ops are deliberately cheap (a single Path.exists() call per layer)
    so adding the check between every layer has negligible cost.
    """
    if not pause_path.exists():
        return

    # Consume the pause instruction so its presence always reflects a
    # pending request. Defensive try/except: the observer may race us.
    try:
        pause_path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass

    log.pipeline_paused(run_id, next_layer=next_layer,
                        sentinel=str(pause_path))
    if config.verbose:
        print(f"  ⏸  Paused before layer {next_layer}. "
              f"Create {resume_path.name!r} in .state/ to resume.")

    pause_started = time.monotonic()
    while not resume_path.exists():
        time.sleep(PAUSE_POLL_SECONDS)

    try:
        resume_path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass

    waited = time.monotonic() - pause_started
    log.pipeline_resumed(run_id, resumed_layer=next_layer, waited_s=waited)
    if config.verbose:
        print(f"  ▶  Resumed at layer {next_layer} "
              f"(paused for {waited:.0f}s).")


def _next_history_version(history_dir: Path) -> int:
    """Scan history/v<N>_* folders and return N for the next run."""
    if not history_dir.exists():
        return 1
    nums: list[int] = []
    for d in history_dir.iterdir():
        if not d.is_dir():
            continue
        m = re.match(r"^v(\d+)_", d.name)
        if m:
            nums.append(int(m.group(1)))
    return (max(nums) + 1) if nums else 1


def _snapshot_run(
    pipeline_dir:  Path,
    agents_base:   Path,
    agents_def:    list[dict],
    run_id:        str,
    summary:       dict[str, Any],
    config:        OrchestratorConfig,
    events_path:   Path,
    events_offset: int,
) -> Path:
    """
    Freeze the full state of one run under pipeline_dir/history/v{N}_{run_id}/.

    Captured per run:
      • pipeline.json                 — DAG/topology at run time
      • agents/<id>/                  — every input and artefact:
            00_config.json, 01_system.md, 02_prompt.md,
            03_inputs/ (incl. from_*.md and static/),
            04_context.md, 05_output.md, 05_usage.json,
            06_status.json, routing.json (if present)
      • events.jsonl                  — this run's slice of .state/events.jsonl,
                                        taken by byte offset captured at start.
      • summary.json                  — run_pipeline's return dict.
      • env.snapshot.json             — sanitised config (no secrets): model
                                        limits, timeouts, retry policy, etc.

    The snapshot runs in a try/finally in run_pipeline() so failed runs are
    also captured — typically the most interesting ones to inspect.
    """
    history_dir = pipeline_dir / "history"
    history_dir.mkdir(parents=True, exist_ok=True)
    version = _next_history_version(history_dir)
    snap    = history_dir / f"v{version}_{run_id}"
    snap.mkdir()

    # pipeline.json snapshot
    src_pj = pipeline_dir / "pipeline.json"
    if src_pj.exists():
        shutil.copy2(src_pj, snap / "pipeline.json")

    # Per-agent files
    snap_agents_dir = snap / "agents"
    snap_agents_dir.mkdir()
    for a in agents_def:
        aid = a.get("id")
        if not aid:
            continue
        src = agents_base / aid
        if not src.is_dir():
            continue
        dst = snap_agents_dir / aid
        dst.mkdir()
        for name in _AGENT_SNAPSHOT_FILES:
            sp = src / name
            if sp.is_file():
                shutil.copy2(sp, dst / name)
        inputs_src = src / "03_inputs"
        if inputs_src.is_dir():
            shutil.copytree(inputs_src, dst / "03_inputs")

    # events.jsonl slice — bytes appended since run start
    if events_path.exists():
        try:
            with open(events_path, "rb") as f:
                f.seek(events_offset)
                slice_bytes = f.read()
            (snap / "events.jsonl").write_bytes(slice_bytes)
        except OSError:
            pass  # log file races are non-fatal for the snapshot

    # summary.json
    (snap / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    # env.snapshot.json — tunables that shape behaviour, no secrets
    env_snap = {
        "claude_bin":            str(config.claude_bin),
        "agent_timeout":         config.agent_timeout,
        "context_limit":         config.context_limit,
        "input_budget_fraction": config.input_budget_fraction,
        "input_token_budget":    config.input_token_budget,
        "max_parallel_agents":   config.max_parallel_agents,
        "max_retries":           config.max_retries,
        "retry_delays_s":        list(config.retry_delays_s),
    }
    (snap / "env.snapshot.json").write_text(
        json.dumps(env_snap, indent=2),
        encoding="utf-8",
    )

    return snap


def _sum_pipeline_tokens(
    agents_base: Path,
    agents_def: list[dict],
) -> dict[str, Any]:
    """
    Walk every agent's 06_status.json, sum the `usage` blocks written
    by exec_agent(). Agents that ran on an older claude CLI (no usage)
    or that were bypassed/skipped contribute nothing — `agents_counted`
    tells the operator how many agents actually reported numbers.
    """
    totals = {
        "total_input":          0,
        "total_output":         0,
        "total_cache_creation": 0,
        "total_cache_read":     0,
        "total_cost_usd":       0.0,
        "agents_counted":       0,
    }
    for a in agents_def:
        status_path = agents_base / a["id"] / "06_status.json"
        if not status_path.exists():
            continue
        try:
            doc = json.loads(status_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        usage = doc.get("usage")
        if not isinstance(usage, dict):
            continue
        totals["total_input"]          += int(usage.get("input_tokens", 0))
        totals["total_output"]         += int(usage.get("output_tokens", 0))
        totals["total_cache_creation"] += int(usage.get("cache_creation_tokens", 0))
        totals["total_cache_read"]     += int(usage.get("cache_read_tokens", 0))
        totals["total_cost_usd"]       += float(usage.get("cost_usd", 0.0))
        totals["agents_counted"]       += 1
    totals["total_cost_usd"] = round(totals["total_cost_usd"], 6)
    return totals


def _run_layer(
    agent_ids: list[str],
    agents_base: Path,
    agents_def: list[dict],
    graph: Any,
    config: OrchestratorConfig,
    log: EventLog,
    run_id: str,
    parallel: bool,
) -> tuple[int, int]:
    """Run one layer.  Returns (agents_run, agents_skipped)."""

    # Build a dep lookup: agent_id → list of dependency IDs
    dep_map = {a["id"]: a.get("depends_on", []) for a in agents_def}

    run_count  = 0
    skip_count = 0
    errors: list[Exception] = []

    def _run_one(aid: str) -> str:
        deps = dep_map.get(aid, [])
        if _HAS_NX and graph is not None:
            from .dag import get_dependencies
            deps = get_dependencies(graph, aid)
        return exec_agent(aid, agents_base, deps, config, log, run_id, graph)

    if not parallel or len(agent_ids) == 1:
        for aid in agent_ids:
            status = _run_one(aid)
            if status in ("done",):  run_count  += 1
            else:                    skip_count += 1
    else:
        max_workers = min(len(agent_ids), config.max_parallel_agents)
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(_run_one, aid): aid for aid in agent_ids}
            for fut in as_completed(futures):
                aid = futures[fut]
                try:
                    status = fut.result()
                    if status in ("done",):  run_count  += 1
                    else:                    skip_count += 1
                except Exception as exc:
                    errors.append(exc)

        if errors:
            raise errors[0]   # re-raise first failure after all futures settle

    return run_count, skip_count


# ── Observability helpers ─────────────────────────────────────────────────────

def pipeline_status(pipeline_dir: Path) -> None:
    """Print a live status table for all agents."""
    agents_base = pipeline_dir / "agents"
    pipeline_json = pipeline_dir / "pipeline.json"

    if not pipeline_json.exists():
        print("No pipeline.json found.")
        return

    data = json.loads(pipeline_json.read_text(encoding="utf-8"))
    agents = data.get("agents", [])

    print(f"\n{'AGENT':<28} {'STATUS':<12} {'DUR(s)':<8} {'STARTED':<22} {'OUTPUT'}")
    print("─" * 90)
    for a in agents:
        aid = a["id"]
        s   = read_status(agents_base / aid)
        print(
            f"{aid:<28} "
            f"{s.get('status','pending'):<12} "
            f"{str(s.get('duration_s','-')):<8} "
            f"{s.get('started_at','-'):<22} "
            f"{s.get('output_bytes', '-')}"
        )
    print()


def watch_events(pipeline_dir: Path, follow: bool = True) -> None:
    """Stream events.jsonl to stdout."""
    import sys
    log_path = pipeline_dir / ".state" / "events.jsonl"
    if not log_path.exists():
        print("No events.jsonl yet.")
        return

    with open(log_path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                _pretty_event(line.strip())
        if follow:
            import time as _time
            while True:
                line = f.readline()
                if line and line.strip():
                    _pretty_event(line.strip())
                else:
                    _time.sleep(0.5)


def _pretty_event(raw: str) -> None:
    try:
        r = json.loads(raw)
        agent = f" · {r['agent']}" if "agent" in r else ""
        print(f"[{r['ts']}] {r['event']}{agent}")
    except Exception:
        print(raw)
