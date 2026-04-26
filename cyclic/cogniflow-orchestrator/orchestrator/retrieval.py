"""
Cogniflow Orchestrator v3.0 — Context retrieval (REQ-INDEX-005/006).

Two-pass retrieval using claude.exe as the retriever:
  Pass 1 — tags only (fast, cheap)
  Pass 2 — synopsis disambiguation for ambiguous entries (skipped if none)

Returns chunks to inject as the "Retrieved context" layer in the prompt.
"""
from __future__ import annotations

import json
import subprocess
from typing import Any, TYPE_CHECKING

from .memory import (
    get_index, extract_chunk_text, compress_index_if_needed,
    _strip_json_fences, _parse_tokens_from_stderr,
)

if TYPE_CHECKING:
    from .config import OrchestratorConfig
    from .events import EventLog


_PASS1_SYSTEM = (
    "You are a retrieval assistant. Return ONLY valid JSON with no preamble "
    "or markdown fences.\n"
    "Given a query and a list of index entries (id + tags), identify:\n"
    "  matched_ids   — entries clearly relevant to the query from their tags alone\n"
    "  ambiguous_ids — entries that partially match but need synopsis to decide\n"
    "  confidence    — 'high' if ≥1 clear match, 'medium' if only partial, "
    "'low' if no match\n"
    "Return: {\"matched_ids\":[], \"ambiguous_ids\":[], \"confidence\":\"\"}"
)

_PASS2_SYSTEM = (
    "You are a retrieval assistant. Return ONLY valid JSON with no preamble "
    "or markdown fences.\n"
    "Given a query and ambiguous index entries with synopsis text, decide:\n"
    "  include_ids — entries to include\n"
    "  exclude_ids — entries to exclude\n"
    "Return: {\"include_ids\":[], \"exclude_ids\":[]}"
)


def run_retrieval(
    mem_dir,
    agent_id: str,
    query: str,
    tags_hint: list[str],
    cycle: int,
    config: "OrchestratorConfig",
    log: "EventLog",
    thread_id: str = "",
) -> str:
    """
    Execute two-pass retrieval and return the text to inject as
    "Retrieved context" in the prompt.  Returns "" on miss.
    """
    log.context_retrieval_request(agent_id, query, tags_hint, thread_id)

    compress_index_if_needed(mem_dir, cycle, config)
    index = get_index(mem_dir)
    active_chunks = [c for c in index.get("chunks", []) if not c.get("archived")]

    if not active_chunks:
        log.context_retrieval_miss(agent_id, query, "index is empty")
        return ""

    # ── Pass 1 — tags only ────────────────────────────────────────────────────
    index_for_pass1 = [{"id": c["id"], "tags": c["tags"]} for c in active_chunks]
    pass1_prompt = (
        f"Query: \"{query}\"\n"
        f"Index entries (id and tags only):\n"
        f"{json.dumps(index_for_pass1, indent=2)}"
    )

    pass1_result = _call_retrieval(pass1_prompt, _PASS1_SYSTEM, config)
    if pass1_result is None:
        log.context_retrieval_miss(agent_id, query, "retrieval call failed")
        return ""

    matched_ids   = pass1_result.get("matched_ids", [])
    ambiguous_ids = pass1_result.get("ambiguous_ids", [])
    confidence    = pass1_result.get("confidence", "low")

    # ── Pass 2 — synopsis for ambiguous entries (skip if none) ────────────────
    if ambiguous_ids:
        ambiguous_entries = [
            {"id": c["id"], "tags": c["tags"], "synopsis": c.get("synopsis", "")}
            for c in active_chunks if c["id"] in ambiguous_ids
        ]
        pass2_prompt = (
            f"Query: \"{query}\"\n"
            f"Ambiguous entries:\n"
            f"{json.dumps(ambiguous_entries, indent=2)}"
        )
        pass2_result = _call_retrieval(pass2_prompt, _PASS2_SYSTEM, config)
        if pass2_result:
            matched_ids.extend(pass2_result.get("include_ids", []))

    if not matched_ids or confidence == "low":
        log.context_retrieval_miss(agent_id, query,
                                   f"no matches found (confidence={confidence})")
        return ""

    # ── Extract chunk content from full_context.md ────────────────────────────
    chunk_by_id = {c["id"]: c for c in active_chunks}
    injected_texts = []
    for mid in matched_ids:
        chunk = chunk_by_id.get(mid)
        if not chunk:
            continue
        text = extract_chunk_text(mem_dir, chunk["entry_id"], chunk["line_range"])
        if text.strip():
            injected_texts.append(f"--- chunk {mid} ---\n{text}")

    if not injected_texts:
        log.context_retrieval_miss(agent_id, query, "chunks extracted as empty")
        return ""

    log.context_retrieval_result(agent_id, matched_ids, confidence, len(injected_texts))
    return "\n\n".join(injected_texts)


def _call_retrieval(user_prompt: str, system: str,
                    config: "OrchestratorConfig") -> dict[str, Any] | None:
    """
    Call claude.exe for one retrieval pass.  Returns parsed JSON or None.
    """
    try:
        args = (
            [config.claude_bin]
            + config.retrieval_model_args()
            + ["--output-format", "json", "--system-prompt", system, "-p"]
        )
        result = subprocess.run(
            args,
            input=user_prompt.encode("utf-8"),
            capture_output=True,
            timeout=60,
        )
        if result.returncode != 0:
            return None
        raw = result.stdout.decode("utf-8", errors="replace").strip()
        raw = _strip_json_fences(raw)
        return json.loads(raw)
    except Exception:
        return None
