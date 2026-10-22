"""Opt-in real SessionHost/owner integration; mock ACP is not native Copilot."""

import json
import os
import time

import pytest

from mock_acp_ordinary_fixture import OrdinaryWitness
import test_workspace_creation

native_owner = test_workspace_creation.native_owner

pytestmark = [
    pytest.mark.contract("agent_bridge.workspace_creation"),
    pytest.mark.skipif(os.environ.get("AGENT_WORKTREES_CREATION_NATIVE") != "1",
                       reason="opt-in connected native-owner process boundary"),
    pytest.mark.timeout(120),  # Same item bound as the existing native-owner cases.
]


@pytest.fixture
def witness(native_owner, monkeypatch):
    root, _command = native_owner
    test = OrdinaryWitness(root, monkeypatch)
    test.api.__enter__()
    try:
        yield test
    finally:
        test.close()


@pytest.fixture
def supervised_witness(native_owner, monkeypatch):
    root, _command = native_owner
    test = OrdinaryWitness(root, monkeypatch, supervised=True)
    test.api.__enter__()
    try:
        yield test
    finally:
        test.close()


@pytest.fixture
def unicode_witness(native_owner, monkeypatch):
    root, _command = native_owner
    test = OrdinaryWitness(root, monkeypatch, assignment='préambule "quoted"\n第二行\r\nlast')
    test.api.__enter__()
    try:
        yield test
    finally:
        test.close()


def test_mock_acp_ordinary_preamble(witness):
    handle = witness.start()
    session_id = handle["session"].removeprefix("local-body:")
    witness.settle(session_id)
    state = witness.census("preamble-settled")
    assert (state["allocations"], state["peer_boots"],
            state["conversations"], state["turns"]) == (1, 1, 1, 1)
    assert len(state["host_records"]) == 1
    owner = state["owner_record"]["worktree"]
    assert owner["creation_nonce"] and owner["last_session_id"] == state["peer_ids"][0]
    assert owner["session_count"] == 1 and owner["sessions_indexed"] is True
    assert not (witness.root / "home/.copilot/session-state").exists()
    assert state["task_status"] == "queued"
    assert len(state["peer_prompts"]) == 1
    turn = witness.db.execute_read("SELECT * FROM turns")[0]
    assert turn["response_text"] == "mock-result-1"
    assert turn["thought_text"] == "mock-thought"
    messages = [json.loads(line) for line in witness.transcript.read_text().splitlines()]
    request = next(m["message"] for m in messages
                   if m["kind"] == "inbound" and m["message"]["method"] == "session/prompt")
    assert any(m["kind"] == "outbound" and m["message"].get("id") == request["id"]
               and m["message"].get("result") == {"stopReason": "end_turn"} for m in messages)


def test_mock_acp_settled_response_loss_keeps_one_turn(supervised_witness):
    witness = supervised_witness
    witness.drop_settled_response = True
    assert witness.poll() == []
    before = witness.census("before-finite-supervisor-pass")
    assert (before["allocations"], before["conversations"], before["turns"]) == (1, 1, 1)
    reservation = witness.queue.latest_reservation(witness.task.id)
    assert reservation.state == "deferred", "first exception did not settle through the supervisor"
    assert [task["id"] for task in witness.supervisor._eligible(time.time())] == [witness.task.id]
    # Exactly one further real poll, after canonical deferral and positive eligibility.
    assert witness.poll() == [witness.task.id]
    after = witness.census("after-finite-supervisor-pass")
    witness.save()
    assert (after["allocations"], after["peer_boots"], after["conversations"]) == (1, 1, 1)
    assert before["peer_ids"] == after["peer_ids"]
    for field in ("worktree_id", "repo", "worktree_path", "creation_nonce"):
        assert before["persisted_owner_record"][field] == after["persisted_owner_record"][field]
    assert len(witness.evidence["supervisor_passes"]) == 2
    assert before["task_status"] == after["task_status"] == "queued"
    assert after["turns"] == 1, "settled accepted turn was submitted a second time"
    assert len(after["peer_prompts"]) == 1, "a second actual SDK/stdio prompt was submitted"
    invocation = witness.queue.latest_reservation(witness.task.id).agent_info["startup_invocation"]
    prompt_blocks = after["peer_prompts"][0]["params"]["prompt"]
    assert "".join(block["text"] for block in prompt_blocks if block["type"] == "text") == invocation["prompt"]


def test_mock_acp_unicode_frozen_prompt_roundtrip(unicode_witness):
    witness = unicode_witness
    witness.start()
    state = witness.census("unicode-roundtrip")
    invocation = witness.queue.latest_reservation(witness.task.id).agent_info["startup_invocation"]
    assert len(state["peer_prompts"]) == state["turns"] == 1
    blocks = state["peer_prompts"][0]["params"]["prompt"]
    actual = "".join(block["text"] for block in blocks if block["type"] == "text")
    assert actual == invocation["prompt"]
    assert actual.startswith('préambule "quoted"\n第二行\r\nlast\n\n')
