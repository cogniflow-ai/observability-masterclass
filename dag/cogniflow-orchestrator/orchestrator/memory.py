"""
Cogniflow Orchestrator v3.0 — Per-agent memory management (REQ-MEM).

Manages five files per agent under .state/agents/{agent_id}/:
  full_context.md          — append-only verbatim record (REQ-MEM-001)
  structured_summary.json  — compressed decisions/questions (REQ-MEM-002)
  recent_thread.md         — sliding verbatim window (REQ-MEM-006)
  context_index.json       — retrieval index (REQ-INDEX-001)
  08_token_budget.json     — per-invocation token accounting (REQ-TOKEN-001)
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, TYPE_CHECKING

from .budget import estimate_tokens_str

if TYPE_CHECKING:
    from .config import OrchestratorConfig
    from .events import EventLog


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── Memory directory setup ────────────────────────────────────────────────────

def init_agent_memory(mem_dir: Path, agent_id: str, run_id: str) -> None:
    """Create memory files with empty/default content for a new run."""
    mem_dir.mkdir(parents=True, exist_ok=True)

    # full_context.md — start empty
    fc = mem_dir / "full_context.md"
    if not fc.exists():
        fc.write_text("", encoding="utf-8")

    # structured_summary.json — empty schema
    ss = mem_dir / "structured_summary.json"
    if not ss.exists():
        ss.write_text(json.dumps({
            "agent_id": agent_id,
            "pipeline_run_id": run_id,
            "last_updated_cycle": 0,
            "decisions": [],
            "open_questions": [],
            "constraints": [],
            "acknowledgements": [],
        }, indent=2), encoding="utf-8")

    # recent_thread.md — start empty
    rt = mem_dir / "recent_thread.md"
    if not rt.exists():
        rt.write_text("", encoding="utf-8")

    # context_index.json — empty index
    ci = mem_dir / "context_index.json"
    if not ci.exists():
        ci.write_text(json.dumps({
            "agent_id": agent_id,
            "chunks": [],
        }, indent=2), encoding="utf-8")

    # 08_token_budget.json — zero counters
    tb = mem_dir / "08_token_budget.json"
    if not tb.exists():
        tb.write_text(json.dumps({
            "agent_id": agent_id,
            "run_id": run_id,
            "budget_tokens": None,
            "used_tokens": 0,
            "invocation_count": 0,
            "by_type": {
                "agent_response": 0,
                "summary_update": 0,
                "retrieval_call": 0,
            },
            "per_cycle": [],
            "warning_threshold": None,
            "warning_emitted": False,
        }, indent=2), encoding="utf-8")


# ── Full context ──────────────────────────────────────────────────────────────

ENTRY_START_RE = re.compile(r"^---ENTRY (\S+) cycle=\d+ ts=.+---$")
ENTRY_END_RE   = re.compile(r"^---END (\S+)---$")


def write_entry_start(mem_dir: Path, message_id: str, cycle: int) -> None:
    """Write the ENTRY header marker (REQ-FAULT-006)."""
    marker = f"---ENTRY {message_id} cycle={cycle} ts={_now()}---\n"
    with open(mem_dir / "full_context.md", "a", encoding="utf-8") as fh:
        fh.write(marker)


def write_entry_body(mem_dir: Path, sender: str,
                     incoming: str, response: str) -> None:
    """Append the incoming message and response body to full_context.md."""
    block = (
        f"INCOMING FROM {sender}:\n{incoming}\n\n"
        f"RESPONSE:\n{response}\n"
    )
    with open(mem_dir / "full_context.md", "a", encoding="utf-8") as fh:
        fh.write(block)


def write_entry_end(mem_dir: Path, message_id: str) -> None:
    """
    Write the END marker atomically (REQ-MEM-001).
    Uses write-to-tmp-then-rename pattern.
    """
    fc_path = mem_dir / "full_context.md"
    marker  = f"---END {message_id}---\n\n"
    # Append to tmp copy, then rename over original
    tmp = fc_path.with_suffix(".tmp")
    content = fc_path.read_text(encoding="utf-8") if fc_path.exists() else ""
    tmp.write_text(content + marker, encoding="utf-8")
    tmp.replace(fc_path)


def has_complete_entry(mem_dir: Path, message_id: str) -> bool:
    """Return True if full_context.md has a complete ---END {message_id}--- marker."""
    fc = mem_dir / "full_context.md"
    if not fc.exists():
        return False
    end_marker = f"---END {message_id}---"
    return end_marker in fc.read_text(encoding="utf-8")


def truncate_to_last_complete_entry(mem_dir: Path) -> None:
    """
    Remove the last incomplete entry from full_context.md (REQ-FAULT-004).
    Finds the last ---END marker and truncates everything after it.
    """
    fc = mem_dir / "full_context.md"
    if not fc.exists():
        return
    text  = fc.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)

    last_end_pos = 0
    for i, line in enumerate(lines):
        if ENTRY_END_RE.match(line.strip()):
            last_end_pos = i + 1

    truncated = "".join(lines[:last_end_pos])
    fc.write_text(truncated, encoding="utf-8")


def extract_chunk_text(mem_dir: Path, entry_id: str,
                       line_range: list[int]) -> str:
    """
    Extract lines [start, end] (1-indexed) from the RESPONSE section
    of the entry identified by *entry_id* in full_context.md.
    Used by the retrieval system (REQ-INDEX-006).
    """
    fc = mem_dir / "full_context.md"
    if not fc.exists():
        return ""

    text         = fc.read_text(encoding="utf-8")
    in_entry     = False
    in_response  = False
    response_lines: list[str] = []
    entry_marker = f"---ENTRY {entry_id}"
    end_marker   = f"---END {entry_id}---"

    for line in text.splitlines():
        if line.startswith(entry_marker):
            in_entry    = True
            in_response = False
            response_lines = []
            continue
        if in_entry and line.startswith("RESPONSE:"):
            in_response = True
            continue
        if in_entry and line.startswith(end_marker):
            break
        if in_response:
            response_lines.append(line)

    start = max(0, line_range[0] - 1)
    end   = line_range[1] if len(line_range) > 1 else len(response_lines)
    return "\n".join(response_lines[start:end])


# ── Recent thread ─────────────────────────────────────────────────────────────

def append_turn_to_thread(
    mem_dir: Path,
    incoming: str,
    response: str,
    sender: str,
    responder: str,
    cycle: int,
    config: "OrchestratorConfig",
) -> None:
    """
    Append one turn to recent_thread.md and enforce the sliding window.
    A "turn" = the TURN marker + incoming + response (REQ-MEM-006).
    """
    rt_path = mem_dir / "recent_thread.md"
    turn = (
        f"=== TURN {cycle} ===\n"
        f"→ FROM {sender} TO {responder}:\n{incoming}\n\n"
        f"← FROM {responder} TO {sender}:\n{response}\n\n"
    )
    current = rt_path.read_text(encoding="utf-8") if rt_path.exists() else ""
    updated = current + turn

    # Enforce token budget by removing oldest turns
    while (estimate_tokens_str(updated) > config.thread_token_budget
           and "=== TURN " in updated):
        # Find and remove the first TURN block
        first_turn = updated.find("=== TURN ")
        next_turn  = updated.find("=== TURN ", first_turn + 1)
        if next_turn == -1:
            break  # only one turn left, keep it
        updated = updated[next_turn:]

    rt_path.write_text(updated, encoding="utf-8")


def get_recent_thread(mem_dir: Path) -> str:
    """Return the current content of recent_thread.md."""
    rt = mem_dir / "recent_thread.md"
    return rt.read_text(encoding="utf-8") if rt.exists() else ""


# ── Structured summary ────────────────────────────────────────────────────────

def get_summary(mem_dir: Path) -> dict[str, Any]:
    """Load and return the structured_summary.json."""
    ss = mem_dir / "structured_summary.json"
    if not ss.exists():
        return {}
    try:
        return json.loads(ss.read_text(encoding="utf-8"))
    except Exception:
        return {}


def format_summary_for_prompt(summary: dict[str, Any]) -> str:
    """Render structured_summary.json as readable text for the prompt."""
    if not summary:
        return "No prior decisions. No open questions. First activation."

    lines = []
    decisions = [d for d in summary.get("decisions", [])
                 if not d.get("superseded_by")]
    if decisions:
        lines.append("DECISIONS:")
        for d in decisions:
            lines.append(f"  {d['id']}: {d['text']} [cycle:{d['cycle']}]")

    open_q = [q for q in summary.get("open_questions", [])
              if q.get("status", "open") == "open"]
    if open_q:
        lines.append("\nOPEN QUESTIONS:")
        for q in open_q:
            lines.append(f"  {q['id']}: {q['text']} [to:{q['to']}]")

    if summary.get("constraints"):
        lines.append("\nCONSTRAINTS:")
        for c in summary["constraints"]:
            lines.append(f"  - {c}")

    if summary.get("acknowledgements"):
        lines.append("\nACKNOWLEDGEMENTS:")
        for a in summary["acknowledgements"]:
            lines.append(f"  - {a}")

    if not lines:
        return "No prior decisions. No open questions."
    return "\n".join(lines)


def update_summary(
    mem_dir: Path,
    agent_id: str,
    incoming_content: str,
    response_body: str,
    sender: str,
    cycle: int,
    config: "OrchestratorConfig",
    log: "EventLog",
) -> None:
    """
    Run the summary update claude.exe call and write the result (REQ-MEM-002/003).
    """
    current = get_summary(mem_dir)
    current_json = json.dumps(current, indent=2)

    system = (
        "You maintain a structured JSON summary. Return ONLY valid JSON with no "
        "preamble or markdown fences.\n"
        "Preserve all existing IDs. Update only fields affected by the new exchange.\n"
        "Mark revised decisions: add \"supersedes\":\"D-XX\" to the new entry and "
        "\"superseded_by\":\"D-YY\" to the old entry.\n"
        "If an open_question was answered in this exchange, set its status to \"closed\"."
    )
    user_prompt = (
        f"Current summary:\n{current_json}\n\n"
        f"New exchange —\n"
        f"  Incoming from {sender}: {incoming_content[:800]}\n"
        f"  Agent response: {response_body[:1200]}\n\n"
        "Return the complete updated summary JSON."
    )

    try:
        args = (
            [config.claude_bin]
            + config.summary_model_args()
            + ["--output-format", "json", "--system-prompt", system, "-p"]
        )
        result = subprocess.run(
            args,
            input=user_prompt.encode("utf-8"),
            capture_output=True,
            timeout=120,
        )
        raw    = result.stdout.decode("utf-8", errors="replace").strip()

        # Parse token count from stderr
        tokens_used = _parse_tokens_from_stderr(result.stderr.decode("utf-8", errors="replace"))

        # Strip any accidental markdown fences
        raw = _strip_json_fences(raw)
        new_summary = json.loads(raw)

        # Compression: if decisions > 20, archive superseded ones
        decisions = new_summary.get("decisions", [])
        if len(decisions) > 20:
            active     = [d for d in decisions if not d.get("superseded_by")]
            superseded = [d for d in decisions if d.get("superseded_by")]
            new_summary["decisions"] = active
            new_summary["archived_decisions_count"] = (
                new_summary.get("archived_decisions_count", 0) + len(superseded)
            )
            log.summary_overflow(agent_id, cycle, len(superseded))

        new_summary["last_updated_cycle"] = cycle
        (mem_dir / "structured_summary.json").write_text(
            json.dumps(new_summary, indent=2), encoding="utf-8"
        )

        d_count = len([d for d in new_summary.get("decisions", [])
                       if not d.get("superseded_by")])
        q_count = len([q for q in new_summary.get("open_questions", [])
                       if q.get("status", "open") == "open"])
        log.summary_updated(agent_id, cycle, d_count, q_count)
        return tokens_used

    except Exception:
        # Don't crash the pipeline over a summary update failure
        pass
    return 0


# ── Context index ─────────────────────────────────────────────────────────────

def get_index(mem_dir: Path) -> dict[str, Any]:
    ci = mem_dir / "context_index.json"
    if not ci.exists():
        return {"chunks": []}
    try:
        return json.loads(ci.read_text(encoding="utf-8"))
    except Exception:
        return {"chunks": []}


def append_chunks(mem_dir: Path, chunks: list[dict], entry_id: str) -> None:
    """Append chunk metadata from the routing block to context_index.json."""
    index = get_index(mem_dir)
    existing_ids = {c["id"] for c in index["chunks"]}
    for chunk in chunks:
        if chunk.get("id") and chunk["id"] not in existing_ids:
            entry = {
                "id":        chunk["id"],
                "tags":      chunk.get("tags", []),
                "synopsis":  chunk.get("synopsis"),
                "line_range": chunk.get("line_range", [1, 10]),
                "entry_id":  entry_id,
                "archived":  False,
            }
            index["chunks"].append(entry)
    (mem_dir / "context_index.json").write_text(
        json.dumps(index, indent=2), encoding="utf-8"
    )


def compress_index_if_needed(
    mem_dir: Path, cycle: int, config: "OrchestratorConfig"
) -> None:
    """
    Archive old entries when the index exceeds the compression threshold
    (REQ-INDEX-004).  Entries tagged decision or constraint are never archived.
    """
    index = get_index(mem_dir)
    active = [c for c in index["chunks"] if not c.get("archived")]
    if len(active) <= config.index_compression_threshold:
        return

    cutoff = max(0, cycle - config.index_compression_threshold)
    for chunk in index["chunks"]:
        if chunk.get("archived"):
            continue
        chunk_cycle = int(chunk.get("entry_id", "0").split("-")[1]
                          if "-" in chunk.get("entry_id", "") else "0")
        protected = any(t in chunk.get("tags", [])
                        for t in ("decision", "constraint"))
        if chunk_cycle < cutoff and not protected:
            chunk["archived"] = True

    (mem_dir / "context_index.json").write_text(
        json.dumps(index, indent=2), encoding="utf-8"
    )


# ── Token budget ──────────────────────────────────────────────────────────────

def record_tokens(
    mem_dir: Path,
    tokens: int,
    invocation_type: str,
    cycle: int,
    agent_cfg: dict,
    log: "EventLog",
    agent_id: str,
    config: "OrchestratorConfig",
) -> None:
    """
    Update 08_token_budget.json and emit warning/exceeded events (REQ-TOKEN).
    """
    tb_path = mem_dir / "08_token_budget.json"
    tb: dict[str, Any] = {}
    try:
        tb = json.loads(tb_path.read_text(encoding="utf-8"))
    except Exception:
        pass

    budget  = agent_cfg.get("cyclic_token_budget")
    warn_pct = float(agent_cfg.get("cyclic_token_warning_pct", 0.8))

    tb["used_tokens"]      = tb.get("used_tokens", 0) + tokens
    tb["invocation_count"] = tb.get("invocation_count", 0) + 1
    by_type = tb.setdefault("by_type", {})
    by_type[invocation_type] = by_type.get(invocation_type, 0) + tokens
    tb.setdefault("per_cycle", []).append({
        "cycle": cycle, "tokens": tokens, "invocation_type": invocation_type,
    })
    if budget is not None:
        tb["budget_tokens"]    = budget
        tb["warning_threshold"] = int(budget * warn_pct)

    # Emit events
    used = tb["used_tokens"]
    if budget is not None:
        threshold = int(budget * warn_pct)
        if used >= threshold and not tb.get("warning_emitted"):
            log.budget_warning(agent_id, used, budget, threshold)
            tb["warning_emitted"] = True
        if used >= budget:
            log.hard_budget_exceeded(agent_id, used, budget, "finalise_now")

    tb_path.write_text(json.dumps(tb, indent=2), encoding="utf-8")
    return used, budget


def is_budget_exceeded(mem_dir: Path, agent_cfg: dict) -> bool:
    """Return True if the agent has exceeded its cyclic_token_budget."""
    budget = agent_cfg.get("cyclic_token_budget")
    if budget is None:
        return False
    tb_path = mem_dir / "08_token_budget.json"
    if not tb_path.exists():
        return False
    try:
        tb = json.loads(tb_path.read_text(encoding="utf-8"))
        return tb.get("used_tokens", 0) >= budget
    except Exception:
        return False


# ── Shared artifact workspace ─────────────────────────────────────────────────

def write_artifact(
    shared_dir: Path,
    artifact_id: str,
    content: str,
    written_by: str,
    summary: str,
    cycle: int,
    log: "EventLog",
) -> None:
    """Write an artifact and update ARTIFACT_INDEX.json (REQ-ARTIFACT-001/002)."""
    shared_dir.mkdir(parents=True, exist_ok=True)
    idx_path = shared_dir / "ARTIFACT_INDEX.json"
    index: dict[str, Any] = {"artifacts": []}
    try:
        index = json.loads(idx_path.read_text(encoding="utf-8"))
    except Exception:
        pass

    # Find existing entry or create new
    existing = next((a for a in index["artifacts"] if a["id"] == artifact_id), None)
    version  = (existing["version"] + 1) if existing else 1
    artifact_path = shared_dir / f"{artifact_id}.md"
    artifact_path.write_text(content, encoding="utf-8")

    entry = {
        "id": artifact_id, "path": str(artifact_path),
        "written_by": written_by, "version": version,
        "cycle": cycle, "summary": summary,
    }
    if existing:
        index["artifacts"] = [a if a["id"] != artifact_id else entry
                               for a in index["artifacts"]]
    else:
        index["artifacts"].append(entry)

    idx_path.write_text(json.dumps(index, indent=2), encoding="utf-8")
    log.artifact_written(written_by, artifact_id, version, cycle)


def get_relevant_artifacts(
    shared_dir: Path,
    agent_id: str,
    reachable_from: list[str],
    config: "OrchestratorConfig",
) -> str:
    """
    Return artifact content (or summaries) for artifacts written by agents
    that have an outbound edge to *agent_id* (REQ-ARTIFACT-003/004).
    """
    idx_path = shared_dir / "ARTIFACT_INDEX.json"
    if not idx_path.exists():
        return ""
    try:
        index = json.loads(idx_path.read_text(encoding="utf-8"))
    except Exception:
        return ""

    relevant = [a for a in index.get("artifacts", [])
                if a.get("written_by") in reachable_from]
    if not relevant:
        return ""

    parts = []
    total_tokens = 0
    for a in relevant:
        path = shared_dir / f"{a['id']}.md"
        if path.exists():
            content = path.read_text(encoding="utf-8")
            content_tokens = estimate_tokens_str(content)
            if total_tokens + content_tokens <= config.artifact_max_inject_tokens:
                parts.append(f"--- {a['id']}.md (v{a['version']}) ---\n{content}")
                total_tokens += content_tokens
            else:
                parts.append(f"--- {a['id']}.md (v{a['version']}) [SUMMARY] ---\n{a['summary']}")

    return "\n\n".join(parts)


# ── Utility ───────────────────────────────────────────────────────────────────

def _strip_json_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text  = "\n".join(lines[1:])
        if text.endswith("```"):
            text = text[:-3]
    return text.strip()


def _parse_tokens_from_stderr(stderr: str) -> int:
    """
    Extract token count from claude.exe stderr output (REQ-INVOKE-002).
    Looks for patterns like: Tokens: input=NNN output=NNN  or  Total: NNN tokens
    Returns 0 if not found (caller uses estimate_tokens() as fallback).
    """
    # Pattern: "Total tokens: NNN" or "tokens: NNN"
    m = re.search(r"(?:total\s+)?tokens?[:\s=]+(\d+)", stderr, re.IGNORECASE)
    if m:
        return int(m.group(1))
    # Pattern: "input=NNN output=NNN"
    m_in  = re.search(r"input=(\d+)", stderr, re.IGNORECASE)
    m_out = re.search(r"output=(\d+)", stderr, re.IGNORECASE)
    if m_in and m_out:
        return int(m_in.group(1)) + int(m_out.group(1))
    return 0
