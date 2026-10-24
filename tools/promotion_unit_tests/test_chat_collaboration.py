"""Pure Chat metadata contracts; no store, provider or process is created."""

import copy
import sys
from pathlib import Path

import pytest

_path = sys.path.copy()
try:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "services/chat/src"))
    from agent_chat_service import ChatError
    from agent_chat_service.collaboration import normalize, validate_view
    from agent_chat_service.store import validate_request
    from agent_chat_service.store import json_bytes
finally:
    sys.path[:] = _path


def test_metadata_normalization_preserves_input_and_plain_text_is_not_promoted():
    value = {
        "schema": "agent-chat.collaboration", "version": 1, "kind": "question",
        "evidence": ["artifact:\N{SNOWMAN}"],
    }
    original = copy.deepcopy(value)
    assert normalize(value) == {**value, "to": []}
    assert value == original
    assert normalize(normalize(value)) == normalize(value)
    assert validate_request({
        "op": "post", "channel": "room", "codename": "agent-a",
        "request_id": "original", "text": '{"kind":"question","to":["agent-b"]}',
    }) == "post"


@pytest.mark.parametrize("change", [
    {"version": True}, {"kind": "task"}, {"to": ["agent-b", "agent-b"]},
    {"reply_to": True}, {"evidence": ["\n"]}, {"thread_root": 1},
    {"kind": "answer"}, {"evidence": ["x" * 512] * 8},
], ids=["version", "kind", "duplicate", "boolean-id", "reference", "root", "reply", "bytes"])
def test_invalid_metadata_is_a_pure_refusal(change):
    value = {"schema": "agent-chat.collaboration", "version": 1, "kind": "question", **change}
    with pytest.raises(ChatError) as caught:
        normalize(value)
    assert caught.value.code == "invalid"


@pytest.mark.parametrize("change", [
    {"after": 1}, {"head": 1}, {"epoch": "0" * 32},
    {"epoch": "0" * 32, "head": True}, {"recent_after": 1},
    {"limit": False}, {"max_bytes": 1023},
], ids=["after", "head", "epoch", "boolean-head", "recent", "limit", "bytes"])
def test_view_controls_require_a_complete_typed_boundary(change):
    with pytest.raises(ChatError) as caught:
        validate_view({"op": "inbox", **change})
    assert caught.value.code == "invalid"


def test_valid_view_controls_do_not_resolve_membership_or_history():
    validate_view({
        "op": "inbox", "epoch": "0" * 32, "head": 12, "after": 1,
        "recent_after": 0, "recent_limit": 0, "limit": 2, "max_bytes": 4096,
    })
    validate_view({"op": "thread", "event_id": 12})


def test_metadata_byte_and_recipient_boundaries_are_exact():
    value = {
        "schema": "agent-chat.collaboration", "version": 1, "kind": "notice",
        "evidence": ["x" * 512] * 3 + ["x"],
    }
    value["evidence"][-1] += "x" * (2048 - len(json_bytes(normalize(value))))
    assert len(value["evidence"][-1]) <= 512
    assert len(json_bytes(normalize(value))) == 2048
    value["evidence"][-1] += "x"
    with pytest.raises(ChatError, match="metadata is too large"):
        normalize(value)
    reference = "\N{SNOWMAN}" * 170 + "xx"
    assert len(reference.encode("utf-8")) == 512
    value["evidence"] = [reference]
    assert normalize(value)["evidence"] == [reference]
    value["evidence"] = [reference + "x"]
    with pytest.raises(ChatError, match="evidence is too large"):
        normalize(value)
    value["evidence"] = []
    value["to"] = [f"agent-{number}" for number in range(16)]
    assert len(normalize(value)["to"]) == 16
    value["to"].append("agent-16")
    with pytest.raises(ChatError, match="Invalid collaboration to"):
        normalize(value)
