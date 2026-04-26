"""
Cogniflow Orchestrator v3.0 — Event logger.

Writes structured JSONL events to .state/events.jsonl.
Thread-safe: uses filelock so parallel agent threads can append
without corruption.  Falls back to threading.Lock if filelock absent.

All 17 existing v2.1.0 event types are preserved unchanged.
New cyclic-mode events are added without modifying existing ones.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from filelock import FileLock
    _HAS_FILELOCK = True
except ImportError:
    _HAS_FILELOCK = False


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class EventLog:
    """Append-only JSONL event log with cross-thread file safety."""

    def __init__(self, log_path: Path) -> None:
        self.path      = log_path
        self.lock_path = log_path.with_suffix(".lock")
        self._tlock    = threading.Lock()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        if not log_path.exists():
            log_path.touch()

    # ── Public API ────────────────────────────────────────────────────────────

    def emit(self, event: str, **kwargs: Any) -> dict:
        """Write one event record. Returns the dict written."""
        record = {"ts": _now(), "event": event, **kwargs}
        line   = json.dumps(record, ensure_ascii=False)
        self._append(line)
        return record

    # ── Convenience emitters — existing v2.1.0 events (unchanged) ────────────

    def pipeline_start(self, pipeline: str, run_id: str) -> None:
        self.emit("pipeline_start", pipeline=pipeline, run_id=run_id)

    def pipeline_done(self, run_id: str, duration_s: float) -> None:
        self.emit("pipeline_done", run_id=run_id, duration_s=duration_s)

    def pipeline_error(self, run_id: str, error: str) -> None:
        self.emit("pipeline_error", run_id=run_id, error=error)

    def agent_start(self, agent: str, layer: int = 0, attempt: int = 1) -> None:
        self.emit("agent_start", agent=agent, layer=layer, attempt=attempt)

    def agent_done(self, agent: str, duration_s: float, exit_code: int = 0,
                   invocation_n: int = 1, thread_id: str | None = None,
                   output_bytes: int = 0, run_id: str = "",
                   attempt: int = 1) -> None:
        kwargs: dict[str, Any] = dict(agent=agent, duration_s=duration_s,
                                      exit_code=exit_code, invocation_n=invocation_n,
                                      attempt=attempt)
        if output_bytes:
            kwargs["output_bytes"] = output_bytes
        if run_id:
            kwargs["run_id"] = run_id
        if thread_id is not None:
            kwargs["thread_id"] = thread_id
        self.emit("agent_done", **kwargs)

    def agent_fail(self, agent: str, exit_code: int,
                   duration_s: float = 0.0, reason: str = "",
                   attempt: int = 1) -> None:
        kwargs: dict[str, Any] = dict(agent=agent, exit_code=exit_code,
                                      attempt=attempt)
        if duration_s:
            kwargs["duration_s"] = round(duration_s, 1)
        if reason:
            kwargs["reason"] = reason
        self.emit("agent_fail", **kwargs)

    def agent_timeout(self, agent: str, timeout_s: int = 0, attempt: int = 1) -> None:
        kwargs: dict[str, Any] = dict(agent=agent, attempt=attempt)
        if timeout_s:
            kwargs["timeout_s"] = timeout_s
        self.emit("agent_timeout", **kwargs)

    def agent_skip(self, agent: str, reason: str = "") -> None:
        kwargs: dict[str, Any] = dict(agent=agent)
        if reason:
            kwargs["reason"] = reason
        self.emit("agent_skip", **kwargs)

    def agent_approval_required(self, agent: str) -> None:
        self.emit("agent_approval_required", agent=agent)

    def agent_approved(self, agent: str, approved_by: str) -> None:
        self.emit("agent_approved", agent=agent, approved_by=approved_by)

    def agent_rejected(self, agent: str, approved_by: str, note: str = "") -> None:
        self.emit("agent_rejected", agent=agent, approved_by=approved_by, note=note)

    def budget_applied(self, agent: str, strategy: str,
                       original_tokens: int, final_tokens: int) -> None:
        self.emit("budget_applied", agent=agent, strategy=strategy,
                  original_tokens=original_tokens, final_tokens=final_tokens)

    def budget_strategy(self, agent: str, strategy: str) -> None:
        self.emit("budget_strategy", agent=agent, strategy=strategy)

    def secret_warning(self, agent: str, pattern: str) -> None:
        self.emit("secret_warning", agent=agent, pattern=pattern)

    def secret_substitution_warning(self, agent: str, var: str) -> None:
        self.emit("secret_substitution_warning", agent=agent, var=var)

    def router_decision(self, agent: str, decision: str,
                        activated: list[str] | None = None,
                        skipped: list[str] | None = None,
                        reason: str = "") -> None:
        # activated/skipped are the v1 IMP-08 payload; reason is the v3.0
        # shape. Both are accepted so pipelines written against either
        # version continue to emit meaningful events.
        fields: dict[str, Any] = {"agent": agent, "decision": decision}
        if activated is not None:
            fields["activated"] = activated
        if skipped is not None:
            fields["skipped"] = skipped
        if reason:
            fields["reason"] = reason
        self.emit("router_decision", **fields)

    def agent_bypassed(self, agent: str, by_router: str) -> None:
        self.emit("agent_bypassed", agent=agent, by_router=by_router)

    def validation_error(self, errors: list[str]) -> None:
        self.emit("validation_error", errors=errors)

    # ── v1 observability events (pause/resume, per-agent IMP-01..IMP-09) ─────

    def agent_launched(self, agent: str) -> None:
        self.emit("agent_launched", agent=agent)

    def agent_inputs_collected(self, agent: str, input_count: int) -> None:
        self.emit("agent_inputs_collected", agent=agent, input_count=input_count)

    def agent_budget_estimated(self, agent: str, bytes_: int, tokens_est: int) -> None:
        self.emit("agent_budget_estimated", agent=agent,
                  context_bytes=bytes_, tokens_estimated=tokens_est)

    def agent_context_ready(self, agent: str, bytes_: int, tokens_est: int) -> None:
        self.emit("agent_context_ready", agent=agent,
                  context_bytes=bytes_, tokens_estimated=tokens_est)

    def agent_budget_exceeded(self, agent: str, estimated: int,
                              budget: int, strategy: str) -> None:
        self.emit("agent_budget_exceeded", agent=agent,
                  tokens_estimated=estimated, budget=budget, strategy=strategy)

    def agent_retry_scheduled(self, agent: str, *,
                              attempt: int, max_attempts: int,
                              reason: str, delay_s: int,
                              next_attempt: int,
                              stderr_excerpt: str = "") -> None:
        """A call just failed and another attempt is queued (IMP-09)."""
        self.emit("agent_retry_scheduled", agent=agent,
                  attempt=attempt, max_attempts=max_attempts,
                  reason=reason, delay_s=delay_s,
                  next_attempt=next_attempt,
                  stderr_excerpt=stderr_excerpt)

    def agent_retry_exhausted(self, agent: str, *,
                              attempts: int, last_reason: str) -> None:
        """All retries failed; emitted just before the final fail/timeout."""
        self.emit("agent_retry_exhausted", agent=agent,
                  attempts=attempts, last_reason=last_reason)

    def agent_tokens(self, agent: str, *,
                     input_tokens: int,
                     output_tokens: int,
                     cache_creation_tokens: int = 0,
                     cache_read_tokens: int = 0,
                     cost_usd: float = 0.0,
                     model: str = "",
                     duration_api_ms: int = 0,
                     run_id: str = "") -> None:
        """Real token accounting from the Claude envelope (IMP-07)."""
        self.emit("agent_tokens", agent=agent,
                  input_tokens=input_tokens,
                  output_tokens=output_tokens,
                  cache_creation_tokens=cache_creation_tokens,
                  cache_read_tokens=cache_read_tokens,
                  cost_usd=round(cost_usd, 6),
                  model=model,
                  duration_api_ms=duration_api_ms,
                  run_id=run_id)

    def agent_tokens_unavailable(self, agent: str, reason: str) -> None:
        """Envelope missing or malformed — no real token counts available."""
        self.emit("agent_tokens_unavailable", agent=agent, reason=reason)

    def pipeline_tokens(self, run_id: str, *,
                        total_input: int,
                        total_output: int,
                        total_cache_creation: int = 0,
                        total_cache_read: int = 0,
                        total_cost_usd: float = 0.0,
                        agents_counted: int = 0) -> None:
        """Pipeline-level rollup of per-agent usage; emitted before pipeline_done."""
        self.emit("pipeline_tokens", run_id=run_id,
                  total_input=total_input,
                  total_output=total_output,
                  total_cache_creation=total_cache_creation,
                  total_cache_read=total_cache_read,
                  total_cost_usd=round(total_cost_usd, 6),
                  agents_counted=agents_counted)

    def layer_start(self, layer: int, agents: list[str], parallel: bool) -> None:
        self.emit("layer_start", layer=layer, agents=agents, parallel=parallel)

    def layer_done(self, layer: int, duration_s: float) -> None:
        self.emit("layer_done", layer=layer, duration_s=round(duration_s, 1))

    def layer_fail(self, layer: int, failed: list[str]) -> None:
        self.emit("layer_fail", layer=layer, failed=failed)

    def pipeline_paused(self, run_id: str, next_layer: int, sentinel: str) -> None:
        """Observer pause sentinel consumed between layers."""
        self.emit("pipeline_paused", run_id=run_id,
                  next_layer=next_layer, sentinel=sentinel)

    def pipeline_resumed(self, run_id: str, resumed_layer: int,
                         waited_s: float) -> None:
        """Observer resume sentinel consumed; execution continues."""
        self.emit("pipeline_resumed", run_id=run_id,
                  resumed_layer=resumed_layer,
                  waited_s=round(waited_s, 1))

    # ── GAP-1: Output schema (v3.5) ──────────────────────────────────────────

    def agent_schema_valid(self, agent: str, phase: str = "output") -> None:
        # Keep the v3.5 event name. Add phase kwarg (output/input) so the
        # Observer can distinguish the two checks without breaking replay.
        kwargs: dict[str, Any] = {"agent": agent}
        if phase != "output":
            kwargs["phase"] = phase
        self.emit("agent_schema_valid", **kwargs)

    def agent_schema_violation(
        self,
        agent: str,
        violations: list[str],
        phase: str = "output",
    ) -> None:
        kwargs: dict[str, Any] = {"agent": agent, "violations": violations}
        if phase != "output":
            kwargs["phase"] = phase
        self.emit("agent_schema_violation", **kwargs)

    # ── v4 — input schema (separate event names for clarity) ─────────────────

    def agent_input_schema_valid(self, agent: str) -> None:
        self.emit("agent_input_schema_valid", agent=agent)

    def agent_input_schema_violation(self, agent: str, violations: list[str]) -> None:
        self.emit("agent_input_schema_violation",
                  agent=agent, violations=violations, phase="input")

    def agent_output_schema_violation(self, agent: str, violations: list[str]) -> None:
        # Alias of the existing agent_schema_violation with phase explicit —
        # the Observer spec lists this explicitly.
        self.emit("agent_output_schema_violation",
                  agent=agent, violations=violations, phase="output")

    # ── v4 — pre-run validation bundle ───────────────────────────────────────

    def pipeline_validation_error(self, run_id: str, errors: list[str]) -> None:
        self.emit("pipeline_validation_error", run_id=run_id, errors=errors)

    # ── v4 — approval routing / feedback ─────────────────────────────────────

    def agent_rejected_redirected(
        self, *,
        gate_agent_id:   str,
        target_agent_id: str,
        note:            str,
        include:         list[str],
    ) -> None:
        self.emit("agent_rejected_redirected",
                  gate_agent_id=gate_agent_id,
                  target_agent_id=target_agent_id,
                  note=note, include=include)

    def agent_approved_redirected(
        self, *,
        gate_agent_id:   str,
        target_agent_id: str,
        include:         list[str],
    ) -> None:
        self.emit("agent_approved_redirected",
                  gate_agent_id=gate_agent_id,
                  target_agent_id=target_agent_id,
                  include=include)

    def agent_awaiting_feedback(self, agent: str, from_gate: str = "") -> None:
        kwargs: dict[str, Any] = {"agent": agent}
        if from_gate:
            kwargs["from_gate"] = from_gate
        self.emit("agent_awaiting_feedback", **kwargs)

    # ── v4 — secrets vault ───────────────────────────────────────────────────

    def secret_substituted(
        self, *,
        agent_id:    str,
        direction:   str,
        secret_name: str,
        file:        str,
        occurrences: int = 1,
    ) -> None:
        self.emit("secret_substituted",
                  agent_id=agent_id, direction=direction,
                  secret_name=secret_name, file=file,
                  occurrences=int(occurrences))

    def secret_missing(self, *, agent_id: str, secret_name: str, file: str) -> None:
        self.emit("secret_missing",
                  agent_id=agent_id, secret_name=secret_name, file=file)

    def secret_leaked(self, *, agent_id: str, secret_name: str, file: str) -> None:
        self.emit("secret_leaked",
                  agent_id=agent_id, secret_name=secret_name, file=file)

    # ── v3.0 cyclic-mode events ───────────────────────────────────────────────

    def message_sent(self, from_agent: str, to_agent: str,
                     message_id: str, thread_id: str, seq: int,
                     kind: str = "normal") -> None:
        fields: dict[str, Any] = {
            "from": from_agent, "to": to_agent,
            "message_id": message_id, "thread_id": thread_id, "seq": seq,
        }
        if kind != "normal":
            fields["kind"] = kind
        self.emit("message_sent", **fields)

    def message_received(self, agent_id: str, message_id: str,
                         thread_id: str, queue_depth: int) -> None:
        self.emit("message_received", agent_id=agent_id, message_id=message_id,
                  thread_id=thread_id, queue_depth=queue_depth)

    def agent_activated(self, agent_id: str, thread_id: str,
                        cycle_count: int, invocation_n: int) -> None:
        self.emit("agent_activated", agent_id=agent_id, thread_id=thread_id,
                  cycle_count=cycle_count, invocation_n=invocation_n)

    def agent_waiting(self, agent_id: str, waiting_for: list[str],
                      thread_id: str) -> None:
        self.emit("agent_waiting", agent_id=agent_id,
                  waiting_for=waiting_for, thread_id=thread_id)

    def feedback_loop_tick(self, between: list[str], cycle_n: int,
                           thread_id: str, tokens_this_cycle: int) -> None:
        self.emit("feedback_loop_tick", between=between, cycle_n=cycle_n,
                  thread_id=thread_id, tokens_this_cycle=tokens_this_cycle)

    def cycle_guard_triggered(self, agent_a: str, agent_b: str,
                               cycle_count: int, action: str) -> None:
        self.emit("cycle_guard_triggered", agent_a=agent_a, agent_b=agent_b,
                  cycle_count=cycle_count, action=action)

    def conversation_thread_start(self, thread_id: str,
                                   participants: list[str], edge_type: str) -> None:
        self.emit("conversation_thread_start", thread_id=thread_id,
                  participants=participants, edge_type=edge_type)

    def conversation_thread_close(self, thread_id: str,
                                   total_turns: int, outcome: str) -> None:
        self.emit("conversation_thread_close", thread_id=thread_id,
                  total_turns=total_turns, outcome=outcome)

    def context_retrieval_request(self, agent_id: str, query: str,
                                   tags_hint: list[str], thread_id: str) -> None:
        self.emit("context_retrieval_request", agent_id=agent_id,
                  query=query, tags_hint=tags_hint, thread_id=thread_id)

    def context_retrieval_result(self, agent_id: str, matched_ids: list[str],
                                  confidence: str, chunks_injected: int) -> None:
        self.emit("context_retrieval_result", agent_id=agent_id,
                  matched_ids=matched_ids, confidence=confidence,
                  chunks_injected=chunks_injected)

    def context_retrieval_miss(self, agent_id: str, query: str, reason: str) -> None:
        self.emit("context_retrieval_miss", agent_id=agent_id,
                  query=query, reason=reason)

    def summary_updated(self, agent_id: str, cycle: int,
                         decisions_count: int, open_q_count: int) -> None:
        self.emit("summary_updated", agent_id=agent_id, cycle=cycle,
                  decisions_count=decisions_count, open_q_count=open_q_count)

    def summary_overflow(self, agent_id: str, cycle: int,
                          truncated_count: int) -> None:
        self.emit("summary_overflow", agent_id=agent_id,
                  cycle=cycle, truncated_count=truncated_count)

    def budget_warning(self, agent_id: str, tokens_used: int,
                        budget: int, threshold: int) -> None:
        self.emit("budget_warning", agent_id=agent_id, tokens_used=tokens_used,
                  budget=budget, threshold=threshold)

    def hard_budget_exceeded(self, agent_id: str, tokens_used: int,
                              budget: int, action: str) -> None:
        self.emit("hard_budget_exceeded", agent_id=agent_id,
                  tokens_used=tokens_used, budget=budget, action=action)

    def deadlock_detected(self, agents: list[str], waiting_graph: dict) -> None:
        self.emit("deadlock_detected", agents=agents, waiting_graph=waiting_graph)

    def malformed_output(self, agent_id: str, attempt: int,
                          error: str, thread_id: str = "") -> None:
        self.emit("malformed_output", agent_id=agent_id, attempt=attempt,
                  error=error, thread_id=thread_id)

    def routing_violation(self, agent_id: str,
                           attempted_target: str, reason: str) -> None:
        self.emit("routing_violation", agent_id=agent_id,
                  attempted_target=attempted_target, reason=reason)

    def artifact_written(self, agent_id: str, artifact_id: str,
                          version: int, cycle: int) -> None:
        self.emit("artifact_written", agent_id=agent_id, artifact_id=artifact_id,
                  version=version, cycle=cycle)

    def pipeline_convergence(self, run_id: str, agents_done: list[str],
                               total_messages: int, total_cycles: int) -> None:
        self.emit("pipeline_convergence", run_id=run_id, agents_done=agents_done,
                  total_messages=total_messages, total_cycles=total_cycles)

    def pipeline_timeout(self, run_id: str, elapsed_s: float,
                          pending_agents: list[str]) -> None:
        self.emit("pipeline_timeout", run_id=run_id, elapsed_s=elapsed_s,
                  pending_agents=pending_agents)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _append(self, line: str) -> None:
        if _HAS_FILELOCK:
            with FileLock(str(self.lock_path), timeout=10):
                with open(self.path, "a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
        else:
            with self._tlock:
                with open(self.path, "a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
