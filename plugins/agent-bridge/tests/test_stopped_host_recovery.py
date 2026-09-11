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
    assert ctx.manager._host_index.get(ctx.session.session_id).resume_on_reattach is False
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


@pytest.mark.parametrize("kind", ["missing", "dead", "child_exit"])
async def test_stop_serializes_destructive_recovery_settlement(
    context, monkeypatch, kind,
):
    from agent_bridge.session_host.spawner import RemoteHostDeadError

    ctx = context
    entered, release = asyncio.Event(), asyncio.Event()
    settled = []

    async def blocked(*_args, **kwargs):
        if kwargs.get("strict"):
            return
        entered.set()
        await release.wait()
        assert ctx.session._lifecycle_lock.locked()
        settled.append(kind)

    if kind == "child_exit":
        ctx.session.client = SimpleNamespace(
            host_child_exit_code=0, shutdown=blocked,
        )
        monkeypatch.setattr(
            ctx.manager, "_reap_host_record",
            lambda *_args: ctx.manager._host_index.remove(ctx.session.session_id),
        )
        operation = ctx.manager.recover_disconnected_hosts()
    else:
        if kind == "dead":
            ctx.spawner.recover_record.side_effect = RemoteHostDeadError("child exited")
        else:
            ctx.spawner.recover_record.return_value = None
        monkeypatch.setattr(ctx.manager, "_drop_forward", blocked)
        operation = ctx.manager.reattach_session_hosts()
    recovery = asyncio.create_task(operation)
    await asyncio.wait_for(entered.wait(), 1)
    stop = asyncio.create_task(ctx.manager.stop_session(ctx.session.session_id))
    await asyncio.sleep(0)
    assert not stop.done()
    release.set()
    await asyncio.wait_for(asyncio.gather(recovery, stop), 2)
    assert settled == [kind]
    assert ctx.session.status == SessionStatus.STOPPED
    assert ctx.manager._host_index.get(ctx.session.session_id) is None
    ctx.factory.reset_mock()
    for _ in range(2):
        assert await ctx.manager.reattach_session_hosts() == 0
        assert await ctx.manager.recover_disconnected_hosts() == 0
    ctx.factory.assert_not_called()
    assert settled == [kind]


@pytest.mark.parametrize("for_restart", [False, True])
async def test_stop_clears_redeploy_nudge_only_for_ordinary_stop(context, for_restart):
    ctx = context
    ctx.manager._host_index.set_resume_flag(ctx.session.session_id, True)
    await ctx.manager.stop_session(ctx.session.session_id, for_restart=for_restart)
    assert (
        ctx.manager._host_index.get(ctx.session.session_id).resume_on_reattach
        is for_restart
    )


@pytest.mark.parametrize("inflight", [False, True])
@pytest.mark.parametrize("for_restart", [False, True])
async def test_stop_contains_independent_relay_monitor(
    context, monkeypatch, inflight, for_restart,
):
    from ssh_manager.config_sources import SSHConfig
    from ssh_manager.relay_channel import SupervisedRelayForward

    ctx = context
    relay = SupervisedRelayForward(SSHConfig(host_alias="example-host"), 51001)
    ticks = asyncio.Queue()
    retry_entered, retry_waiting = asyncio.Event(), asyncio.Event()
    calls = []

    async def establish():
        calls.append("provider")
        if len(calls) == 1:
            return
        retry_entered.set()
        if inflight:
            await asyncio.Event().wait()
        raise ConnectionError("provider unavailable")

    async def sleep(_delay):
        if retry_entered.is_set():
            retry_waiting.set()
        await ticks.get()

    monkeypatch.setattr(relay, "establish", establish)
    monkeypatch.setattr(relay, "_sleep", sleep)
    forward = SimpleNamespace(cancel=AsyncMock())
    ctx.manager._relays[ctx.session.session_id] = [relay]
    ctx.manager._forwards[ctx.session.session_id] = forward
    release_ownership = Mock()
    monkeypatch.setattr(ctx.manager, "_release_container_lock", release_ownership)
    await relay.start()
    monitor = relay._monitor_task
    try:
        ticks.put_nowait(None)
        await asyncio.wait_for(retry_entered.wait(), 1)
        if not inflight:
            await asyncio.wait_for(retry_waiting.wait(), 1)
        await asyncio.wait_for(
            ctx.manager.stop_session(ctx.session.session_id, for_restart=for_restart), 1,
        )
        if for_restart:
            assert not monitor.done()
            forward.cancel.assert_not_awaited()
            assert ctx.manager._relays[ctx.session.session_id] == [relay]
        else:
            assert monitor.done()
            assert relay._monitor_task is None
            forward.cancel.assert_awaited_once()
            assert ctx.session.session_id not in ctx.manager._relays
            assert ctx.session.session_id not in ctx.manager._forwards
            count = len(calls)
            for _ in range(3):
                ticks.put_nowait(None)
                await asyncio.sleep(0)
                assert await ctx.manager.recover_disconnected_hosts() == 0
            assert len(calls) == count
        assert ctx.manager._host_index.get(ctx.session.session_id) is not None
        release_ownership.assert_not_called()
    finally:
        await relay.stop()


@pytest.mark.parametrize("failure", ["relay", "forward"])
async def test_stop_surfaces_transport_teardown_failure_and_retains_retry_handle(
    context, failure,
):
    ctx = context
    relay = SimpleNamespace(stop=AsyncMock())
    forward = SimpleNamespace(cancel=AsyncMock())
    ctx.manager._relays[ctx.session.session_id] = [relay]
    ctx.manager._forwards[ctx.session.session_id] = forward
    failing = relay.stop if failure == "relay" else forward.cancel
    failing.side_effect = RuntimeError("teardown failed")
    with pytest.raises(RuntimeError, match="teardown failed"):
        await ctx.manager.stop_session(ctx.session.session_id)
    assert ctx.db.get_session(ctx.session.session_id)["status"] == "running"
    assert ctx.manager._forwards[ctx.session.session_id] is forward
    if failure == "relay":
        assert ctx.manager._relays[ctx.session.session_id] == [relay]
    failing.side_effect = None
    await ctx.manager.stop_session(ctx.session.session_id)
    assert ctx.session.status == SessionStatus.STOPPED
    assert ctx.session.session_id not in ctx.manager._relays
    assert ctx.session.session_id not in ctx.manager._forwards
    assert ctx.manager._host_index.get(ctx.session.session_id) is not None


@pytest.mark.parametrize("disposition", ["REAP_STOPPED", "FORCE_REAP"])
async def test_startup_version_mux_cannot_reap_during_stop(
    context, monkeypatch, disposition,
):
    from agent_bridge.session_host import version_mux

    ctx = context
    entered, release = asyncio.Event(), asyncio.Event()

    async def quiesce(*_args, **_kwargs):
        entered.set()
        await release.wait()

    monkeypatch.setattr(ctx.manager, "_quiesce_session", quiesce)
    monkeypatch.setattr(ctx.manager, "_recover_remote_host_records", AsyncMock(return_value=0))
    plan = Mock(return_value=SimpleNamespace(
        disposition=getattr(version_mux.HostDisposition, disposition),
        reason="incompatible host",
    ))
    monkeypatch.setattr(version_mux, "plan_host", plan)
    reap = Mock()
    monkeypatch.setattr(ctx.manager, "_reap_host_record", reap)
    stop = asyncio.create_task(ctx.manager.stop_session(ctx.session.session_id))
    await asyncio.wait_for(entered.wait(), 1)
    assert await ctx.manager.reattach_session_hosts() == 0
    plan.assert_not_called()
    reap.assert_not_called()
    release.set()
    await asyncio.wait_for(stop, 1)
    assert await ctx.manager.reattach_session_hosts() == 0
    reap.assert_not_called()
    assert ctx.manager._host_index.get(ctx.session.session_id) is not None


@pytest.mark.parametrize("stop_first", [False, True])
async def test_redeploy_cannot_rearm_resume_nudge_after_stop(
    context, monkeypatch, stop_first,
):
    ctx = context
    ctx.manager._cancel_turns_on_redeploy = True
    entered, release = asyncio.Event(), asyncio.Event()
    client = SimpleNamespace(
        cancel_prompt=AsyncMock(),
        has_active_background_tasks=False,
        active_background_tasks=[],
    )
    ctx.session.client = client

    async def blocked(*_args, **_kwargs):
        entered.set()
        await release.wait()

    if stop_first:
        monkeypatch.setattr(ctx.manager, "_quiesce_session", blocked)
        first = asyncio.create_task(ctx.manager.stop_session(ctx.session.session_id))
    else:
        client.cancel_prompt.side_effect = blocked
        first = asyncio.create_task(ctx.manager.graceful_cancel_for_redeploy(settle_timeout=0))
    await asyncio.wait_for(entered.wait(), 1)
    second = asyncio.create_task(
        ctx.manager.graceful_cancel_for_redeploy(settle_timeout=0)
        if stop_first else ctx.manager.stop_session(ctx.session.session_id)
    )
    await asyncio.sleep(0)
    assert not second.done()
    release.set()
    await asyncio.wait_for(asyncio.gather(first, second), 2)
    assert ctx.session.status == SessionStatus.STOPPED
    assert ctx.manager._host_index.get(ctx.session.session_id).resume_on_reattach is False
    assert await ctx.manager.reattach_session_hosts() == 0
    ctx.attach.assert_not_awaited()


@pytest.mark.parametrize("fails", [False, True])
async def test_stop_waits_for_initial_launch(
    tmp_db, tmp_path, monkeypatch, mock_acp_client, fails,
):
    manager = SessionManager(tmp_db, session_host_state_dir=str(tmp_path / "hosts"))
    entered, release = asyncio.Event(), asyncio.Event()

    async def connect(*_args, **_kwargs):
        entered.set()
        await release.wait()
        if fails:
            raise ConnectionError("launch failed")
        return mock_acp_client, "example-acp"

    monkeypatch.setattr(manager, "_connect_via_session_host", connect)
    monkeypatch.setattr(manager, "_detach_host", AsyncMock())
    monkeypatch.setattr("agent_bridge.session_manager._cleanup_worktree", AsyncMock())
    start = asyncio.create_task(manager.start_session(
        SpawnTarget(type="local", cwd=str(tmp_path)),
    ))
    await asyncio.wait_for(entered.wait(), 1)
    session = manager.list_sessions()[0]
    stop = asyncio.create_task(manager.stop_session(session.session_id))
    await asyncio.sleep(0)
    assert not stop.done()
    release.set()
    await asyncio.wait_for(asyncio.gather(start, stop), 2)
    assert session.status == SessionStatus.STOPPED
    assert session.client is None
    assert not session._lifecycle_lock.locked()
    assert tmp_db.get_session(session.session_id)["status"] == "stopped"


@pytest.mark.parametrize("status,reapable", [("idle", True), ("running", False)])
@pytest.mark.parametrize("for_restart", [False, True])
async def test_stop_preserves_existing_idle_vs_busy_detach_policy(
    context, monkeypatch, mock_acp_client, status, reapable, for_restart,
):
    ctx = context
    monkeypatch.setattr(
        ctx.manager, "_quiesce_session",
        SessionManager._quiesce_session.__get__(ctx.manager),
    )
    monkeypatch.setattr("agent_bridge.session_manager._cleanup_worktree", AsyncMock())
    host_client = SimpleNamespace(detach=AsyncMock())
    mock_acp_client.session_host_client = host_client
    ctx.session.client = mock_acp_client
    ctx.session.status = SessionStatus(status)
    await ctx.manager.stop_session(ctx.session.session_id, for_restart=for_restart)
    host_client.detach.assert_awaited_once_with(reapable)
    assert ctx.manager._host_index.get(ctx.session.session_id) is not None
    assert ctx.session.acp_session_id == "example-acp"


@pytest.mark.parametrize(
    "failure", ["missing_id", "reattach", "provider", "cleanup", "replay", "recreate"],
)
async def test_failed_explicit_resume_clears_restart_provenance(
    context, monkeypatch, failure,
):
    from agent_bridge.session_host.spawner import RemoteSpawnCleanupPendingError

    ctx = context
    ctx.session.status = SessionStatus.STOPPED
    ctx.session.restart_status = "running"
    ctx.db.update_session_status(
        ctx.session.session_id, "stopped", time.time(), restart_status="running",
    )
    attach = AsyncMock(return_value=False)
    refresh = AsyncMock()
    replace = AsyncMock(side_effect=RuntimeError("resume failed"))
    monkeypatch.setattr(ctx.manager, "_try_reattach_live_host", attach)
    monkeypatch.setattr(ctx.manager, "_refresh_provider_target", refresh)
    monkeypatch.setattr(ctx.manager, "_resume_via_new_remote_host", replace)
    monkeypatch.setattr("agent_bridge.session_manager._MAX_RESUME_ROUNDS", 1)
    if failure == "missing_id":
        ctx.session.acp_session_id = None
    elif failure == "reattach":
        attach.side_effect = RuntimeError("reattach failed")
    elif failure == "provider":
        refresh.side_effect = RuntimeError("provider failed")
    elif failure == "cleanup":
        replace.side_effect = RemoteSpawnCleanupPendingError("cleanup pending")
    with pytest.raises(RuntimeError):
        await ctx.manager.resume_session(
            ctx.session.session_id, allow_recreate=failure == "recreate",
        )
    assert ctx.session.status == SessionStatus.STOPPED
    assert ctx.session.restart_status is None
    row = ctx.db.get_session(ctx.session.session_id)
    assert row["status"] == "stopped"
    assert row["restart_status"] is None
    for _ in range(2):
        assert await ctx.manager.recover_disconnected_hosts() == 0
        assert await ctx.manager.reattach_session_hosts() == 0
    ctx.factory.assert_not_called()
    ctx.available.assert_not_awaited()
    ctx.attach.assert_not_awaited()
