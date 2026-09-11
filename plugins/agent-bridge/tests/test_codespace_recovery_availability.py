"""Background recovery must not open SSH to an unavailable CodeSpace."""

from __future__ import annotations

import asyncio
import json
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from agent_bridge.session_host import codespace_transport, protocol
from agent_bridge.session_host.host_index import HostRecord
from agent_bridge.session_manager import Session, SessionManager, SessionStatus
from agent_bridge.transport import SpawnTarget


pytestmark = pytest.mark.asyncio


def _add_session(manager, session_id="example-session"):
    session = Session(
        session_id,
        "example-agent",
        SpawnTarget(
            type="command",
            codespace={"name": "example-space", "repo": "example/repository"},
        ),
    )
    session.acp_session_id = "example-acp"
    session.status = SessionStatus.STOPPED
    manager._sessions[session_id] = session
    record = HostRecord(
        session_id=session_id,
        port=51000,
        host_pid=123,
        child_pid=456,
        protocol_version=protocol.PROTOCOL_VERSION,
        boundary="codespace",
        endpoint={"kind": "codespace", "codespace": "example-space"},
        extra={"remote_authority_v2": True},
    )
    manager._host_index.register(record)
    return session, record


@pytest.fixture
def recovery_context(tmp_db, tmp_path, monkeypatch):
    manager = SessionManager(tmp_db, session_host_state_dir=str(tmp_path))
    real_transport = codespace_transport.CodeSpaceTransport
    transport = SimpleNamespace(
        boundary="codespace",
        is_running=AsyncMock(return_value=False),
        run=AsyncMock(side_effect=AssertionError("unexpected SSH")),
    )
    factory = Mock(return_value=transport)
    monkeypatch.setattr(codespace_transport, "CodeSpaceTransport", factory)
    monkeypatch.setattr("agent_bridge.relay_state.get_live_relay_port", lambda: None)
    attach = AsyncMock(return_value=True)
    monkeypatch.setattr(manager, "_reattach_one", attach)
    session, record = _add_session(manager)
    return SimpleNamespace(
        manager=manager, transport=transport, factory=factory,
        attach=attach, session=session, record=record, real_transport=real_transport,
    )


async def test_recovery_preserves_startup_no_wake_until_available(recovery_context):
    ctx = recovery_context
    manager = ctx.manager
    session_id = ctx.session.session_id

    assert await manager.reattach_session_hosts(remote_recovery_timeout=5.0) == 0
    assert session_id in manager._remote_recovery_skipped
    assert ctx.transport.is_running.await_count == 1

    for _ in range(2):
        assert await manager.recover_disconnected_hosts() == 0
    assert ctx.transport.is_running.await_count == 3
    ctx.attach.assert_not_awaited()
    assert manager._host_index.get(session_id) is not None

    ctx.transport.is_running.return_value = True
    manager._remote_recovery_inconclusive.add(session_id)
    assert await manager.recover_disconnected_hosts() == 1
    assert ctx.transport.is_running.await_count == 4
    ctx.attach.assert_awaited_once()
    assert session_id not in manager._remote_recovery_skipped
    assert session_id not in manager._remote_recovery_inconclusive
    ctx.transport.run.assert_not_awaited()


async def test_recovery_checks_unmarked_venues_once_per_pass(recovery_context):
    ctx = recovery_context
    _add_session(ctx.manager, "second-session")
    assert not ctx.manager._remote_recovery_skipped

    for expected_reads in (1, 2):
        assert await ctx.manager.recover_disconnected_hosts() == 0
        assert ctx.transport.is_running.await_count == expected_reads

    assert ctx.manager._remote_recovery_skipped == {
        ctx.session.session_id, "second-session",
    }
    ctx.attach.assert_not_awaited()
    ctx.transport.run.assert_not_awaited()


async def test_recovery_retries_no_wake_read_after_error(recovery_context):
    ctx = recovery_context
    ctx.transport.is_running.side_effect = [OSError("state unavailable"), True]

    assert await ctx.manager.recover_disconnected_hosts() == 0
    ctx.attach.assert_not_awaited()
    assert await ctx.manager.recover_disconnected_hosts() == 1
    assert ctx.transport.is_running.await_count == 2
    ctx.attach.assert_awaited_once()


async def test_recovery_availability_cancellation_propagates(recovery_context):
    ctx = recovery_context
    ctx.transport.is_running.side_effect = asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await ctx.manager.recover_disconnected_hosts()

    assert not ctx.session._lifecycle_lock.locked()
    ctx.attach.assert_not_awaited()


@pytest.mark.parametrize("guard", ["healthy", "locked"])
async def test_recovery_does_not_probe_healthy_or_locked_sessions(
    recovery_context, guard,
):
    ctx = recovery_context
    if guard == "healthy":
        ctx.session.client = SimpleNamespace(
            is_running=True, host_child_exit_code=None,
        )
    else:
        await ctx.session._lifecycle_lock.acquire()
    try:
        assert await ctx.manager.recover_disconnected_hosts() == 0
    finally:
        if guard == "locked":
            ctx.session._lifecycle_lock.release()

    ctx.factory.assert_not_called()
    ctx.attach.assert_not_awaited()


@pytest.mark.parametrize("identity", ["endpoint", "target", "legacy", "missing"])
async def test_recovery_resolves_venue_before_ssh(recovery_context, identity):
    ctx = recovery_context
    ctx.transport.is_running.return_value = True
    ctx.record.endpoint.pop("codespace")
    ctx.session.target.codespace = None
    if identity == "endpoint":
        ctx.record.endpoint["codespace"] = "persisted-space"
        ctx.session.target.codespace = {"name": "replacement-space"}
        expected_name = "persisted-space"
    elif identity == "target":
        ctx.session.target.codespace = {"name": "structured-space"}
        expected_name = "structured-space"
    elif identity == "legacy":
        ctx.session.target.spawn_command = [
            "agent-codespaces", "ssh", "--stdio", "legacy-space",
            "--remote-cmd", "copilot --acp --stdio",
        ]
        expected_name = "legacy-space"
    ctx.manager._host_index.register(ctx.record)

    if identity == "missing":
        assert await ctx.manager.recover_disconnected_hosts() == 0
        ctx.factory.assert_not_called()
        ctx.attach.assert_not_awaited()
        return

    assert await ctx.manager.recover_disconnected_hosts() == 1
    assert ctx.factory.call_args.args[0] == expected_name
    ctx.attach.assert_awaited_once()


async def test_non_codespace_recovery_does_not_query_availability(recovery_context):
    ctx = recovery_context
    ctx.record.boundary = "ssh"
    ctx.manager._host_index.register(ctx.record)

    assert await ctx.manager.recover_disconnected_hosts() == 1
    ctx.factory.assert_not_called()
    ctx.attach.assert_awaited_once()


@pytest.mark.parametrize("state", ["Available", "Shutdown"])
async def test_recovery_uses_exact_target_lookup_across_accounts(
    recovery_context, monkeypatch, state,
):
    ctx = recovery_context
    monkeypatch.setattr(codespace_transport, "CodeSpaceTransport", ctx.real_transport)
    run = Mock(side_effect=[
        subprocess.CompletedProcess([], 1, "", "gh: Not Found (HTTP 404)"),
        subprocess.CompletedProcess([], 0, "Logged in to github.com account owning-account", ""),
        subprocess.CompletedProcess([], 0, "owner-token", ""),
        subprocess.CompletedProcess(
            [], 0, json.dumps({"name": "example-space", "state": state}), "",
        ),
    ])
    monkeypatch.setattr(codespace_transport.subprocess, "run", run)

    assert await ctx.manager.recover_disconnected_hosts() == (
        1 if state == "Available" else 0
    )
    assert ctx.attach.await_count == (1 if state == "Available" else 0)
    assert run.call_args.args[0][1:3] == ["api", "/user/codespaces/example-space"]
    assert run.call_args.kwargs["env"]["GH_TOKEN"] == "owner-token"
