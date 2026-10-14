"""Generic native identity fences run before ordinary turn admission."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_bridge import __main__ as core
from agent_bridge.models import SessionStatus
from agent_bridge.native_bound_prompt import submit_native_bound_prompt
from agent_bridge.routes.sessions import router
from agent_bridge.send_outcome import SendRefused
from agent_bridge.session_manager import Session
from agent_bridge.session_targeting_cli import _cmd_send
from agent_bridge.transport import SpawnTarget


def prepare(session_manager, tmp_db, mock_acp_client) -> Session:
    session = Session("bridge-a", "test", SpawnTarget(type="local", cwd="."))
    session.status = SessionStatus.IDLE
    session.acp_session_id = "native-a"
    session.client = mock_acp_client
    tmp_db.create_session("bridge-a", "test", None, ".", "local", "idle", 1.0)
    session_manager._sessions["bridge-a"] = session
    session_manager.resume_session = AsyncMock()
    session_manager.handoff_session = AsyncMock()
    session_manager._run_prompt = AsyncMock()
    session_manager._notify_host_reapable = AsyncMock()
    return session


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["replaced", "dead", "stopped", "busy"])
async def test_native_guard_refuses_without_resume_or_turn(
    session_manager, tmp_db, mock_acp_client, fault: str,
) -> None:
    session = prepare(session_manager, tmp_db, mock_acp_client)
    if fault == "replaced":
        session.acp_session_id = "native-other"
    elif fault == "dead":
        session.client.is_running = False
    else:
        session.status = SessionStatus.STOPPED if fault == "stopped" else SessionStatus.RUNNING
    with pytest.raises(ValueError):
        await session_manager.submit_prompt("bridge-a", "work", expected_native_session_id="native-a")
    assert session.turn_count == 0
    session_manager.resume_session.assert_not_awaited()
    session_manager.handoff_session.assert_not_awaited()
    session_manager._run_prompt.assert_not_awaited()


@pytest.mark.asyncio
async def test_guard_checks_identity_after_waiting_for_turn_lock(
    session_manager, tmp_db, mock_acp_client,
) -> None:
    session = prepare(session_manager, tmp_db, mock_acp_client)
    async with session._turn_start_lock:
        pending = asyncio.create_task(session_manager.submit_prompt(
            "bridge-a", "work", expected_native_session_id="native-a",
        ))
        await asyncio.sleep(0)
        session.acp_session_id = "native-other"
    with pytest.raises(ValueError, match="expected_native_mismatch"):
        await pending
    assert session.turn_count == 0


def test_real_http_admission_receipt_and_queue_refusal(
    session_manager, tmp_db, mock_acp_client,
) -> None:
    session = prepare(session_manager, tmp_db, mock_acp_client)
    app = FastAPI()
    app.state.session_manager = session_manager
    app.include_router(router)
    api = TestClient(app)
    body = {"prompt": "work", "expected_native_session_id": "native-a", "no_resume": True}
    path = "/api/v1/sessions/bridge-a/turns/native-bound"
    missing = api.post(path, json={"prompt": "work"})
    assert missing.status_code == 422 and session.turn_count == 0
    queued = api.post(path, json={**body, "queue": True})
    assert queued.status_code == 409 and session.turn_count == 0
    replaced = api.post(path,
                        json={**body, "expected_native_session_id": "native-other"})
    assert replaced.status_code == 409 and session.turn_count == 0
    accepted = api.post(path, json=body)
    assert accepted.status_code == 200
    fixture_path = Path(__file__).resolve().parents[1] / "contract/fixtures/http/current/native-bound-models-generation-33.json"
    assert accepted.json() == json.loads(fixture_path.read_text())["response"]
    assert accepted.json()["native_session_id"] == "native-a"
    assert accepted.json()["session_id"] == "bridge-a"
    assert accepted.json()["queued"] is False and accepted.json()["turn_index"] == 0
    session_manager.resume_session.assert_not_awaited()


def test_old_daemon_refuses_without_post() -> None:
    client = MagicMock()
    client.daemon_supports.return_value = False
    with pytest.raises(SendRefused, match="does not support"):
        submit_native_bound_prompt(client, "bridge-a", "work",
                                   expected_native_session_id="native-a", caller_id=None)
    client._request.assert_not_called()


def test_guarded_cli_skips_live_alias_resume_and_creation(monkeypatch, capsys) -> None:
    client = MagicMock()
    client.daemon_supports.return_value = True
    client._request.side_effect = [
        {"session_id": "bridge-a", "native_session_id": "native-a",
         "queued": False, "turn_index": 0},
    ]
    request = client._request
    monkeypatch.setattr(core, "_get_client", lambda: client)
    monkeypatch.setattr(core, "_caller_id_for", lambda _: None)
    monkeypatch.setattr(core, "_connection_identity", lambda *a: {})
    args = core.build_parser().parse_args([
        "--json", "send", "bridge-a", "work",
        "--expected-native-session-id", "native-a", "--no-wait",
    ])
    _cmd_send(args)
    client.resolve_live_session.assert_not_called()
    client.resume_session.assert_not_called()
    client.submit_prompt.assert_not_called()
    assert request.call_args_list[-1].args[2]["no_resume"] is True
    assert request.call_args_list[-1].args[1].endswith("/turns/native-bound")
    assert '"native_session_id": "native-a"' in capsys.readouterr().out
