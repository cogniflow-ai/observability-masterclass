"""
Cogniflow Orchestrator — Agent executor.

exec_agent() applies all five low-level improvements:
  IMP-01  stdin redirect (not $() expansion)
  IMP-02  --system flag from 01_system.md (correct slot)
  IMP-04  subprocess timeout
  IMP-05  live output file (05_output.md); per-run versioning is now
          handled by the pipeline-level history snapshot in core.py.
  IMP-06  token budget check before context assembly
"""

from __future__ import annotations
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TYPE_CHECKING

from .exceptions import (
    AgentExecutionError, AgentTimeoutError, RouterError
)
from .context import collect_inputs, assemble_context
from .budget import check_and_prepare_inputs
from .validate import load_agent_config

if TYPE_CHECKING:
    from .config import OrchestratorConfig
    from .events import EventLog


STATUS_PENDING  = "pending"
STATUS_RUNNING  = "running"
STATUS_DONE     = "done"
STATUS_FAILED   = "failed"
STATUS_TIMEOUT  = "timeout"
STATUS_BYPASSED = "bypassed"
STATUS_SKIP     = "skipped"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_status(path: Path, data: dict[str, Any]) -> None:
    """Atomic write: tmp file then rename (never a partial status file)."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def read_status(agent_dir: Path) -> dict[str, Any]:
    """Return the status dict, or {'status': 'pending'} if not yet written."""
    p = agent_dir / "06_status.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"status": STATUS_PENDING}


def is_done(agent_dir: Path) -> bool:
    return read_status(agent_dir).get("status") == STATUS_DONE


def mark_bypassed(agent_dir: Path, agent_id: str, by_router: str) -> None:
    _write_status(agent_dir / "06_status.json", {
        "agent_id":  agent_id,
        "status":    STATUS_BYPASSED,
        "by_router": by_router,
        "skipped_at": _now_iso(),
    })


def exec_agent(
    agent_id: str,
    agents_base: Path,
    dependencies: list[str],
    config: "OrchestratorConfig",
    log: "EventLog",
    run_id: str,
    graph: Any = None,            # networkx DiGraph — used for router decisions
) -> str:
    """
    Run one agent end-to-end:
      1. Resume guard (skip if already done)
      2. collect_inputs()
      3. token budget check
      4. assemble_context()
      5. claude subprocess (stdin, --system, timeout, versioned output)
      6. Atomic status update
      7. Router evaluation (if agent is a router)

    Returns the agent's final status string.
    """
    agent_dir  = agents_base / agent_id
    status_path = agent_dir / "06_status.json"
    agent_dir.mkdir(parents=True, exist_ok=True)

    # ── Resume guard ───────────────────────────────────────────────────────
    if is_done(agent_dir):
        log.agent_skip(agent_id, reason="checkpoint")
        _print(config, f"  ⏭  {agent_id} — skipped (checkpoint)")
        return STATUS_SKIP

    log.agent_launched(agent_id)
    _print(config, f"  🤖 {agent_id} — preparing")

    # ── Collect inputs ─────────────────────────────────────────────────────
    collect_inputs(agent_id, agent_dir, dependencies, agents_base, log, run_id)

    # ── Token budget check ─────────────────────────────────────────────────
    agent_cfg = load_agent_config(agent_dir)
    strategy  = agent_cfg.get("budget_strategy", "hard_fail")
    check_and_prepare_inputs(agent_id, agent_dir, strategy, config, log)

    # ── Assemble 04_context.md ─────────────────────────────────────────────
    context_path = assemble_context(agent_id, agent_dir, log)

    # ── Write initial status ───────────────────────────────────────────────
    started = _now_iso()
    _write_status(status_path, {
        "agent_id":   agent_id,
        "status":     STATUS_RUNNING,
        "started_at": started,
        "run_id":     run_id,
    })

    # ── Claude invocation (IMP-01, IMP-02, IMP-04, IMP-05, IMP-07, IMP-09) ─
    #
    # The Claude CLI uses -p to enter non-interactive (print-and-exit) mode.
    # When -p is passed without a prompt argument, claude reads the prompt
    # from stdin — which is what we use here (IMP-01).
    #
    # IMP-01: context goes through stdin. Passing it as a -p argument is
    #         unsafe on Windows: claude.cmd is a batch wrapper, and cmd.exe
    #         treats embedded newlines in arguments as command terminators,
    #         silently truncating a multi-line prompt at the first newline.
    # IMP-02: --system-prompt populates the correct API slot.
    # IMP-04: timeout parameter kills a hung subprocess cleanly.
    # IMP-05: output is written to the live 05_output.md; per-run versioning
    #         is captured by the pipeline-level history snapshot (core.py).
    # IMP-07: --output-format json gives us a structured envelope with usage.
    # IMP-09: each call is wrapped in a retry loop (this function).
    #
    system_text   = (agent_dir / "01_system.md").read_text(encoding="utf-8").strip()
    # Windows/cmd.exe mangles newlines in argv when claude.cmd is invoked
    # via subprocess (CreateProcess auto-wraps .cmd in cmd.exe /c). A
    # multi-line --system-prompt value silently drops --output-format json
    # (→ no usage envelope) AND corrupts the system prompt content.
    # Collapsing newlines to spaces preserves meaning for the model.
    if sys.platform == "win32":
        system_text = system_text.replace("\r\n", " ").replace("\n", " ")
    context_text = context_path.read_text(encoding="utf-8")
    output_path  = agent_dir / "05_output.md"
    usage_path   = agent_dir / "05_usage.json"

    # Ensure the live files are fresh for this run. A legacy symlink (from
    # before the history-snapshot redesign) would otherwise redirect our
    # write to an old versioned file.
    if output_path.is_symlink():
        output_path.unlink()
    if usage_path.exists():
        usage_path.unlink()

    # Optional per-agent extras from 00_config.json
    extra_flags = agent_cfg.get("cli_flags") or []
    subproc_cwd: Path | None = None
    cwd_rel = agent_cfg.get("cwd")
    if cwd_rel:
        subproc_cwd = (agents_base.parent / cwd_rel).resolve()
        subproc_cwd.mkdir(parents=True, exist_ok=True)

    argv = [config.claude_bin,
            "--system-prompt", system_text,
            "--output-format", "json",
            "-p"]
    argv.extend(extra_flags)

    # ── Retry policy (IMP-09) ──────────────────────────────────────────────
    # Per-agent 00_config.json supersedes the env-driven defaults.
    # max_retries=N → up to N+1 total attempts.
    # retry_delays_s: list of sleeps between attempts; padded with its
    # last value if max_retries exceeds its length, so [3,3,10] still
    # works at any retry count.
    max_retries  = agent_cfg.get("max_retries", config.max_retries)
    retry_delays = agent_cfg.get("retry_delays_s", config.retry_delays_s) or [0]
    max_attempts = max(1, int(max_retries) + 1)

    # Carry the latest attempt's outcome out of the loop for the
    # success/failure handlers below.
    success           = False
    attempts_used     = 0
    last_kind         = ""
    last_exit_code    = -1
    last_stderr       = ""
    last_duration     = 0.0
    last_envelope: dict[str, Any] | None = None
    last_parse_error: str | None         = None

    for attempt in range(1, max_attempts + 1):
        attempts_used = attempt
        log.agent_start(agent_id, attempt=attempt)
        if attempt == 1:
            _print(config, f"  ▶  {agent_id} — calling claude")
        else:
            _print(config, f"  ↻  {agent_id} — retry attempt {attempt}/{max_attempts}")
        t0 = time.monotonic()

        try:
            result = subprocess.run(
                argv,
                input=context_text.encode("utf-8"),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=config.agent_timeout,
                cwd=str(subproc_cwd) if subproc_cwd else None,
            )
            last_duration  = time.monotonic() - t0
            last_exit_code = result.returncode
            last_stderr    = result.stderr.decode("utf-8", errors="replace")
            stdout_bytes   = result.stdout

            # Parse envelope and persist (even on a failed attempt the bytes
            # land on disk so the operator can inspect what claude returned).
            answer_text, last_envelope, last_parse_error = _parse_claude_envelope(stdout_bytes)
            if last_envelope is not None:
                output_path.write_text(answer_text, encoding="utf-8")
                usage_path.write_text(
                    json.dumps(last_envelope, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
            else:
                output_path.write_bytes(stdout_bytes)

            output_ok = (
                last_exit_code == 0
                and output_path.exists()
                and output_path.stat().st_size > 0
            )
            if output_ok:
                success = True
                break
            last_kind = f"exit_{last_exit_code}"

        except subprocess.TimeoutExpired:
            last_duration  = time.monotonic() - t0
            last_kind      = "timeout"
            last_exit_code = -1
            last_stderr    = ""
            last_envelope  = None

        # Attempt failed — schedule a retry or give up.
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
            _print(
                config,
                f"  ↻  {agent_id} — attempt {attempt}/{max_attempts} failed "
                f"({last_kind}); waiting {delay}s",
            )
            if delay > 0:
                time.sleep(delay)
        else:
            log.agent_retry_exhausted(
                agent_id, attempts=attempt, last_reason=last_kind
            )

    # ── Final failure handlers (after all retries) ─────────────────────────
    if not success:
        if last_kind == "timeout":
            log.agent_timeout(agent_id, config.agent_timeout, attempt=attempts_used)
            _write_status(status_path, {
                "agent_id":   agent_id,
                "status":     STATUS_TIMEOUT,
                "started_at": started,
                "ended_at":   _now_iso(),
                "duration_s": round(last_duration, 1),
                "timeout_s":  config.agent_timeout,
                "run_id":     run_id,
                "attempts":   attempts_used,
            })
            _print(
                config,
                f"  ⏱  {agent_id} — TIMEOUT after {attempts_used} "
                f"attempt{'s' if attempts_used != 1 else ''}",
            )
            raise AgentTimeoutError(agent_id, config.agent_timeout)
        else:
            log.agent_fail(
                agent_id, last_exit_code, last_duration,
                last_stderr[:300], attempt=attempts_used,
            )
            _write_status(status_path, {
                "agent_id":   agent_id,
                "status":     STATUS_FAILED,
                "started_at": started,
                "ended_at":   _now_iso(),
                "duration_s": round(last_duration, 1),
                "exit_code":  last_exit_code,
                "run_id":     run_id,
                "attempts":   attempts_used,
            })
            if output_path.exists() and output_path.stat().st_size == 0:
                output_path.unlink()
            _print(
                config,
                f"  ✗  {agent_id} — FAILED (exit {last_exit_code}) "
                f"after {attempts_used} attempt{'s' if attempts_used != 1 else ''}",
            )
            raise AgentExecutionError(agent_id, last_exit_code, last_stderr)

    # Success path uses the latest attempt's data.
    duration    = last_duration
    envelope    = last_envelope
    parse_error = last_parse_error

    # ── Live output file (IMP-05) ──────────────────────────────────────────
    # 05_output.md was written directly during the retry loop; no symlink
    # dance needed. Historical per-run copies live in pipeline/history/
    # and are produced by the end-of-run snapshot in core.py.
    out_bytes = output_path.stat().st_size
    log.agent_done(agent_id, duration, out_bytes, run_id, attempt=attempts_used)

    # ── Token accounting (IMP-07) ──────────────────────────────────────────
    # Pull usage from the envelope if present, emit agent_tokens, and embed
    # the same data into 06_status.json so post-hoc rollups (and the
    # `inspect tokens` CLI command) don't have to re-parse events.jsonl.
    usage_record = _extract_usage(envelope)
    status_doc: dict[str, Any] = {
        "agent_id":    agent_id,
        "status":      STATUS_DONE,
        "started_at":  started,
        "ended_at":    _now_iso(),
        "duration_s":  round(duration, 1),
        "exit_code":   0,
        "output_bytes": out_bytes,
        "output_file": str(output_path.name),
        "run_id":      run_id,
        "attempts":    attempts_used,
    }

    if usage_record is not None:
        status_doc["usage"] = usage_record
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
        _print(
            config,
            f"  ✓  {agent_id} — done ({duration:.0f}s, {out_bytes:,} bytes, "
            f"in={usage_record['input_tokens']:,} out={usage_record['output_tokens']:,} "
            f"tok, ${usage_record['cost_usd']:.4f})",
        )
    else:
        log.agent_tokens_unavailable(agent_id, parse_error or "no_usage_in_envelope")
        _print(config, f"  ✓  {agent_id} — done ({duration:.0f}s, {out_bytes:,} bytes)")

    _write_status(status_path, status_doc)

    # ── Router evaluation (IMP-08) ─────────────────────────────────────────
    _evaluate_router(agent_id, agent_dir, agents_base, config, log, graph)

    return STATUS_DONE


def _evaluate_router(
    agent_id: str,
    agent_dir: Path,
    agents_base: Path,
    config: "OrchestratorConfig",
    log: "EventLog",
    graph: Any,
) -> None:
    """
    If the agent has a router block in its config, read routing.json
    and mark bypassed agents accordingly.
    """
    # Router definition lives in 00_config.json
    cfg      = load_agent_config(agent_dir)
    router   = cfg.get("router")
    if not router:
        return

    routing_file = agent_dir / "routing.json"
    if not routing_file.exists():
        # Agent did not write routing.json — skip routing silently
        return

    routing = json.loads(routing_file.read_text(encoding="utf-8"))
    decision = routing.get("decision", "")
    routes   = router.get("routes", {})

    if decision not in routes:
        raise RouterError(agent_id, decision, list(routes.keys()))

    activated = routes[decision]
    skipped   = [t for d, targets in routes.items()
                   for t in targets if d != decision]

    log.router_decision(agent_id, decision, activated, skipped)
    _print(config, f"  ⇒  router '{agent_id}': decision='{decision}', "
                   f"activating {activated}, bypassing {skipped}")

    for skipped_id in skipped:
        skipped_dir = agents_base / skipped_id
        skipped_dir.mkdir(parents=True, exist_ok=True)
        mark_bypassed(skipped_dir, skipped_id, agent_id)
        log.agent_bypassed(skipped_id, agent_id)


def _print(config: "OrchestratorConfig", msg: str) -> None:
    if config.verbose:
        print(msg)


# ── Claude envelope handling (IMP-07) ──────────────────────────────────────

def _parse_claude_envelope(
    stdout_bytes: bytes,
) -> tuple[str, dict[str, Any] | None, str | None]:
    """
    Parse the JSON envelope produced by `claude -p --output-format json`.

    Returns (answer_text, envelope_dict_or_None, parse_error_or_None).

    On success: answer_text is the model's response (envelope["result"]),
    envelope is the full parsed dict, parse_error is None.

    On failure: answer_text is the raw stdout decoded best-effort,
    envelope is None, parse_error is a short string suitable for the
    agent_tokens_unavailable event.
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

    # Some claude CLI versions key the response as "result", others as
    # "text" or include nothing if the call errored. Be permissive.
    answer = env.get("result")
    if answer is None:
        answer = env.get("text", "")
    if not isinstance(answer, str):
        return raw, env, "result_not_string"

    return answer, env, None


def _extract_usage(envelope: dict[str, Any] | None) -> dict[str, Any] | None:
    """
    Pull a normalised usage record out of the claude envelope.

    Returns None if the envelope is missing or has no usage block —
    in which case agent_tokens_unavailable should be emitted instead.
    """
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
