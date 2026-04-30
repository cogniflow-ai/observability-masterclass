#!/usr/bin/env python3
"""
Cogniflow hook script: PostToolUse (REQ-HOOK-004).

Receives JSON on stdin from the Claude CLI, translates it to a
structured events.jsonl entry, and exits 0 (non-blocking observer).

Run by the Claude CLI after every successful tool call.
"""
import json
import os
import sys

# Minimal standalone import — event_writer has ZERO orchestrator dependencies
_script_dir = os.path.dirname(os.path.abspath(__file__))
_pkg_root   = os.path.join(_script_dir, "..", "..")
sys.path.insert(0, _pkg_root)

from orchestrator.event_writer import append_event, find_events_file


def main() -> None:
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            sys.exit(0)
        payload = json.loads(raw)
    except Exception:
        sys.exit(0)

    events_file = find_events_file()
    if not events_file:
        sys.exit(0)

    session_id = payload.get("session_id", "")
    tool_name  = payload.get("tool_name", payload.get("tool", ""))
    tool_input = payload.get("tool_input", {})

    # Map tool name to event type
    tool_map = {
        "Bash":      ("cli_bash_call",       _bash_fields),
        "Read":      ("cli_file_read",        _file_fields),
        "Write":     ("cli_file_write",       _file_write_fields),
        "Edit":      ("cli_file_edit",        _file_fields),
        "MultiEdit": ("cli_file_edit",        _file_fields),
        "WebFetch":  ("cli_web_fetch",        _web_fetch_fields),
        "WebSearch": ("cli_web_search",       _web_search_fields),
        "Task":      ("cli_subagent_dispatch", _task_fields),
    }

    if tool_name in tool_map:
        event_name, field_extractor = tool_map[tool_name]
        extra = field_extractor(tool_input, payload)
    else:
        event_name  = "cli_tool_other"
        extra = {"tool_name": tool_name, "tool_input": json.dumps(tool_input)[:500]}

    append_event(events_file, event_name, session_id=session_id, **extra)
    sys.exit(0)


def _bash_fields(tool_input: dict, payload: dict) -> dict:
    return {
        "command":     (tool_input.get("command") or "")[:300],
        "exit_code":   payload.get("tool_output", {}).get("exit_code", 0),
        "duration_ms": payload.get("duration_ms", 0),
    }


def _file_fields(tool_input: dict, payload: dict) -> dict:
    return {"file_path": tool_input.get("file_path") or tool_input.get("path", "")}


def _file_write_fields(tool_input: dict, payload: dict) -> dict:
    path    = tool_input.get("file_path") or tool_input.get("path", "")
    content = tool_input.get("content", "")
    return {"file_path": path, "bytes_written": len(content.encode("utf-8"))}


def _web_fetch_fields(tool_input: dict, payload: dict) -> dict:
    return {
        "url":         tool_input.get("url", "")[:500],
        "status_code": payload.get("tool_output", {}).get("status_code", 0),
    }


def _web_search_fields(tool_input: dict, payload: dict) -> dict:
    results = payload.get("tool_output", {}).get("results", [])
    return {
        "query":        tool_input.get("query", "")[:200],
        "result_count": len(results) if isinstance(results, list) else 0,
    }


def _task_fields(tool_input: dict, payload: dict) -> dict:
    return {"description": (tool_input.get("description") or "")[:300]}


if __name__ == "__main__":
    main()
