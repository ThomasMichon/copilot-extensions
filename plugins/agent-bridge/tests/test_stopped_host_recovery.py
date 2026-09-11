"""Explicit stop contains provider recovery across maintenance and restart."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from agent_bridge.session_host import protocol
from agent_bridge.session_host.host_index import HostRecord
from agent_bridge.session_manager import Session, SessionManager, SessionStatus
from agent_bridge.transport import SpawnTarget


pytestmark = pytest.mark.asyncio


@pytest.fixture
def context(tmp_db, tmp_path, monkeypatch):
    manager = SessionManager(tmp_db, session_host_state_dir=str(tmp_path / "hosts"))
    target = SpawnTarget(
        type="command",
        codespace={"name": "example-space", "repo": "example/repository"},
    )
    session = Session("example-session", "example-agent", target)
    session.status = SessionStatus.RUNNING
    session.acp_session_id = "example-acp"
    manager._sessions[session.session_id] = session
    tmp_db.create_session(
        session.session_id, session.name, None, ".", "command",
        session.status.value, time.time(), target_json=target.to_json(),
    )
    tmp_db.update_session_acp_id(session.session_id, session.acp_session_id)
    record = HostRecord(
        session_id=session.session_id, port=51000,
        host_pid=123, child_pid=456,
        protocol_version=protocol.PROTOCOL_VERSION,
        boundary="codespace",
        endpoint={"kind": "codespace", "codespace": "example-space"},
        extra={"remote_authority_v2": True},
    )
    manager._host_index.register(record)
    spawner = SimpleNamespace(
        can_inspect_without_wake=AsyncMock(return_value=True),
        recover_record=AsyncMock(return_value=record),
    )
    factory = Mock(return_value=spawner)
    monkeypatch.setattr(
        "agent_bridge.session_host.codespace_transport.build_codespace_spawner",
        factory,
    )
    monkeypatch.setattr("agent_bridge.relay_state.get_live_relay_port", lambda: None)
    monkeypatch.setattr(manager, "_quiesce_session", AsyncMock())
    available = AsyncMock(return_value=True)
    monkeypatch.setattr(manager, "_codespace_host_available", available)
    attach = AsyncMock(return_value=False)
    monkeypatch.setattr(manager, "_reattach_one", attach)
    return SimpleNamespace(
        manager=manager, session=session, record=record, factory=factory,
        spawner=spawner, available=available, attach=attach, db=tmp_db,
        state_dir=str(tmp_path / "hosts"),
    )


@pytest.mark.parametrize("boundary", ["codespace", "container", "ssh"])
@pytest.mark.parametrize("restart_status", [None, "stopped", "running", "starting"])
async def test_stop_suppresses_repeated_recovery_and_restart(
    context, monkeypatch, boundary, restart_status,
):
    ctx = context
    ctx.record.boundary = boundary
    ctx.record.resume_on_reattach = True
    ctx.manager._host_index.register(ctx.record)
    ctx.session.restart_status = restart_status
    assert await ctx.manager.recover_disconnected_hosts() == 0
    ctx.attach.assert_awaited_once()

    await ctx.manager.stop_session(ctx.session.session_id)
    ctx.attach.reset_mock()
    ctx.available.reset_mock()
    for _ in range(3):
        assert await ctx.manager.recover_disconnected_hosts() == 0
        assert await ctx.manager.reattach_session_hosts() == 0
    ctx.factory.assert_not_called()
    ctx.available.assert_not_awaited()
    ctx.attach.assert_not_awaited()

    restarted = SessionManager(ctx.db, session_host_state_dir=ctx.state_dir)
    monkeypatch.setattr(restarted, "_reattach_one", ctx.attach)
    monkeypatch.setattr(restarted, "_codespace_host_available", ctx.available)
    assert await restarted.reattach_session_hosts() == 0
    assert await restarted.recover_disconnected_hosts() == 0
    ctx.factory.assert_not_called()
    ctx.available.assert_not_awaited()
    ctx.attach.assert_not_awaited()
    assert restarted._host_index.get(ctx.session.session_id) is not None
    assert restarted.get_session(ctx.session.session_id).acp_session_id == "example-acp"


@pytest.mark.parametrize("prior", ["running", "idle", "starting"])
@pytest.mark.parametrize("shutdown", [False, True])
@pytest.mark.parametrize("cancel_turn", [False, True])
async def test_restart_intent_survives_repeated_rehydrate(
    context, monkeypatch, prior, shutdown, cancel_turn,
):
    ctx = context
    ctx.session.status = SessionStatus(prior)
    ctx.db.update_session_status(ctx.session.session_id, prior, time.time())
    if shutdown:
        await ctx.manager.stop_session(
            ctx.session.session_id, for_restart=True, cancel_turn=cancel_turn,
        )
    for _ in range(2):
        restarted = SessionManager(ctx.db, session_host_state_dir=ctx.state_dir)
        session = restarted.get_session(ctx.session.session_id)
        assert session.status == SessionStatus.STOPPED
        assert session.restart_status == prior
        monkeypatch.setattr(restarted, "_reattach_one", ctx.attach)
        assert await restarted.reattach_session_hosts() == 0
        assert ctx.attach.await_args.kwargs["send_resume"] == (prior == "starting")
        assert ctx.db.get_session(session.session_id)["restart_status"] == prior
    assert ctx.spawner.recover_record.await_count == 2
    ctx.attach.assert_awaited()


async def test_legacy_stopped_row_and_explicit_lazy_resume(context, monkeypatch):
    ctx = context
    ctx.db.update_session_status(ctx.session.session_id, "stopped", time.time())
    restarted = SessionManager(ctx.db, session_host_state_dir=ctx.state_dir)
    assert await restarted.reattach_session_hosts() == 0
    ctx.factory.assert_not_called()

    async def attach(_record, session, **kwargs):
        assert session._lifecycle_lock.locked()
        session.status = kwargs["new_status"]
        return True

    monkeypatch.setattr(restarted, "_reattach_one", attach)
    resumed = await restarted.resume_session(ctx.session.session_id)
    assert resumed.status == SessionStatus.IDLE
    ctx.spawner.recover_record.assert_awaited_once_with(ctx.session.session_id)
    assert resumed.acp_session_id == "example-acp"


@pytest.mark.parametrize("path", ["heartbeat", "startup", "authority"])
@pytest.mark.parametrize("success", [False, True])
async def test_stop_waits_for_inflight_recovery_then_prevents_rearming(
    context, path, success,
):
    ctx = context
    entered, release = asyncio.Event(), asyncio.Event()

    async def blocked(*args, **kwargs):
        entered.set()
        await release.wait()
        if path == "authority":
            return ctx.record if success else None
        if success:
            ctx.session.status = kwargs["new_status"]
        return success

    if path == "authority":
        ctx.spawner.recover_record.side_effect = blocked
    else:
        ctx.attach.side_effect = blocked
    recovery = asyncio.create_task(
        ctx.manager.recover_disconnected_hosts()
        if path == "heartbeat" else ctx.manager.reattach_session_hosts()
    )
    await asyncio.wait_for(entered.wait(), 1)
    stop = asyncio.create_task(ctx.manager.stop_session(ctx.session.session_id))
    await asyncio.sleep(0)
    assert not stop.done()
    release.set()
    await asyncio.wait_for(asyncio.gather(recovery, stop), 2)
    assert ctx.session.status == SessionStatus.STOPPED
    assert not ctx.session._lifecycle_lock.locked()

    calls = (
        ctx.attach.await_count, ctx.available.await_count,
        ctx.spawner.recover_record.await_count,
        ctx.spawner.can_inspect_without_wake.await_count,
    )
    for _ in range(3):
        assert await ctx.manager.recover_disconnected_hosts() == 0
        assert await ctx.manager.reattach_session_hosts() == 0
    assert calls == (
        ctx.attach.await_count, ctx.available.await_count,
        ctx.spawner.recover_record.await_count,
        ctx.spawner.can_inspect_without_wake.await_count,
    )


async def test_startup_rechecks_stop_after_collecting_candidates(context):
    ctx = context

    async def stop_before_inspect():
        await ctx.manager.stop_session(ctx.session.session_id)

    stop = asyncio.create_task(stop_before_inspect())
    assert await ctx.manager.reattach_session_hosts() == 0
    await stop
    ctx.spawner.can_inspect_without_wake.assert_not_awaited()
    ctx.spawner.recover_record.assert_not_awaited()
    ctx.attach.assert_not_awaited()


async def test_cancelled_startup_releases_authority_recovery_locks(context):
    ctx = context
    entered = asyncio.Event()

    async def blocked():
        entered.set()
        await asyncio.Event().wait()

    ctx.spawner.can_inspect_without_wake.side_effect = blocked
    recovery = asyncio.create_task(ctx.manager.reattach_session_hosts())
    await asyncio.wait_for(entered.wait(), 1)
    assert ctx.session._lifecycle_lock.locked()
    recovery.cancel()
    with pytest.raises(asyncio.CancelledError):
        await recovery
    assert not ctx.session._lifecycle_lock.locked()
    await asyncio.wait_for(ctx.manager.stop_session(ctx.session.session_id), 1)
    ctx.spawner.recover_record.assert_not_awaited()
