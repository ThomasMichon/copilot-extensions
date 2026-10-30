"""Synthetic route/CLI receipt observations; no native runtime qualification."""

from __future__ import annotations

import asyncio
import io
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_native_send_admission import _command
from test_native_send_admission import native_send as native_send

from agent_bridge.models import SessionStatus
from agent_bridge.native_send_admission import submit
from agent_bridge.routes.sessions import router

PATH = "/api/v1/sessions/bridge-a/turns/native-bound/send/receipt"


def test_running_health_route_advertises_receipt_capability(monkeypatch):
    from agent_bridge.routes import health

    monkeypatch.setattr(health, "get_default_manager", lambda: SimpleNamespace(
        carrier_diagnostics=lambda: {},
    ))
    monkeypatch.setattr(health, "config_dir", lambda: None)
    monkeypatch.setattr(health, "slot_descriptor", lambda *args, **kwargs: {})
    app = FastAPI()
    app.include_router(health.router)
    with TestClient(app) as api:
        response = api.get("/health")
    assert response.status_code == 200
    assert response.json()["protocol_version"] >= 51
    assert response.json()["capabilities"]["native_send_receipt"] == 1


def _app(manager):
    app = FastAPI()
    app.state.session_manager = manager
    app.include_router(router)
    return app


async def _seed(native_send):
    manager, session, request = native_send
    receipt = await submit(manager, session.session_id, request)
    await session._prompt_task
    manager._run_prompt.reset_mock()
    return receipt


def _forbid_effects(manager):
    manager._submit_prompt_locked = AsyncMock(side_effect=AssertionError("observation submitted"))
    manager.resume_session.side_effect = AssertionError("observation resumed")
    manager.handoff_session.side_effect = AssertionError("observation handed off")


def _assert_no_effects(manager):
    manager._submit_prompt_locked.assert_not_awaited()
    manager.resume_session.assert_not_awaited()
    manager.handoff_session.assert_not_awaited()
    manager._run_prompt.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("found", [False, True])
@pytest.mark.parametrize("status", [
    SessionStatus.IDLE, SessionStatus.RUNNING, SessionStatus.STOPPED,
])
async def test_route_found_or_missing_never_changes_session_or_journal(
    native_send, tmp_db, status, found,
):
    manager, session, request = native_send
    expected = await _seed(native_send) if found else None
    session.status = status
    _forbid_effects(manager)
    before = (
        tmp_db.execute_read("SELECT * FROM turns"),
        tmp_db.execute_read("SELECT * FROM events"),
        tmp_db.execute_read("SELECT * FROM sessions"),
    )
    with TestClient(_app(manager)) as api:
        response = api.post(PATH, json=request.model_dump())
    if found:
        assert response.status_code == 200 and response.json() == expected
        assert response.json()["admission"] == "accepted"
        assert "completion" not in response.json()
    else:
        assert response.status_code == 404
        assert response.json() == {"detail": {"code": "native_receipt_missing"}}
    assert session.status == status
    assert session.turn_count == int(found)
    assert before == (
        tmp_db.execute_read("SELECT * FROM turns"),
        tmp_db.execute_read("SELECT * FROM events"),
        tmp_db.execute_read("SELECT * FROM sessions"),
    )
    _assert_no_effects(manager)


@pytest.mark.asyncio
async def test_route_receipt_observation_is_available_while_draining(
    native_send, monkeypatch,
):
    manager, session, request = native_send
    receipt = await _seed(native_send)
    session.status = SessionStatus.STOPPED
    monkeypatch.setattr(type(manager), "is_draining", property(lambda _: True))
    _forbid_effects(manager)
    with TestClient(_app(manager)) as api:
        response = api.post(PATH, json=request.model_dump())
    assert response.status_code == 200 and response.json() == receipt
    assert session.status == SessionStatus.STOPPED
    _assert_no_effects(manager)


@pytest.mark.asyncio
@pytest.mark.parametrize(("field", "value", "code", "status"), [
    ("prompt", "different prompt", "native_invocation_conflict", 409),
    ("caller_id", "other-caller", "native_invocation_conflict", 409),
    ("expected_native_session_id", "other-native", "expected_native_mismatch", 409),
    ("invocation_key", "other-key", "native_receipt_missing", 404),
])
async def test_route_identity_mismatch_or_wrong_key_is_not_a_send(
    native_send, field, value, code, status,
):
    manager, session, request = native_send
    await _seed(native_send)
    _forbid_effects(manager)
    with TestClient(_app(manager)) as api:
        response = api.post(PATH, json={**request.model_dump(), field: value})
    assert response.status_code == status
    assert response.json() == {"detail": {"code": code}}
    assert session.turn_count == 1
    _assert_no_effects(manager)


@pytest.mark.asyncio
@pytest.mark.parametrize("history", [
    None, "{", "null", "[]", "{}", '{"facts":{},"receipt":{}}',
])
async def test_route_malformed_or_corrupt_history_is_never_missing_or_accepted(
    native_send, tmp_db, history,
):
    manager, _, request = native_send
    await _seed(native_send)
    tmp_db.execute_write("UPDATE turns SET native_admission_json=?", (history,))
    _forbid_effects(manager)
    with TestClient(_app(manager)) as api:
        response = api.post(PATH, json=request.model_dump())
    assert response.status_code == 409
    assert response.json()["detail"]["code"] in {
        "native_admission_history_unproven", "native_invocation_conflict",
    }
    _assert_no_effects(manager)


@pytest.mark.asyncio
@pytest.mark.parametrize("corruption", ["receipt", "duplicates", "bridge"])
async def test_route_rejects_mismatched_receipt_duplicate_rows_and_wrong_bridge(
    native_send, tmp_db, monkeypatch, corruption,
):
    manager, _, request = native_send
    await _seed(native_send)
    read = tmp_db.execute_read

    def corrupt(sql, *args, **kwargs):
        rows = read(sql, *args, **kwargs)
        if "WHERE acquisition_delivery_id=?" not in sql:
            return rows
        rows = [dict(row) for row in rows]
        if corruption == "duplicates":
            return rows * 2
        if corruption == "bridge":
            rows[0]["session_id"] = "other-bridge"
        else:
            history = json.loads(rows[0]["native_admission_json"])
            history["receipt"]["turn_index"] = 10
            rows[0]["native_admission_json"] = json.dumps(history)
        return rows

    monkeypatch.setattr(tmp_db, "execute_read", corrupt)
    _forbid_effects(manager)
    with TestClient(_app(manager)) as api:
        response = api.post(PATH, json=request.model_dump())
    assert response.status_code == 409
    _assert_no_effects(manager)


def test_route_missing_session_and_invalid_request_are_explicit(native_send):
    manager, _, request = native_send
    _forbid_effects(manager)
    with TestClient(_app(manager)) as api:
        missing = api.post(PATH.replace("bridge-a", "missing"), json=request.model_dump())
        assert missing.status_code == 404
        assert missing.json() == {"detail": {"code": "native_session_unavailable"}}
        for body in (
            {**request.model_dump(), "queue": True},
            {**request.model_dump(), "expected_native_session_id": " "},
            {**request.model_dump(), "invocation_key": ""},
        ):
            assert api.post(PATH, json=body).status_code == 422
    _assert_no_effects(manager)


@pytest.mark.asyncio
@pytest.mark.parametrize("replacement", ["native", "holder"])
async def test_route_rechecks_identity_under_holder_lock(native_send, monkeypatch, replacement):
    manager, session, request = native_send
    await _seed(native_send)
    _forbid_effects(manager)
    entered = asyncio.Event()
    get_session = manager.get_session

    def lookup(session_id):
        found = get_session(session_id)
        entered.set()
        return found

    monkeypatch.setattr(manager, "get_session", lookup)
    transport = httpx.ASGITransport(app=_app(manager))
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as api:
        async with session._turn_start_lock:
            pending = asyncio.create_task(api.post(PATH, json=request.model_dump()))
            await asyncio.wait_for(entered.wait(), timeout=2)
            assert not pending.done()
            if replacement == "native":
                session.acp_session_id = "replacement"
            else:
                manager._sessions.pop(session.session_id)
        response = await asyncio.wait_for(pending, timeout=2)
    assert response.status_code == 409
    assert response.json() == {"detail": {"code": "expected_native_mismatch"}}
    _assert_no_effects(manager)


def _receipt_command(
    native_send, monkeypatch, *, generation=51, capability=1, transform=None,
):
    from agent_bridge import __main__ as core

    _, calls = _command(
        native_send, monkeypatch, generation=generation, transform=transform,
        capabilities={"native_send_receipt": capability},
    )
    monkeypatch.setattr(core, "_caller_id_for", lambda args: args.caller)
    request = native_send[2]
    monkeypatch.setattr("sys.stdin", io.StringIO(request.prompt))
    args = core.build_parser().parse_args([
        "--json", "send", "bridge-a", "--expected-native-session-id", "native-a",
        "--idempotency-key", "same-operation", "--caller", "fixture-controller",
        "--prompt-file", "-", "--receipt-only", "--no-wait",
    ])
    return args, calls


@pytest.mark.asyncio
@pytest.mark.parametrize("found", [False, True])
async def test_real_cli_observes_route_without_send_resume_or_fallback(
    native_send, monkeypatch, capsys, found,
):
    manager, session, request = native_send
    receipt = await _seed(native_send) if found else None
    session.status = SessionStatus.STOPPED
    _forbid_effects(manager)
    args, calls = _receipt_command(native_send, monkeypatch)
    if found:
        args.func(args)
        assert json.loads(capsys.readouterr().out) == {**receipt, "outcome": "accepted"}
    else:
        with pytest.raises(SystemExit) as missing:
            args.func(args)
        assert missing.value.code == 69
        payload = json.loads(capsys.readouterr().out)
        assert payload["outcome"] == "refused_unavailable"
        assert payload["reason"] == "native_receipt_missing"
        assert "not_sent" not in json.dumps(payload)
    assert calls == [("POST", PATH, request.model_dump())]
    assert session.status == SessionStatus.STOPPED
    _assert_no_effects(manager)


@pytest.mark.parametrize("generation", [0, 45, 50, 51])
def test_real_cli_refuses_old_running_provider_before_receipt_post(
    native_send, monkeypatch, capsys, generation,
):
    manager, session, _ = native_send
    _forbid_effects(manager)
    args, calls = _receipt_command(
        native_send, monkeypatch, generation=generation, capability=None,
    )
    with pytest.raises(SystemExit) as refused:
        args.func(args)
    assert refused.value.code == 69
    assert json.loads(capsys.readouterr().out)["reason"] == "unsupported_protocol"
    assert calls == [] and session.turn_count == 0
    _assert_no_effects(manager)


@pytest.mark.parametrize("capability", [None, 0, True, "1"])
def test_real_cli_refuses_missing_or_invalid_receipt_capability(
    native_send, monkeypatch, capsys, capability,
):
    manager, session, _ = native_send
    _forbid_effects(manager)
    args, calls = _receipt_command(native_send, monkeypatch, capability=capability)
    with pytest.raises(SystemExit) as refused:
        args.func(args)
    assert refused.value.code == 69
    assert json.loads(capsys.readouterr().out)["reason"] == "unsupported_protocol"
    assert calls == [] and session.turn_count == 0
    _assert_no_effects(manager)


@pytest.mark.asyncio
@pytest.mark.parametrize(("field", "value", "code", "exit_code"), [
    ("prompt", "different", "native_invocation_conflict", 65),
    ("caller", "other", "native_invocation_conflict", 65),
    ("expected_native_session_id", "other", "expected_native_mismatch", 65),
    ("idempotency_key", "other", "native_receipt_missing", 69),
    ("target", "missing", "native_session_unavailable", 69),
])
async def test_real_cli_preserves_exact_request_identity_on_observation(
    native_send, monkeypatch, capsys, field, value, code, exit_code,
):
    manager, _, _ = native_send
    await _seed(native_send)
    _forbid_effects(manager)
    args, calls = _receipt_command(native_send, monkeypatch)
    if field == "prompt":
        monkeypatch.setattr("sys.stdin", io.StringIO(value))
    else:
        setattr(args, field, value)
    with pytest.raises(SystemExit) as refused:
        args.func(args)
    assert refused.value.code == exit_code
    assert json.loads(capsys.readouterr().out)["reason"] == code
    assert len(calls) == 1 and calls[0][1].endswith("/send/receipt")
    _assert_no_effects(manager)


@pytest.mark.asyncio
async def test_real_cli_corrupt_history_is_unproven_without_retrying_admission(
    native_send, tmp_db, monkeypatch, capsys,
):
    manager, _, _ = native_send
    await _seed(native_send)
    tmp_db.execute_write("UPDATE turns SET native_admission_json=NULL")
    _forbid_effects(manager)
    args, calls = _receipt_command(native_send, monkeypatch)
    with pytest.raises(SystemExit) as unproven:
        args.func(args)
    assert unproven.value.code == 69
    payload = json.loads(capsys.readouterr().out)
    assert payload["reason"] == "native_admission_history_unproven"
    assert payload["outcome"] == "refused_unavailable"
    assert len(calls) == 1 and calls[0][1] == PATH
    _assert_no_effects(manager)


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", [
    {}, {"native_session_id": "other"}, {"session_id": "other"},
    {"invocation_key": "other"}, {"version": True}, {"queued": True},
    {"admission": "completed"}, {"turn_index": True}, {"turn_index": -1},
])
async def test_real_cli_malformed_response_remains_unproven(
    native_send, monkeypatch, capsys, mutation,
):
    manager, _, _ = native_send
    await _seed(native_send)
    _forbid_effects(manager)
    args, calls = _receipt_command(
        native_send, monkeypatch,
        transform=lambda value: {**value, **mutation} if mutation else {},
    )
    with pytest.raises(SystemExit) as unproven:
        args.func(args)
    assert unproven.value.code == 70
    payload = json.loads(capsys.readouterr().out)
    assert payload["outcome"] == "unknown"
    assert payload["reason"] == "native_receipt_unproven"
    assert payload["idempotency_key"] == "same-operation"
    assert len(calls) == 1 and calls[0][1] == PATH
    _assert_no_effects(manager)


@pytest.mark.parametrize("options", [
    [], ["--expected-native-session-id", "native-a"],
    ["--idempotency-key", "key"],
    ["--expected-native-session-id", " ", "--idempotency-key", "key"],
    ["--expected-native-session-id", "native-a", "--idempotency-key", " "],
    ["--expected-native-session-id", "native-a", "--idempotency-key", "k" * 1025],
])
def test_real_cli_receipt_only_requires_valid_native_identity_and_key(
    monkeypatch, capsys, options,
):
    from agent_bridge import __main__ as core

    monkeypatch.setattr(core, "_get_client", lambda: pytest.fail("invalid options reached client"))
    args = core.build_parser().parse_args([
        "--json", "send", "bridge-a", "prompt", "--receipt-only", *options,
    ])
    with pytest.raises(SystemExit) as invalid:
        args.func(args)
    assert invalid.value.code == 2
    assert json.loads(capsys.readouterr().out)["reason"] == "invalid_native_receipt_options"


@pytest.mark.parametrize("options", [
    ["--force"], ["--queue"], ["--steer"], ["--interrupt"],
    ["--delivery", "queue"], ["--delivery", "interrupt"], ["--notify"],
    ["--status-check"], ["--wait-reply"], ["--full-history"],
    ["--sender", "sender"], ["--reply-to", "other"],
    ["--expected-session-id", "other"], ["--plugin-context-stdin"],
    ["--kind", "notify"], ["--reply-timeout", "1"], ["--new"],
])
def test_real_cli_receipt_only_rejects_mixed_delivery_before_client(
    monkeypatch, capsys, options,
):
    from agent_bridge import __main__ as core

    monkeypatch.setattr(core, "_get_client", lambda: pytest.fail("mixed options reached client"))
    args = core.build_parser().parse_args([
        "--json", "send", "bridge-a", "prompt", "--receipt-only",
        "--expected-native-session-id", "native-a", "--idempotency-key", "key",
        *options,
    ])
    with pytest.raises(SystemExit) as invalid:
        args.func(args)
    assert invalid.value.code == 2
    assert json.loads(capsys.readouterr().out)["reason"] == "invalid_native_receipt_options"


@pytest.mark.asyncio
@pytest.mark.parametrize("unavailable", [False, True])
async def test_receipt_cli_uses_real_client_factory_without_daemon_ensure(
    native_send, monkeypatch, capsys, unavailable,
):
    from agent_bridge import __main__ as core
    from agent_bridge.client import BridgeClient, BridgeConnectionError

    await _seed(native_send)
    original_get_client = core._get_client
    args, _calls = _receipt_command(native_send, monkeypatch)
    client = core._get_client()
    client.assert_client_supported = lambda: None
    if unavailable:
        def disconnected(*_args, **_kwargs):
            raise BridgeConnectionError("synthetic unavailable provider")
        client._request = disconnected
    monkeypatch.setattr(BridgeClient, "from_config", lambda: client)
    monkeypatch.setattr(core, "_get_client", original_get_client)
    monkeypatch.setattr(
        core, "_ensure_daemon", lambda: pytest.fail("receipt observation started Bridge"),
    )
    if unavailable:
        with pytest.raises(SystemExit) as refused:
            args.func(args)
        assert refused.value.code == 70
        assert json.loads(capsys.readouterr().out)["outcome"] == "unknown"
    else:
        args.func(args)
        assert json.loads(capsys.readouterr().out)["outcome"] == "accepted"
