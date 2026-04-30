"""Tests for the structured routing block parser."""
import pytest
from orchestrator.cyclic_agent import parse_routing_block


def test_basic_working_block():
    text = """Here is my design.

JWT with 15min expiry.

{"send_to": ["developer_1"], "status": "working", "chunks": []}"""
    body, routing = parse_routing_block(text)
    assert "JWT with 15min expiry" in body
    assert routing["status"] == "working"
    assert routing["send_to"] == ["developer_1"]
    assert routing["chunks"] == []


def test_waiting_status():
    text = 'I have a question.\n{"send_to":["architect"],"status":"waiting","chunks":[]}'
    body, routing = parse_routing_block(text)
    assert routing["status"] == "waiting"


def test_done_status():
    text = 'All done.\n{"send_to":["tester"],"status":"done","chunks":[]}'
    _, routing = parse_routing_block(text)
    assert routing["status"] == "done"


def test_send_to_string_normalised_to_list():
    text = '{"send_to":"developer_1","status":"working","chunks":[]}'
    _, routing = parse_routing_block(text)
    assert isinstance(routing["send_to"], list)
    assert routing["send_to"] == ["developer_1"]


def test_inline_json_in_body_ignored():
    text = (
        'Here is some JSON: {"foo": "bar"} in the middle.\n'
        'And more text.\n'
        '{"send_to":["pm"],"status":"done","chunks":[]}'
    )
    body, routing = parse_routing_block(text)
    assert routing["status"] == "done"
    assert '{"foo": "bar"}' in body


def test_missing_json_raises():
    with pytest.raises(ValueError, match="No JSON block"):
        parse_routing_block("No JSON here at all.")


def test_missing_required_field_raises():
    with pytest.raises(ValueError, match="missing fields"):
        parse_routing_block('{"send_to":["pm"],"chunks":[]}')  # no status


def test_invalid_status_raises():
    with pytest.raises(ValueError, match="Invalid status"):
        parse_routing_block('{"send_to":["pm"],"status":"thinking","chunks":[]}')


def test_chunks_with_all_fields():
    text = """{
  "send_to": ["developer_1"],
  "status": "working",
  "chunks": [
    {
      "id": "arch-1-c1",
      "tags": ["auth", "jwt", "decision"],
      "synopsis": null,
      "line_range": [1, 12]
    }
  ],
  "context_request": {
    "query": "API endpoint signatures",
    "tags_hint": ["auth", "api"]
  }
}"""
    _, routing = parse_routing_block(text)
    assert routing["chunks"][0]["id"] == "arch-1-c1"
    assert routing["context_request"]["query"] == "API endpoint signatures"


def test_synopsis_null_allowed():
    text = '{"send_to":["a"],"status":"working","chunks":[{"id":"x-1-c1","tags":["auth"],"synopsis":null,"line_range":[1,5]}]}'
    _, routing = parse_routing_block(text)
    assert routing["chunks"][0]["synopsis"] is None


def test_context_request_null_allowed():
    text = '{"send_to":["a"],"status":"working","chunks":[],"context_request":null}'
    _, routing = parse_routing_block(text)
    assert routing["context_request"] is None
