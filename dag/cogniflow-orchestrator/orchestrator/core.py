"""
Cogniflow Orchestrator v3.5 — Core pipeline runner.

run_pipeline() auto-detects mode and dispatches:
  • Acyclic: layered ThreadPoolExecutor path (from v2.1.0, extended with
             v1 features: pause/resume handshake, history snapshots,
             pipeline-level token rollup).
  • Cyclic:  run_cyclic_pipeline() event loop (unchanged).

All v3.0/v3.5 behaviour is preserved (gitignore, hooks, CLAUDE.md). Run
artefacts are still written to ``.state/``.
"""
from __future__ import annotations

import json
import re
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TYPE_CHECKING

from .agent import run_agent, read_status, STATUS_BYPASSED, STATUS_DONE
from .cyclic_engine import run_cyclic_pipeline as _run_cyclic
from .dag import build_dag, is_cyclic_pipeline
from .debug import get_logger, setup_logging
from .events import EventLog
from .exceptions import PipelineError
from .hooks import generate_claude_md, install_hooks
from .secrets import generate_gitignore
from .validate import validate_pipeline

if TYPE_CHECKING:
    from .config import OrchestratorConfig


# Backwards-compat alias — tests (and any v1-era code) that monkeypatch
# `orchestrator.core.exec_agent` still work against the v3.5 runner.
exec_agent = run_agent


# ── Pause / resume sentinels (v1 observer handshake) ──────────────────────────

PAUSE_SENTINEL      = "pause"
RESUME_SENTINEL     = "resume"
PAUSE_POLL_SECONDS  = 1.0


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


def resolve_agent_dir(pipeline_dir: Path, agent_spec: dict[str, Any]) -> Path:
    """
    Locate an agent's directory with backward compatibility.

    Resolution order:
      1. ``dir`` field in pipeline.json (v3.5 style — wins if set)
      2. ``<pipeline_dir>/agents/<id>/`` (v1 style — preferred fallback)
      3. ``<pipeline_dir>/<id>/`` (v3.5 implicit fallback)
    """
    aid = agent_spec.get("id", "")
    explicit = agent_spec.get("dir")
    if explicit:
        return pipeline_dir / explicit
    v1_path = pipeline_dir / "agents" / aid
    if v1_path.exists():
        return v1_path
    return pipeline_dir / aid


_EMPTY_TOKEN_TOTALS: dict[str, Any] = {
    "total_input":          0,
    "total_output":         0,
    "total_cache_creation": 0,
    "total_cache_read":     0,
    "total_cost_usd":       0.0,
    "agents_counted":       0,
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _run_id() -> str:
    return datetime.now(timezone.utc).strftime("run-%Y%m%d-%H%M%S")


# ── Public API ────────────────────────────────────────────────────────────────

def run_pipeline(
    pipeline_dir: Path,
    config: "OrchestratorConfig | None" = None,
    mode: str = "auto",
    force_hooks_install: bool = False,
    run_id: str | None = None,
) -> dict[str, Any]:
    """
    Validate and run the pipeline at *pipeline_dir*.

    Returns a summary dict: {run_id, status, duration_s, layers,
    agents_run, agents_skipped, tokens, label, note}. Cyclic mode returns
    the same dict shape with layers=0 and minimal fields populated.

    Mode detection:
      auto   → cyclic if any feedback/peer edge exists, else DAG
      dag    → force Kahn's path
      cyclic → force cyclic event loop
    """
    # Late-imported to dodge the circular import config ↔ core.
    if config is None:
        from .config import OrchestratorConfig
        config = OrchestratorConfig.from_pipeline_dir(Path(pipeline_dir))

    pipeline_dir = Path(pipeline_dir)
    spec = validate_pipeline(pipeline_dir)
    run_id = run_id or _run_id()

    # GAP-2 — ensure .gitignore excludes .state/ before we write any state
    generate_gitignore(pipeline_dir)

    state_dir = pipeline_dir / ".state"
    state_dir.mkdir(exist_ok=True)
    events_path = state_dir / "events.jsonl"
    log = EventLog(events_path)
    # Byte offset used to slice this run's events at snapshot time.
    events_offset = events_path.stat().st_size if events_path.exists() else 0

    setup_logging(pipeline_dir, config.debug_enabled, config.debug_logfile)
    dlog = get_logger()
    dlog.debug(f"[engine] run_id={run_id} mode={mode} "
               f"pipeline={spec.get('name')!r}")
    dlog.debug(f"[engine] claude_bin={config.claude_bin} "
               f"timeout={config.agent_timeout}s "
               f"max_parallel={config.max_parallel_agents} "
               f"retries={config.max_retries}")

    # ── Banner (v1 style) ─────────────────────────────────────────────────────
    if config.verbose:
        print("\n" + "═" * 50)
        print("  Cogniflow Multi-Agent Orchestrator")
        print("═" * 50)
        print(f"  Pipeline dir : {pipeline_dir}")
        print(f"  Run ID       : {run_id}")
        print(f"  Claude bin   : {config.claude_bin}")
        print(f"  Timeout      : {config.agent_timeout}s per agent")
        print("═" * 50 + "\n")

    if mode == "auto":
        use_cyclic = is_cyclic_pipeline(spec)
    elif mode == "cyclic":
        use_cyclic = True
    else:
        use_cyclic = False
    dlog.debug(f"[engine] mode resolved → {'cyclic' if use_cyclic else 'dag'}")

    agents_def = spec.get("agents", [])
    total = len(agents_def)
    log.pipeline_start(spec.get("name", pipeline_dir.name), run_id)

    t0 = time.monotonic()
    summary_status   = STATUS_DONE
    summary_layers   = 0
    agents_run       = 0
    agents_skipped   = 0
    totals: dict[str, Any] = _EMPTY_TOKEN_TOTALS.copy()
    agent_dirs: dict[str, Path] = {
        a["id"]: resolve_agent_dir(pipeline_dir, a)
        for a in agents_def
    }

    try:
        if use_cyclic:
            generate_claude_md(pipeline_dir, spec)
            if force_hooks_install or not (pipeline_dir / ".claude" / "settings.json").exists():
                install_hooks(pipeline_dir)
            _run_cyclic(pipeline_dir, spec, config, log, run_id)
        else:
            summary_layers, agents_run, agents_skipped = _run_dag_pipeline(
                pipeline_dir, spec, agent_dirs, config, log, run_id,
            )

        # ── Token rollup (IMP-07) ────────────────────────────────────────────
        totals = _sum_pipeline_tokens(agent_dirs)
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

        duration = round(time.monotonic() - t0, 2)
        log.pipeline_done(run_id, duration)
        if config.verbose:
            print("═" * 50)
            print(f"  Done  ·  {summary_layers} layers  ·  {duration:.0f}s  ·  "
                  f"{agents_run} run, {agents_skipped} skipped")
            if totals["agents_counted"] > 0:
                print(f"  Tokens · in {totals['total_input']:,} · "
                      f"out {totals['total_output']:,} · "
                      f"cache_read {totals['total_cache_read']:,} · "
                      f"${totals['total_cost_usd']:.4f}  "
                      f"({totals['agents_counted']}/{total} agents reported)")
            print("═" * 50 + "\n")

    except PipelineError as exc:
        summary_status = "failed"
        log.pipeline_error(run_id, str(exc))
        if totals["agents_counted"] == 0:
            try:
                totals = _sum_pipeline_tokens(agent_dirs)
            except Exception:
                totals = _EMPTY_TOKEN_TOTALS.copy()
        raise
    except Exception as exc:
        summary_status = "failed"
        log.pipeline_error(run_id, str(exc))
        if totals["agents_counted"] == 0:
            try:
                totals = _sum_pipeline_tokens(agent_dirs)
            except Exception:
                totals = _EMPTY_TOKEN_TOTALS.copy()
        raise
    finally:
        final_duration = round(time.monotonic() - t0, 2)
        summary = {
            "label":          "",
            "note":           "",
            "run_id":         run_id,
            "status":         summary_status,
            "duration_s":     final_duration,
            "layers":         summary_layers,
            "agents_run":     agents_run,
            "agents_skipped": agents_skipped,
            "tokens":         totals,
        }
        try:
            snap_dir = _snapshot_run(
                pipeline_dir, agent_dirs, agents_def,
                run_id, summary, config, events_path, events_offset,
            )
            if config.verbose and snap_dir:
                print(f"  History  : history/{snap_dir.name}\n")
        except Exception as snap_exc:
            if config.verbose:
                print(f"  ⚠  History snapshot failed: {snap_exc}\n")

    return summary


# ── DAG (acyclic) execution path ──────────────────────────────────────────────

def _run_dag_pipeline(
    pipeline_dir: Path,
    spec: dict[str, Any],
    agent_dirs: dict[str, Path],
    config: "OrchestratorConfig",
    log: EventLog,
    run_id: str,
) -> tuple[int, int, int]:
    """
    Run the DAG. Returns (num_layers, agents_run, agents_skipped).

    The observer pause/resume handshake is applied between layers.
    """
    layers = build_dag(spec)
    dlog = get_logger()

    agent_map = {a["id"]: a for a in spec["agents"]}
    dlog.debug(f"[engine] computed {len(layers)} layer(s): "
               + " → ".join(f"[{', '.join(layer)}]" for layer in layers))

    state_dir   = pipeline_dir / ".state"
    pause_path  = state_dir / PAUSE_SENTINEL
    resume_path = state_dir / RESUME_SENTINEL

    agents_run     = 0
    agents_skipped = 0
    last_layer_idx = len(layers) - 1

    for layer_idx, layer in enumerate(layers):
        # Skip agents bypassed by a router in a previous layer.
        active_agents = [
            aid for aid in layer
            if read_status(agent_dirs[aid]).get("status") != STATUS_BYPASSED
        ]
        if not active_agents:
            continue

        parallel    = len(active_agents) > 1
        layer_label = f"parallel ×{len(active_agents)}" if parallel else "sequential"
        if config.verbose:
            print(f"── Layer {layer_idx} [{layer_label}]: {', '.join(active_agents)}")
        dlog.debug(f"[engine] layer {layer_idx} start · agents={active_agents} "
                   f"parallel={parallel}")
        t_layer = time.monotonic()
        log.layer_start(layer_idx, active_agents, parallel)

        errors: list[BaseException] = []
        results: dict[str, str] = {}

        def _run_one(aid: str) -> str:
            agent_spec = agent_map[aid]
            adir       = agent_dirs[aid]
            deps       = agent_spec.get("depends_on", [])
            active_deps = _resolve_router_deps(deps, agent_dirs)
            # Use the module-level attribute so tests that monkeypatch
            # `orchestrator.core.exec_agent` are still honoured.
            return exec_agent(aid, adir, active_deps, agent_dirs, config, log, run_id)

        if not parallel:
            for aid in active_agents:
                try:
                    results[aid] = _run_one(aid)
                except Exception as exc:  # noqa: BLE001 — first failure is re-raised
                    errors.append(exc)
                    break
        else:
            workers = min(len(active_agents), config.max_parallel_agents)
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futures = {ex.submit(_run_one, aid): aid for aid in active_agents}
                for fut in as_completed(futures):
                    aid = futures[fut]
                    exc = fut.exception()
                    if exc is not None:
                        errors.append(exc)
                    else:
                        results[aid] = fut.result()

        if errors:
            log.layer_fail(layer_idx, active_agents)
            log.pipeline_error("layer_failed", str(errors[0]))
            raise errors[0]

        for status in results.values():
            if status == STATUS_DONE:
                agents_run += 1
            else:
                agents_skipped += 1

        log.layer_done(layer_idx, time.monotonic() - t_layer)
        dlog.debug(f"[engine] layer {layer_idx} complete")

        # ── Observer pause/resume handshake (after layer completes) ──────────
        if layer_idx < last_layer_idx:
            _check_pause(pause_path, resume_path,
                         next_layer=layer_idx + 1,
                         log=log, run_id=run_id, config=config)

    return len(layers), agents_run, agents_skipped


def _resolve_router_deps(
    declared_deps: list[str],
    agent_dirs: dict[str, Path],
) -> list[str]:
    """Drop dependencies that were bypassed by a router upstream."""
    active = []
    for dep in declared_deps:
        dep_dir = agent_dirs.get(dep)
        if dep_dir is None:
            active.append(dep)
            continue
        st = read_status(dep_dir)
        if st.get("status") == STATUS_BYPASSED:
            continue
        active.append(dep)
    return active


# ── Observer pause/resume ─────────────────────────────────────────────────────

def _check_pause(
    pause_path:  Path,
    resume_path: Path,
    next_layer:  int,
    log:         EventLog,
    run_id:      str,
    config:      "OrchestratorConfig",
) -> None:
    """
    Between-layer pause/resume handshake driven by the observer.

    If ``pause_path`` exists:
      1. Consume it, emit pipeline_paused.
      2. Poll every PAUSE_POLL_SECONDS until ``resume_path`` appears.
      3. Consume the resume file, emit pipeline_resumed, return.

    No sentinel present → no-op (a single existence check per layer).
    """
    if not pause_path.exists():
        return

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

    t_pause = time.monotonic()
    while not resume_path.exists():
        time.sleep(PAUSE_POLL_SECONDS)

    try:
        resume_path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass

    waited = time.monotonic() - t_pause
    log.pipeline_resumed(run_id, resumed_layer=next_layer, waited_s=waited)
    if config.verbose:
        print(f"  ▶  Resumed at layer {next_layer} "
              f"(paused for {waited:.0f}s).")


# ── History snapshot ──────────────────────────────────────────────────────────

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
    agent_dirs:    dict[str, Path],
    agents_def:    list[dict],
    run_id:        str,
    summary:       dict[str, Any],
    config:        "OrchestratorConfig",
    events_path:   Path,
    events_offset: int,
) -> Path:
    """
    Freeze the full state of one run under
    ``pipeline_dir/history/v{N}_{run_id}/``.

    Captured per run:
      • pipeline.json                  — DAG/topology at run time
      • agents/<id>/                   — every input and artefact
      • events.jsonl                   — this run's slice of .state/events.jsonl
      • summary.json                   — run_pipeline's return dict
      • env.snapshot.json              — sanitised config (no secrets)

    Called inside a try/finally so failed runs are also captured —
    typically the most interesting ones to inspect.
    """
    history_dir = pipeline_dir / "history"
    history_dir.mkdir(parents=True, exist_ok=True)
    version = _next_history_version(history_dir)
    snap    = history_dir / f"v{version}_{run_id}"
    snap.mkdir()

    src_pj = pipeline_dir / "pipeline.json"
    if src_pj.exists():
        shutil.copy2(src_pj, snap / "pipeline.json")

    snap_agents_dir = snap / "agents"
    snap_agents_dir.mkdir()
    for a in agents_def:
        aid = a.get("id")
        if not aid:
            continue
        src = agent_dirs.get(aid)
        if src is None or not src.is_dir():
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

    if events_path.exists():
        try:
            with open(events_path, "rb") as f:
                f.seek(events_offset)
                slice_bytes = f.read()
            (snap / "events.jsonl").write_bytes(slice_bytes)
        except OSError:
            pass

    (snap / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    env_snap = {
        "claude_bin":            str(config.claude_bin),
        "agent_timeout":         config.agent_timeout,
        "context_limit":         config.model_context_limit,
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


# ── Pipeline-level token rollup (IMP-07) ──────────────────────────────────────

def _sum_pipeline_tokens(agent_dirs: dict[str, Path]) -> dict[str, Any]:
    """Walk every agent's 06_status.json and sum its ``usage`` block."""
    totals = dict(_EMPTY_TOKEN_TOTALS)
    for adir in agent_dirs.values():
        status_path = adir / "06_status.json"
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


# ── Observability helpers ─────────────────────────────────────────────────────

def pipeline_status(pipeline_dir: Path) -> list[dict[str, Any]]:
    """
    Return a list of agent status dicts.

    DAG agents write 06_status.json into their own source directory.
    Cyclic agents write into .state/agents/<id>/. Both locations are read.
    """
    pipeline_dir = Path(pipeline_dir)
    result: list[dict[str, Any]] = []

    pj_path = pipeline_dir / "pipeline.json"
    if pj_path.exists():
        try:
            spec = json.loads(pj_path.read_text(encoding="utf-8"))
            for agent in spec.get("agents", []):
                adir = resolve_agent_dir(pipeline_dir, agent)
                status_file = adir / "06_status.json"
                if status_file.exists():
                    try:
                        result.append(json.loads(status_file.read_text(encoding="utf-8")))
                    except Exception:
                        pass
        except Exception:
            pass

    agents_state = pipeline_dir / ".state" / "agents"
    if agents_state.exists():
        for status_file in sorted(agents_state.glob("*/06_status.json")):
            try:
                result.append(json.loads(status_file.read_text(encoding="utf-8")))
            except Exception:
                pass

    return result


def watch_events(pipeline_dir: Path, follow: bool = False) -> None:
    """Stream events.jsonl to stdout."""
    log_path = Path(pipeline_dir) / ".state" / "events.jsonl"
    if not log_path.exists():
        print("No events.jsonl yet.")
        return

    with open(log_path, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                _pretty_event(line.strip())
        if follow:
            while True:
                line = fh.readline()
                if line and line.strip():
                    _pretty_event(line.strip())
                else:
                    time.sleep(0.5)


def _pretty_event(raw: str) -> None:
    try:
        r     = json.loads(raw)
        agent = f" · {r['agent']}"    if "agent"    in r else ""
        agent = f" · {r['agent_id']}" if "agent_id" in r else agent
        print(f"[{r['ts']}] {r['event']}{agent}")
    except Exception:
        print(raw)
