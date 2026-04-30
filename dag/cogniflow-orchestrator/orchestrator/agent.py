"""
Cogniflow Orchestrator v3.5 — Acyclic (DAG path) agent runner.

run_agent() is called by the ThreadPoolExecutor in core.py for every
agent in a DAG pipeline. The cyclic path uses cyclic_agent.py instead.

Pipeline (per agent):
  1. Resume guard (skip if 06_status.json says done; resume approval wait)
  2. Collect upstream outputs into 03_inputs/ (v1 IMP-01)
  3. GAP-2 — scan 01_system.md and 02_prompt.md for credential patterns
  4. Apply token budget (IMP-06)
  5. Apply GAP-2 ``{{VAR}}`` substitutions while assembling 04_context.md
  6. Invoke claude with retries (IMP-02, IMP-04, IMP-07 envelope, IMP-09)
  7. GAP-1 — validate 05_output.md against output_schema if configured
  8. GAP-3 — pause for human approval if requires_approval: true
  9. Router (IMP-08) — evaluate routing.json and bypass skipped branches
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TYPE_CHECKING

from .approval import request_approval, wait_for_approval
from .budget import apply_budget
from .context import collect_inputs, assemble_context, find_pipeline_dir
from .debug import elided_argv, get_logger, snippet
from .exceptions import (
    AgentExecutionError, AgentTimeoutError,
    ApprovalRejectedError, ApprovalTimeoutError,
    RouterError, SchemaViolationError,
)
from .schema import (
    schema_from_agent_config, validate_output_schema,
    input_schema_from_agent_config, validate_input_schema,
)
from .secrets import apply_substitutions, scan_for_secrets
from .vault import AuditCtx, open_vault_for

if TYPE_CHECKING:
    from .config import OrchestratorConfig
    from .events import EventLog


STATUS_PENDING           = "pending"
STATUS_RUNNING           = "running"
STATUS_DONE              = "done"
STATUS_FAILED            = "failed"
STATUS_TIMEOUT           = "timeout"
STATUS_BYPASSED          = "bypassed"
STATUS_SKIP              = "skipped"
STATUS_INPUT_SCHEMA_FAIL = "input_schema_failed"
STATUS_OUTPUT_SCHEMA_FAIL = "output_schema_failed"
STATUS_AWAITING_FEEDBACK = "awaiting_feedback"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_status(agent_dir: Path) -> dict[str, Any]:
    """Return the status dict, or {'status': 'pending'} if not yet written."""
    p = agent_dir / "06_status.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {"status": STATUS_PENDING}
    return {"status": STATUS_PENDING}


def is_done(agent_dir: Path) -> bool:
    return read_status(agent_dir).get("status") == STATUS_DONE


def mark_bypassed(agent_dir: Path, agent_id: str, by_router: str) -> None:
    agent_dir.mkdir(parents=True, exist_ok=True)
    _write_status_doc(agent_dir / "06_status.json", {
        "agent_id":   agent_id,
        "status":     STATUS_BYPASSED,
        "by_router":  by_router,
        "skipped_at": _now_iso(),
    })


def run_agent(
    agent_id: str,
    agent_dir: Path,
    dependencies: list[str],
    agent_dirs: dict[str, Path],
    config: "OrchestratorConfig",
    log: "EventLog",
    run_id: str,
) -> str:
    dlog = get_logger()
    status_path = agent_dir / "06_status.json"
    agent_dir.mkdir(parents=True, exist_ok=True)

    dlog.debug(f"[agent:{agent_id}] dir={agent_dir} deps={dependencies}")

    # ── 1. Resume guard ───────────────────────────────────────────────────────
    st = read_status(agent_dir)
    current = st.get("status")
    if current == STATUS_DONE:
        log.agent_skip(agent_id, reason="checkpoint")
        dlog.debug(f"[agent:{agent_id}] resume-guard → already done, skipping")
        if config.verbose:
            print(f"  ↷ {agent_id} (already done)")
        return STATUS_SKIP
    if current == STATUS_BYPASSED:
        log.agent_skip(agent_id, reason="bypassed")
        dlog.debug(f"[agent:{agent_id}] resume-guard → bypassed by router")
        if config.verbose:
            print(f"  ↷ {agent_id} (bypassed)")
        return STATUS_BYPASSED
    if current == "awaiting_approval":
        dlog.debug(f"[agent:{agent_id}] resume-guard → awaiting_approval, "
                   "re-entering wait_for_approval without re-invoking claude")
        if config.verbose:
            print(f"  ⏸  {agent_id} — resuming approval wait")
        try:
            wait_for_approval(agent_id, agent_dir, config, log)
        except (ApprovalRejectedError, ApprovalTimeoutError) as exc:
            _write_status(status_path, agent_id, "rejected",
                          duration_s=0, exit_code=-2)
            raise AgentExecutionError(agent_id, -2, str(exc)) from exc
        _write_status(status_path, agent_id, STATUS_DONE,
                      duration_s=st.get("duration_s", 0),
                      exit_code=0)
        log.agent_done(agent_id, st.get("duration_s", 0.0), 0)
        _evaluate_router(agent_id, agent_dir, agent_dirs, config, log)
        return STATUS_DONE

    log.agent_launched(agent_id)
    log.agent_start(agent_id)
    if config.verbose:
        print(f"  🤖 {agent_id} — preparing")

    # ── 2. Collect upstream inputs ────────────────────────────────────────────
    total_input_bytes = collect_inputs(
        agent_id, agent_dir, dependencies, agent_dirs, log, run_id,
    )
    dlog.debug(f"[agent:{agent_id}] inputs collected: {len(dependencies)} dep(s), "
               f"{total_input_bytes:,} bytes")

    # ── 3. GAP-2 credential scan (advisory) ───────────────────────────────────
    findings = scan_for_secrets(agent_id, agent_dir, log)
    if findings:
        dlog.debug(f"[agent:{agent_id}] secret scan: {len(findings)} finding(s) — "
                   + ", ".join(f"{f['file']}={f['pattern']}" for f in findings))

    # ── 4. Token budget ───────────────────────────────────────────────────────
    apply_budget(agent_id, agent_dir, config, log)

    # ── 5. Assemble context with GAP-2 substitutions ──────────────────────────
    assemble_context(agent_id, agent_dir, log, config)

    # ── 5a. v4 — input-schema enforcement (before Claude is invoked) ──────────
    pipeline_dir  = find_pipeline_dir(agent_dir)
    pipeline_name = pipeline_dir.name
    in_schema = input_schema_from_agent_config(agent_dir)
    if in_schema:
        upstream_outputs: dict[str, str] = {}
        for dep_id in dependencies:
            dep_dir = agent_dirs.get(dep_id)
            if dep_dir is None:
                continue
            dep_output = dep_dir / "05_output.md"
            if dep_output.exists():
                try:
                    upstream_outputs[dep_id] = dep_output.read_text(encoding="utf-8")
                except OSError:
                    upstream_outputs[dep_id] = ""
        static_map: dict[str, str] = {}
        static_dir = agent_dir / "03_inputs" / "static"
        if static_dir.exists():
            for f in sorted(static_dir.iterdir()):
                if f.is_file():
                    try:
                        static_map[f.name] = f.read_text(encoding="utf-8",
                                                         errors="replace")
                    except OSError:
                        static_map[f.name] = ""
        try:
            validate_input_schema(agent_id, in_schema, upstream_outputs, static_map)
            log.agent_input_schema_valid(agent_id)
            dlog.debug(f"[input-schema:{agent_id}] PASS")
        except SchemaViolationError as exc:
            log.agent_input_schema_violation(agent_id, exc.violations)
            dlog.debug(f"[input-schema:{agent_id}] FAIL · {exc.violations}")
            _write_status(status_path, agent_id, STATUS_INPUT_SCHEMA_FAIL,
                          duration_s=0, exit_code=0,
                          schema_violations=exc.violations,
                          run_id=run_id)
            if config.verbose:
                print(f"  ✗  {agent_id} — INPUT SCHEMA INVALID")
                for v in exc.violations:
                    print(f"       • {v}")
            raise AgentExecutionError(
                agent_id, 0,
                "Input schema violations:\n" + "\n".join(exc.violations),
            ) from exc

    # ── 5b. v4 — open vault and prepare audit context ────────────────────────
    vault = open_vault_for(config, pipeline_dir)

    def _ctx(file_label: str) -> AuditCtx:
        return AuditCtx(run_id=run_id, pipeline_name=pipeline_name,
                        agent_id=agent_id, file=file_label)

    # ── 6. Load system prompt, agent config ──────────────────────────────────
    sys_path    = agent_dir / "01_system.md"
    system_text = sys_path.read_text(encoding="utf-8").strip() if sys_path.exists() else ""
    system_text = apply_substitutions(system_text, config.substitutions, agent_id, log)
    # v4: rehydrate secrets for the outbound send. 01_system.md is a SOURCE
    # file authored by the user — never rewritten on disk.
    system_text = vault.rehydrate(
        system_text, ctx=_ctx("01_system"), direction="outbound", event_log=log,
    )
    # Windows: cmd.exe mangles newlines in argv when claude.cmd shim is
    # invoked. A multi-line --system-prompt silently drops --output-format
    # json (→ no usage envelope) AND corrupts the system prompt. Collapse
    # to spaces to preserve meaning.
    if sys.platform == "win32":
        system_text = system_text.replace("\r\n", " ").replace("\n", " ")

    ctx_path = agent_dir / "04_context.md"
    ctx_text = ctx_path.read_text(encoding="utf-8") if ctx_path.exists() else ""
    # v4: rehydrate secrets in the context that goes to Claude. If the
    # operator wants rehydrated on-disk artefacts, rewrite 04_context.md
    # with the rehydrated content now.
    ctx_text = vault.rehydrate(
        ctx_text, ctx=_ctx("04_context"), direction="outbound", event_log=log,
    )
    if config.rehydrate_outputs and ctx_path.exists():
        ctx_path.write_text(ctx_text, encoding="utf-8")
    dlog.debug(f"[agent:{agent_id}] system: {len(system_text):,} chars · "
               f"context: {len(ctx_text):,} chars")

    cfg: dict[str, Any] = {}
    cfg_path = agent_dir / "00_config.json"
    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    requires_approval = bool(cfg.get("requires_approval", False))
    agent_model       = cfg.get("model")
    extra_flags       = cfg.get("cli_flags") or []
    cwd_rel           = cfg.get("cwd")
    subproc_cwd: Path | None = None
    if cwd_rel:
        # Resolve cwd relative to the pipeline_dir (agent_dir.parent.parent
        # for `agents/<id>/` layouts, or wherever pipeline.json lives).
        subproc_cwd = _resolve_agent_cwd(agent_dir, cwd_rel)
        subproc_cwd.mkdir(parents=True, exist_ok=True)

    # IMP-09 — retry policy: per-agent 00_config.json overrides config.json.
    max_retries  = int(cfg.get("max_retries", config.max_retries))
    retry_delays = cfg.get("retry_delays_s", config.retry_delays_s) or [0]
    max_attempts = max(1, max_retries + 1)

    # ── 7. Invoke claude with retries ─────────────────────────────────────────
    argv = (
        [config.claude_bin]
        + config.model_args(agent_model)
        + ["--system-prompt", system_text,
           "--output-format", "json",  # IMP-07: get usage envelope
           "-p"]
        + list(extra_flags)
    )

    output_path = agent_dir / "05_output.md"
    usage_path  = agent_dir / "05_usage.json"
    # Clear a stale symlink (legacy v1 layouts) + stale usage file.
    if output_path.is_symlink():
        output_path.unlink()
    if usage_path.exists():
        usage_path.unlink()

    _write_status(status_path, agent_id, STATUS_RUNNING)

    success         = False
    attempts_used   = 0
    last_kind       = ""
    last_exit_code  = -1
    last_stderr     = ""
    last_duration   = 0.0
    last_envelope: dict[str, Any] | None = None
    last_parse_error: str | None          = None

    for attempt in range(1, max_attempts + 1):
        attempts_used = attempt
        if attempt > 1:
            log.agent_start(agent_id, attempt=attempt)
            if config.verbose:
                print(f"  ↻  {agent_id} — retry attempt {attempt}/{max_attempts}")
        else:
            if config.verbose:
                print(f"  ▶  {agent_id} — calling claude")

        dlog.debug(f"[claude:{agent_id}] invoking: {elided_argv(argv)} "
                   f"stdin=<{len(ctx_text):,} chars> "
                   f"(timeout={config.agent_timeout}s, attempt={attempt})")

        t0 = time.monotonic()
        try:
            result = subprocess.run(
                argv,
                input=ctx_text.encode("utf-8"),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=config.agent_timeout,
                cwd=str(subproc_cwd) if subproc_cwd else None,
            )
            last_duration  = time.monotonic() - t0
            last_exit_code = result.returncode
            last_stderr    = result.stderr.decode("utf-8", errors="replace")
            stdout_bytes   = result.stdout

            answer_text, last_envelope, last_parse_error = _parse_claude_envelope(stdout_bytes)
            if last_envelope is not None:
                # v4: scan for leaked raw secret values, then (if enabled)
                # rehydrate <<secret:...>> references the model chose to
                # echo back. The file on disk reflects the chosen policy.
                vault.scan_leaks(answer_text, ctx=_ctx("response_raw"), event_log=log)
                if config.rehydrate_outputs:
                    answer_text = vault.rehydrate(
                        answer_text, ctx=_ctx("05_output"),
                        direction="inbound", event_log=log,
                    )
                output_path.write_text(answer_text, encoding="utf-8")
                usage_path.write_text(
                    json.dumps(last_envelope, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
            else:
                # Envelope-less fallback — decode, scan, optionally rehydrate,
                # and write as text so the output is consistent with the
                # envelope path.
                raw_text = stdout_bytes.decode("utf-8", errors="replace")
                vault.scan_leaks(raw_text, ctx=_ctx("response_raw"), event_log=log)
                if config.rehydrate_outputs:
                    raw_text = vault.rehydrate(
                        raw_text, ctx=_ctx("05_output"),
                        direction="inbound", event_log=log,
                    )
                output_path.write_text(raw_text, encoding="utf-8")

            ok = (
                last_exit_code == 0
                and output_path.exists()
                and output_path.stat().st_size > 0
            )
            dlog.debug(f"[claude:{agent_id}] exit={last_exit_code} "
                       f"duration={last_duration:.1f}s "
                       f"output={output_path.stat().st_size if output_path.exists() else 0:,} bytes "
                       f"envelope={'yes' if last_envelope else 'no'}")
            if last_stderr.strip():
                dlog.debug(f"[claude:{agent_id}] stderr: {snippet(last_stderr, 200)!r}")
            if ok:
                success = True
                break
            last_kind = f"exit_{last_exit_code}"

        except subprocess.TimeoutExpired:
            last_duration = time.monotonic() - t0
            last_kind     = "timeout"
            last_exit_code = -1
            last_stderr    = ""
            last_envelope  = None
            dlog.debug(f"[claude:{agent_id}] TIMEOUT after {config.agent_timeout}s "
                       f"(attempt {attempt}/{max_attempts})")

        if attempt < max_attempts:
            delay_idx = min(attempt - 1, len(retry_delays) - 1)
            delay     = int(retry_delays[delay_idx])
            log.agent_retry_scheduled(
                agent_id,
                attempt=attempt,
                max_attempts=max_attempts,
                reason=last_kind,
                delay_s=delay,
                next_attempt=attempt + 1,
                stderr_excerpt=last_stderr[:200],
            )
            if config.verbose:
                print(f"  ↻  {agent_id} — attempt {attempt}/{max_attempts} failed "
                      f"({last_kind}); waiting {delay}s")
            if delay > 0:
                time.sleep(delay)
        else:
            log.agent_retry_exhausted(
                agent_id, attempts=attempt, last_reason=last_kind,
            )

    # ── Final failure handlers (after all retries) ───────────────────────────
    if not success:
        if last_kind == "timeout":
            log.agent_timeout(agent_id, config.agent_timeout, attempt=attempts_used)
            _write_status(status_path, agent_id, STATUS_TIMEOUT,
                          duration_s=round(last_duration, 1),
                          exit_code=-1,
                          timeout_s=config.agent_timeout,
                          run_id=run_id,
                          attempts=attempts_used)
            if config.verbose:
                print(f"  ⏱  {agent_id} — TIMEOUT after {attempts_used} "
                      f"attempt{'s' if attempts_used != 1 else ''}")
            raise AgentTimeoutError(agent_id, config.agent_timeout)
        else:
            log.agent_fail(
                agent_id, last_exit_code,
                duration_s=last_duration,
                reason=last_stderr[:300],
                attempt=attempts_used,
            )
            _write_status(status_path, agent_id, STATUS_FAILED,
                          duration_s=round(last_duration, 1),
                          exit_code=last_exit_code,
                          run_id=run_id,
                          attempts=attempts_used)
            if output_path.exists() and output_path.stat().st_size == 0:
                output_path.unlink()
            if config.verbose:
                print(f"  ✗  {agent_id} — FAILED (exit {last_exit_code}) "
                      f"after {attempts_used} attempt"
                      f"{'s' if attempts_used != 1 else ''}")
            raise AgentExecutionError(agent_id, last_exit_code, last_stderr)

    # ── Success path ──────────────────────────────────────────────────────────
    duration    = last_duration
    envelope    = last_envelope
    parse_error = last_parse_error
    out_bytes   = output_path.stat().st_size

    # Versioned copy (IMP-05, preserves v3.5 behaviour).
    if config.keep_output_versions:
        inv = _next_version(agent_dir)
        (agent_dir / f"05_output.v{inv}.md").write_bytes(output_path.read_bytes())

    # ── 8. GAP-1 — schema validation ──────────────────────────────────────────
    schema = cfg.get("output_schema") or schema_from_agent_config(agent_dir)
    if schema:
        modes = schema.get("mode", [])
        dlog.debug(f"[schema:{agent_id}] validating modes={modes}")
        if config.verbose:
            print(f"  🔍 {agent_id} — validating output schema")
        try:
            validate_output_schema(agent_id, output_path, schema)
            log.agent_schema_valid(agent_id)
            dlog.debug(f"[schema:{agent_id}] PASS")
            if config.verbose:
                print(f"  ✓  {agent_id} — schema valid")
        except SchemaViolationError as exc:
            log.agent_schema_violation(agent_id, exc.violations, phase="output")
            dlog.debug(f"[schema:{agent_id}] FAIL · violations: {exc.violations}")
            _write_status(status_path, agent_id, STATUS_OUTPUT_SCHEMA_FAIL,
                          duration_s=round(duration, 2), exit_code=0,
                          schema_violations=exc.violations)
            if config.verbose:
                print(f"  ✗  {agent_id} — SCHEMA INVALID")
                for v in exc.violations:
                    print(f"       • {v}")
            raise AgentExecutionError(
                agent_id, 0,
                "Output schema violations:\n" + "\n".join(exc.violations),
            ) from exc

    # ── IMP-07 — token accounting ─────────────────────────────────────────────
    usage_record = _extract_usage(envelope)
    status_extras: dict[str, Any] = {
        "output_bytes": out_bytes,
        "output_file":  str(output_path.name),
        "run_id":       run_id,
        "attempts":     attempts_used,
    }
    if usage_record is not None:
        status_extras["usage"] = usage_record
        log.agent_tokens(
            agent_id,
            input_tokens=usage_record["input_tokens"],
            output_tokens=usage_record["output_tokens"],
            cache_creation_tokens=usage_record["cache_creation_tokens"],
            cache_read_tokens=usage_record["cache_read_tokens"],
            cost_usd=usage_record["cost_usd"],
            model=usage_record["model"],
            duration_api_ms=usage_record["duration_api_ms"],
            run_id=run_id,
        )
        if config.verbose:
            print(f"  ✓  {agent_id} — done ({duration:.0f}s, {out_bytes:,} bytes, "
                  f"in={usage_record['input_tokens']:,} "
                  f"out={usage_record['output_tokens']:,} tok, "
                  f"${usage_record['cost_usd']:.4f})")
    else:
        log.agent_tokens_unavailable(agent_id, parse_error or "no_usage_in_envelope")
        if config.verbose:
            print(f"  ✓  {agent_id} — done ({duration:.0f}s, {out_bytes:,} bytes)")

    # ── 9. GAP-3 — approval gate ──────────────────────────────────────────────
    if requires_approval:
        dlog.debug(f"[approval:{agent_id}] requires_approval=true → pausing pipeline")
        _write_status(status_path, agent_id, "awaiting_approval",
                      duration_s=round(duration, 2), exit_code=0,
                      **status_extras)
        request_approval(agent_id, agent_dir, run_id, log)
        try:
            wait_for_approval(agent_id, agent_dir, config, log)
        except ApprovalRejectedError as exc:
            _write_status(status_path, agent_id, "rejected",
                          duration_s=round(duration, 2), exit_code=-2,
                          **status_extras)
            raise AgentExecutionError(agent_id, -2, str(exc)) from exc
        except ApprovalTimeoutError as exc:
            _write_status(status_path, agent_id, "approval_timeout",
                          duration_s=round(duration, 2), exit_code=-3,
                          **status_extras)
            raise AgentExecutionError(agent_id, -3, str(exc)) from exc

    _write_status(status_path, agent_id, STATUS_DONE,
                  duration_s=round(duration, 2), exit_code=0,
                  **status_extras)
    log.agent_done(agent_id, round(duration, 2), 0,
                   output_bytes=out_bytes, run_id=run_id,
                   attempt=attempts_used)

    # ── 10. Router (IMP-08) ───────────────────────────────────────────────────
    _evaluate_router(agent_id, agent_dir, agent_dirs, config, log)

    return STATUS_DONE


# ── Router ────────────────────────────────────────────────────────────────────

def _evaluate_router(
    agent_id: str,
    agent_dir: Path,
    agent_dirs: dict[str, Path],
    config: "OrchestratorConfig",
    log: "EventLog",
) -> None:
    """
    If the agent has a `router` block in 00_config.json and has written
    `routing.json`, mark non-activated branches as bypassed.
    """
    cfg_path = agent_dir / "00_config.json"
    if not cfg_path.exists():
        return
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception:
        return

    router = cfg.get("router")
    if not router:
        return

    routing_file = agent_dir / "routing.json"
    if not routing_file.exists():
        return  # agent didn't write a decision — silent no-op

    try:
        routing = json.loads(routing_file.read_text(encoding="utf-8"))
    except Exception:
        return
    decision = routing.get("decision", "")
    routes   = router.get("routes", {}) or {}

    if decision not in routes:
        raise RouterError(agent_id, f"unknown decision '{decision}'. Valid: {list(routes)}")

    activated = list(routes[decision])
    skipped   = [t for d, targets in routes.items()
                   for t in (targets or []) if d != decision]

    log.router_decision(agent_id, decision, activated=activated, skipped=skipped,
                        reason=routing.get("reason", ""))
    if config.verbose:
        print(f"  ⇒  router '{agent_id}': decision='{decision}', "
              f"activating {activated}, bypassing {skipped}")

    for skipped_id in skipped:
        target_dir = agent_dirs.get(skipped_id, agent_dir.parent / skipped_id)
        mark_bypassed(target_dir, skipped_id, agent_id)
        log.agent_bypassed(skipped_id, agent_id)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _write_status(path: Path, agent_id: str, status: str,
                  duration_s: float = 0.0, exit_code: int = 0,
                  **extra: Any) -> None:
    now = _now_iso()
    data: dict[str, Any] = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    data.update({
        "agent_id":   agent_id,
        "status":     status,
        "duration_s": duration_s,
        "exit_code":  exit_code,
    })
    data.update(extra)
    if status == STATUS_RUNNING:
        data["started_at"] = now
    elif status in (STATUS_DONE, STATUS_FAILED, STATUS_TIMEOUT,
                    "schema_invalid", "rejected", "approval_timeout",
                    STATUS_BYPASSED):
        data["ended_at"] = now
    _write_status_doc(path, data)


def _write_status_doc(path: Path, data: dict[str, Any]) -> None:
    """Atomic write: tmp file then rename (never a partial status file)."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _next_version(agent_dir: Path) -> int:
    versions = list(agent_dir.glob("05_output.v*.md"))
    return len(versions) + 1


def _resolve_agent_cwd(agent_dir: Path, cwd_rel: str) -> Path:
    """Resolve a per-agent `cwd` path relative to the pipeline_dir."""
    candidate = agent_dir.parent
    for _ in range(6):
        if (candidate / "pipeline.json").exists():
            return (candidate / cwd_rel).resolve()
        if candidate.parent == candidate:
            break
        candidate = candidate.parent
    return (agent_dir.parent / cwd_rel).resolve()


# ── Claude envelope handling (IMP-07) ─────────────────────────────────────────

def _parse_claude_envelope(
    stdout_bytes: bytes,
) -> tuple[str, dict[str, Any] | None, str | None]:
    """
    Parse the JSON envelope produced by `claude -p --output-format json`.

    Returns (answer_text, envelope_dict_or_None, parse_error_or_None).
    """
    raw = stdout_bytes.decode("utf-8", errors="replace")
    if not raw.strip():
        return "", None, "empty_stdout"

    try:
        env = json.loads(raw)
    except json.JSONDecodeError as e:
        return raw, None, f"json_decode_error: {e.msg}"

    if not isinstance(env, dict):
        return raw, None, "envelope_not_object"

    answer = env.get("result")
    if answer is None:
        answer = env.get("text", "")
    if not isinstance(answer, str):
        return raw, env, "result_not_string"

    return answer, env, None


def _extract_usage(envelope: dict[str, Any] | None) -> dict[str, Any] | None:
    """Normalise the envelope's usage block or return None if absent."""
    if not envelope:
        return None
    usage = envelope.get("usage")
    if not isinstance(usage, dict):
        return None
    return {
        "input_tokens":          int(usage.get("input_tokens", 0) or 0),
        "output_tokens":         int(usage.get("output_tokens", 0) or 0),
        "cache_creation_tokens": int(usage.get("cache_creation_input_tokens", 0) or 0),
        "cache_read_tokens":     int(usage.get("cache_read_input_tokens", 0) or 0),
        "cost_usd":              float(envelope.get("total_cost_usd", 0.0) or 0.0),
        "model":                 str(envelope.get("model", "") or ""),
        "duration_api_ms":       int(envelope.get("duration_api_ms", 0) or 0),
    }
