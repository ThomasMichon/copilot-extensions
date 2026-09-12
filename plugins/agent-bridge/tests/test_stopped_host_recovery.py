"""Explicit stop contains provider recovery across maintenance and restart."""

from __future__ import annotations

import asyncio
import threading
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
    ctx.manager._host_index.set_resume_flag(ctx.session.session_id, True)
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
    assert restarted._host_index.get(ctx.session.session_id).resume_on_reattach is False
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
        assert monitor.done()
        assert relay._monitor_task is None
        forward.cancel.assert_awaited_once()
        assert ctx.session.session_id not in ctx.manager._relays
        assert ctx.session.session_id not in ctx.manager._forwards
        assert ctx.manager._background_recovery_allowed(ctx.session) is for_restart
        if not for_restart:
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
    assert ctx.db.get_session(ctx.session.session_id)["status"] == "failed"
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
    ctx.manager._host_index.set_resume_flag(ctx.session.session_id, True)
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
    assert ctx.manager._host_index.get(ctx.session.session_id).resume_on_reattach is False
    for _ in range(2):
        assert await ctx.manager.recover_disconnected_hosts() == 0
        assert await ctx.manager.reattach_session_hosts() == 0
    ctx.factory.assert_not_called()
    ctx.available.assert_not_awaited()
    ctx.attach.assert_not_awaited()


async def test_restart_releases_forward_before_successor_rebinds_descriptor(
    context, monkeypatch,
):
    ctx = context
    bound = True

    async def cancel():
        nonlocal bound
        bound = False

    old = SimpleNamespace(cancel=AsyncMock(side_effect=cancel))
    ctx.manager._forwards[ctx.session.session_id] = old
    await ctx.manager.stop_session(ctx.session.session_id, for_restart=True)
    old.cancel.assert_awaited_once()
    assert not bound
    restarted = SessionManager(ctx.db, session_host_state_dir=ctx.state_dir)
    record = restarted._host_index.get(ctx.session.session_id)
    assert record is not None

    async def establish():
        nonlocal bound
        assert not bound, "old frontend still owns the persisted local port"
        bound = True
        return 51000

    successor = SimpleNamespace(
        establish=AsyncMock(side_effect=establish), cancel=AsyncMock(),
    )
    monkeypatch.setattr(
        "agent_bridge.session_host.endpoints.forward_from_endpoint",
        lambda _endpoint: successor,
    )
    await restarted._ensure_forward(record)
    successor.establish.assert_awaited_once()
    assert restarted._forwards[ctx.session.session_id] is successor
    assert restarted._background_recovery_allowed(restarted.get_session(ctx.session.session_id))


async def test_frontend_exit_closes_disconnected_channels_without_changing_intent(
    context, monkeypatch,
):
    ctx = context
    assert ctx.session.client is None
    relay = SimpleNamespace(stop=AsyncMock())
    forward = SimpleNamespace(cancel=AsyncMock())
    ctx.manager._relays[ctx.session.session_id] = [relay]
    ctx.manager._forwards[ctx.session.session_id] = forward
    release_ownership = Mock()
    monkeypatch.setattr(ctx.manager, "_release_container_lock", release_ownership)
    await ctx.manager.close_frontend_transports()
    relay.stop.assert_awaited_once()
    forward.cancel.assert_awaited_once()
    assert ctx.manager._relays == {}
    assert ctx.manager._forwards == {}
    assert ctx.session.status == SessionStatus.RUNNING
    assert ctx.db.get_session(ctx.session.session_id)["status"] == "running"
    assert ctx.manager._host_index.get(ctx.session.session_id) is not None
    release_ownership.assert_not_called()


async def test_frontend_exit_reports_failure_but_closes_other_channels(context):
    ctx = context
    relay = SimpleNamespace(stop=AsyncMock(side_effect=RuntimeError("stop failed")))
    forward = SimpleNamespace(cancel=AsyncMock())
    ctx.manager._relays[ctx.session.session_id] = [relay]
    ctx.manager._forwards["other-session"] = forward
    with pytest.raises(RuntimeError, match="Frontend transport cleanup failed"):
        await ctx.manager.close_frontend_transports()
    forward.cancel.assert_awaited_once()
    assert ctx.manager._forwards == {}
    assert ctx.manager._relays[ctx.session.session_id] == [relay]
    assert not ctx.session._lifecycle_lock.locked()


@pytest.mark.parametrize("kind", ["dead", "stranded"])
@pytest.mark.parametrize("stopping", [False, True])
async def test_host_sweeps_preserve_stopped_or_lifecycle_owned_records(
    context, monkeypatch, kind, stopping,
):
    from agent_bridge.session_host import version_mux

    ctx = context
    ctx.record.boundary = "local"
    ctx.manager._host_index.register(ctx.record)
    monkeypatch.setattr(ctx.manager, "_rec_host_alive", lambda _rec: kind != "dead")
    plan = Mock(return_value=SimpleNamespace(
        disposition=version_mux.HostDisposition.FORCE_REAP,
        reason="expired incompatible host",
    ))
    monkeypatch.setattr(version_mux, "plan_host", plan)
    monkeypatch.setattr(ctx.manager, "_rec_child_alive", lambda _rec: True)
    loop_thread = threading.get_ident()

    def assert_off_loop(*_args):
        assert threading.get_ident() != loop_thread
        assert ctx.session._turn_start_lock.locked()
        assert ctx.session._lifecycle_lock.locked()

    reap = Mock(side_effect=assert_off_loop)
    monkeypatch.setattr(ctx.manager, "_terminate_local_host_processes", reap)

    def forget(record):
        assert threading.get_ident() == loop_thread
        ctx.manager._host_index.remove(record.session_id)

    monkeypatch.setattr(ctx.manager, "_forget_host_record", forget)
    entered, release = asyncio.Event(), asyncio.Event()

    async def quiesce(*_args, **_kwargs):
        entered.set()
        await release.wait()

    if stopping:
        monkeypatch.setattr(ctx.manager, "_quiesce_session", quiesce)
        stop = asyncio.create_task(ctx.manager.stop_session(ctx.session.session_id))
        await asyncio.wait_for(entered.wait(), 1)
    else:
        await ctx.manager.stop_session(ctx.session.session_id)
    try:
        ctx.manager._prune_dead_hosts()
        assert await ctx.manager.sweep_stranded_hosts() == 0
        plan.assert_not_called()
        reap.assert_not_called()
        assert ctx.manager._host_index.get(ctx.session.session_id) is not None
    finally:
        if stopping:
            release.set()
            await asyncio.wait_for(stop, 1)
    assert await ctx.manager.sweep_stranded_hosts() == 0
    ctx.session.status = SessionStatus.RUNNING
    count = await ctx.manager.sweep_stranded_hosts()
    if kind == "dead":
        assert ctx.manager._host_index.get(ctx.session.session_id) is None
    else:
        assert count == 1
        reap.assert_called_once()


@pytest.mark.parametrize("cancel_stop", [False, True])
async def test_stop_waits_for_its_pending_remote_reap_only(context, monkeypatch, cancel_stop):
    from dataclasses import replace

    ctx = context
    entered, release = asyncio.Event(), asyncio.Event()
    other_release = asyncio.Event()

    async def remote_reap(record, _endpoint):
        if record.session_id == ctx.session.session_id:
            entered.set()
            await release.wait()
        else:
            await other_release.wait()
        return True

    monkeypatch.setattr(ctx.manager, "_remote_reap", remote_reap)
    ctx.manager._schedule_remote_reap(ctx.record, "prior maintenance")
    other = replace(ctx.record, session_id="other-session")
    ctx.manager._schedule_remote_reap(other, "independent maintenance")
    other_task = next(iter(ctx.manager._remote_reaps_by_session[other.session_id]))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        stop = asyncio.create_task(ctx.manager.stop_session(ctx.session.session_id))
        await asyncio.sleep(0)
        assert not stop.done()
        if cancel_stop:
            stop.cancel()
            await asyncio.sleep(0)
            assert not stop.done()
            assert all(
                not task.cancelled()
                for task in ctx.manager._remote_reaps_by_session[ctx.session.session_id]
            )
        release.set()
        if cancel_stop:
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(stop, 1)
            await ctx.manager.stop_session(ctx.session.session_id)
        else:
            await asyncio.wait_for(stop, 1)
        assert ctx.session.status == SessionStatus.STOPPED
        assert ctx.session.session_id not in ctx.manager._remote_reaps_by_session
        assert not other_task.done()
    finally:
        other_release.set()
        await asyncio.wait_for(other_task, 1)
    assert ctx.manager._remote_reap_tasks == set()
    assert ctx.manager._remote_reaps_by_session == {}


async def test_cancelled_local_sweep_keeps_lock_until_worker_settles(context, monkeypatch):
    from agent_bridge.session_host import version_mux

    ctx = context
    ctx.record.boundary = "local"
    ctx.manager._host_index.register(ctx.record)
    monkeypatch.setattr(ctx.manager, "_rec_host_alive", lambda _rec: True)
    monkeypatch.setattr(ctx.manager, "_rec_child_alive", lambda _rec: True)
    monkeypatch.setattr(version_mux, "plan_host", lambda **_kwargs: SimpleNamespace(
        disposition=version_mux.HostDisposition.FORCE_REAP, reason="expired",
    ))
    loop = asyncio.get_running_loop()
    entered = asyncio.Event()
    release = threading.Event()

    def reap(*_args):
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(timeout=2)
        assert ctx.session._lifecycle_lock.locked()

    monkeypatch.setattr(ctx.manager, "_terminate_local_host_processes", reap)
    monkeypatch.setattr(
        ctx.manager, "_forget_host_record",
        lambda rec: ctx.manager._host_index.remove(rec.session_id),
    )
    sweep = asyncio.create_task(ctx.manager.sweep_stranded_hosts())
    try:
        await asyncio.wait_for(entered.wait(), 1)
        sweep.cancel()
        await asyncio.sleep(0)
        stop = asyncio.create_task(ctx.manager.stop_session(ctx.session.session_id))
        await asyncio.sleep(0)
        assert not stop.done()
        assert not sweep.done()
        sweep.cancel()
        await asyncio.sleep(0)
        assert not sweep.done()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await sweep
    await asyncio.wait_for(stop, 1)
    assert ctx.session.status == SessionStatus.STOPPED


@pytest.mark.parametrize("live_turn", [False, True])
async def test_wedged_reconciliation_rechecks_after_stop(
    context, monkeypatch, mock_acp_client, live_turn,
):
    ctx = context
    entered, release = asyncio.Event(), asyncio.Event()
    turn_done = asyncio.Event()

    async def quiesce(*_args, **_kwargs):
        entered.set()
        await release.wait()

    monkeypatch.setattr(ctx.manager, "_quiesce_session", quiesce)
    monkeypatch.setattr(
        ctx.session, "liveness_state",
        lambda _now: "stalled" if live_turn else "disconnected",
    )
    spawn = AsyncMock(side_effect=AssertionError("unexpected recovery spawn"))
    monkeypatch.setattr("agent_bridge.session_manager.spawn", spawn)
    if live_turn:
        ctx.session.client = mock_acp_client
        ctx.session._prompt_task = asyncio.create_task(turn_done.wait())
        ctx.session.last_output_at = time.time() - 100
        ctx.manager._live_stall_interrupt_after_s = 1
    stop = asyncio.create_task(ctx.manager.stop_session(ctx.session.session_id))
    await asyncio.wait_for(entered.wait(), 1)
    recovery = asyncio.create_task(ctx.manager.reconcile_wedged_running())
    await asyncio.sleep(0)
    assert not recovery.done()
    release.set()
    try:
        await asyncio.wait_for(stop, 1)
        assert await asyncio.wait_for(recovery, 1) == 0
        assert ctx.session.status == SessionStatus.STOPPED
        spawn.assert_not_awaited()
        mock_acp_client.cancel_prompt.assert_not_awaited()
    finally:
        turn_done.set()
        if live_turn:
            await ctx.session._prompt_task


async def test_stop_serializes_with_already_admitted_prompt(
    context, monkeypatch, mock_acp_client,
):
    ctx = context
    ctx.session.status = SessionStatus.IDLE
    ctx.session.client = mock_acp_client
    mock_acp_client.session_host_client = None
    entered, release = asyncio.Event(), asyncio.Event()

    async def prompt(_text):
        await asyncio.Event().wait()

    mock_acp_client.send_prompt.side_effect = prompt

    async def submit(session_id, text):
        entered.set()
        await release.wait()
        return await SessionManager._submit_prompt_locked(ctx.manager, session_id, text)

    monkeypatch.setattr(ctx.manager, "_submit_prompt_locked", submit)
    monkeypatch.setattr(
        ctx.manager, "_quiesce_session", SessionManager._quiesce_session.__get__(ctx.manager),
    )
    monkeypatch.setattr("agent_bridge.session_manager._cleanup_worktree", AsyncMock())
    send = asyncio.create_task(ctx.manager.submit_prompt(ctx.session.session_id, "work"))
    await asyncio.wait_for(entered.wait(), 1)
    stop = asyncio.create_task(ctx.manager.stop_session(ctx.session.session_id))
    await asyncio.sleep(0)
    assert not stop.done()
    release.set()
    await asyncio.wait_for(asyncio.gather(send, stop), 1)
    assert ctx.session._prompt_task.done()
    assert ctx.session.status == SessionStatus.STOPPED
    assert ctx.db.get_session(ctx.session.session_id)["status"] == "stopped"


@pytest.mark.parametrize("outcome", ["healthy", "transport_lost", "child_exited"])
async def test_startup_resume_nudge_respects_lock_order_and_transport_loss(
    context, monkeypatch, mock_acp_client, outcome,
):
    ctx = context
    ctx.manager._host_index.set_resume_flag(ctx.session.session_id, True)
    monkeypatch.setattr(
        ctx.manager, "_reattach_one", SessionManager._reattach_one.__get__(ctx.manager),
    )
    monkeypatch.setattr(ctx.manager, "_recover_remote_host_records", AsyncMock(return_value=0))
    monkeypatch.setattr(ctx.manager, "_ensure_forward", AsyncMock())
    monkeypatch.setattr(ctx.manager, "_reap_host_record", Mock())
    sock = SimpleNamespace(attach=AsyncMock(), close=AsyncMock(), send_status=AsyncMock())
    streams = SimpleNamespace(
        reader=Mock(), writer=Mock(),
        child_exit_code=0 if outcome == "child_exited" else None,
        aclose=AsyncMock(),
    )
    monkeypatch.setattr(
        "agent_bridge.session_host.client.SessionHostClient.connect", AsyncMock(return_value=sock),
    )
    monkeypatch.setattr(
        "agent_bridge.session_host.acp_adapter.open_acp_streams", AsyncMock(return_value=streams),
    )
    mock_acp_client.start_streams = AsyncMock()
    mock_acp_client.is_running = outcome != "transport_lost"
    monkeypatch.setattr("agent_bridge.session_manager.AcpClient", lambda **_kwargs: mock_acp_client)
    if outcome == "child_exited":
        async def old_driver():
            try:
                await asyncio.Event().wait()
            finally:
                ctx.session.status = SessionStatus.IDLE
                ctx.db.update_session_status(ctx.session.session_id, "idle", time.time())

        ctx.session._prompt_task = asyncio.create_task(old_driver())
        await asyncio.sleep(0)
    assert await asyncio.wait_for(ctx.manager.reattach_session_hosts(), 1) == (
        0 if outcome == "child_exited" else 1
    )
    if outcome == "child_exited":
        assert ctx.session._prompt_task.done()
        assert ctx.session.status == SessionStatus.STOPPED
        assert ctx.db.get_session(ctx.session.session_id)["status"] == "stopped"
    elif outcome == "transport_lost":
        assert ctx.session._prompt_task is None
        assert ctx.manager._host_index.get(ctx.session.session_id).resume_on_reattach is True
    else:
        await asyncio.wait_for(ctx.session._prompt_task, 1)
        mock_acp_client.send_prompt.assert_awaited_once_with("Resume")
    assert not ctx.session._turn_start_lock.locked()
    assert not ctx.session._lifecycle_lock.locked()


async def test_child_exit_settlement_joins_late_prompt_state_writer(
    context, monkeypatch, mock_acp_client,
):
    ctx = context
    cancelled, release = asyncio.Event(), asyncio.Event()

    async def old_driver():
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            await release.wait()
            ctx.session.status = SessionStatus.IDLE
            ctx.db.update_session_status(ctx.session.session_id, "idle", time.time())
            raise

    ctx.session._prompt_task = asyncio.create_task(old_driver())
    await asyncio.sleep(0)
    mock_acp_client.host_child_exit_code = 0
    ctx.session.client = mock_acp_client
    monkeypatch.setattr(ctx.manager, "_reap_host_record", Mock())
    recovery = asyncio.create_task(ctx.manager.recover_disconnected_hosts())
    await asyncio.wait_for(cancelled.wait(), 1)
    assert not recovery.done()
    release.set()
    assert await asyncio.wait_for(recovery, 1) == 0
    assert ctx.session._prompt_task.done()
    assert ctx.session.status == SessionStatus.STOPPED
    assert ctx.db.get_session(ctx.session.session_id)["status"] == "stopped"


@pytest.mark.parametrize("result", ["live", "missing"])
@pytest.mark.parametrize("change", ["replace", "remove", "stop_resume"])
async def test_remote_authority_results_cannot_overwrite_newer_lifecycle(
    context, monkeypatch, result, change,
):
    from dataclasses import replace

    ctx = context
    replacement = replace(ctx.record, port=52000, nonce="replacement")
    stale = replace(ctx.record, port=53000, nonce="stale")
    operations = []

    async def reattach(session):
        session.status = SessionStatus.IDLE
        ctx.db.update_session_status(session.session_id, "idle", time.time())
        return True

    monkeypatch.setattr(ctx.manager, "_try_reattach_live_host", reattach)

    async def stop_resume():
        await ctx.manager.stop_session(ctx.session.session_id)
        await ctx.manager.resume_session(ctx.session.session_id)

    async def inspect(_session_id):
        if change == "replace":
            ctx.manager._host_index.register(replacement)
        elif change == "remove":
            ctx.manager._host_index.remove(ctx.session.session_id)
        else:
            operations.append(asyncio.create_task(stop_resume()))
        return stale if result == "live" else None

    ctx.spawner.recover_record.side_effect = inspect
    assert await ctx.manager._recover_remote_host_records(background=True) == 0
    if operations:
        await asyncio.gather(*operations)
    expected = None if change == "remove" else (
        replacement if change == "replace" else ctx.record
    )
    assert ctx.manager._host_index.get(ctx.session.session_id) == expected
    if change == "stop_resume":
        assert ctx.session.status == SessionStatus.IDLE
    ctx.spawner.recover_record.assert_awaited_once()


@pytest.mark.parametrize("change", ["replace", "remove", "resume", "healthy"])
@pytest.mark.parametrize("driver", ["startup", "heartbeat"])
async def test_host_iterator_rechecks_later_candidates(context, monkeypatch, change, driver):
    from dataclasses import replace

    ctx = context
    other = Session("second-session", "second-agent", ctx.session.target)
    other.status = SessionStatus.RUNNING
    other.acp_session_id = "second-acp"
    ctx.manager._sessions[other.session_id] = other
    ctx.db.create_session(
        other.session_id, other.name, None, ".", "command", "running", time.time(),
        target_json=other.target.to_json(),
    )
    ctx.db.update_session_acp_id(other.session_id, other.acp_session_id)
    record = replace(ctx.record, session_id=other.session_id)
    ctx.manager._host_index.register(record)
    if change == "healthy":
        other.client = SimpleNamespace(is_running=True, host_child_exit_code=None)
    calls = []

    async def resume(session):
        session.status = SessionStatus.IDLE
        session.client = SimpleNamespace(is_running=False, host_child_exit_code=None)
        return True

    async def attach(candidate, _session, **_kwargs):
        calls.append(candidate.session_id)
        if candidate.session_id == ctx.session.session_id:
            if change == "replace":
                ctx.manager._host_index.register(replace(record, nonce="replacement"))
            elif change == "remove":
                ctx.manager._host_index.remove(other.session_id)
            elif change == "resume":
                await ctx.manager.stop_session(other.session_id)
                await ctx.manager.resume_session(other.session_id)
        return True

    monkeypatch.setattr(ctx.manager, "_try_reattach_live_host", resume)
    monkeypatch.setattr(ctx.manager, "_recover_remote_host_records", AsyncMock(return_value=0))
    monkeypatch.setattr(ctx.manager, "_reattach_one", attach)
    recover = (
        ctx.manager.reattach_session_hosts if driver == "startup"
        else ctx.manager.recover_disconnected_hosts
    )
    assert await recover() == 1
    assert calls == [ctx.session.session_id]


async def test_explicit_end_owns_authority_recovery_before_stop(context, monkeypatch):
    from agent_bridge.session_manager import RemoteHostRecoveryPendingError

    ctx = context
    ctx.manager._host_index.remove(ctx.session.session_id)
    ctx.manager._remote_recovery_inconclusive.add(ctx.session.session_id)
    entered, release = asyncio.Event(), asyncio.Event()

    async def recover(**_kwargs):
        assert ctx.session._turn_start_lock.locked()
        assert ctx.session._lifecycle_lock.locked()
        entered.set()
        await release.wait()
        raise RuntimeError("authority unavailable")

    monkeypatch.setattr(ctx.manager, "_recover_remote_host_records", recover)
    end = asyncio.create_task(ctx.manager.end_session(ctx.session.session_id))
    await asyncio.wait_for(entered.wait(), 1)
    stop = asyncio.create_task(ctx.manager.stop_session(ctx.session.session_id))
    await asyncio.sleep(0)
    assert not stop.done()
    release.set()
    with pytest.raises(RemoteHostRecoveryPendingError):
        await asyncio.wait_for(end, 1)
    await asyncio.wait_for(stop, 1)
    assert ctx.session.status == SessionStatus.STOPPED


async def test_queued_drain_kick_cannot_resume_after_later_stop(
    context, monkeypatch, mock_acp_client,
):
    ctx = context
    ctx.session.status = SessionStatus.IDLE
    ctx.session.client = mock_acp_client
    ctx.db.enqueue_prompt(ctx.session.session_id, "earlier", time.time())
    entered, release = asyncio.Event(), asyncio.Event()
    original_kick = ctx.manager._kick_pending_drain

    async def delayed_kick(session, *, expected_generation):
        entered.set()
        await release.wait()
        await original_kick(session, expected_generation=expected_generation)

    monkeypatch.setattr(ctx.manager, "_kick_pending_drain", delayed_kick)
    resume = AsyncMock(side_effect=AssertionError("stale kick resumed stopped session"))
    monkeypatch.setattr(ctx.manager, "_resume_session_admitted", resume)
    submit = asyncio.create_task(
        ctx.manager.submit_or_queue_prompt(ctx.session.session_id, "later")
    )
    await asyncio.wait_for(entered.wait(), 1)
    await ctx.manager.stop_session(ctx.session.session_id)
    release.set()
    assert (await asyncio.wait_for(submit, 1))["queued"] is True
    resume.assert_not_awaited()
    assert ctx.db.count_pending_prompts(ctx.session.session_id) == 2
    assert ctx.session.status == SessionStatus.STOPPED


async def test_resync_excludes_prompt_admission_until_client_replacement(
    context, monkeypatch, mock_acp_client,
):
    ctx = context
    ctx.session.status = SessionStatus.IDLE
    ctx.session.client = mock_acp_client
    entered, release, delivered = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def shutdown():
        entered.set()
        await release.wait()

    async def prompt(_text):
        delivered.set()
        await asyncio.Event().wait()

    mock_acp_client.shutdown.side_effect = shutdown
    replacement = SimpleNamespace(
        start=AsyncMock(), load_session=AsyncMock(), shutdown=AsyncMock(),
        send_prompt=AsyncMock(side_effect=prompt), cancel_prompt=AsyncMock(),
        is_running=True, pid=456, has_active_background_tasks=False,
        active_background_tasks=[], session_host_client=None,
    )
    monkeypatch.setattr("agent_bridge.session_manager.AcpClient", lambda **_kwargs: replacement)
    process = SimpleNamespace(proc=Mock(), alive=True, pid=456)

    async def kill():
        process.alive = False

    process.kill = AsyncMock(side_effect=kill)
    monkeypatch.setattr(
        "agent_bridge.session_manager.spawn",
        AsyncMock(return_value=process),
    )
    monkeypatch.setattr("agent_bridge.session_manager._cleanup_worktree", AsyncMock())
    monkeypatch.setattr(
        ctx.manager, "_quiesce_session", SessionManager._quiesce_session.__get__(ctx.manager),
    )
    resync = asyncio.create_task(ctx.manager.resync_session(ctx.session.session_id))
    await asyncio.wait_for(entered.wait(), 1)
    submit = asyncio.create_task(ctx.manager.submit_prompt(ctx.session.session_id, "new work"))
    await asyncio.sleep(0)
    assert not submit.done()
    release.set()
    await asyncio.wait_for(resync, 1)
    assert await asyncio.wait_for(submit, 1) == 0
    await asyncio.wait_for(delivered.wait(), 1)
    mock_acp_client.send_prompt.assert_not_awaited()
    replacement.send_prompt.assert_awaited_once_with("new work")
    await ctx.manager.stop_session(ctx.session.session_id)
    assert ctx.session.status == SessionStatus.STOPPED


@pytest.mark.parametrize("queued_api", [False, True])
async def test_direct_resume_serializes_with_followup_prompt(
    context, monkeypatch, mock_acp_client, queued_api,
):
    ctx = context
    ctx.session.status = SessionStatus.STOPPED
    entered, release = asyncio.Event(), asyncio.Event()
    mock_acp_client.session_host_client = None

    async def attach(session):
        entered.set()
        await release.wait()
        session.client = mock_acp_client
        session.status = SessionStatus.IDLE
        ctx.db.update_session_status(session.session_id, "idle", time.time())
        return True

    attach_mock = AsyncMock(side_effect=attach)
    monkeypatch.setattr(ctx.manager, "_try_reattach_live_host", attach_mock)
    resume = asyncio.create_task(ctx.manager.resume_session(ctx.session.session_id, drain=False))
    await asyncio.wait_for(entered.wait(), 1)
    submit = (
        ctx.manager.submit_or_queue_prompt if queued_api else ctx.manager.submit_prompt
    )
    send = asyncio.create_task(submit(ctx.session.session_id, "follow-up"))
    await asyncio.sleep(0)
    assert not send.done()
    release.set()
    await asyncio.wait_for(resume, 1)
    result = await asyncio.wait_for(send, 1)
    if queued_api:
        assert result["queued"] is False
    else:
        assert result == 0
    await asyncio.wait_for(ctx.session._prompt_task, 1)
    attach_mock.assert_awaited_once()
    mock_acp_client.send_prompt.assert_awaited_once_with("follow-up")
