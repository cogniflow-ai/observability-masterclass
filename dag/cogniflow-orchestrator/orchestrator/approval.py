"""
Cogniflow Orchestrator v3.5 — Human-in-the-loop approval (GAP-3, restored).

When an agent's 00_config.json contains ``"requires_approval": true``,
the orchestrator pauses after a successful invocation and waits for an
operator to run ``python cli.py approve``/``reject``.

Files written/read in the agent's state directory:

  ``07_approval_request.json``  — the "hey, review me" marker written
                                  when the agent enters awaiting_approval
  ``07_approval.json``          — the operator's decision, written by the
                                  CLI command

Poll interval and timeout come from ``config.json`` (``approval`` block).
Approver identity comes from ``config.approver`` (default ``"operator"``).

Restart safety: if the process dies during the wait, a subsequent
``cli.py run`` sees status=awaiting_approval and re-enters the wait loop
without re-invoking claude.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from .exceptions import ApprovalRejectedError, ApprovalTimeoutError

if TYPE_CHECKING:
    from .config import OrchestratorConfig
    from .events import EventLog


STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
_REMINDER_INTERVAL_S = 60  # print a "still waiting" message every minute


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


# ── Engine-side API ───────────────────────────────────────────────────────────

def request_approval(
    agent_id: str,
    agent_dir: Path,
    run_id: str,
    log: "EventLog",
) -> None:
    """
    Mark the agent as awaiting approval and write the request file.
    """
    request_path = agent_dir / "07_approval_request.json"
    _write_json(request_path, {
        "agent_id":     agent_id,
        "run_id":       run_id,
        "status":       "pending",
        "requested_at": _now_iso(),
        "output_file":  str(agent_dir / "05_output.md"),
        "instructions": (
            f"Review the output at {agent_dir / '05_output.md'}, then run:\n"
            f"  python cli.py approve <pipeline_dir> --agent {agent_id}\n"
            f"or reject with:\n"
            f"  python cli.py reject <pipeline_dir> --agent {agent_id} --note \"...\""
        ),
    })
    log.agent_approval_required(agent_id)


def wait_for_approval(
    agent_id: str,
    agent_dir: Path,
    config: "OrchestratorConfig",
    log: "EventLog",
) -> None:
    """
    Block until 07_approval.json appears with a decision.

    Raises:
      ApprovalTimeoutError  — if approval_timeout_s elapses
      ApprovalRejectedError — if the operator writes status=rejected
    """
    from .debug import get_logger
    dlog = get_logger()

    approval_file = agent_dir / "07_approval.json"
    deadline      = time.monotonic() + config.approval_timeout_s
    last_reminder = time.monotonic()
    poll_s        = max(1, int(config.approval_poll_interval_s))

    dlog.debug(f"[approval:{agent_id}] wait started · poll={poll_s}s "
               f"timeout={config.approval_timeout_s}s · "
               f"watching {approval_file}")

    if config.verbose:
        print(f"\n  ⏸  {agent_id} — awaiting approval")
        print(f"       Inspect:  python cli.py inspect <pipeline_dir> --agent {agent_id} --file output")
        print(f"       Approve:  python cli.py approve <pipeline_dir> --agent {agent_id}")
        print(f"       Reject:   python cli.py reject  <pipeline_dir> --agent {agent_id} --note \"...\"\n")

    while time.monotonic() < deadline:
        if approval_file.exists():
            try:
                decision = json.loads(approval_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                # Incomplete write — wait and retry
                time.sleep(poll_s)
                continue

            status = decision.get("status", "")
            if status == STATUS_APPROVED:
                log.agent_approved(agent_id, decision.get("approved_by", "operator"))
                dlog.debug(f"[approval:{agent_id}] APPROVED by "
                           f"{decision.get('approved_by', 'operator')} · "
                           f"note={decision.get('note', '')!r}")
                if config.verbose:
                    print(f"  ✓  {agent_id} — approved by "
                          f"{decision.get('approved_by', 'operator')}")
                return

            if status == STATUS_REJECTED:
                note = decision.get("note", "")
                log.agent_rejected(
                    agent_id,
                    decision.get("approved_by", "operator"),
                    note,
                )
                dlog.debug(f"[approval:{agent_id}] REJECTED by "
                           f"{decision.get('approved_by', 'operator')} · note={note!r}")
                if config.verbose:
                    print(f"  ✗  {agent_id} — rejected: {note}")
                raise ApprovalRejectedError(agent_id, note)

        now = time.monotonic()
        if now - last_reminder >= _REMINDER_INTERVAL_S:
            remaining = int(deadline - now)
            if config.verbose:
                print(f"  ⏸  {agent_id} — still waiting ({remaining}s remaining)")
            last_reminder = now

        time.sleep(poll_s)

    raise ApprovalTimeoutError(agent_id, config.approval_timeout_s)


# ── CLI-side API (called by cli.py approve/reject) ────────────────────────────

def write_approval(
    agent_dir: Path,
    agent_id: str,
    approver: str,
    approved: bool,
    note: str = "",
) -> Path:
    """
    Write 07_approval.json with an operator's decision.
    Returns the path written.
    """
    data = {
        "agent_id":    agent_id,
        "status":      STATUS_APPROVED if approved else STATUS_REJECTED,
        "approved_by": approver,
        "note":        note,
        "decided_at":  _now_iso(),
    }
    path = agent_dir / "07_approval.json"
    _write_json(path, data)
    return path


def get_approval_status(agent_dir: Path) -> str:
    """Return 'approved' / 'rejected' / 'awaiting_approval' / 'none'."""
    approval = agent_dir / "07_approval.json"
    if approval.exists():
        try:
            data = json.loads(approval.read_text(encoding="utf-8"))
            return data.get("status", "unknown")
        except json.JSONDecodeError:
            return "unknown"
    if (agent_dir / "07_approval_request.json").exists():
        return "awaiting_approval"
    return "none"
