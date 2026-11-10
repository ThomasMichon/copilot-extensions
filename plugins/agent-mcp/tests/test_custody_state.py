"""Custody owns the commit fence, request ledgers and unknown-outcome boundary."""

import pytest

from agent_mcp import custody_state as cs


def active():
    state = cs.CustodyState()
    attachment = state.prepare()
    state.prepared(attachment)
    state.commit(attachment)
    state.activated(attachment)
    return state, attachment


def test_epoch_exhaustion_preserves_the_current_owner_without_preparation():
    state, owner = active()
    state._next_epoch = cs.MAX_SEQUENCE
    with pytest.raises(ValueError, match="epoch exhausted"):
        state.prepare()
    assert state.current is owner and state.candidate is None


def request(mid=1):
    return {"jsonrpc": "2.0", "id": mid, "method": "tools/call", "params": {}}


def response(mid=1):
    return {"jsonrpc": "2.0", "id": mid, "result": {"content": []}}


def test_output_receipt_and_full_settlement_are_required_before_exclusive_commit():
    state, old = active()
    state.admit(old, 1, request())
    state.fence()
    candidate = state.prepare()
    state.prepared(candidate)
    with pytest.raises(ValueError, match="unsettled"):
        state.commit(candidate)
    delivery = state.output(old, 1, response())
    with pytest.raises(ValueError, match="not been acknowledged"):
        state.settled(old, 1)
    state.delivered(old, delivery)
    assert state.output(old, 1, response()) is None
    with pytest.raises(ValueError, match="contradictory"):
        state.output(old, 1, {**response(), "result": {"different": True}})
    state.settled(old, 1)
    assert state.buffered_bytes == 0
    state.commit(candidate)
    for operation in (
        lambda: state.admit(old, 2, request(2)),
        lambda: state.output(old, 0, {"jsonrpc": "2.0", "method": "notifications/tools/list_changed"}),
        lambda: state.resumed(old),
        lambda: state.discard(candidate),
    ):
        with pytest.raises(ValueError):
            operation()
    with pytest.raises(ValueError, match="fenced"):
        state.admit(candidate, 2, request(2))
    state.activated(candidate)
    state.admit(candidate, 2, request(2))


def test_server_request_remains_retained_until_its_response_is_actually_sent():
    state, old = active()
    state.fence()
    server_request = {"jsonrpc": "2.0", "id": "server-1", "method": "sampling/createMessage"}
    state.upstream(server_request)
    candidate = state.prepare()
    state.prepared(candidate)
    with pytest.raises(ValueError, match="unsettled"):
        state.commit(candidate)
    control = response("server-1")
    state.admit(old, 1, control)
    with pytest.raises(ValueError, match="not been sent"):
        state.settled(old, 1)
    state.sent(old, control)
    state.settled(old, 1)
    state.commit(candidate)
    assert state.buffered_bytes == 0


def test_precommit_failure_can_resume_only_the_unchanged_owned_epoch():
    state, old = active()
    state.fence()
    failed = state.prepare()
    with pytest.raises(ValueError, match="not been discarded"):
        state.resumed(old)
    state.discard(failed)
    state.resumed(old)
    state.admit(old, 1, request())
    candidate = state.prepare()
    state.prepared(candidate)
    assert candidate.epoch > failed.epoch
    with pytest.raises(ValueError, match="fenced"):
        state.admit(cs.Attachment(old.epoch), 2, request(2))


def test_lost_activation_or_output_ack_never_authorizes_replay_or_rollback():
    state, old = active()
    state.admit(old, 1, request())
    state.output(old, 1, response())
    state.unknown()
    assert 1 in state.accepted and len(state.deliveries) == 1
    for operation in (
        lambda: state.admit(old, 1, request()),
        lambda: state.settled(old, 1),
        lambda: state.prepare(),
        lambda: state.resumed(old),
    ):
        with pytest.raises(ConnectionError, match="unknown"):
            operation()
    state, old = active()
    state.fence()
    candidate = state.prepare()
    state.prepared(candidate)
    state.commit(candidate)
    state.unknown()
    with pytest.raises(ConnectionError, match="unknown"):
        state.discard(candidate)
    assert state.current is candidate


def test_full_application_window_does_not_spend_reserved_control_capacity():
    state, old = active()
    for origin in range(1, cs.MAX_APPLICATION + 1):
        state.admit(old, origin, request(origin))
    with pytest.raises(ValueError, match="window is full"):
        state.admit(old, cs.MAX_APPLICATION + 1, request(1000))
    state.fence()
    cancelled = {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 1}}
    origin = cs.MAX_APPLICATION + 2
    state.admit(old, origin, cancelled)
    state.settled(old, origin)
    assert len(state.accepted) == cs.MAX_APPLICATION


def test_duplicate_ids_and_origins_or_unmatched_server_responses_have_no_admission():
    state, old = active()
    state.admit(old, 1, request())
    before = state.buffered_bytes
    for origin, message in ((1, request(2)), (2, request()), (3, response("foreign"))):
        with pytest.raises(ValueError):
            state.admit(old, origin, message)
    assert list(state.accepted) == [1]
    assert state.buffered_bytes == before


def test_unprepared_candidate_and_unowned_generated_send_cannot_produce_effects():
    state, old = active()
    candidate = state.prepare()
    state.fence()
    with pytest.raises(ValueError, match="not prepared"):
        state.commit(candidate)
    state.discard(candidate)
    state.resumed(old)
    with pytest.raises(ValueError, match="no accepted input"):
        state.sending(old, 1, request())
    state.admit(old, 1, request())
    generated = request("internal-subcall")
    assert state.sending(old, 1, generated) == generated
    with pytest.raises(ValueError, match="another custody input"):
        state.sending(old, 1, response())


def test_aggregate_byte_budget_refuses_before_recording_an_input_or_delivery(monkeypatch):
    state, old = active()
    state.admit(old, 1, request())
    before = state.buffered_bytes
    monkeypatch.setattr(cs, "MAX_BUFFERED_BYTES", before)
    monkeypatch.setattr(cs, "MAX_OUTPUT_BYTES", 0)
    with pytest.raises(ValueError, match="byte window"):
        state.admit(old, 2, request(2))
    with pytest.raises(ValueError, match="byte window"):
        state.output(old, 1, response())
    assert state.buffered_bytes == before
    assert list(state.accepted) == [1]
    assert state.accepted[1].response is None
    assert not state.deliveries
    cancellation = {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 1}}
    state.admit(old, 3, cancellation)
    state.settled(old, 3)
    assert state.buffered_bytes == before
