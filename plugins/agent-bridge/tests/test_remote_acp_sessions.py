"""Ledger and HTTP admission contracts independent of process launch machinery."""

from __future__ import annotations

from types import SimpleNamespace
from pathlib import Path
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
from agent_bridge.remote_acp_transport import RemoteTransportError
from agent_bridge.session_manager import session_manager_from_config
from agent_bridge.routes.sessions import start_session
from agent_bridge.client import BridgeClient, BridgeClientError
from agent_bridge.models import AnswerPermissionRequest
from agent_bridge.routes.sessions import answer_permission
from acp.schema import PermissionOption, ToolCallUpdate
from agent_bridge.transport import SpawnTarget, spawn
from agent_bridge.attention_current import current_owned_attention
from agent_bridge.attention_wait import evaluate_owned_attention


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


def _remote_request(manager):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        session_manager=manager, resolver=None, ready=True,
    )))


@pytest.mark.asyncio
async def test_route_url_and_name_reuse_same_canonical_conversation_and_resume(remote_manager):
    manager, calls = remote_manager
    request = _remote_request(manager)
    first = await start_session(StartSessionRequest(
        agent="https://example.com/acp", caller_id="caller",
    ), request)
    again = await start_session(StartSessionRequest(
        agent="remote-acp:example", caller_id="caller",
    ), request)
    assert first.session_id == again.session_id
    assert calls == ["connect", "new"]
    await manager.stop_session(first.session_id)
    resumed = await start_session(StartSessionRequest(
        agent="https://example.com/acp", caller_id="caller",
    ), request)
    assert resumed.session_id == first.session_id and resumed.status == SessionStatus.IDLE
    assert calls == ["connect", "new", "connect", ("load", "server-owned-session")]
    await manager.stop_session(first.session_id)


@pytest.mark.asyncio
async def test_route_reuse_rechecks_current_credential_approval(remote_manager):
    from fastapi import HTTPException

    manager, calls = remote_manager
    request = _remote_request(manager)
    body = StartSessionRequest(agent="remote-acp:example", caller_id="caller")
    first = await start_session(body, request)
    manager._remote_credential_policy = SimpleNamespace(
        bind=Mock(side_effect=PermissionError("Approval withdrawn")),
    )
    with pytest.raises(HTTPException) as refused:
        await start_session(body, request)
    assert refused.value.status_code == 422 and calls == ["connect", "new"]
    assert manager.get_session(first.session_id).status == SessionStatus.IDLE
    await manager.stop_session(first.session_id)


@pytest.mark.asyncio
async def test_reuse_resolves_selected_project_and_current_binding_before_busy_conflict(remote_manager):
    from fastapi import HTTPException

    manager, calls = remote_manager
    original = manager._remote_acp_targets["example"]
    bindings = {
        "one": original,
        "two": original.model_copy(update={"endpoint": "https://second.example.com/acp"}),
    }
    manager._remote_acp_catalog = SimpleNamespace(
        resolve=lambda address, *, project: ("example", bindings[project]),
    )
    request = _remote_request(manager)
    first = await start_session(StartSessionRequest(
        agent="remote-acp:example", caller_id="caller", sender_repo="one",
    ), request)
    first_session = manager.get_session(first.session_id)
    first_session.status = SessionStatus.RUNNING
    second = await start_session(StartSessionRequest(
        agent="remote-acp:example", caller_id="caller", sender_repo="two",
    ), request)
    assert second.session_id != first.session_id
    assert manager.get_session(second.session_id).target.remote_acp["endpoint"] == bindings["two"].endpoint
    with pytest.raises(HTTPException) as busy:
        await start_session(StartSessionRequest(
            agent="remote-acp:example", caller_id="caller", sender_repo="one",
        ), request)
    assert busy.value.status_code == 409
    assert busy.value.detail["existing_session_id"] == first.session_id
    bindings["one"] = bindings["two"]
    with pytest.raises(HTTPException) as refused:
        await start_session(StartSessionRequest(
            agent="remote-acp:example", caller_id="caller", sender_repo="one",
        ), request)
    assert refused.value.status_code == 422
    assert first_session.status == SessionStatus.RUNNING
    assert calls == ["connect", "new", "connect", "new"]
    fresh = await start_session(StartSessionRequest(
        agent="remote-acp:example", caller_id="caller", sender_repo="one", force_new=True,
    ), request)
    assert fresh.session_id not in {first.session_id, second.session_id}
    for session in list(manager._sessions.values()):
        await manager.stop_session(session.session_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("options", [
    {"caller_id": None}, {"caller_id": "caller", "force_new": True},
])
async def test_route_does_not_reuse_anonymous_or_explicit_new_requests(remote_manager, options):
    manager, calls = remote_manager
    request = _remote_request(manager)
    first = await start_session(StartSessionRequest(agent="remote-acp:example", **options), request)
    second = await start_session(StartSessionRequest(agent="remote-acp:example", **options), request)
    assert first.session_id != second.session_id and calls.count("new") == 2
    await manager.stop_session(first.session_id)
    await manager.stop_session(second.session_id)


@pytest.mark.asyncio
async def test_concurrent_reuse_reserves_starting_before_handshake(remote_manager, monkeypatch):
    manager, calls = remote_manager
    original = AcpClient.start_remote
    entered, release = asyncio.Event(), asyncio.Event()

    async def delayed(client, *args, **kwargs):
        entered.set()
        await release.wait()
        await original(client, *args, **kwargs)

    monkeypatch.setattr(AcpClient, "start_remote", delayed)
    request = _remote_request(manager)
    body = StartSessionRequest(agent="remote-acp:example", caller_id="caller")
    pending = asyncio.create_task(start_session(body, request))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        second = await asyncio.wait_for(start_session(body, request), 2)
        assert second.status == SessionStatus.STARTING and len(manager._sessions) == 1
        release.set()
        first = await asyncio.wait_for(pending, 2)
        assert first.session_id == second.session_id and calls == ["connect", "new"]
    finally:
        release.set()
        await asyncio.gather(pending, return_exceptions=True)
        for session in list(manager._sessions.values()):
            await manager.stop_session(session.session_id)


@pytest.mark.asyncio
async def test_concurrent_explicit_load_reuses_requested_native_id_before_confirmation(remote_manager, monkeypatch):
    manager, calls = remote_manager
    original = AcpClient.load_session
    entered, release = asyncio.Event(), asyncio.Event()

    async def delayed(client, **kwargs):
        entered.set()
        await release.wait()
        await original(client, **kwargs)

    monkeypatch.setattr(AcpClient, "load_session", delayed)
    request = _remote_request(manager)
    body = StartSessionRequest(
        agent="remote-acp:example", caller_id="caller", remote_session_id="saved",
    )
    pending = asyncio.create_task(start_session(body, request))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        session = next(iter(manager._sessions.values()))
        assert session.acp_session_id is None
        second = await asyncio.wait_for(start_session(body, request), 2)
        assert second.session_id == session.session_id and second.status == SessionStatus.STARTING
        assert len(manager._sessions) == 1 and calls == ["connect"]
        release.set()
        first = await asyncio.wait_for(pending, 2)
        assert first.session_id == second.session_id
        assert calls == ["connect", ("load", "saved")]
    finally:
        release.set()
        await asyncio.gather(pending, return_exceptions=True)
        for session in list(manager._sessions.values()):
            await manager.stop_session(session.session_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["resume", "stop"])
async def test_caller_reuse_shares_explicit_lifecycle_lock(remote_manager, operation):
    import threading

    manager, calls = remote_manager
    session = await connect_remote_session(manager, "example", caller_id="caller")
    if operation == "resume":
        await manager.stop_session(session.session_id)
    lock = session._lifecycle_lock
    competing = asyncio.Event()
    entries = 0

    class ObservedLock:
        async def __aenter__(self):
            nonlocal entries
            entries += 1
            if entries == 2:
                competing.set()
            return await lock.__aenter__()

        async def __aexit__(self, *args):
            return await lock.__aexit__(*args)

    session._lifecycle_lock = ObservedLock()
    entered, release = threading.Event(), threading.Event()
    resolve = manager._remote_acp_catalog.resolve

    def paused(*args, **kwargs):
        if not entered.is_set():
            entered.set()
            assert release.wait(5)
        return resolve(*args, **kwargs)

    if operation == "resume":
        manager._remote_acp_catalog.resolve = paused
        explicit = asyncio.create_task(manager.resume_session(session.session_id, drain=False))
    else:
        shutdown = session.client.shutdown

        async def paused_shutdown():
            entered.set()
            assert await asyncio.to_thread(release.wait, 5)
            await shutdown()

        session.client.shutdown = paused_shutdown
        explicit = asyncio.create_task(manager.stop_session(session.session_id))
    ordinary = None
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        ordinary = asyncio.create_task(start_session(StartSessionRequest(
            agent="remote-acp:example", caller_id="caller",
        ), _remote_request(manager)))
        await asyncio.wait_for(competing.wait(), 2)
        assert not ordinary.done() and calls == ["connect", "new"]
        release.set()
        resumed, reused = await asyncio.wait_for(asyncio.gather(explicit, ordinary), 3)
        assert resumed is (session if operation == "resume" else None)
        assert reused.session_id == session.session_id
        assert session.status == SessionStatus.IDLE
        assert calls == ["connect", "new", "connect", ("load", "server-owned-session")]
    finally:
        release.set()
        await asyncio.gather(explicit, *([ordinary] if ordinary else []), return_exceptions=True)
        session._lifecycle_lock = lock
        await manager.stop_session(session.session_id)


@pytest.mark.asyncio
async def test_concurrent_stopped_reuse_reserves_before_old_client_shutdown(remote_manager):
    manager, calls = remote_manager
    session = await connect_remote_session(manager, "example", caller_id="caller")
    session.client._remote_lost()
    session.status = SessionStatus.STOPPED
    original = session.client.shutdown
    entered, release = asyncio.Event(), asyncio.Event()

    async def delayed():
        entered.set()
        await release.wait()
        await original()

    session.client.shutdown = delayed
    request = _remote_request(manager)
    body = StartSessionRequest(agent="remote-acp:example", caller_id="caller")
    pending = asyncio.create_task(start_session(body, request))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        second = await asyncio.wait_for(start_session(body, request), 2)
        assert second.session_id == session.session_id and second.status == SessionStatus.STARTING
        release.set()
        first = await asyncio.wait_for(pending, 2)
        assert first.session_id == session.session_id
        assert calls == ["connect", "new", "connect", ("load", "server-owned-session")]
    finally:
        release.set()
        await asyncio.gather(pending, return_exceptions=True)
        await manager.stop_session(session.session_id)


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
async def test_drain_during_policy_lookup_does_not_create_starting_ledger_row(remote_manager):
    import threading

    manager, calls = remote_manager
    entered, release = threading.Event(), threading.Event()
    catalog = manager._remote_acp_catalog
    original = catalog.resolve

    def resolve(*args, **kwargs):
        entered.set()
        assert release.wait(2)
        return original(*args, **kwargs)

    catalog.resolve = resolve
    pending = asyncio.create_task(connect_remote_session(manager, "example"))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        manager._draining = True
        release.set()
        with pytest.raises(ValueError, match="draining"):
            await pending
        assert calls == [] and manager.list_sessions() == []
        assert manager._active_acquisitions == 0
        assert manager.db.list_sessions() == []
    finally:
        release.set()
        await asyncio.gather(pending, return_exceptions=True)


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


@pytest.mark.asyncio
async def test_request_scoped_form_attention_retains_live_token_and_empty_native_id(remote_manager):
    manager, _ = remote_manager
    session = await connect_remote_session(manager, "example")
    wire = session.client._remote_transport
    wire.sessions.add(session.acp_session_id)
    wire.outgoing_session({
        "jsonrpc": "2.0", "id": 7, "method": "session/prompt",
        "params": {"sessionId": session.acp_session_id},
    })
    pending = asyncio.create_task(session.client._remote_elicitation(
        "Choose", SimpleNamespace(request_id=7, requested_schema={}), {},
    ))
    await asyncio.sleep(0)
    try:
        current = current_owned_attention(session, session.session_id)
        assert current.reason.value == "input_required" and current.availability == "available"
        attention = evaluate_owned_attention(
            db=manager.db, session=session, requested_ref=session.session_id,
            reasons=["input_required"],
        )
        value = attention.reference.value
        assert attention.reference.availability == "available"
        assert value["tool_call_id"] == "" and value["native_request_id"] == 7
        assert session.client.resolve_elicitation(
            "", {"choice": "explicit"}, request_id=value["request_id"],
        )
        await pending
        assert current_owned_attention(session, session.session_id).reason is None
    finally:
        if not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        await manager.stop_session(session.session_id)


@pytest.mark.asyncio
async def test_remote_usage_reports_pressure_without_process_handoff(remote_manager):
    manager, _ = remote_manager
    manager._auto_handoff.enabled = True
    session = await connect_remote_session(manager, "example")
    manager.handoff_session = AsyncMock(side_effect=AssertionError("unsupported successor"))
    session.client.send_prompt = AsyncMock(return_value={
        "response_text": "reply", "thought_text": "", "tool_calls": [],
        "stop_reason": "end_turn", "error": None,
    })
    manager._handle_usage_update(session, {"context_size": 100, "context_used": 99})
    assert session.context_pct == 99 and not manager._auto_handoff_eligible(session)
    await manager.submit_prompt(session.session_id, "fresh")
    await asyncio.wait_for(session._prompt_task, 5)
    manager.handoff_session.assert_not_called()
    session.client.send_prompt.assert_awaited_once_with("fresh")
    await manager.stop_session(session.session_id)


@pytest.mark.asyncio
async def test_process_idle_reaper_preserves_non_loadable_remote_conversation(remote_manager):
    manager, _ = remote_manager
    session = await connect_remote_session(manager, "example")
    session.client._remote_can_load = False
    session.turn_count = 1
    manager._idle_reap_ttl_seconds = 1
    session.updated_at = 1
    manager.stop_session = AsyncMock(wraps=manager.stop_session)
    assert await manager.sweep_idle_sessions(now=1000) == 0
    manager.stop_session.assert_not_awaited()
    assert session.client.is_running and session.status == SessionStatus.IDLE
    await manager.stop_session(session.session_id)


@pytest.mark.asyncio
async def test_stop_during_remote_startup_does_not_resurrect_the_session(remote_manager, monkeypatch):
    manager, calls = remote_manager
    original = AcpClient.start_remote
    entered, release = asyncio.Event(), asyncio.Event()

    async def delayed(client, *args, **kwargs):
        await original(client, *args, **kwargs)
        entered.set()
        await release.wait()

    monkeypatch.setattr(AcpClient, "start_remote", delayed)
    pending = asyncio.create_task(connect_remote_session(manager, "example"))
    await entered.wait()
    session = next(iter(manager._sessions.values()))
    await manager.stop_session(session.session_id)
    release.set()
    with pytest.raises(RemoteTransportError, match="closed or superseded"):
        await pending
    assert session.client is None and session.status == SessionStatus.STOPPED
    assert manager.db.get_session(session.session_id)["status"] == "stopped"
    assert "new" not in calls


@pytest.mark.asyncio
async def test_accepted_session_id_survives_stream_start_failure(remote_manager, monkeypatch):
    manager, calls = remote_manager
    from agent_bridge import telemetry
    records = []
    monkeypatch.setattr(telemetry, "emit", records.append)

    async def accepted_then_failed(client, **kwargs):
        client._acp_session_id = "accepted-server-session"
        raise RemoteTransportError("Synthetic session stream failed")

    monkeypatch.setattr(AcpClient, "new_session", accepted_then_failed)
    with pytest.raises(RemoteTransportError, match="stream failed"):
        await connect_remote_session(manager, "example")
    session = next(iter(manager._sessions.values()))
    assert manager.db.get_session(session.session_id)["acp_session_id"] == "accepted-server-session"
    assert any(record.get("acp_session_id") == "accepted-server-session" for record in records)
    await manager.resume_session(session.session_id)
    assert calls[-1] == ("load", "accepted-server-session")
    await manager.stop_session(session.session_id)


@pytest.mark.asyncio
async def test_remote_start_telemetry_uses_learned_server_session_identity(remote_manager, monkeypatch):
    from agent_bridge import telemetry

    records = []
    monkeypatch.setattr(telemetry, "emit", records.append)
    manager, _ = remote_manager
    session = await connect_remote_session(manager, "example")
    assert any(record.get("acp_session_id") == session.acp_session_id for record in records)
    await manager.stop_session(session.session_id)


@pytest.mark.asyncio
async def test_remote_resume_attachment_blocks_clean_drain_before_old_client_shutdown(remote_manager):
    manager, calls = remote_manager
    session = await connect_remote_session(manager, "example")
    client = session.client
    client._remote_lost()
    session.status = SessionStatus.STOPPED
    shutdown = client.shutdown
    entered, release = asyncio.Event(), asyncio.Event()

    async def paused():
        entered.set()
        await release.wait()
        await shutdown()

    client.shutdown = paused
    pending = asyncio.create_task(manager.resume_session(session.session_id, drain=False))
    await entered.wait()
    try:
        assert manager._active_acquisitions > 0
        result = await manager.drain(timeout=0)
        assert not result["clean"]
        release.set()
        with pytest.raises(ValueError, match="draining"):
            await pending
        assert manager._active_acquisitions == 0
        assert calls.count("connect") == 1
    finally:
        release.set()
        manager.set_draining(False)
        if not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        await manager.stop_session(session.session_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["failure", "cancellation"])
async def test_remote_attachment_accounting_releases_on_failed_old_client_shutdown(remote_manager, outcome):
    manager, _ = remote_manager
    session = await connect_remote_session(manager, "example")
    client = session.client
    client._remote_lost()
    session.status = SessionStatus.STOPPED
    entered, release = asyncio.Event(), asyncio.Event()

    async def paused():
        entered.set()
        await release.wait()
        raise RuntimeError("Synthetic old-client shutdown failed")

    shutdown = client.shutdown
    client.shutdown = paused
    pending = asyncio.create_task(manager.resume_session(session.session_id, drain=False))
    await entered.wait()
    if outcome == "cancellation":
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
    else:
        release.set()
        with pytest.raises(RuntimeError, match="Synthetic"):
            await pending
    assert manager._active_acquisitions == 0
    client.shutdown = shutdown
    await manager.stop_session(session.session_id)


@pytest.mark.asyncio
async def test_rehydration_ignores_historical_progress_and_freshness(remote_manager):
    fixture = json.loads((
        Path(__file__).resolve().parents[1] / "contract" / "fixtures" / "http"
        / "current" / "remote-acp-progress-generation-38.json"
    ).read_text(encoding="utf-8"))
    manager, _ = remote_manager
    session = await connect_remote_session(manager, "example")
    live = fixture["events"][0]["data"]
    session.event_log.append("agent_message", live)
    manager._capture_progress(session, "agent_message", live)
    await manager.stop_session(session.session_id)
    historical = fixture["events"][1]["data"]
    session.event_log.append("agent_message", historical)
    session.last_output_at = 123
    manager._capture_progress(session, "agent_message", historical)
    assert session.last_output_at == 123 and session.progress == fixture["expected"]["progress"]
    restored = session_manager_from_config(
        manager.db, ServiceConfig(remote_acp=manager._remote_acp_targets),
    )
    assert restored._sessions[session.session_id].progress == fixture["expected"]["progress"]


@pytest.mark.asyncio
async def test_end_during_old_client_shutdown_prevents_untracked_remote_resume(remote_manager):
    manager, calls = remote_manager
    session = await connect_remote_session(manager, "example")
    client = session.client
    client._remote_lost()
    session.status = SessionStatus.STOPPED
    shutdown = client.shutdown
    entered, release = asyncio.Event(), asyncio.Event()
    invocations = 0

    async def pause_resume_only():
        nonlocal invocations
        invocations += 1
        if invocations == 1:
            entered.set()
            await release.wait()
        await shutdown()

    client.shutdown = pause_resume_only
    pending = asyncio.create_task(manager.resume_session(session.session_id, drain=False))
    await entered.wait()
    await asyncio.wait_for(manager.end_session(session.session_id), 5)
    assert session.session_id not in manager._sessions
    assert manager.db.get_session(session.session_id) is None
    release.set()
    with pytest.raises(RemoteTransportError, match="ended or was superseded"):
        await asyncio.wait_for(pending, 5)
    assert calls.count("connect") == 1 and not any(isinstance(call, tuple) for call in calls)
    assert manager._active_acquisitions == 0
    assert session.status == SessionStatus.ENDED


def test_cli_status_request_scoped_form_has_token_without_empty_tool_flag(monkeypatch, capsys):
    from agent_bridge import service_start_cli

    client = Mock()
    client.get_session_status.return_value = {
        "pending_ask_user": [
            {"tool_call_id": "", "request_id": token, "message": "Explicit choice"}
            for token in ("live-one", "live-two")
        ],
    }
    monkeypatch.setattr(service_start_cli, "_core", lambda: SimpleNamespace(
        _get_client=lambda **kwargs: client,
        _caller_id_for=lambda args: None,
        _short_dt=lambda value: "",
        _liveness_line=lambda value: "",
    ))
    service_start_cli._cmd_session_status(argparse.Namespace(
        session_id="bridge-session", caller_id=None, json=False, steps=0,
    ))
    output = capsys.readouterr().out
    assert "--request-id live-one`" in output and "--request-id live-two`" in output
    assert "--tool-call-id" not in output
