#!/usr/bin/env python3
"""
Cogniflow hook script: Stop (REQ-HOOK-005).

Receives JSON on stdin when claude.exe finishes a response cleanly.
Emits cli_agent_stop to events.jsonl.
"""
import json
import os
import sys

_script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_script_dir, "..", ".."))

from orchestrator.event_writer import append_event, find_events_file


def main() -> None:
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:
        sys.exit(0)

    events_file = find_events_file()
    if not events_file:
        sys.exit(0)

    append_event(
        events_file,
        "cli_agent_stop",
        session_id=payload.get("session_id", ""),
        stop_reason=payload.get("stop_reason", payload.get("reason", "turn_complete")),
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
