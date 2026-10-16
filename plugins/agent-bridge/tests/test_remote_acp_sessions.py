"""Ledger and HTTP admission contracts independent of process launch machinery."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import asyncio
import json
import argparse

import pytest

from agent_bridge.acp_client import AcpClient
from agent_bridge.models import ServiceConfig, SessionStatus, StartSessionRequest
from agent_bridge.remote_acp_config import RemoteAcpTarget
from agent_bridge.remote_acp_sessions import connect_remote_session
from agent_bridge.remote_acp_transport import RemoteTransport
from agent_bridge.session_manager import session_manager_from_config
from agent_bridge.routes.sessions import start_session
from agent_bridge.client import BridgeClient, BridgeClientError
from agent_bridge.models import AnswerPermissionRequest
from agent_bridge.routes.sessions import answer_permission
from acp.schema import PermissionOption, ToolCallUpdate
from agent_bridge.transport import SpawnTarget, spawn


@pytest.fixture
def remote_manager(tmp_db, monkeypatch):
    target = RemoteAcpTarget(
        endpoint="https://example.com/acp", transport="streamable-http",
        auth={"source": "git-credential", "identity": "example"},
    )
    calls = []

    async def start(client, spec, **kwargs):
        calls.append("connect")
        client._remote_transport = RemoteTransport(spec)
        client._remote_answers = {}
        client._remote_negotiated = True
        client._remote_can_load = True
        client._host_mode = True
        client._host_transport_alive = True
        client._host_closer = client._remote_transport.close
        client._connection = SimpleNamespace(close=AsyncMock())

    async def new(client, **kwargs):
        calls.append("new")
        client._acp_session_id = "server-owned-session"
        return client._acp_session_id

    async def load(client, *, cwd, session_id, suppress_replay):
        assert suppress_replay is False
        calls.append(("load", session_id))
        client._acp_session_id = session_id

    monkeypatch.setattr(AcpClient, "start_remote", start)
    monkeypatch.setattr(AcpClient, "new_session", new)
    monkeypatch.setattr(AcpClient, "load_session", load)
    manager = session_manager_from_config(
        tmp_db, ServiceConfig(remote_acp={"example": target}),
    )
    return manager, calls


@pytest.mark.asyncio
async def test_remote_new_stop_resume_persists_no_workspace_or_process(remote_manager):
    manager, calls = remote_manager
    session = await connect_remote_session(manager, "example", cwd="/remote/workspace")
    assert session.status == SessionStatus.IDLE and session.pid is None
    assert session.target.worktree_id is None
    row = manager.db.get_session(session.session_id)
    assert row["target_type"] == "remote-acp"
    assert row["acp_session_id"] == "server-owned-session"
    assert not row["background_recovery_enabled"]
    assert "Authorization" not in row["target_json"]
    assert json.loads(row["target_json"])["remote_acp_capabilities"]["load_session"] is True
    with pytest.raises(ValueError, match="process resync"):
        await manager.resync_session(session.session_id)
    await manager.stop_session(session.session_id)
    await manager.resume_session(session.session_id)
    assert calls == ["connect", "new", "connect", ("load", "server-owned-session")]
    await manager.stop_session(session.session_id)
    manager._remote_acp_targets["example"] = RemoteAcpTarget(
        endpoint="https://other.example.com/acp", transport="streamable-http",
        auth={"source": "git-credential", "identity": "example"},
    )
    with pytest.raises(ValueError, match="changed"):
        await manager.resume_session(session.session_id)


@pytest.mark.asyncio
async def test_route_resolves_remote_before_workspace_or_agent_provider(remote_manager):
    manager, calls = remote_manager
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        session_manager=manager, resolver=None, ready=True,
    )))
    result = await start_session(
        StartSessionRequest(agent="remote-acp:example", target_dir="/remote/workspace"),
        request,
    )
    assert result.status == SessionStatus.IDLE
    assert calls == ["connect", "new"]
    with pytest.raises(Exception) as refused:
        await start_session(
            StartSessionRequest(agent="remote-acp:example", worktree_id="not-a-host-workspace"),
            request,
        )
    assert refused.value.status_code == 422
    await manager.stop_session(result.session_id)


@pytest.mark.asyncio
async def test_ambiguous_remote_session_ids_do_not_cross_endpoints(remote_manager):
    manager, _ = remote_manager
    one = await connect_remote_session(manager, "example")
    two = await connect_remote_session(manager, "example")
    assert manager.get_session("server-owned-session") is None
    assert manager.get_session(one.session_id) is one
    assert manager.get_session(two.session_id) is two
    await manager.stop_session(one.session_id)
    await manager.stop_session(two.session_id)


def test_client_refuses_old_daemon_before_remote_start_or_live_answer():
    client = BridgeClient.__new__(BridgeClient)
    client._daemon_proto = (0, 0)
    calls = []
    client._request = lambda *args, **kwargs: calls.append((args, kwargs)) or {}
    client.daemon_supports = lambda version: False
    with pytest.raises(BridgeClientError) as refused:
        client.start_session(agent="remote-acp:example")
    assert refused.value.status == 426
    with pytest.raises(BridgeClientError) as refused:
        client.answer_ask_user("bridge-session", "tool", {}, request_id="live-token")
    assert refused.value.status == 426
    with pytest.raises(BridgeClientError) as refused:
        client.answer_permission("bridge-session", "live-token", "offered")
    assert refused.value.status == 426
    assert calls == []
    client.daemon_supports = lambda version: True
    client.start_session(agent="remote-acp:example", remote_session_id="server-session")
    assert calls[-1][0][2]["remote_session_id"] == "server-session"
    client.answer_ask_user("bridge-session", "tool", {}, request_id="live-token")
    assert calls[-1][1]["body"]["request_id"] == "live-token"
    client.answer_permission("bridge-session", "live-token", "offered")
    assert calls[-1][1]["body"] == {"request_id": "live-token", "option_id": "offered"}


@pytest.mark.asyncio
async def test_operator_relay_route_is_correlated_and_cross_session_safe(remote_manager):
    manager, _ = remote_manager
    one = await connect_remote_session(manager, "example")
    two = await connect_remote_session(manager, "example")
    pending = asyncio.create_task(one.client._remote_permission(
        [PermissionOption(option_id="reject/native", name="Reject", kind="reject_once")],
        "server-owned-session", ToolCallUpdate(tool_call_id="native/decision"), {},
    ))
    await asyncio.sleep(0)
    token = next(iter(one.client._remote_answers))
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(session_manager=manager)))
    answer = AnswerPermissionRequest(request_id=token, option_id="reject/native")
    with pytest.raises(Exception) as refused:
        await answer_permission(two.session_id, answer, request)
    assert refused.value.status_code == 409
    assert not pending.done()
    assert await answer_permission(one.session_id, answer, request) == {"status": "answered"}
    assert (await pending).outcome.option_id == "reject/native"
    with pytest.raises(Exception) as refused:
        await answer_permission(one.session_id, answer, request)
    assert refused.value.status_code == 409
    await manager.stop_session(one.session_id)
    await manager.stop_session(two.session_id)


@pytest.mark.asyncio
async def test_process_dispatcher_refuses_remote_target_before_local_spawn(monkeypatch):
    from agent_bridge import transport
    local = AsyncMock(side_effect=AssertionError("must not spawn"))
    monkeypatch.setattr(transport, "spawn_local", local)
    with pytest.raises(ValueError, match="not spawn targets"):
        await spawn(SpawnTarget(type="remote-acp", cwd="/server/workspace"))
    local.assert_not_called()
    assert "remote_acp" not in json.loads(SpawnTarget(type="local").to_json())
    assert "remote_acp_capabilities" not in json.loads(SpawnTarget(type="local").to_json())
    legacy = SpawnTarget("ssh", "/workspace", "example.com")
    assert legacy.cwd == "/workspace" and legacy.host == "example.com"


def test_headless_cli_permission_answer_requires_explicit_correlation(monkeypatch):
    from agent_bridge import session_lifecycle_cli
    client = SimpleNamespace(answer_permission=Mock())
    monkeypatch.setattr(session_lifecycle_cli, "_core", lambda: SimpleNamespace(
        _get_client=lambda: client, _caller_id_for=lambda args: None,
    ))
    args = argparse.Namespace(
        session_id="bridge-session", option_id="reject/opaque", request_id="live-token",
        decline=False, cancel=False, fields=[], content_json=None, tool_call_id=None,
    )
    session_lifecycle_cli._cmd_answer(args)
    client.answer_permission.assert_called_once_with("bridge-session", "live-token", "reject/opaque")
    args.request_id = None
    with pytest.raises(SystemExit) as refused:
        session_lifecycle_cli._cmd_answer(args)
    assert refused.value.code == 2
    assert client.answer_permission.call_count == 1
