"""Actual parser, negotiated HTTP and private journal; no native actor or rollout."""

from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_bridge import __main__ as core
from agent_bridge.auth import BearerAuthMiddleware
from agent_bridge.client import BridgeClientError, BridgeConnectionError
from agent_bridge.db import Database
from agent_bridge.native_send_admission import NativeSendPrompt, submit
from agent_bridge.native_bound_prompt import submit_native_bound_prompt
from agent_bridge.native_send_settlement import settle
from agent_bridge.routes.sessions import router
from agent_bridge.session_manager import DaemonDrainingError
from agent_bridge.session_status import SessionStatus
from agent_bridge.send_outcome import SendRefused
from test_native_keyed_admission import keyed as keyed
from test_native_send_settlement import _seed

PATH = "/api/v1/sessions/bridge-a/turns/native-bound/send/settle"


def _expected() -> dict[str, Any]:
    path = Path(__file__).resolve().parents[1] / "contract/inputs/http52/owner-membership.json"
    value: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    expected: dict[str, Any] = value["native_send_settlement"]
    return expected


def _app(manager: Any) -> FastAPI:
    app = FastAPI()
    app.state.session_manager = manager
    app.include_router(router)
    return app


def _command(
    manager: Any, request: NativeSendPrompt, monkeypatch: pytest.MonkeyPatch, *,
    health_override: dict[str, Any] | None = None, fault: str | None = None,
) -> tuple[Any, list[tuple[str, str]], TestClient]:
    from agent_bridge.routes import health

    monkeypatch.setattr(health, "get_default_manager", lambda: SimpleNamespace(
        carrier_diagnostics=lambda: {},
    ))
    monkeypatch.setattr(health, "config_dir", lambda: None)
    monkeypatch.setattr(health, "slot_descriptor", lambda *args, **kwargs: {})
    monkeypatch.setattr(health, "observe_worktree_writer", lambda: {
        "installation_root": None, "runtime_generation": None,
    })
    app = _app(manager)
    app.include_router(health.router)
    api = TestClient(app)
    calls = []

    class Client:
        _base = "http://testserver"
        _connect_grace = 0.0

        def _request(
            self, method: str, path: str, body: dict[str, Any] | None = None, **kwargs: Any,
        ) -> Any:
            calls.append((method, path))
            if path == "/health" and health_override is not None:
                return health_override
            response = api.request(method, path, json=body)
            if response.status_code >= 400:
                raise BridgeClientError(response.status_code, response.json().get("detail"))
            result = response.json()
            if path == "/health":
                assert result["capabilities"]["native_send_settlement"] == _expected()["capability"]
            if path == PATH:
                assert kwargs["allow_retry"] is False
                if fault == "lost":
                    raise BridgeConnectionError("synthetic acknowledgement lost")
                if fault == "server":
                    raise BridgeClientError(500, "synthetic post-commit response failure")
                if fault == "digest":
                    result["request_digest"] = "not-the-original-request"
                if fault == "native":
                    result["native_session_id"] = "other-native"
                if fault == "fence":
                    result["fenced"] = False
                if fault == "turn":
                    result["turn_index"] = 0
            return result

    client = Client()
    def get_client(**kwargs: Any) -> Any:
        assert kwargs == {"ensure": False}
        return client

    monkeypatch.setattr(core, "_get_client", get_client)
    monkeypatch.setattr("sys.stdin", io.StringIO(request.prompt))
    args = core.build_parser().parse_args([
        "--json", "send", "bridge-a", "--expected-native-session-id", "native-a",
        "--idempotency-key", request.invocation_key, "--caller", "fixture-controller",
        "--prompt-file", "-", "--settle-native-send", "--no-wait",
    ])
    return args, calls, api


@pytest.mark.asyncio
@pytest.mark.parametrize("accepted", [False, True])
async def test_real_cli_settles_original_through_advertised_provider_without_sending(
    keyed: tuple[Any, Any, Any], tmp_db: Database, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], accepted: bool,
) -> None:
    manager, session, request = await _seed(keyed, tmp_db)
    if accepted:
        await submit(manager, session.session_id, request)
        await session._prompt_task
    session.status = SessionStatus.STOPPED
    manager._run_prompt.reset_mock()
    before = len(tmp_db.get_turns(session.session_id))
    args, calls, api = _command(manager, request, monkeypatch)
    try:
        args.func(args)
        result = json.loads(capsys.readouterr().out)
        expected = _expected()
        assert result["admission"] == expected["accepted" if accepted else "unadmitted"]
        assert [path for method, path in calls if method == "POST"] == [PATH]
        assert len(tmp_db.get_turns(session.session_id)) == before
        assert session.status == SessionStatus.STOPPED
        if not accepted:
            refused = api.post(PATH.removesuffix("/settle"), json=request.model_dump())
            assert refused.status_code == 409
            assert refused.json()["detail"]["code"] == "native_send_settled"
        manager.resume_session.assert_not_awaited()
        manager._run_prompt.assert_not_awaited()
    finally:
        api.close()


def test_executing_cli_declares_settlement_without_contacting_a_daemon(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    client = Mock(side_effect=AssertionError("contract discovery contacted a daemon"))
    monkeypatch.setattr(core, "_get_client", client)
    args = core.build_parser().parse_args(["version", "--contracts-json"])
    args.func(args)
    result = json.loads(capsys.readouterr().out)
    assert result["contracts"]["agent-bridge.send"] == _expected()["cli"]
    client.assert_not_called()


def test_ordinary_sender_preserves_proved_settled_key_refusal() -> None:
    client = Mock()
    client._base = "http://testserver"
    client._connect_grace = 0.0
    client.daemon_supports.return_value = True
    client.get_session.return_value = {
        "session_id": "bridge-a", "acp_session_id": "native-a",
    }

    def request(method: str, path: str, *args: Any, **kwargs: Any) -> Any:
        if path == "/health":
            return {"protocol_version": 52, "min_protocol_version": 1}
        raise BridgeClientError(409, {"code": "native_send_settled"})

    transport = client._request
    transport.side_effect = request
    with pytest.raises(SendRefused) as refused:
        submit_native_bound_prompt(
            client, "bridge-a", "Original prompt", expected_native_session_id="native-a",
            idempotency_key="original-wake", caller_id="fixture-controller",
        )
    assert refused.value.reason == "native_send_settled"
    assert refused.value.retryable is False
    assert [call.args[0] for call in transport.call_args_list] == ["GET", "POST"]


@pytest.mark.asyncio
async def test_explicit_reobservation_recovers_lost_settlement_without_another_turn(
    keyed: tuple[Any, Any, Any], tmp_db: Database, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    manager, session, request = await _seed(keyed, tmp_db)
    args, _, api = _command(manager, request, monkeypatch, fault="lost")
    try:
        with pytest.raises(SystemExit) as unknown:
            args.func(args)
        assert unknown.value.code == 70
        capsys.readouterr()
    finally:
        api.close()
    args, calls, api = _command(manager, request, monkeypatch)
    try:
        args.func(args)
        assert json.loads(capsys.readouterr().out)["admission"] == "not_admitted"
        assert [path for method, path in calls if method == "POST"] == [PATH]
        assert len(tmp_db.execute_read("SELECT * FROM native_send_settlements")) == 1
        assert len(tmp_db.get_turns(session.session_id)) == 1
        manager._run_prompt.assert_not_awaited()
    finally:
        api.close()


@pytest.mark.parametrize("extra", [
    ["--receipt-only"], ["--native-invocation-file", ""], ["--live-exact"],
    ["--queue"], ["--force"], ["--wait"], ["--new"],
])
def test_mixed_settlement_modes_refuse_before_input_or_client(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], extra: list[str],
) -> None:
    client = Mock(side_effect=AssertionError("invalid mode discovered a client"))
    monkeypatch.setattr(core, "_get_client", client)
    monkeypatch.setattr("sys.stdin", Mock(read=Mock(side_effect=AssertionError("read input"))))
    args = core.build_parser().parse_args([
        "--json", "send", "bridge-a", "--expected-native-session-id", "native-a",
        "--idempotency-key", "original-wake", "--prompt-file", "-",
        "--settle-native-send", *extra,
    ])
    with pytest.raises(SystemExit) as refused:
        args.func(args)
    assert refused.value.code == 2
    assert json.loads(capsys.readouterr().out)["reason"] == "invalid_native_settlement_options"
    client.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("version,capability", [(50, 1), (52, None), (52, 0), (52, True)])
async def test_unsupported_running_provider_refuses_before_mutation(
    keyed: tuple[Any, Any, Any], tmp_db: Database, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], version: int, capability: object,
) -> None:
    manager, _, request = await _seed(keyed, tmp_db)
    args, calls, api = _command(manager, request, monkeypatch, health_override={
        "protocol_version": version, "min_protocol_version": 1,
        "capabilities": {"native_send_settlement": capability},
    })
    try:
        with pytest.raises(SystemExit) as refused:
            args.func(args)
        assert refused.value.code == 69
        assert json.loads(capsys.readouterr().out)["reason"] == "unsupported_protocol"
        assert all(method == "GET" for method, _ in calls)
        assert tmp_db.execute_read("SELECT * FROM native_send_settlements") == []
        manager._run_prompt.assert_not_awaited()
    finally:
        api.close()


@pytest.mark.asyncio
async def test_unavailable_existing_client_refuses_without_ensure_or_settlement(
    keyed: tuple[Any, Any, Any], tmp_db: Database, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    manager, _, request = await _seed(keyed, tmp_db)
    args, calls, api = _command(manager, request, monkeypatch)
    client = Mock(side_effect=BridgeConnectionError("synthetic existing service unavailable"))
    monkeypatch.setattr(core, "_get_client", client)
    try:
        with pytest.raises(SystemExit) as refused:
            args.func(args)
        assert refused.value.code == 69
        assert json.loads(capsys.readouterr().out)["reason"] == "native_settlement_unavailable"
        client.assert_called_once_with(ensure=False)
        assert calls == []
        assert tmp_db.execute_read("SELECT * FROM native_send_settlements") == []
        manager._run_prompt.assert_not_awaited()
    finally:
        api.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["lost", "server", "digest", "native", "fence", "turn"])
async def test_post_commit_unknown_response_never_retries_or_replays_prompt(
    keyed: tuple[Any, Any, Any], tmp_db: Database, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], fault: str,
) -> None:
    manager, session, request = await _seed(keyed, tmp_db)
    args, calls, api = _command(manager, request, monkeypatch, fault=fault)
    try:
        with pytest.raises(SystemExit) as unknown:
            args.func(args)
        assert unknown.value.code == 70
        result = json.loads(capsys.readouterr().out)
        assert result["outcome"] == "unknown"
        assert result["reason"] == "native_send_settlement_unknown"
        assert [path for method, path in calls if method == "POST"] == [PATH]
        assert len(tmp_db.execute_read("SELECT * FROM native_send_settlements")) == 1
        assert len(tmp_db.get_turns(session.session_id)) == 1
        manager._run_prompt.assert_not_awaited()
        manager.resume_session.assert_not_awaited()
    finally:
        api.close()


@pytest.mark.asyncio
async def test_drain_winning_the_turn_lock_refuses_settlement(
    keyed: tuple[Any, Any, Any], tmp_db: Database,
) -> None:
    manager, session, request = await _seed(keyed, tmp_db)
    async with session._turn_start_lock:
        pending = asyncio.create_task(settle(manager, session.session_id, request))
        await asyncio.sleep(0)
        manager._draining = True
    with pytest.raises(DaemonDrainingError):
        await pending
    assert tmp_db.execute_read("SELECT * FROM native_send_settlements") == []
    manager._run_prompt.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_authentication_cannot_reach_settlement_owner(
    keyed: tuple[Any, Any, Any], tmp_db: Database,
) -> None:
    manager, _, request = await _seed(keyed, tmp_db)
    app = _app(manager)
    app.add_middleware(BearerAuthMiddleware, token="synthetic-auth-value")
    with TestClient(app) as api:
        response = api.post(PATH, json=request.model_dump())
    assert response.status_code == 401
    assert tmp_db.execute_read("SELECT * FROM native_send_settlements") == []
