"""Tests for the Mailbox message queue."""
import json
import pytest
from pathlib import Path
from orchestrator.mailbox import Mailbox, Message


@pytest.fixture
def mailbox(tmp_path):
    mb = Mailbox(tmp_path / "mailbox")
    mb.init_agents(["architect", "developer_1", "pm"])
    return mb


def test_enqueue_and_next_pending(mailbox):
    msg = mailbox.enqueue(
        send_to="architect", sender="pm",
        content="Design the auth module.",
        thread_id="pm-architect-thread-001",
    )
    assert msg.send_to == "architect"
    assert msg.sender  == "pm"

    pending = mailbox.next_pending()
    assert pending is not None
    assert pending.message_id == msg.message_id
    assert pending.content    == "Design the auth module."


def test_next_pending_returns_none_when_empty(mailbox):
    assert mailbox.next_pending() is None


def test_commit_moves_to_processed(mailbox, tmp_path):
    msg = mailbox.enqueue(
        send_to="developer_1", sender="architect",
        content="Here is the design.",
        thread_id="arch-dev-thread-001",
    )
    inbox_file = tmp_path / "mailbox" / "developer_1" / "inbox" / f"{msg.message_id}.json"
    assert inbox_file.exists()

    mailbox.commit(msg)
    processed_file = tmp_path / "mailbox" / "developer_1" / "processed" / f"{msg.message_id}.json"
    assert processed_file.exists()
    assert not inbox_file.exists()


def test_queue_depth(mailbox):
    assert mailbox.queue_depth("architect") == 0
    mailbox.enqueue("architect", "pm", "msg1", "t-001")
    mailbox.enqueue("architect", "pm", "msg2", "t-001")
    assert mailbox.queue_depth("architect") == 2


def test_all_inboxes_empty(mailbox):
    assert mailbox.all_inboxes_empty()
    mailbox.enqueue("architect", "pm", "content", "t-001")
    assert not mailbox.all_inboxes_empty()


def test_suspended_agents_skipped(mailbox):
    mailbox.enqueue("architect", "pm", "for architect", "t-001")
    # architect is suspended — should not be returned
    pending = mailbox.next_pending(suspended={"architect"})
    assert pending is None


def test_thread_id_deterministic(mailbox):
    tid1 = mailbox.make_thread_id("architect", "developer_1")
    tid2 = mailbox.make_thread_id("architect", "developer_1")
    assert tid1 != tid2  # increments each call
    assert "architect" in tid1
    assert "developer_1" in tid1


def test_message_from_dict_roundtrip(mailbox):
    msg = mailbox.enqueue(
        send_to="pm", sender="architect",
        content="Need decision.",
        thread_id="arch-pm-001",
        in_reply_to="arch-001",
        status_of_sender="waiting",
    )
    d = msg.to_dict()
    restored = Message.from_dict(d)
    assert restored.message_id == msg.message_id
    assert restored.in_reply_to == "arch-001"
    assert restored.status_of_sender == "waiting"


def test_enqueue_system_message(mailbox):
    msg = mailbox.enqueue_system("pm", "Deadlock detected.", "pm-sys-001")
    assert msg.sender == "_system"
    assert msg.send_to == "pm"
