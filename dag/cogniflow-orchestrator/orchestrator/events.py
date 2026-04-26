"""
Cogniflow Orchestrator — Event logger.

Writes structured JSONL events to .state/events.jsonl.
Thread-safe: uses filelock so parallel agent subthreads
can all append to the same file without corruption.

Event format:
  {"ts": "ISO8601Z", "event": "event_name", ...extra_fields}
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
    """
    Append-only JSONL event log with cross-thread safety.

    Uses filelock when available (recommended: pip install filelock).
    Falls back to a threading.Lock for single-process use.
    """

    def __init__(self, log_path: Path) -> None:
        self.path      = log_path
        self.lock_path = log_path.with_suffix(".lock")
        self._tlock    = threading.Lock()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        if not log_path.exists():
            log_path.touch()

    # ── Public API ────────────────────────────────────────────────────────

    def emit(self, event: str, **kwargs: Any) -> dict:
        """Write one event record.  Returns the dict that was written."""
        record = {"ts": _now(), "event": event, **kwargs}
        self._append(json.dumps(record, ensure_ascii=False))
        return record

    # Convenience wrappers — mirrors the bash emit() call patterns

    def pipeline_start(self, name: str, run_id: str, total_agents: int) -> None:
        self.emit("pipeline_start", name=name, run_id=run_id,
                  total_agents=total_agents)

    def pipeline_done(self, run_id: str, layers: int, duration_s: float) -> None:
        self.emit("pipeline_done", run_id=run_id, layers=layers,
                  duration_s=round(duration_s, 1))

    def pipeline_error(self, reason: str, detail: str = "") -> None:
        self.emit("pipeline_error", reason=reason, detail=detail)

    def pipeline_paused(self, run_id: str, next_layer: int,
                         sentinel: str) -> None:
        """
        Emitted when the observer's pause sentinel is consumed between
        layers. The orchestrator removes the pause file and blocks until
        a resume sentinel appears.
        """
        self.emit("pipeline_paused", run_id=run_id,
                  next_layer=next_layer, sentinel=sentinel)

    def pipeline_resumed(self, run_id: str, resumed_layer: int,
                          waited_s: float) -> None:
        """
        Emitted when the observer's resume sentinel is consumed. The
        orchestrator removes the resume file and continues into
        `resumed_layer`. `waited_s` is how long the process spent paused.
        """
        self.emit("pipeline_resumed", run_id=run_id,
                  resumed_layer=resumed_layer,
                  waited_s=round(waited_s, 1))

    def layer_start(self, layer: int, agents: list[str], parallel: bool) -> None:
        self.emit("layer_start", layer=layer, agents=agents, parallel=parallel)

    def layer_done(self, layer: int, duration_s: float) -> None:
        self.emit("layer_done", layer=layer, duration_s=round(duration_s, 1))

    def layer_fail(self, layer: int, failed: list[str]) -> None:
        self.emit("layer_fail", layer=layer, failed=failed)

    def agent_launched(self, agent_id: str) -> None:
        self.emit("agent_launched", agent=agent_id)

    def agent_inputs_collected(self, agent_id: str, count: int) -> None:
        self.emit("agent_inputs_collected", agent=agent_id, input_count=count)

    def agent_context_ready(self, agent_id: str, bytes_: int, tokens_est: int) -> None:
        self.emit("agent_context_ready", agent=agent_id,
                  context_bytes=bytes_, tokens_estimated=tokens_est)

    def agent_budget_estimated(self, agent_id: str, bytes_: int, tokens_est: int) -> None:
        self.emit("agent_budget_estimated", agent=agent_id,
                  context_bytes=bytes_, tokens_estimated=tokens_est)

    def agent_start(self, agent_id: str, attempt: int = 1) -> None:
        self.emit("agent_start", agent=agent_id, attempt=attempt)

    def agent_done(self, agent_id: str, duration_s: float,
                   output_bytes: int, run_id: str,
                   attempt: int = 1) -> None:
        self.emit("agent_done", agent=agent_id,
                  duration_s=round(duration_s, 1),
                  output_bytes=output_bytes, run_id=run_id,
                  attempt=attempt)

    def agent_skip(self, agent_id: str, reason: str = "checkpoint") -> None:
        self.emit("agent_skip", agent=agent_id, reason=reason)

    def agent_fail(self, agent_id: str, exit_code: int,
                   duration_s: float, reason: str = "",
                   attempt: int = 1) -> None:
        self.emit("agent_fail", agent=agent_id, exit_code=exit_code,
                  duration_s=round(duration_s, 1), reason=reason,
                  attempt=attempt)

    def agent_timeout(self, agent_id: str, timeout_s: int,
                       attempt: int = 1) -> None:
        self.emit("agent_timeout", agent=agent_id, timeout_s=timeout_s,
                  attempt=attempt)

    def agent_retry_scheduled(self, agent_id: str, *,
                                attempt: int, max_attempts: int,
                                reason: str, delay_s: int,
                                next_attempt: int,
                                stderr_excerpt: str = "") -> None:
        """
        A call just failed and another attempt is queued.
        Emitted *before* the sleep, so the operator can see the wait
        in real time when tailing events.jsonl.
        """
        self.emit("agent_retry_scheduled", agent=agent_id,
                  attempt=attempt, max_attempts=max_attempts,
                  reason=reason, delay_s=delay_s,
                  next_attempt=next_attempt,
                  stderr_excerpt=stderr_excerpt)

    def agent_retry_exhausted(self, agent_id: str, *,
                                attempts: int, last_reason: str) -> None:
        """
        All retries failed. Emitted just before the final
        agent_fail / agent_timeout that ends the agent.
        """
        self.emit("agent_retry_exhausted", agent=agent_id,
                  attempts=attempts, last_reason=last_reason)

    def agent_bypassed(self, agent_id: str, by_router: str) -> None:
        self.emit("agent_bypassed", agent=agent_id, by_router=by_router)

    def agent_budget_exceeded(self, agent_id: str,
                               estimated: int, budget: int,
                               strategy: str) -> None:
        self.emit("agent_budget_exceeded", agent=agent_id,
                  tokens_estimated=estimated, budget=budget, strategy=strategy)

    def agent_tokens(self, agent_id: str, *,
                     input_tokens: int,
                     output_tokens: int,
                     cache_creation_tokens: int = 0,
                     cache_read_tokens: int = 0,
                     cost_usd: float = 0.0,
                     model: str = "",
                     duration_api_ms: int = 0,
                     run_id: str = "") -> None:
        """
        Real token accounting reported by the Claude CLI envelope.
        Emitted after a successful agent call when claude was invoked
        with --output-format json and the envelope contained `usage`.
        """
        self.emit("agent_tokens", agent=agent_id,
                  input_tokens=input_tokens,
                  output_tokens=output_tokens,
                  cache_creation_tokens=cache_creation_tokens,
                  cache_read_tokens=cache_read_tokens,
                  cost_usd=round(cost_usd, 6),
                  model=model,
                  duration_api_ms=duration_api_ms,
                  run_id=run_id)

    def agent_tokens_unavailable(self, agent_id: str, reason: str) -> None:
        """
        Fallback marker: the JSON envelope was missing, malformed, or
        had no usage block, so no real token counts are available for
        this agent call. Operators can still consult agent_budget_estimated.
        """
        self.emit("agent_tokens_unavailable", agent=agent_id, reason=reason)

    def pipeline_tokens(self, run_id: str, *,
                        total_input: int,
                        total_output: int,
                        total_cache_creation: int = 0,
                        total_cache_read: int = 0,
                        total_cost_usd: float = 0.0,
                        agents_counted: int = 0) -> None:
        """
        Pipeline-level rollup: sum of per-agent usage. Emitted just
        before pipeline_done.
        """
        self.emit("pipeline_tokens", run_id=run_id,
                  total_input=total_input,
                  total_output=total_output,
                  total_cache_creation=total_cache_creation,
                  total_cache_read=total_cache_read,
                  total_cost_usd=round(total_cost_usd, 6),
                  agents_counted=agents_counted)

    def router_decision(self, agent_id: str, decision: str,
                         activated: list[str], skipped: list[str]) -> None:
        self.emit("router_decision", agent=agent_id, decision=decision,
                  activated=activated, skipped=skipped)

    # ── Internal ──────────────────────────────────────────────────────────

    def _append(self, line: str) -> None:
        if _HAS_FILELOCK:
            with FileLock(str(self.lock_path), timeout=10):
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
        else:
            with self._tlock:
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")

    # ── Queries ───────────────────────────────────────────────────────────

    def tail(self, n: int = 20) -> list[dict]:
        """Return the last n events as parsed dicts."""
        lines = self.path.read_text(encoding="utf-8").splitlines()
        return [json.loads(l) for l in lines[-n:] if l.strip()]

    def filter(self, event_type: str) -> list[dict]:
        """Return all events of a given type."""
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            if rec.get("event") == event_type:
                out.append(rec)
        return out
