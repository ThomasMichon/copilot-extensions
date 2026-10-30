"""Ordinary send owns native idempotency; callers do not build startup intents."""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_bridge.events import EventLog
from agent_bridge.client import BridgeClientError
from agent_bridge.models import SessionStatus
from agent_bridge.native_send_admission import NativeSendPrompt, submit
from agent_bridge.routes.sessions import router
from test_native_bound_prompt import prepare


@pytest.fixture
def native_send(session_manager, tmp_db, mock_acp_client):
    session = prepare(session_manager, tmp_db, mock_acp_client)
    session.event_log = EventLog(db=tmp_db, session_id=session.session_id)
    request = NativeSendPrompt(
        invocation_key="same-operation", expected_native_session_id="native-a",
        prompt='Continue the assignment.\nKeep "prior context".',
        caller_id="fixture-controller",
    )
    return session_manager, session, request


@pytest.mark.asyncio
async def test_standard_native_send_replay_is_the_original_user_turn(native_send, tmp_db):
    manager, session, request = native_send
    first = await submit(manager, session.session_id, request)
    await session._prompt_task
    session.status = SessionStatus.IDLE
    events = tmp_db.execute_read("SELECT * FROM events")
    assert await submit(manager, session.session_id, request) == first
    assert session.turn_count == 1
    assert len(tmp_db.get_turns(session.session_id)) == 1
    assert tmp_db.execute_read("SELECT * FROM events") == events
    manager._run_prompt.assert_awaited_once_with(session, 0, request.prompt)
    manager.handoff_session.assert_not_awaited()
    row, = tmp_db.execute_read("SELECT native_admission_json FROM turns")
    assert json.loads(row["native_admission_json"])["facts"]["caller_id"] == "fixture-controller"
    user_event, = [event for event in session.event_log.get_events(0) if event.event == "user_message"]
    assert user_event.data == {
        "content": request.prompt, "source": "agent-bridge", "caller_id": "fixture-controller",
    }


@pytest.mark.asyncio
async def test_concurrent_standard_retries_do_not_submit_another_turn(native_send):
    manager, session, request = native_send
    results = await asyncio.gather(*(submit(manager, session.session_id, request) for _ in range(4)))
    await session._prompt_task
    assert all(result == results[0] for result in results)
    assert session.turn_count == 1
    manager._run_prompt.assert_awaited_once()


@pytest.mark.asyncio
async def test_new_operation_is_a_deliberate_followup_not_startup_replay(native_send):
    manager, session, request = native_send
    await submit(manager, session.session_id, request)
    await session._prompt_task
    session.status = SessionStatus.IDLE
    second = request.model_copy(update={"invocation_key": "next-operation", "prompt": "Next round"})
    assert (await submit(manager, session.session_id, second))["turn_index"] == 1
    await session._prompt_task
    assert session.turn_count == 2
    assert manager._run_prompt.await_count == 2


@pytest.mark.asyncio
async def test_same_operation_changed_prompt_refuses_without_effect(native_send):
    manager, session, request = native_send
    await submit(manager, session.session_id, request)
    await session._prompt_task
    changed = request.model_copy(update={"prompt": "Different intent"})
    with pytest.raises(ValueError, match="conflict"):
        await submit(manager, session.session_id, changed)
    assert session.turn_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("cached", [False, True])
async def test_replaced_native_is_never_an_alias_for_the_original(native_send, cached):
    manager, session, request = native_send
    if cached:
        await submit(manager, session.session_id, request)
        await session._prompt_task
    count = session.turn_count
    session.acp_session_id = "different-native"
    with pytest.raises(ValueError, match="expected_native_mismatch"):
        await submit(manager, session.session_id, request)
    assert session.turn_count == count
    manager.resume_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_stopped_context_uses_strict_resume_without_recreate(native_send):
    manager, session, request = native_send
    session.status = SessionStatus.STOPPED

    async def resume(*args, **kwargs):
        assert args == ("bridge-a",)
        assert kwargs == {"drain": False, "allow_recreate": False}
        session.status = SessionStatus.IDLE
        return session

    manager.resume_session.side_effect = resume
    receipt = await submit(manager, session.session_id, request)
    await session._prompt_task
    assert receipt["native_session_id"] == "native-a"
    manager.resume_session.assert_awaited_once()
    manager.handoff_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_concurrent_stopped_retries_reconcile_after_actual_resume_state_race(
    native_send, monkeypatch, tmp_db,
):
    from unittest.mock import AsyncMock
    from agent_bridge import hosting_policy
    from agent_bridge.session_manager import SessionManager

    manager, session, request = native_send
    session.status = SessionStatus.STOPPED
    entered, release = asyncio.Event(), asyncio.Event()

    async def reattach(retained):
        assert retained is session
        entered.set()
        await release.wait()
        retained.status = SessionStatus.IDLE
        return True

    monkeypatch.setattr(hosting_policy, "require_session_hosting", AsyncMock())
    manager._try_reattach_live_host = AsyncMock(side_effect=reattach)
    manager.resume_session = AsyncMock(wraps=SessionManager.resume_session.__get__(manager))
    first = asyncio.create_task(submit(manager, session.session_id, request))
    await asyncio.wait_for(entered.wait(), timeout=2)
    second = asyncio.create_task(submit(manager, session.session_id, request))
    await asyncio.sleep(0)
    release.set()
    receipts = await asyncio.wait_for(asyncio.gather(first, second), timeout=2)
    await session._prompt_task
    assert receipts[0] == receipts[1]
    assert len(tmp_db.get_turns(session.session_id)) == 1
    assert manager.resume_session.await_count == 2
    manager._try_reattach_live_host.assert_awaited_once()
    manager._run_prompt.assert_awaited_once_with(session, 0, request.prompt)


@pytest.mark.asyncio
async def test_failed_resume_without_a_matching_receipt_remains_a_failure(native_send):
    manager, session, request = native_send
    session.status = SessionStatus.STOPPED
    manager.resume_session.side_effect = ValueError("synthetic resume refusal")
    with pytest.raises(ValueError, match="synthetic resume refusal"):
        await submit(manager, session.session_id, request)
    assert session.turn_count == 0
    manager._run_prompt.assert_not_awaited()


@pytest.mark.asyncio
async def test_resume_identity_change_refuses_before_prompt(native_send):
    manager, session, request = native_send
    session.status = SessionStatus.STOPPED

    async def resume(*args, **kwargs):
        session.status = SessionStatus.IDLE
        session.acp_session_id = "replacement"
        return session

    manager.resume_session.side_effect = resume
    with pytest.raises(ValueError, match="expected_native_mismatch"):
        await submit(manager, session.session_id, request)
    manager._run_prompt.assert_not_awaited()


@pytest.mark.asyncio
async def test_busy_refusal_does_not_create_a_turn_or_an_inbox_message(native_send, tmp_db):
    manager, session, request = native_send
    session.status = SessionStatus.RUNNING
    with pytest.raises(ValueError, match="not idle"):
        await submit(manager, session.session_id, request)
    assert tmp_db.get_turns(session.session_id) == []
    assert session.turn_count == 0
    manager._run_prompt.assert_not_awaited()


def test_real_api_uses_standard_native_send_receipt(native_send):
    manager, session, request = native_send
    app = FastAPI()
    app.state.session_manager = manager
    app.include_router(router)
    api = TestClient(app)
    path = "/api/v1/sessions/bridge-a/turns/native-bound/send"
    assert api.post(path, json={**request.model_dump(), "queue": True}).status_code == 422
    first = api.post(path, json=request.model_dump())
    assert first.status_code == 200
    assert api.post(path, json=request.model_dump()).json() == first.json()
    assert first.json() == {
        "version": 1, "session_id": "bridge-a", "native_session_id": "native-a",
        "invocation_key": "same-operation", "turn_index": 0,
        "queued": False, "admission": "accepted",
    }


def _command(
    native_send, monkeypatch, *, generation=51, transform=None, api=None, capabilities=None,
):
    from agent_bridge import __main__ as core

    manager, session, request = native_send
    if api is None:
        app = FastAPI()
        app.state.session_manager = manager
        app.include_router(router)
        api = TestClient(app, raise_server_exceptions=False)
    calls = []

    class Client:
        _connect_grace = 0
        _base = "http://testserver"

        def daemon_supports(self, floor):
            return generation >= floor

        def get_session(self, target):
            assert target == "native-a"
            return {"session_id": "bridge-a", "acp_session_id": "native-a"}

        def _request(self, method, path, body=None, **kwargs):
            if path == "/health":
                return {"protocol_version": generation, "min_protocol_version": 1,
                        "capabilities": capabilities or {}}
            calls.append((method, path, body))
            result = api.request(method, path, json=body)
            if result.is_error:
                try:
                    detail = result.json().get("detail")
                except ValueError:
                    detail = result.text
                raise BridgeClientError(result.status_code, detail)
            value = result.json()
            return transform(value) if transform is not None else value

        def resolve_live_session(self, *args, **kwargs):
            pytest.fail("native send must not use a represented inbox")

    monkeypatch.setattr(core, "_get_client", lambda **_kwargs: Client())
    monkeypatch.setattr(core, "_caller_id_for", lambda args: "fixture-controller")
    monkeypatch.setattr(core, "_connection_identity", lambda *args: {})
    args = core.build_parser().parse_args([
        "--json", "send", "native-a", request.prompt,
        "--expected-native-session-id", "native-a",
        "--idempotency-key", "same-operation", "--no-wait",
    ])
    return args, calls


@pytest.mark.parametrize("generation", [50, 51])
def test_real_send_parser_keeps_the_native_target_and_gates_the_receiving_daemon(
    native_send, monkeypatch, capsys, generation,
):
    _, session, request = native_send
    args, calls = _command(native_send, monkeypatch, generation=generation)
    if generation == 50:
        with pytest.raises(SystemExit) as refused:
            args.func(args)
        assert refused.value.code == 69
        assert json.loads(capsys.readouterr().out)["outcome"] == "refused_unavailable"
        assert calls == [] and session.turn_count == 0
        return
    args.func(args)
    assert len(calls) == 1
    assert calls[0] == (
        "POST", "/api/v1/sessions/bridge-a/turns/native-bound/send", request.model_dump(),
    )
    assert json.loads(capsys.readouterr().out)["invocation_key"] == "same-operation"
    assert session.turn_count == 1


@pytest.mark.asyncio
async def test_admission_survives_lost_reply_without_becoming_new_send(native_send, monkeypatch):
    manager, session, request = native_send
    original = manager.db.update_session_status

    def fail_after_admission(*args, **kwargs):
        raise RuntimeError("lost reply after turn journal commit")

    monkeypatch.setattr(manager.db, "update_session_status", fail_after_admission)
    with pytest.raises(RuntimeError, match="lost reply"):
        await submit(manager, session.session_id, request)
    monkeypatch.setattr(manager.db, "update_session_status", original)
    assert (await submit(manager, session.session_id, request))["turn_index"] == 0
    assert session.turn_count == 1
    manager._run_prompt.assert_not_awaited()


def test_post_commit_http_500_is_unknown_and_same_key_recovers_receipt(
    native_send, monkeypatch, capsys,
):
    manager, session, _ = native_send
    args, _ = _command(native_send, monkeypatch)
    original = manager.db.update_session_status

    def fail_after_admission(*args, **kwargs):
        raise RuntimeError("HTTP failure after journal commit")

    monkeypatch.setattr(manager.db, "update_session_status", fail_after_admission)
    with pytest.raises(SystemExit) as unknown:
        args.func(args)
    assert unknown.value.code == 70
    result = json.loads(capsys.readouterr().out)
    assert result["outcome"] == "unknown"
    assert result["target"] == "native-a"
    assert result["idempotency_key"] == "same-operation"
    assert result["reason"] == "native_send_acceptance_unknown"
    monkeypatch.setattr(manager.db, "update_session_status", original)
    args.func(args)
    assert json.loads(capsys.readouterr().out)["turn_index"] == 0
    assert session.turn_count == 1
    manager._run_prompt.assert_not_awaited()


def test_malformed_post_receipt_is_unknown_not_proven_refusal(native_send, monkeypatch, capsys):
    _, session, _ = native_send
    malformed = [True]
    args, _ = _command(
        native_send, monkeypatch,
        transform=lambda value: {} if malformed[0] else value,
    )
    with pytest.raises(SystemExit) as unknown:
        args.func(args)
    assert unknown.value.code == 70
    assert json.loads(capsys.readouterr().out)["outcome"] == "unknown"
    malformed[0] = False
    args.func(args)
    assert json.loads(capsys.readouterr().out)["turn_index"] == 0
    assert session.turn_count == 1


@pytest.mark.asyncio
async def test_strict_resume_reaches_actual_cold_reconnect_without_holding_turn_lock(
    native_send, monkeypatch, tmp_db,
):
    from agent_bridge import cold_outages

    manager, session, request = native_send
    session.status = SessionStatus.STOPPED
    outage = {
        "outage_id": "fixture-outage", "native_session_id": "native-a",
        "state": "interrupted", "recovery_phase": "registered",
    }
    monkeypatch.setattr(cold_outages, "latest", lambda *args: outage)
    monkeypatch.setattr(cold_outages, "require_custody", lambda *args: None)
    monkeypatch.setattr(cold_outages, "_assert_recovery_current", lambda *args: None)

    async def resume(*args, **kwargs):
        assert not session._turn_start_lock.locked()
        assert kwargs == {"drain": False, "allow_recreate": False}
        session.status = SessionStatus.IDLE
        await cold_outages.reconnect(manager, session, outage)
        return session

    manager.resume_session.side_effect = resume
    with pytest.raises(ValueError, match="cold outage|not idle"):
        await asyncio.wait_for(submit(manager, session.session_id, request), timeout=2)
    await session._prompt_task
    [turn] = tmp_db.get_turns(session.session_id)
    assert turn["prompt"] == cold_outages.RECONNECT_PROMPT
    assert turn["acquisition_delivery_id"] == "cold-outage:fixture-outage"
    assert session.acp_session_id == "native-a"
    assert turn["prompt"] != request.prompt
