"""
Cogniflow Orchestrator v3.0 — Mailbox (REQ-MAILBOX).

Each agent has a filesystem queue under .state/mailbox/{agent_id}/:
  inbox/     ← pending messages (one JSON file per message)
  processed/ ← delivered messages (moved here after commit, never deleted)

Messages are delivered in ascending seq order within a thread_id.
The inbox message is moved to processed/ only AFTER full_context.md
has received its ---END {message_id}--- marker (REQ-MAILBOX-004).
"""
from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


# ── Message envelope ──────────────────────────────────────────────────────────

@dataclass
class Message:
    message_id:       str
    thread_id:        str
    seq:              int
    sender:           str
    send_to:          str
    content:          str
    in_reply_to:      Optional[str] = None
    status_of_sender: str = "working"
    sent_at:          str = field(default_factory=lambda: _now())
    # v4 — message kind. "normal" for routine agent messages, plus special
    # values emitted by the approval-routing machinery:
    #   "rejection_feedback" — routed reply after an operator rejection
    #   "approval_task"      — routed forward after an operator approval
    # The receiving agent's system prompt can treat them specially; the
    # Observer uses this field to tag feedback threads.
    kind:             str = "normal"

    def to_dict(self) -> dict[str, Any]:
        return {
            "message_id":       self.message_id,
            "thread_id":        self.thread_id,
            "seq":              self.seq,
            "in_reply_to":      self.in_reply_to,
            "sender":           self.sender,
            "send_to":          self.send_to,
            "content":          self.content,
            "status_of_sender": self.status_of_sender,
            "sent_at":          self.sent_at,
            "kind":             self.kind,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Message":
        return cls(
            message_id       = d["message_id"],
            thread_id        = d["thread_id"],
            seq              = d["seq"],
            sender           = d["sender"],
            send_to          = d["send_to"],
            content          = d["content"],
            in_reply_to      = d.get("in_reply_to"),
            status_of_sender = d.get("status_of_sender", "working"),
            sent_at          = d.get("sent_at", _now()),
            kind             = d.get("kind", "normal"),
        )


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── Mailbox manager ───────────────────────────────────────────────────────────

class Mailbox:
    """
    Manages the .state/mailbox/ directory tree for a pipeline run.
    """

    def __init__(self, mailbox_root: Path) -> None:
        self.root = mailbox_root
        self._seq_counters: dict[str, int] = {}   # thread_id → next seq
        self._thread_n: dict[frozenset, int] = {}  # frozenset(a,b) → thread count

    def init_agents(self, agent_ids: list[str]) -> None:
        """Create inbox/ and processed/ directories for each agent."""
        for aid in agent_ids:
            (self.root / aid / "inbox").mkdir(parents=True, exist_ok=True)
            (self.root / aid / "processed").mkdir(parents=True, exist_ok=True)

    # ── Thread ID generation ──────────────────────────────────────────────────

    def make_thread_id(self, agent_a: str, agent_b: str) -> str:
        """
        Return a deterministic bidirectional thread ID for a new conversation.
        Format: {min}-{max}-thread-{n}
        """
        key = frozenset([agent_a, agent_b])
        n   = self._thread_n.get(key, 0) + 1
        self._thread_n[key] = n
        lo, hi = sorted([agent_a, agent_b])
        return f"{lo}-{hi}-thread-{n:03d}"

    # ── Enqueue ───────────────────────────────────────────────────────────────

    def enqueue(
        self,
        send_to: str,
        sender: str,
        content: str,
        thread_id: str,
        in_reply_to: Optional[str] = None,
        status_of_sender: str = "working",
        message_id: Optional[str] = None,
        kind: str = "normal",
    ) -> Message:
        """Write one message to the target agent's inbox."""
        seq = self._next_seq(thread_id)
        mid = message_id or f"{sender}-{seq:04d}"
        msg = Message(
            message_id=mid, thread_id=thread_id, seq=seq,
            sender=sender, send_to=send_to, content=content,
            in_reply_to=in_reply_to, status_of_sender=status_of_sender,
            kind=kind,
        )
        inbox_path = self.root / send_to / "inbox" / f"{mid}.json"
        tmp_path   = inbox_path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(msg.to_dict(), indent=2), encoding="utf-8")
        tmp_path.rename(inbox_path)
        return msg

    def enqueue_system(
        self, send_to: str, content: str, thread_id: str,
    ) -> Message:
        """Inject an orchestrator system message to an agent's inbox."""
        return self.enqueue(
            send_to=send_to, sender="_system", content=content,
            thread_id=thread_id, status_of_sender="working",
        )

    # ── Dequeue ───────────────────────────────────────────────────────────────

    def next_pending(self, suspended: set[str] | None = None) -> Optional[Message]:
        """
        Return the oldest pending message across all inboxes (global FIFO,
        seq-ordered within thread).  Returns None if all inboxes are empty.
        Agents in the *suspended* set are skipped.
        """
        suspended = suspended or set()
        oldest_msg: Optional[Message] = None
        oldest_ts:  Optional[str]     = None

        for inbox_dir in self.root.glob("*/inbox"):
            agent_id = inbox_dir.parent.name
            if agent_id in suspended:
                continue
            # Collect all messages for this agent's inbox
            msgs = self._load_inbox(inbox_dir)
            if not msgs:
                continue
            # Pick the one with the lowest sent_at (FIFO) and lowest seq within a thread
            for m in msgs:
                if oldest_ts is None or m.sent_at < oldest_ts:
                    oldest_ts  = m.sent_at
                    oldest_msg = m

        return oldest_msg

    def queue_depth(self, agent_id: str) -> int:
        """Return the number of pending messages for an agent."""
        inbox = self.root / agent_id / "inbox"
        if not inbox.exists():
            return 0
        return len(list(inbox.glob("*.json")))

    def all_inboxes_empty(self, suspended: set[str] | None = None) -> bool:
        """True when every non-suspended agent has an empty inbox."""
        suspended = suspended or set()
        for inbox_dir in self.root.glob("*/inbox"):
            if inbox_dir.parent.name in suspended:
                continue
            if any(inbox_dir.glob("*.json")):
                return False
        return True

    # ── Commit (move to processed) ────────────────────────────────────────────

    def commit(self, message: Message) -> None:
        """
        Move a delivered message from inbox/ to processed/.
        MUST be called only after full_context.md END marker is written.
        """
        src = self.root / message.send_to / "inbox"     / f"{message.message_id}.json"
        dst = self.root / message.send_to / "processed" / f"{message.message_id}.json"
        if src.exists():
            shutil.move(str(src), str(dst))

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _next_seq(self, thread_id: str) -> int:
        n = self._seq_counters.get(thread_id, 0) + 1
        self._seq_counters[thread_id] = n
        return n

    def _load_inbox(self, inbox_dir: Path) -> list[Message]:
        msgs = []
        for f in inbox_dir.glob("*.json"):
            try:
                msgs.append(Message.from_dict(json.loads(f.read_text(encoding="utf-8"))))
            except Exception:
                pass
        # Sort by sent_at then seq for stable ordering
        msgs.sort(key=lambda m: (m.sent_at, m.seq))
        return msgs
