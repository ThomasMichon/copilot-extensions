"""Admission and cancellation contracts using owned, disposable local children."""

from __future__ import annotations

import asyncio
import contextlib
import sys
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import pytest_asyncio
from agent_procutil import no_window_kwargs

from agent_bridge.events import EventLog
from agent_bridge.models import SessionStatus
from agent_bridge.session_host.host_index import HostRecord
from agent_bridge.session_host.protocol import PROTOCOL_VERSION
from agent_bridge.session_manager import Session, SessionManager
from agent_bridge.transport import AgentProcess, SpawnTarget

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def owned_context(tmp_db, tmp_path, monkeypatch):
    manager = SessionManager(tmp_db, session_host_state_dir=str(tmp_path / "hosts"))
    target = SpawnTarget(type="local", cwd=str(tmp_path))
    session = Session("example-session", "example-agent", target)
    session.status = SessionStatus.STOPPED
    session.acp_session_id = "example-acp"
    tmp_db.create_session(
        session.session_id, session.name, None, str(tmp_path), "local", "stopped",
        time.time(), target_json=target.to_json(),
    )
    tmp_db.update_session_acp_id(session.session_id, session.acp_session_id)
    session.event_log = EventLog(db=tmp_db, session_id=session.session_id)
    manager._sessions[session.session_id] = session
    state = SimpleNamespace(
        manager=manager, session=session, db=tmp_db, phase="load",
        entered=asyncio.Event(), release=asyncio.Event(), processes=[],
        block_cleanup=False, cleanup_entered=asyncio.Event(),
        cleanup_release=asyncio.Event(), start_error=None,
    )

    async def spawn(target, **_kwargs):
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-c",
            "import time; print('ready', flush=True); time.sleep(60)",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            **no_window_kwargs(),
        )
        state.processes.append(process)
        assert (await asyncio.wait_for(process.stdout.readline(), 3)).strip() == b"ready"
        if state.phase == "spawn":
            state.entered.set()
            await state.release.wait()
        return AgentProcess(process, target)

    async def stage(name):
        if state.phase == name:
            state.entered.set()
            await state.release.wait()

    class Client:
        session_host_client = None
        has_active_background_tasks = False
        active_background_tasks = []

        def __init__(self, **_kwargs):
            self.process = None

        @property
        def pid(self):
            return self.process.pid if self.process else None

        @property
        def is_running(self):
            return self.process is not None and self.process.returncode is None

        async def start(self, process):
            self.process = process
            if state.start_error is not None:
                raise state.start_error
            await stage("start")

        async def load_session(self, **_kwargs):
            if state.phase == "recreate":
                raise RuntimeError("replay unavailable")
            await stage("load")

        async def new_session(self, **_kwargs):
            await stage("recreate")
            return "new-acp"

        async def shutdown(self):
            # Deliberately leave the child for the ownership fallback to reap.
            if state.block_cleanup:
                state.cleanup_entered.set()
                await state.cleanup_release.wait()

        async def cancel_prompt(self):
            return None

        def stderr_tail(self):
            return ""

    monkeypatch.setattr("agent_bridge.session_manager.spawn", spawn)
    monkeypatch.setattr("agent_bridge.session_manager.AcpClient", Client)
    monkeypatch.setattr("agent_bridge.session_manager._MAX_RESUME_ROUNDS", 1)
    monkeypatch.setattr("agent_bridge.session_manager._cleanup_worktree", AsyncMock())
    monkeypatch.setattr(manager, "_try_reattach_live_host", AsyncMock(return_value=False))
    monkeypatch.setattr(manager, "_refresh_provider_target", AsyncMock())
    monkeypatch.setattr(manager, "_resume_via_new_remote_host", AsyncMock(return_value=None))
    monkeypatch.setattr(manager, "_detach_host", AsyncMock())
    try:
        yield state
    finally:
        state.release.set()
        state.cleanup_release.set()
        for process in state.processes:
            if process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
            await asyncio.wait_for(process.wait(), 3)


@pytest.mark.parametrize("phase", ["spawn", "start", "load", "recreate", "resync"])
async def test_cancelled_resume_reaps_every_owned_child(owned_context, phase):
    ctx = owned_context
    ctx.phase = "load" if phase == "resync" else phase
    operation = (
        ctx.manager.resync_session(ctx.session.session_id)
        if phase == "resync"
        else ctx.manager.resume_session(
            ctx.session.session_id, drain=False, allow_recreate=phase == "recreate",
        )
    )
    task = asyncio.create_task(operation)
    await asyncio.wait_for(ctx.entered.wait(), 5)
    assert ctx.processes[-1].returncode is None
    task.cancel()
    await asyncio.sleep(0)
    if phase == "spawn":
        assert not task.done()
        ctx.release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 5)
    assert len(ctx.processes) == (2 if phase == "recreate" else 1)
    assert all(process.returncode is not None for process in ctx.processes)
    assert ctx.session._owned_process is None
    assert ctx.session.client is None
    assert ctx.session.status == SessionStatus.STOPPED
    assert ctx.db.get_session(ctx.session.session_id)["status"] == "stopped"
    assert not ctx.session._lifecycle_lock.locked()
    assert not ctx.session._turn_start_lock.locked()


async def test_repeated_resume_cancellation_waits_for_cleanup(owned_context):
    ctx = owned_context
    ctx.phase = "start"
    ctx.block_cleanup = True
    task = asyncio.create_task(ctx.manager.resume_session(ctx.session.session_id))
    await asyncio.wait_for(ctx.entered.wait(), 5)
    task.cancel()
    await asyncio.wait_for(ctx.cleanup_entered.wait(), 3)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    assert ctx.session._lifecycle_lock.locked()
    assert ctx.processes[0].returncode is None
    ctx.cleanup_release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 5)
    assert ctx.processes[0].returncode is not None
    assert ctx.session.status == SessionStatus.STOPPED


async def test_successful_resume_retains_process_until_stop(owned_context, monkeypatch):
    ctx = owned_context
    ctx.phase = "success"
    await ctx.manager.resume_session(ctx.session.session_id, drain=False)
    assert ctx.session.status == SessionStatus.IDLE
    assert ctx.session._owned_process.alive
    await ctx.manager.stop_session(ctx.session.session_id, for_restart=True, cancel_turn=False)
    assert ctx.processes[0].returncode is not None
    assert ctx.session._owned_process is None
    assert ctx.db.get_session(ctx.session.session_id)["restart_status"] == "idle"
    restarted = SessionManager(ctx.db)
    restored = restarted.get_session(ctx.session.session_id)
    assert restarted._background_recovery_allowed(restored)
    monkeypatch.setattr(restarted, "_try_reattach_live_host", AsyncMock(return_value=False))
    monkeypatch.setattr(restarted, "_refresh_provider_target", AsyncMock())
    monkeypatch.setattr(restarted, "_resume_via_new_remote_host", AsyncMock(return_value=None))
    monkeypatch.setattr(restarted, "_detach_host", AsyncMock())
    await restarted.resume_session(restored.session_id, drain=False)
    assert restored.status == SessionStatus.IDLE
    assert restored.acp_session_id == "example-acp"
    await restarted.stop_session(restored.session_id)
    assert all(process.returncode is not None for process in ctx.processes)


async def test_cancelled_initial_process_launch_retains_no_child(owned_context):
    ctx = owned_context
    ctx.phase = "start"
    task = asyncio.create_task(ctx.manager.start_session(
        SpawnTarget(type="command", cwd=ctx.session.target.cwd, spawn_command=["example-agent"]),
    ))
    await asyncio.wait_for(ctx.entered.wait(), 5)
    created = next(s for s in ctx.manager.list_sessions() if s is not ctx.session)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 5)
    assert ctx.processes[0].returncode is not None
    assert created._owned_process is None
    assert created.status == SessionStatus.STOPPED


@pytest.mark.parametrize("structured_error", [False, True])
@pytest.mark.parametrize("cleanup_phase", ["shutdown", "kill"])
async def test_failed_start_cleanup_settles_repeated_cancellation(
    owned_context, monkeypatch, structured_error, cleanup_phase,
):
    from agent_bridge.connect import ConnectError, ConnectStage

    ctx = owned_context
    ctx.start_error = (
        ConnectError(ConnectStage.LAUNCH_ACP, "handshake failed", retryable=False)
        if structured_error else RuntimeError("handshake failed")
    )
    ctx.phase = "success"
    ctx.block_cleanup = cleanup_phase == "shutdown"
    original_kill = AgentProcess.kill

    async def kill(process):
        ctx.cleanup_entered.set()
        await ctx.cleanup_release.wait()
        await original_kill(process)

    if cleanup_phase == "kill":
        monkeypatch.setattr(AgentProcess, "kill", kill)
    task = asyncio.create_task(ctx.manager.start_session(
        SpawnTarget(type="command", cwd=ctx.session.target.cwd, spawn_command=["example-agent"]),
    ))
    await asyncio.wait_for(ctx.cleanup_entered.wait(), 5)
    created = next(s for s in ctx.manager.list_sessions() if s is not ctx.session)
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    assert created._lifecycle_lock.locked()
    assert ctx.processes[0].returncode is None
    ctx.cleanup_release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 5)
    assert ctx.processes[0].returncode is not None
    assert created._owned_process is None
    assert created.client is None
    assert created.status == SessionStatus.STOPPED
    assert ctx.db.get_session(created.session_id)["status"] == "stopped"
    assert not created._lifecycle_lock.locked()


@pytest.mark.parametrize("phase", ["quiesce", "process", "relay", "forward"])
@pytest.mark.parametrize("for_restart", [False, True])
async def test_stop_cancellation_finishes_every_stage(
    owned_context, monkeypatch, phase, for_restart,
):
    ctx = owned_context
    ctx.session.status = SessionStatus.RUNNING
    ctx.db.update_session_status(ctx.session.session_id, "running", time.time())
    entered, release = asyncio.Event(), asyncio.Event()
    interrupted = []

    async def stage(name):
        if name == phase:
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                interrupted.append(name)
                raise

    async def quiesce(*_args, **_kwargs):
        await stage("quiesce")

    process = SimpleNamespace(alive=True, pid=123, proc=Mock())

    async def kill():
        await stage("process")
        process.alive = False

    process.kill = AsyncMock(side_effect=kill)
    async def stop_relay():
        await stage("relay")

    async def stop_forward():
        await stage("forward")

    relay = SimpleNamespace(stop=AsyncMock(side_effect=stop_relay))
    forward = SimpleNamespace(cancel=AsyncMock(side_effect=stop_forward))
    ctx.session._owned_process = process
    ctx.manager._relays[ctx.session.session_id] = [relay]
    ctx.manager._forwards[ctx.session.session_id] = forward
    monkeypatch.setattr(ctx.manager, "_quiesce_session", quiesce)
    task = asyncio.create_task(ctx.manager.stop_session(
        ctx.session.session_id, for_restart=for_restart,
    ))
    await asyncio.wait_for(entered.wait(), 3)
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    assert ctx.session._turn_start_lock.locked()
    assert ctx.session._lifecycle_lock.locked()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 3)
    assert interrupted == []
    process.kill.assert_awaited_once()
    relay.stop.assert_awaited_once()
    forward.cancel.assert_awaited_once()
    assert ctx.session._owned_process is None
    assert ctx.session.status == SessionStatus.STOPPED
    assert ctx.manager._forwards == {}
    assert ctx.manager._relays == {}
    row = ctx.db.get_session(ctx.session.session_id)
    assert row["status"] == "stopped"
    assert row["restart_status"] == ("running" if for_restart else None)


async def test_cleanup_failure_retains_process_for_explicit_retry(owned_context, monkeypatch):
    ctx = owned_context
    process = SimpleNamespace(alive=True, pid=123, proc=Mock())
    process.kill = AsyncMock(side_effect=PermissionError("kill denied"))

    async def spawn(*_args, **_kwargs):
        return process

    monkeypatch.setattr("agent_bridge.session_manager.spawn", spawn)
    ctx.phase = "start"
    task = asyncio.create_task(ctx.manager.resume_session(ctx.session.session_id))
    await asyncio.wait_for(ctx.entered.wait(), 3)
    task.cancel()
    with pytest.raises(PermissionError, match="kill denied"):
        await asyncio.wait_for(task, 3)
    assert ctx.session.status == SessionStatus.FAILED
    assert ctx.session._owned_process is process
    assert not ctx.manager._background_recovery_allowed(ctx.session)

    async def kill():
        process.alive = False

    process.kill.side_effect = kill
    await ctx.manager.stop_session(ctx.session.session_id)
    assert ctx.session.status == SessionStatus.STOPPED
    assert ctx.session._owned_process is None


def _remote_record(ctx):
    ctx.session.status = SessionStatus.IDLE
    ctx.session.target = SpawnTarget(
        type="command", codespace={"name": "example-space", "repo": "example/repository"},
    )
    record = HostRecord(
        session_id=ctx.session.session_id, port=51000, host_pid=123, child_pid=456,
        protocol_version=PROTOCOL_VERSION, boundary="codespace",
        endpoint={"kind": "codespace", "codespace": "example-space"},
        extra={"remote_authority_v2": True},
    )
    ctx.manager._host_index.register(record)
    return record


@pytest.mark.parametrize("lock_name", ["_turn_start_lock", "_lifecycle_lock"])
async def test_authority_result_defers_to_existing_admission_or_lifecycle_owner(
    owned_context, monkeypatch, lock_name,
):
    ctx = owned_context
    record = _remote_record(ctx)
    held, release = asyncio.Event(), asyncio.Event()
    holders = []

    async def hold():
        async with getattr(ctx.session, lock_name):
            held.set()
            await release.wait()

    async def recover(_session_id):
        holders.append(asyncio.create_task(hold()))
        return None

    spawner = SimpleNamespace(
        can_inspect_without_wake=AsyncMock(return_value=True),
        recover_record=AsyncMock(side_effect=recover),
    )
    monkeypatch.setattr(
        "agent_bridge.session_host.codespace_transport.build_codespace_spawner",
        lambda *_args, **_kwargs: spawner,
    )
    monkeypatch.setattr("agent_bridge.relay_state.get_live_relay_port", lambda: None)
    cleanup = AsyncMock()
    monkeypatch.setattr(ctx.manager, "_drop_forward", cleanup)
    recovery = asyncio.create_task(ctx.manager._recover_remote_host_records(background=True))
    try:
        await asyncio.wait_for(held.wait(), 3)
        assert await asyncio.wait_for(recovery, 3) == 0
        assert ctx.manager._host_index.get(ctx.session.session_id) == record
        cleanup.assert_not_awaited()
    finally:
        release.set()
        await asyncio.gather(*holders)


async def test_stranded_sweep_defers_to_turn_admission(owned_context, monkeypatch):
    from agent_bridge.session_host import version_mux

    ctx = owned_context
    record = _remote_record(ctx)
    plan = Mock(return_value=SimpleNamespace(
        disposition=version_mux.HostDisposition.FORCE_REAP, reason="expired",
    ))
    monkeypatch.setattr(version_mux, "plan_host", plan)
    reap = Mock()
    monkeypatch.setattr(ctx.manager, "_reap_host_record", reap)
    async with ctx.session._turn_start_lock:
        assert await ctx.manager.sweep_stranded_hosts() == 0
    plan.assert_not_called()
    reap.assert_not_called()
    assert ctx.manager._host_index.get(ctx.session.session_id) == record


async def test_authority_result_is_stale_after_completed_prompt_admission(
    owned_context, monkeypatch,
):
    ctx = owned_context
    record = _remote_record(ctx)
    ctx.session.client = SimpleNamespace(
        is_running=True, pid=123, session_host_client=None,
        has_active_background_tasks=False, active_background_tasks=[],
        send_prompt=AsyncMock(return_value={
            "response_text": "done", "stop_reason": "end_turn",
        }),
    )

    async def recover(_session_id):
        admission = asyncio.create_task(
            ctx.manager.submit_prompt(ctx.session.session_id, "new turn")
        )
        await admission
        return None

    spawner = SimpleNamespace(
        can_inspect_without_wake=AsyncMock(return_value=True),
        recover_record=AsyncMock(side_effect=recover),
    )
    monkeypatch.setattr(
        "agent_bridge.session_host.codespace_transport.build_codespace_spawner",
        lambda *_args, **_kwargs: spawner,
    )
    monkeypatch.setattr("agent_bridge.relay_state.get_live_relay_port", lambda: None)
    cleanup = AsyncMock()
    monkeypatch.setattr(ctx.manager, "_drop_forward", cleanup)
    assert await ctx.manager._recover_remote_host_records(background=True) == 0
    await ctx.session._prompt_task
    cleanup.assert_not_awaited()
    assert ctx.manager._host_index.get(ctx.session.session_id) == record


async def test_shutdown_joins_launch_without_client_and_rejects_new_work(owned_context):
    from agent_bridge.session_manager import DaemonDrainingError
    from agent_bridge.session_teardown import detach_for_restart

    ctx = owned_context
    ctx.phase = "start"
    launch = asyncio.create_task(ctx.manager.start_session(
        SpawnTarget(type="command", cwd=ctx.session.target.cwd, spawn_command=["example-agent"]),
    ))
    await asyncio.wait_for(ctx.entered.wait(), 5)
    starting = next(s for s in ctx.manager.list_sessions() if s is not ctx.session)
    assert starting.client is None
    shutdown = asyncio.create_task(detach_for_restart(ctx.manager))
    await asyncio.sleep(0)
    assert not shutdown.done()
    assert ctx.manager._shutting_down
    ctx.release.set()
    await asyncio.wait_for(launch, 3)
    await asyncio.wait_for(shutdown, 5)
    assert ctx.processes[0].returncode is not None
    assert starting._owned_process is None
    assert starting.status == SessionStatus.STOPPED
    assert ctx.db.get_session(starting.session_id)["restart_status"] == "idle"
    for operation in (
        ctx.manager.start_session(ctx.session.target),
        ctx.manager.resume_session(starting.session_id),
        ctx.manager.resync_session(starting.session_id),
        ctx.manager.submit_prompt(starting.session_id, "too late"),
    ):
        with pytest.raises(DaemonDrainingError):
            await operation
    result = await ctx.manager.submit_or_queue_prompt(starting.session_id, "preserve for later")
    assert result["queued"] is True
    assert len(ctx.processes) == 1


async def test_channel_close_fences_an_already_admitted_resume(owned_context):
    from agent_bridge.session_manager import DaemonDrainingError

    ctx = owned_context
    forward = SimpleNamespace(cancel=AsyncMock())
    ctx.manager._forwards[ctx.session.session_id] = forward
    await ctx.session._lifecycle_lock.acquire()
    send = asyncio.create_task(ctx.manager.submit_prompt(ctx.session.session_id, "late work"))
    await asyncio.sleep(0)
    assert ctx.session._turn_start_lock.locked()
    close = asyncio.create_task(ctx.manager.close_frontend_transports())
    await asyncio.sleep(0)
    assert ctx.manager._shutting_down
    ctx.session._lifecycle_lock.release()
    with pytest.raises(DaemonDrainingError):
        await asyncio.wait_for(send, 3)
    await asyncio.wait_for(close, 3)
    forward.cancel.assert_awaited_once()
    assert ctx.processes == []
    assert ctx.manager._forwards == {}


async def test_failed_explicit_remote_reap_keeps_authority_for_retry(owned_context, monkeypatch):
    from agent_bridge.session_manager import RemoteHostRecoveryPendingError

    ctx = owned_context
    record = _remote_record(ctx)
    reap = AsyncMock(return_value=False)
    monkeypatch.setattr(ctx.manager, "_remote_reap", reap)
    monkeypatch.setattr(
        ctx.manager, "_forget_host_record",
        lambda rec: ctx.manager._host_index.remove(rec.session_id),
    )
    with pytest.raises(RemoteHostRecoveryPendingError, match="authority and ownership retained"):
        await ctx.manager.stop_session(ctx.session.session_id, reap_host=True)
    assert ctx.session.status == SessionStatus.FAILED
    assert ctx.manager._host_index.get(ctx.session.session_id) == record
    assert not ctx.manager._background_recovery_allowed(ctx.session)
    reap.return_value = True
    await ctx.manager.stop_session(ctx.session.session_id, reap_host=True)
    assert ctx.session.status == SessionStatus.STOPPED
    assert ctx.manager._host_index.get(ctx.session.session_id) is None


async def test_failed_pending_remote_reap_cannot_acknowledge_stop(owned_context, monkeypatch):
    from agent_bridge.session_manager import RemoteHostRecoveryPendingError

    ctx = owned_context
    record = _remote_record(ctx)
    entered, release = asyncio.Event(), asyncio.Event()

    async def reap(*_args):
        entered.set()
        await release.wait()
        return False

    monkeypatch.setattr(ctx.manager, "_remote_reap", reap)
    ctx.manager._reap_host_record(record, "prior cleanup")
    assert ctx.manager._host_index.get(ctx.session.session_id) == record
    await asyncio.wait_for(entered.wait(), 3)
    stop = asyncio.create_task(ctx.manager.stop_session(ctx.session.session_id))
    await asyncio.sleep(0)
    release.set()
    with pytest.raises(RemoteHostRecoveryPendingError, match="inconclusive"):
        await asyncio.wait_for(stop, 3)
    assert ctx.session.status == SessionStatus.FAILED
    assert ctx.manager._host_index.get(ctx.session.session_id) == record


@pytest.mark.parametrize("scheduled_during_inspection", [False, True])
async def test_authority_recovery_never_applies_while_remote_reap_is_pending(
    owned_context, monkeypatch, scheduled_during_inspection,
):
    from dataclasses import replace

    ctx = owned_context
    record = _remote_record(ctx)
    release = asyncio.Event()
    tasks = []

    async def reap(*_args):
        await release.wait()
        return True

    def schedule():
        ctx.manager._schedule_remote_reap(record, "existing cleanup")
        tasks.extend(ctx.manager._remote_reaps_by_session[record.session_id])

    async def recover(_session_id):
        if scheduled_during_inspection:
            schedule()
        return replace(record, nonce="stale-result")

    spawner = SimpleNamespace(
        can_inspect_without_wake=AsyncMock(return_value=True),
        recover_record=AsyncMock(side_effect=recover),
    )
    factory = Mock(return_value=spawner)
    monkeypatch.setattr(
        "agent_bridge.session_host.codespace_transport.build_codespace_spawner", factory,
    )
    monkeypatch.setattr("agent_bridge.relay_state.get_live_relay_port", lambda: None)
    monkeypatch.setattr(ctx.manager, "_remote_reap", reap)
    monkeypatch.setattr(
        ctx.manager, "_forget_host_record",
        lambda rec: ctx.manager._host_index.remove(rec.session_id),
    )
    if not scheduled_during_inspection:
        schedule()
    try:
        assert await ctx.manager._recover_remote_host_records(background=True) == 0
        assert ctx.manager._host_index.get(record.session_id) == record
        if not scheduled_during_inspection:
            factory.assert_not_called()
    finally:
        release.set()
        await asyncio.gather(*tasks)


@pytest.mark.parametrize("phase", ["ready", "prepare"])
@pytest.mark.parametrize("has_authority", [False, True])
async def test_early_container_resume_cancellation_releases_only_unowned_claims(
    owned_context, monkeypatch, phase, has_authority,
):
    ctx = owned_context
    ctx.session.target = SpawnTarget(type="command", container={"name": "example-container"})
    releases = []

    def acquire(session_id, name):
        ctx.manager._container_lock_sessions[session_id] = name

    def release(session_id):
        releases.append(session_id)
        ctx.manager._container_lock_sessions.pop(session_id, None)

    entered = asyncio.Event()

    async def ready(*_args, **_kwargs):
        if phase == "ready":
            entered.set()
            await asyncio.Event().wait()

    async def prepare(*_args, **_kwargs):
        entered.set()
        await asyncio.Event().wait()

    if has_authority:
        ctx.manager._host_index.register(HostRecord(
            session_id=ctx.session.session_id, port=51000, host_pid=123, child_pid=456,
            boundary="container",
        ))
    monkeypatch.setattr(ctx.manager, "_acquire_container_lock", acquire)
    monkeypatch.setattr(ctx.manager, "_release_container_lock", release)
    monkeypatch.setattr(
        ctx.manager, "_resume_via_new_remote_host",
        SessionManager._resume_via_new_remote_host.__get__(ctx.manager),
    )
    monkeypatch.setattr("agent_bridge.session_host.container_transport.ensure_container_ready", ready)
    monkeypatch.setattr("agent_bridge.session_host.container_transport.prepare_container_session_host", prepare)
    monkeypatch.setattr("agent_bridge.relay_state.get_live_relay_port", lambda: None)
    task = asyncio.create_task(ctx.manager.resume_session(ctx.session.session_id, drain=False))
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 3)
    assert ctx.session.status == SessionStatus.STOPPED
    assert (ctx.session.session_id in ctx.manager._container_lock_sessions) is has_authority
    assert releases == ([] if has_authority else [ctx.session.session_id])
    assert ctx.processes == []


async def test_remote_reap_without_endpoint_cannot_acknowledge_success(owned_context, monkeypatch):
    from agent_bridge.session_manager import RemoteHostRecoveryPendingError

    ctx = owned_context
    record = _remote_record(ctx)
    record.endpoint = {}
    ctx.manager._host_index.register(record)
    reap = AsyncMock(side_effect=AssertionError("missing endpoint must not be probed"))
    monkeypatch.setattr(ctx.manager, "_remote_reap", reap)
    with pytest.raises(RemoteHostRecoveryPendingError, match="authority and ownership retained"):
        await ctx.manager.stop_session(ctx.session.session_id, reap_host=True)
    reap.assert_not_awaited()
    assert ctx.manager._host_index.get(ctx.session.session_id) == record
    assert ctx.session.status == SessionStatus.FAILED
