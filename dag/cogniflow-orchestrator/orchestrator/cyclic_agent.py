"""
Cogniflow Orchestrator v3.0 — Cyclic-mode agent runner (REQ-EXEC-003).

Assembles the four-layer prompt, invokes claude.exe, parses the
routing block from the response, and updates all memory files.

Prompt layers (REQ-PROMPT-001):
  1. Structured summary  — from structured_summary.json
  2. Recent thread       — from recent_thread.md
  3. Retrieved context   — injected only when context_request was emitted
  4. Current artifacts   — shared workspace content (within token limit)
  5. Incoming message    — the message envelope content

The system prompt is the agent's 01_system.md with the cyclic protocol
block appended at runtime.  CLAUDE.md is injected natively by the CLI.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Optional, TYPE_CHECKING

from .approval import request_approval, wait_for_approval
from .exceptions import (
    AgentExecutionError, MalformedOutputError,
    ApprovalRejectedError, ApprovalTimeoutError,
    SchemaViolationError,
)
from .memory import (
    write_entry_start, write_entry_body, write_entry_end,
    has_complete_entry, format_summary_for_prompt,
    get_summary, get_recent_thread, append_chunks,
    append_turn_to_thread, record_tokens, get_relevant_artifacts,
    write_artifact, is_budget_exceeded, _parse_tokens_from_stderr,
)
from .retrieval import run_retrieval
from .schema import (
    validate_output_schema,
    input_schema_from_agent_config, validate_input_schema,
)
from .vault import AuditCtx, open_vault_for

if TYPE_CHECKING:
    from .config import OrchestratorConfig
    from .events import EventLog
    from .mailbox import Message


# ── Routing block parser ──────────────────────────────────────────────────────

_ROUTING_REQUIRED = {"send_to", "status", "chunks"}
_VALID_STATUS     = {"working", "waiting", "done"}


def parse_routing_block(text: str) -> tuple[str, dict[str, Any]]:
    """
    Locate and parse the OUTERMOST trailing JSON object in *text*.
    Scans all top-level {...} blocks from the end and returns the last
    one that contains the required routing fields.
    Returns (response_body, routing_dict).
    Raises ValueError on parse failure.
    """
    # Collect all candidate top-level JSON blocks by scanning for balanced braces
    candidates: list[tuple[int, int]] = []  # (start, end) offsets
    i = 0
    while i < len(text):
        if text[i] == "{":
            depth = 0
            start = i
            for j in range(i, len(text)):
                if text[j] == "{":
                    depth += 1
                elif text[j] == "}":
                    depth -= 1
                    if depth == 0:
                        candidates.append((start, j + 1))
                        i = j  # continue scanning after this block
                        break
        i += 1

    if not candidates:
        raise ValueError("No JSON block found in response")

    # Try candidates from last to first — take the last one that has routing fields
    for start, end in reversed(candidates):
        try:
            routing = json.loads(text[start:end])
            if not isinstance(routing, dict):
                continue
            if not _ROUTING_REQUIRED.issubset(routing.keys()):
                continue

            # Normalise send_to to list
            if isinstance(routing["send_to"], str):
                routing["send_to"] = [routing["send_to"]]

            # Validate status
            if routing["status"] not in _VALID_STATUS:
                raise ValueError(f"Invalid status: '{routing['status']}'")

            response_body = text[:start].rstrip()
            return response_body, routing
        except (json.JSONDecodeError, TypeError):
            continue
        except ValueError:
            raise  # re-raise our own validation errors

    # No valid routing block found — try parsing the last candidate for error detail
    last_start, last_end = candidates[-1]
    try:
        routing = json.loads(text[last_start:last_end])
        missing = _ROUTING_REQUIRED - set(routing.keys())
        raise ValueError(f"Routing block missing fields: {missing}")
    except json.JSONDecodeError as exc:
        raise ValueError(f"Routing block is not valid JSON: {exc}")

    # Validate required fields
    missing = _ROUTING_REQUIRED - set(routing.keys())
    if missing:
        raise ValueError(f"Routing block missing fields: {missing}")

    # Normalise send_to to list
    if isinstance(routing["send_to"], str):
        routing["send_to"] = [routing["send_to"]]

    # Validate status
    if routing["status"] not in _VALID_STATUS:
        raise ValueError(f"Invalid status: '{routing['status']}'")

    response_body = text[:last_brace].rstrip()
    return response_body, routing


# ── Cyclic protocol block (appended to 01_system.md at runtime) ──────────────

def _build_protocol_block(
    agent_id: str,
    contacts: list[str],
    cycle_count: int,
    max_cycles: int,
    domain_tags: list[str],
) -> str:
    structural_tags = (
        "decision, revision, superseded, open-question, proposal, rejection, "
        "constraint, acknowledgement, escalation, clarification"
    )
    domain_str = ", ".join(domain_tags) if domain_tags else "(none defined)"
    contacts_str = ", ".join(contacts) if contacts else "pm"

    return f"""

## Communication protocol (injected by Cogniflow orchestrator)

You are operating in a cyclic multi-agent pipeline.

**Your contacts:**
- You MAY send messages to: {contacts_str}
- You MUST escalate to: pm (for unresolvable conflicts or scope decisions)

**Cycle status:** Turn {cycle_count} of {max_cycles} on this thread.

**Required output format — EVERY response MUST end with this JSON block:**
```json
{{
  "send_to": ["agent_id"],
  "status": "working | waiting | done",
  "chunks": [
    {{ "id": "{agent_id}-{cycle_count}-c1", "tags": ["tag1","tag2"], "synopsis": null, "line_range": [1, 10] }}
  ],
  "context_request": null
}}
```

**Tag vocabulary:**
Structural: {structural_tags}
Domain    : {domain_str}

**Status semantics:**
- working  — you sent a message and expect further exchange
- waiting  — you sent a question and are blocked until the peer responds
- done     — you have no further messages on this thread

**Context request (optional):** To retrieve specific details from your history,
add a context_request field: {{"query": "...", "tags_hint": ["tag1","tag2"]}}

**Convergence:** Emit status:done only when your convergence checklist is satisfied.
Do not emit done prematurely. Do not re-open closed decisions.
"""


# ── Prompt assembler ──────────────────────────────────────────────────────────

def assemble_cyclic_prompt(
    agent_id: str,
    agent_dir: Path,
    mem_dir: Path,
    shared_dir: Path,
    message: "Message",
    contacts: list[str],
    reachable_from: list[str],
    cycle_count: int,
    max_cycles: int,
    domain_tags: list[str],
    retrieved_context: str,
    config: "OrchestratorConfig",
) -> tuple[str, str]:
    """
    Assemble the full prompt and return (system_text, context_text).
    Also writes the assembled context to 04_context.md.
    """
    # ── System prompt with appended protocol block ────────────────────────────
    sys_path = agent_dir / "01_system.md"
    system_base = sys_path.read_text(encoding="utf-8") if sys_path.exists() else ""
    protocol    = _build_protocol_block(
        agent_id, contacts, cycle_count, max_cycles, domain_tags
    )
    system_text = system_base + protocol

    # ── Context (layers 1–5) ──────────────────────────────────────────────────
    summary    = format_summary_for_prompt(get_summary(mem_dir))
    thread     = get_recent_thread(mem_dir)
    artifacts  = get_relevant_artifacts(shared_dir, agent_id, reachable_from, config)

    parts: list[str] = []
    parts.append(f"## Current state summary\n{summary}")

    if thread.strip():
        parts.append(f"## Recent thread\n{thread}")

    if retrieved_context.strip():
        parts.append(f"## Retrieved context (from your history)\n{retrieved_context}")

    if artifacts.strip():
        parts.append(f"## Current artifacts\n{artifacts}")

    parts.append(
        f"## Incoming message\nFROM: {message.sender}\n"
        f"MESSAGE: {message.content}"
    )

    context_text = "\n\n".join(parts)

    # Write assembled prompt to 04_context.md (REQ-PROMPT-003)
    (agent_dir / "04_context.md").write_text(context_text, encoding="utf-8")

    return system_text, context_text


# ── Main cyclic agent runner ──────────────────────────────────────────────────

def run_cyclic_agent(
    agent_id: str,
    agent_dir: Path,
    mem_dir: Path,
    shared_dir: Path,
    message: "Message",
    contacts: list[str],
    reachable_from: list[str],
    cycle_count: int,
    max_cycles: int,
    domain_tags: list[str],
    agent_cfg: dict[str, Any],
    config: "OrchestratorConfig",
    log: "EventLog",
    pipeline_dir: Path,
    run_id: str = "",
) -> dict[str, Any]:
    """
    Run one cyclic invocation for *agent_id*.

    Returns the parsed routing dict with an extra "_response_body" key.
    Raises AgentExecutionError after max_retries exhausted.
    """
    max_retries = int(agent_cfg.get("max_retries", 2))
    agent_model = agent_cfg.get("model")

    # Check token budget before invocation
    if is_budget_exceeded(mem_dir, agent_cfg):
        # Inject finalise_now and override message content
        message.content = (
            "[SYSTEM: You have exceeded your token budget. "
            "Please wrap up immediately with status:done.]"
        )

    # Context retrieval (if previous turn requested it)
    retrieved_ctx = ""
    # Note: context_request from the previous routing block is passed in via
    # the message envelope's metadata (set by the engine if present)
    prev_context_req = getattr(message, "_context_request", None)
    if prev_context_req and isinstance(prev_context_req, dict):
        retrieved_ctx = run_retrieval(
            mem_dir=mem_dir,
            agent_id=agent_id,
            query=prev_context_req.get("query", ""),
            tags_hint=prev_context_req.get("tags_hint", []),
            cycle=cycle_count,
            config=config,
            log=log,
            thread_id=message.thread_id,
        )

    # Write entry start marker BEFORE invocation (REQ-FAULT-006)
    write_entry_start(mem_dir, message.message_id, cycle_count)

    # Assemble prompt
    system_text, context_text = assemble_cyclic_prompt(
        agent_id=agent_id,
        agent_dir=agent_dir,
        mem_dir=mem_dir,
        shared_dir=shared_dir,
        message=message,
        contacts=contacts,
        reachable_from=reachable_from,
        cycle_count=cycle_count,
        max_cycles=max_cycles,
        domain_tags=domain_tags,
        retrieved_context=retrieved_ctx,
        config=config,
    )

    # ── v4 — input-schema check on the incoming message ─────────────────────
    # In cyclic mode there is no static DAG upstream; the "upstream" for any
    # given invocation is the sender of the triggering message. If the agent
    # declares input_schema.require_upstream, we only validate when the
    # sender is in that list; otherwise every message is validated.
    in_schema = input_schema_from_agent_config(agent_dir)
    if in_schema and not message.sender.startswith("_"):
        require = in_schema.get("require_upstream")
        should_check = (
            require is None
            or (isinstance(require, list) and message.sender in require)
            or (isinstance(require, str) and message.sender == require)
        )
        if should_check:
            trimmed = dict(in_schema)
            trimmed["require_upstream"] = [message.sender]
            try:
                validate_input_schema(
                    agent_id, trimmed, {message.sender: message.content},
                )
                log.agent_input_schema_valid(agent_id)
            except SchemaViolationError as exc:
                log.agent_input_schema_violation(agent_id, exc.violations)
                raise AgentExecutionError(
                    agent_id, 0,
                    "Input schema violations:\n" + "\n".join(exc.violations),
                ) from exc

    # ── v4 — open vault and rehydrate outbound prompt text ──────────────────
    pipeline_name = pipeline_dir.name
    vault = open_vault_for(config, pipeline_dir)

    def _ctx(file_label: str) -> AuditCtx:
        return AuditCtx(
            run_id=run_id or message.thread_id,
            pipeline_name=pipeline_name,
            agent_id=agent_id,
            file=file_label,
        )

    system_text  = vault.rehydrate(
        system_text, ctx=_ctx("01_system"), direction="outbound", event_log=log,
    )
    context_text = vault.rehydrate(
        context_text, ctx=_ctx("04_context"), direction="outbound", event_log=log,
    )
    if config.rehydrate_outputs:
        (agent_dir / "04_context.md").write_text(context_text, encoding="utf-8")

    log.agent_activated(agent_id, message.thread_id, cycle_count,
                        agent_cfg.get("_invocation_n", 1))

    # ── Invocation loop with retry ────────────────────────────────────────────
    last_error = ""
    response_body = ""
    routing: dict[str, Any] = {}

    for attempt in range(1, max_retries + 2):  # +1 for initial try
        try:
            args = (
                [config.claude_bin]
                + config.model_args(agent_model)
                + ["--system-prompt", system_text, "-p"]
            )
            t0     = time.time()
            result = subprocess.run(
                args,
                input=context_text.encode("utf-8"),
                capture_output=True,
                timeout=config.agent_timeout,
            )
            duration = time.time() - t0
            stdout   = result.stdout.decode("utf-8", errors="replace").strip()
            stderr   = result.stderr.decode("utf-8", errors="replace")

            # Parse token count from stderr
            tokens = _parse_tokens_from_stderr(stderr)

            if result.returncode != 0:
                last_error = f"exit code {result.returncode}"
                if attempt <= max_retries:
                    log.emit("agent_retry", agent_id=agent_id, attempt=attempt,
                             reason="non_zero_exit")
                    continue
                _record_restart(agent_dir, attempt, "non_zero_exit")
                raise AgentExecutionError(agent_id, result.returncode, last_error)

            # Try to parse routing block
            try:
                response_body, routing = parse_routing_block(stdout)
            except ValueError as exc:
                last_error = str(exc)
                log.malformed_output(agent_id, attempt, last_error, message.thread_id)
                if attempt <= max_retries:
                    # Append correction to context and retry
                    context_text = context_text + (
                        "\n\n[CORRECTION]: Your previous response did not contain "
                        "the required routing JSON block. Please re-emit your "
                        "complete response ending with:\n"
                        '{"send_to":[...],"status":"working|waiting|done","chunks":[...]}'
                    )
                    continue
                raise MalformedOutputError(agent_id, attempt)

            # GAP-1 — validate response_body against output_schema if declared
            schema_cfg = agent_cfg.get("output_schema")
            if schema_cfg:
                try:
                    validate_output_schema(agent_id, response_body, schema_cfg)
                    log.agent_schema_valid(agent_id)
                except SchemaViolationError as exc:
                    log.agent_schema_violation(agent_id, exc.violations)
                    last_error = "; ".join(exc.violations)
                    if attempt <= max_retries:
                        context_text = context_text + (
                            "\n\n[CORRECTION]: Your previous response failed "
                            "output_schema validation with these violations:\n  • "
                            + "\n  • ".join(exc.violations)
                            + "\nPlease re-emit your response correcting the issues "
                            "above and ending with the required routing JSON block."
                        )
                        continue
                    raise AgentExecutionError(
                        agent_id, 0,
                        "Output schema violations:\n" + "\n".join(exc.violations),
                    ) from exc

            break  # success — routing parsed and schema (if any) passed

        except subprocess.TimeoutExpired:
            log.agent_timeout(agent_id)
            last_error = f"timeout after {config.agent_timeout}s"
            if attempt <= max_retries:
                log.emit("agent_retry", agent_id=agent_id, attempt=attempt,
                         reason="timeout")
                continue
            raise AgentExecutionError(agent_id, -1, last_error)

    # ── Write memory files ────────────────────────────────────────────────────

    # v4 — scan response for leaked raw secret values, then rehydrate any
    # <<secret:...>> placeholders the model chose to echo back.
    vault.scan_leaks(response_body, ctx=_ctx("response_raw"), event_log=log)
    if config.rehydrate_outputs:
        response_body = vault.rehydrate(
            response_body, ctx=_ctx("05_output"),
            direction="inbound", event_log=log,
        )

    # Write response body to 05_output.md
    (agent_dir / "05_output.md").write_text(response_body, encoding="utf-8")
    if config.keep_output_versions:
        inv_n = agent_cfg.get("_invocation_n", 1)
        (agent_dir / f"05_output.v{inv_n}.md").write_text(response_body, encoding="utf-8")

    # Append to full_context.md body
    write_entry_body(mem_dir, message.sender, message.content, response_body)

    # Append chunks to context index
    append_chunks(mem_dir, routing.get("chunks", []), message.message_id)

    # Write END marker ATOMICALLY before dispatching (REQ-MEM-001)
    write_entry_end(mem_dir, message.message_id)

    # Update recent thread
    append_turn_to_thread(
        mem_dir=mem_dir,
        incoming=message.content,
        response=response_body,
        sender=message.sender,
        responder=agent_id,
        cycle=cycle_count,
        config=config,
    )

    # Update structured summary (separate claude.exe call)
    summary_tokens = update_summary(
        mem_dir=mem_dir,
        agent_id=agent_id,
        incoming_content=message.content,
        response_body=response_body,
        sender=message.sender,
        cycle=cycle_count,
        config=config,
        log=log,
    )

    # Handle artifact_write signal
    artifact_write = routing.get("artifact_write")
    if artifact_write and isinstance(artifact_write, dict):
        write_artifact(
            shared_dir=shared_dir,
            artifact_id=artifact_write.get("id", "artifact"),
            content=artifact_write.get("content", ""),
            written_by=agent_id,
            summary=artifact_write.get("summary", ""),
            cycle=cycle_count,
            log=log,
        )

    # Record token budget
    record_tokens(
        mem_dir=mem_dir,
        tokens=tokens,
        invocation_type="agent_response",
        cycle=cycle_count,
        agent_cfg=agent_cfg,
        log=log,
        agent_id=agent_id,
        config=config,
    )
    if summary_tokens:
        record_tokens(
            mem_dir=mem_dir,
            tokens=summary_tokens,
            invocation_type="summary_update",
            cycle=cycle_count,
            agent_cfg=agent_cfg,
            log=log,
            agent_id=agent_id,
            config=config,
        )

    # GAP-3 — approval gate (optional per agent)
    # v4 — when approval_routes is configured, rejection / approval can be
    # routed into the graph as a feedback or task message to another agent
    # instead of failing the run. The engine enqueues the actual mailbox
    # message on the basis of the routing dict below.
    if agent_cfg.get("requires_approval", False):
        request_approval(agent_id, agent_dir, message.thread_id, log)
        approval_routes = agent_cfg.get("approval_routes") or {}
        try:
            wait_for_approval(agent_id, agent_dir, config, log)
        except ApprovalRejectedError as exc:
            on_reject = approval_routes.get("on_reject") or {}
            target    = on_reject.get("target")
            if target:
                include = on_reject.get("include", ["output", "note"])
                mode    = on_reject.get("mode", "feedback")
                note    = getattr(exc, "note", "") or ""
                routing["_approval_redirected"] = "rejected"
                routing["_redirect_target"]     = target
                routing["_redirect_include"]    = list(include)
                routing["_redirect_mode"]       = mode
                routing["_redirect_note"]       = note
                routing["_redirect_output"]     = response_body
                routing["_gate_agent_id"]       = agent_id
                # Suppress the gate's own outbound routing so the engine
                # does not also enqueue a regular follow-up message.
                routing["status"]  = "waiting"
                routing["send_to"] = []
                log.agent_rejected_redirected(
                    gate_agent_id=agent_id,
                    target_agent_id=target,
                    note=note, include=list(include),
                )
            else:
                raise AgentExecutionError(agent_id, -2, str(exc)) from exc
        except ApprovalTimeoutError as exc:
            raise AgentExecutionError(agent_id, -3, str(exc)) from exc
        else:
            # Approved — if on_approve is configured, post a task message.
            # approval_routes is authoritative: suppress the gate's own
            # outbound send_to so the target does not receive two messages.
            on_approve = approval_routes.get("on_approve") or {}
            target = on_approve.get("target")
            if target:
                include = on_approve.get("include", ["output"])
                mode    = on_approve.get("mode", "task")
                routing["_approval_redirected"] = "approved"
                routing["_redirect_target"]     = target
                routing["_redirect_include"]    = list(include)
                routing["_redirect_mode"]       = mode
                routing["_redirect_output"]     = response_body
                routing["_gate_agent_id"]       = agent_id
                routing["send_to"]              = []
                log.agent_approved_redirected(
                    gate_agent_id=agent_id,
                    target_agent_id=target,
                    include=list(include),
                )

    log.agent_done(
        agent=agent_id,
        duration_s=round(duration, 2),
        exit_code=0,
        invocation_n=agent_cfg.get("_invocation_n", 1),
        thread_id=message.thread_id,
    )

    routing["_response_body"] = response_body
    routing["_tokens"]        = tokens
    return routing


def _record_restart(agent_dir: Path, attempt: int, reason: str) -> None:
    """Append to restart_history in 06_status.json."""
    import datetime as _dt
    status_path = agent_dir / "06_status.json"
    status: dict[str, Any] = {}
    if status_path.exists():
        try:
            status = json.loads(status_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    status.setdefault("restart_history", []).append({
        "attempt": attempt, "reason": reason,
        "ts": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    })
    status_path.write_text(json.dumps(status, indent=2), encoding="utf-8")


# Re-export update_summary for use here (memory.py)
from .memory import update_summary
