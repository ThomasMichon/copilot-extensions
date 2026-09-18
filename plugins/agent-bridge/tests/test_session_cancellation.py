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

        async def shutdown(self, *, strict=False):
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
    admissions = []
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
        admissions.append(admission)
        await asyncio.sleep(0)
        assert not admission.done()
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
    await admissions[0]
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


@pytest.mark.parametrize("cancel_shutdown", [False, True])
async def test_shutdown_retains_failed_process_cleanup_until_verified_exit(
    owned_context, monkeypatch, cancel_shutdown,
):
    from agent_bridge.session_teardown import detach_for_restart

    ctx = owned_context
    ctx.phase = "success"
    await ctx.manager.resume_session(ctx.session.session_id, drain=False)
    original_kill = AgentProcess.kill
    retry_entered, retry_release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def kill(process):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise PermissionError("transient cleanup refusal")
        retry_entered.set()
        await retry_release.wait()
        await original_kill(process)

    monkeypatch.setattr(AgentProcess, "kill", kill)
    monkeypatch.setattr("agent_bridge.session_teardown._SHUTDOWN_CLEANUP_RETRY_SECONDS", 0.01)
    shutdown = asyncio.create_task(detach_for_restart(ctx.manager))
    await asyncio.wait_for(retry_entered.wait(), 3)
    assert not shutdown.done()
    assert ctx.processes[0].returncode is None
    assert ctx.session._owned_process is not None
    assert ctx.db.get_session(ctx.session.session_id)["status"] == "failed"
    if cancel_shutdown:
        shutdown.cancel()
        await asyncio.sleep(0)
        shutdown.cancel()
        await asyncio.sleep(0)
        assert not shutdown.done()
    retry_release.set()
    if cancel_shutdown:
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(shutdown, 3)
    else:
        await asyncio.wait_for(shutdown, 3)
    assert ctx.processes[0].returncode is not None
    assert ctx.session._owned_process is None
    assert ctx.session.status == SessionStatus.STOPPED
    assert calls == 2
    assert ctx.db.get_session(ctx.session.session_id)["restart_status"] == "idle"


@pytest.mark.parametrize("channel", ["relay", "forward"])
@pytest.mark.parametrize("known_session", [False, True])
async def test_shutdown_retries_channel_cleanup_without_losing_restart_intent(
    owned_context, monkeypatch, channel, known_session,
):
    from agent_bridge.session_teardown import detach_for_restart

    ctx = owned_context
    session_id = ctx.session.session_id if known_session else "orphan-channel"
    ctx.session.status = SessionStatus.IDLE
    ctx.db.update_session_status(ctx.session.session_id, "idle", time.time())
    retry_entered, retry_release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def close():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("transient channel failure")
        retry_entered.set()
        await retry_release.wait()

    if channel == "relay":
        ctx.manager._relays[session_id] = [SimpleNamespace(stop=close)]
    else:
        ctx.manager._forwards[session_id] = SimpleNamespace(cancel=close)
    monkeypatch.setattr("agent_bridge.session_teardown._SHUTDOWN_CLEANUP_RETRY_SECONDS", 0.01)
    shutdown = asyncio.create_task(detach_for_restart(ctx.manager))
    await asyncio.wait_for(retry_entered.wait(), 2)
    assert not shutdown.done()
    if known_session:
        row = ctx.db.get_session(session_id)
        assert row["status"] == "failed"
        assert row["restart_status"] == "idle"
        assert ctx.session.restart_status == "idle"
    retry_release.set()
    await asyncio.wait_for(shutdown, 2)
    assert ctx.manager._forwards == {}
    assert ctx.manager._relays == {}
    assert ctx.db.get_session(ctx.session.session_id)["restart_status"] == "idle"
    restarted = SessionManager(ctx.db)
    assert restarted._background_recovery_allowed(restarted.get_session(ctx.session.session_id))


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


async def test_resync_refuses_pending_remote_reap_without_mutation(owned_context, monkeypatch):
    from agent_bridge.session_manager import RemoteHostRecoveryPendingError

    ctx = owned_context
    release = asyncio.Event()
    reap = asyncio.create_task(release.wait())
    ctx.manager._remote_reaps_by_session[ctx.session.session_id] = {reap}
    generation = ctx.session._lifecycle_generation
    try:
        with pytest.raises(RemoteHostRecoveryPendingError, match="cleanup is pending"):
            await ctx.manager.resync_session(ctx.session.session_id)
        assert ctx.session._lifecycle_generation == generation
        assert ctx.session.status == SessionStatus.STOPPED
        assert ctx.processes == []
    finally:
        release.set()
        await reap


@pytest.mark.parametrize("boundary", ["local", "codespace", "container"])
@pytest.mark.parametrize("background", [False, True])
async def test_resync_refuses_retained_completed_host_before_cleanup(
    owned_context, monkeypatch, boundary, background,
):
    from agent_bridge.session_manager import RemoteHostRecoveryPendingError

    ctx = owned_context
    record = _remote_record(ctx)
    record.boundary = boundary
    ctx.manager._host_index.register(record)
    ctx.session.status = SessionStatus.RUNNING if background else SessionStatus.STOPPED
    generation = ctx.session._lifecycle_generation
    cleanup = AsyncMock(side_effect=AssertionError("retained host must not be detached for direct spawn"))
    monkeypatch.setattr("agent_bridge.session_resume.cleanup_failed_resume", cleanup)
    with pytest.raises(RemoteHostRecoveryPendingError, match="authority is retained"):
        await ctx.manager.resync_session(record.session_id, background=background)
    cleanup.assert_not_awaited()
    assert ctx.manager._host_index.get(record.session_id) == record
    assert ctx.session._lifecycle_generation == generation
    assert ctx.session.status == (SessionStatus.RUNNING if background else SessionStatus.STOPPED)
    assert ctx.processes == []


@pytest.mark.parametrize("fault", ["error", "cancel"])
@pytest.mark.parametrize("retain_host", [False, True])
async def test_preconnect_start_claim_cleanup(owned_context, monkeypatch, fault, retain_host):
    ctx = owned_context
    target = SpawnTarget(
        type="command", caller_worktree="example-owner",
        codespace={"name": "example-space", "repo": "example/repo", "acp_command": "example-agent"},
    )
    entered = asyncio.Event()
    release = Mock(return_value=True)
    monkeypatch.setattr("agent_bridge.session_manager._claim_codespace", Mock(return_value=("ok", "")))
    monkeypatch.setattr("agent_bridge.session_manager._release_codespace_claim", release)
    monkeypatch.setattr("agent_bridge.session_manager._resolve_relay_launch_env", Mock(return_value=("", None)))
    monkeypatch.setattr("agent_bridge.relay_state.get_live_relay_port", lambda: None)
    monkeypatch.setattr(
        "agent_bridge.session_host.codespace_transport.build_codespace_spawner",
        Mock(return_value=SimpleNamespace(transport=object())),
    )

    async def prepare(*_args):
        created = next(s for s in ctx.manager.list_sessions() if s is not ctx.session)
        if retain_host:
            ctx.manager._host_index.register(HostRecord(
                session_id=created.session_id, port=51000, host_pid=123, child_pid=456,
                boundary="codespace",
            ))
        entered.set()
        if fault == "error":
            raise OSError("prepare failed")
        await asyncio.Event().wait()

    monkeypatch.setattr("agent_bridge.session_manager._resolve_remote_ai_plugin_dirs", prepare)
    task = asyncio.create_task(ctx.manager.start_session(target))
    await asyncio.wait_for(entered.wait(), 2)
    if fault == "cancel":
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 3)
    else:
        await asyncio.wait_for(task, 3)
    if retain_host:
        release.assert_not_called()
    else:
        release.assert_called_once_with("example-space", "example-owner")
    assert ctx.processes == []


@pytest.mark.parametrize("replacement", ["none", "changed", "identical", "flag-aba"])
async def test_remote_reap_fences_container_cleanup_to_exact_record(
    owned_context, monkeypatch, replacement,
):
    from dataclasses import replace
    from agent_bridge.session_teardown import reap_remote_record

    ctx = owned_context
    record = _remote_record(ctx)
    session_id = ctx.session.session_id
    ctx.session.target = SpawnTarget(
        type="command",
        container={"name": "example-container", "launch_pending_session_id": session_id},
    )
    lock = Mock()
    ctx.manager._container_locks["example-container"] = (lock, session_id)
    ctx.manager._container_lock_sessions[session_id] = "example-container"
    newer = replace(record, nonce="replacement-nonce") if replacement == "changed" else replace(record)

    async def disconnect(_host):
        if replacement == "flag-aba":
            ctx.manager._host_index.set_resume_flag(session_id, True)
            ctx.manager._host_index.set_resume_flag(session_id, False)
        elif replacement != "none":
            ctx.manager._host_index.register(newer)

    connection = SimpleNamespace(
        ensure_connected=AsyncMock(),
        exec_command=AsyncMock(return_value=SimpleNamespace(exit_code=0, stdout="__REAPED__")),
        disconnect=AsyncMock(side_effect=disconnect),
    )
    monkeypatch.setattr("ssh_manager.ConnectionManager", Mock(return_value=connection))
    monkeypatch.setattr(
        "agent_bridge.session_host.endpoints.ssh_config_from_endpoint",
        Mock(return_value=SimpleNamespace(host_alias="example-host")),
    )
    monkeypatch.setattr(
        ctx.manager, "_forget_host_record",
        lambda rec: ctx.manager._host_index.remove(rec.session_id),
    )
    assert await reap_remote_record(ctx.manager, record)
    if replacement != "none":
        assert ctx.manager._host_index.get(session_id) == newer
        assert ctx.session.target.container["launch_pending_session_id"] == session_id
        assert ctx.manager._container_lock_sessions[session_id] == "example-container"
        lock.release.assert_not_called()
    else:
        assert ctx.manager._host_index.get(session_id) is None
        assert "launch_pending_session_id" not in ctx.session.target.container
        assert session_id not in ctx.manager._container_lock_sessions
        lock.release.assert_called_once()


@pytest.mark.parametrize("failure_stage", ["index", "marker"])
async def test_remote_index_removal_failure_retains_cleanup_ownership(
    owned_context, monkeypatch, failure_stage,
):
    import json

    ctx = owned_context
    record = _remote_record(ctx)
    session_id = record.session_id
    ctx.session.target = SpawnTarget(
        type="command",
        container={"name": "example-container", "launch_pending_session_id": session_id},
    )
    lock = Mock()
    ctx.manager._container_locks["example-container"] = (lock, session_id)
    ctx.manager._container_lock_sessions[session_id] = "example-container"
    ctx.db.update_session_target(session_id, ctx.session.target.to_json(), ".")
    monkeypatch.setattr(ctx.manager, "_remote_reap", AsyncMock(return_value=True))
    flush = ctx.manager._host_index._flush
    update_target = ctx.db.update_session_target
    if failure_stage == "index":
        monkeypatch.setattr(ctx.manager._host_index, "_flush", Mock(side_effect=OSError("remove failed")))
    else:
        monkeypatch.setattr(ctx.db, "update_session_target", Mock(side_effect=OSError("remove failed")))
    ctx.manager._schedule_remote_reap(record, "old cleanup")
    task = next(iter(ctx.manager._remote_reaps_by_session[session_id]))
    with pytest.raises(OSError, match="remove failed"):
        await task
    await asyncio.sleep(0)
    assert ctx.manager._host_index.get(session_id) == record
    assert ctx.manager._remote_reap_pending(session_id)
    assert ctx.session.target.container["launch_pending_session_id"] == session_id
    assert json.loads(ctx.db.get_session(session_id)["target_json"])["container"]["launch_pending_session_id"] == session_id
    lock.release.assert_not_called()
    monkeypatch.setattr(ctx.manager._host_index, "_flush", flush)
    monkeypatch.setattr(ctx.db, "update_session_target", update_target)
    await ctx.manager.stop_session(session_id, reap_host=True)
    assert not ctx.manager._remote_reap_pending(session_id)
    assert ctx.manager._host_index.get(session_id) is None
    lock.release.assert_called_once()


async def test_recovered_container_releases_new_lock_after_index_failure(owned_context, monkeypatch):
    ctx = owned_context
    session_id = ctx.session.session_id
    ctx.session.status = SessionStatus.IDLE
    ctx.session.target = SpawnTarget(type="command", container={"name": "example-container"})
    record = HostRecord(
        session_id=session_id, port=51000, host_pid=123, child_pid=456,
        boundary="container", extra={"remote_authority_v2": True},
    )
    spawner = SimpleNamespace(
        boundary="container", can_inspect_without_wake=AsyncMock(return_value=True),
        recover_record=AsyncMock(return_value=record),
    )
    lock = Mock()
    monkeypatch.setattr("ssh_manager.TargetLock", Mock(return_value=lock))
    monkeypatch.setattr(
        "agent_bridge.session_host.container_transport.build_container_spawner",
        Mock(return_value=spawner),
    )
    monkeypatch.setattr(ctx.manager._host_index, "_flush", Mock(side_effect=OSError("persist failed")))
    with pytest.raises(OSError, match="persist failed"):
        await ctx.manager._recover_remote_host_records(background=True)
    lock.acquire.assert_called_once()
    lock.release.assert_called_once()
    assert session_id not in ctx.manager._container_lock_sessions
    assert ctx.manager._host_index.get(session_id) is None
    assert session_id in ctx.manager._remote_recovery_inconclusive


async def test_forward_persistence_failure_refuses_duplicate_resume(owned_context, monkeypatch):
    from agent_bridge.session_manager import RemoteHostRecoveryPendingError

    ctx = owned_context
    record = _remote_record(ctx)
    record.extra = {}
    ctx.manager._host_index.register(record)
    ctx.session.status = SessionStatus.STOPPED
    original_port = record.port
    original_endpoint = dict(record.endpoint)
    forward = SimpleNamespace(refresh=AsyncMock(return_value=51005), cancel=AsyncMock())
    ctx.manager._forwards[record.session_id] = forward
    monkeypatch.setattr(
        ctx.manager, "_try_reattach_live_host",
        SessionManager._try_reattach_live_host.__get__(ctx.manager),
    )
    monkeypatch.setattr(ctx.manager._host_index, "_flush", Mock(side_effect=OSError("persist failed")))
    duplicate = AsyncMock(side_effect=AssertionError("must not create a duplicate host"))
    monkeypatch.setattr(ctx.manager, "_resume_via_new_remote_host", duplicate)
    with pytest.raises(RemoteHostRecoveryPendingError, match="persist refreshed forward"):
        await ctx.manager.resume_session(record.session_id, drain=False)
    duplicate.assert_not_awaited()
    assert ctx.manager._host_index.get(record.session_id) is record
    assert record.port == original_port
    assert record.endpoint == original_endpoint
    assert ctx.manager._forwards[record.session_id] is forward
    assert ctx.processes == []


async def test_stop_observes_reap_failure_completed_during_transport_cleanup(
    owned_context, monkeypatch,
):
    from agent_bridge.session_manager import RemoteHostRecoveryPendingError

    ctx = owned_context
    record = _remote_record(ctx)
    release = asyncio.Event()

    async def reap(*_args):
        await release.wait()
        return False

    remote = AsyncMock(side_effect=reap)
    monkeypatch.setattr(ctx.manager, "_remote_reap", remote)
    ctx.manager._schedule_remote_reap(record, "existing cleanup")
    task = next(iter(ctx.manager._remote_reaps_by_session[record.session_id]))

    async def close_forward():
        release.set()
        await task
        await asyncio.sleep(0)

    ctx.manager._forwards[record.session_id] = SimpleNamespace(cancel=close_forward)
    with pytest.raises(RemoteHostRecoveryPendingError, match="inconclusive"):
        await ctx.manager.stop_session(record.session_id)
    assert ctx.session.status == SessionStatus.FAILED
    assert ctx.manager._remote_reap_pending(record.session_id)
    assert ctx.manager._host_index.get(record.session_id) == record
    remote.side_effect = None
    remote.return_value = True
    monkeypatch.setattr(
        ctx.manager, "_forget_host_record",
        lambda rec: ctx.manager._host_index.remove(rec.session_id),
    )
    await ctx.manager.stop_session(record.session_id, reap_host=True)
    assert ctx.session.status == SessionStatus.STOPPED
    assert not ctx.manager._remote_reap_pending(record.session_id)
    assert record.session_id not in ctx.manager._remote_reaps_by_session


@pytest.mark.parametrize("status", ["dead", "missing"])
@pytest.mark.parametrize("channel", ["relay", "forward"])
async def test_dead_authority_retains_failed_transport_cleanup(
    owned_context, monkeypatch, status, channel,
):
    from agent_bridge.session_host.spawner import RemoteHostDeadError

    ctx = owned_context
    record = _remote_record(ctx)
    spawner = SimpleNamespace(
        can_inspect_without_wake=AsyncMock(return_value=True),
        recover_record=AsyncMock(
            return_value=None,
            side_effect=RemoteHostDeadError("child exited") if status == "dead" else None,
        ),
    )
    monkeypatch.setattr(
        "agent_bridge.session_host.codespace_transport.build_codespace_spawner",
        Mock(return_value=spawner),
    )
    monkeypatch.setattr("agent_bridge.relay_state.get_live_relay_port", lambda: None)
    close = AsyncMock(side_effect=OSError("channel cleanup failed"))
    if channel == "relay":
        handle = SimpleNamespace(stop=close)
        ctx.manager._relays[record.session_id] = [handle]
    else:
        handle = SimpleNamespace(cancel=close)
        ctx.manager._forwards[record.session_id] = handle
    with pytest.raises(OSError, match="channel cleanup failed"):
        await ctx.manager._recover_remote_host_records(background=True)
    assert ctx.manager._host_index.get(record.session_id) == record
    if channel == "relay":
        assert ctx.manager._relays[record.session_id] == [handle]
    else:
        assert ctx.manager._forwards[record.session_id] is handle
    assert record.session_id in ctx.manager._remote_recovery_inconclusive
    close.side_effect = None
    await ctx.manager._recover_remote_host_records(background=True)
    assert ctx.manager._host_index.get(record.session_id) is None
    assert record.session_id not in ctx.manager._forwards
    assert record.session_id not in ctx.manager._relays


@pytest.mark.parametrize("confirmed", [False, True])
async def test_stranded_sweep_counts_only_confirmed_remote_reaps(
    owned_context, monkeypatch, confirmed,
):
    from agent_bridge.session_host.version_mux import HostDisposition

    ctx = owned_context
    record = _remote_record(ctx)
    entered, release = asyncio.Event(), asyncio.Event()

    async def reap(*_args):
        entered.set()
        await release.wait()
        return confirmed

    monkeypatch.setattr(ctx.manager, "_remote_reap", reap)
    monkeypatch.setattr(
        "agent_bridge.session_host.version_mux.plan_host",
        lambda **_kwargs: SimpleNamespace(disposition=HostDisposition.FORCE_REAP, reason="obsolete host"),
    )
    monkeypatch.setattr(
        ctx.manager, "_forget_host_record",
        lambda rec: ctx.manager._host_index.remove(rec.session_id),
    )
    task = asyncio.create_task(ctx.manager.sweep_stranded_hosts())
    await asyncio.wait_for(entered.wait(), 2)
    assert not task.done()
    assert ctx.manager._host_index.get(record.session_id) == record
    release.set()
    assert await asyncio.wait_for(task, 2) == int(confirmed)
    assert (ctx.manager._host_index.get(record.session_id) is None) is confirmed


@pytest.mark.parametrize("failure", [None, "relay", "forward"])
async def test_stranded_remote_reap_contains_channels_before_forgetting_authority(
    owned_context, monkeypatch, failure,
):
    from agent_bridge.session_host.version_mux import HostDisposition

    ctx = owned_context
    record = _remote_record(ctx)
    relay = SimpleNamespace(stop=AsyncMock())
    forward = SimpleNamespace(cancel=AsyncMock())
    ctx.manager._relays[record.session_id] = [relay]
    ctx.manager._forwards[record.session_id] = forward
    release = Mock()
    monkeypatch.setattr(ctx.manager, "_release_container_lock", release)
    monkeypatch.setattr(ctx.manager, "_remote_reap", AsyncMock(return_value=True))
    monkeypatch.setattr(
        "agent_bridge.session_host.version_mux.plan_host",
        lambda **_kwargs: SimpleNamespace(disposition=HostDisposition.FORCE_REAP, reason="obsolete host"),
    )
    failing = relay.stop if failure == "relay" else forward.cancel
    if failure is not None:
        failing.side_effect = OSError("channel cleanup failed")
        with pytest.raises(OSError, match="channel cleanup failed"):
            await ctx.manager.sweep_stranded_hosts()
        assert ctx.manager._host_index.get(record.session_id) == record
        assert (record.session_id in ctx.manager._relays) is (failure == "relay")
        assert (record.session_id in ctx.manager._forwards) is (failure == "forward")
        release.assert_not_called()
        failing.side_effect = None
    assert await ctx.manager.sweep_stranded_hosts() == 1
    assert ctx.manager._host_index.get(record.session_id) is None
    assert record.session_id not in ctx.manager._relays
    assert record.session_id not in ctx.manager._forwards
    release.assert_called_once_with(record.session_id)


@pytest.mark.parametrize("confirmed", [False, True])
async def test_end_joins_existing_remote_reap_before_cleanup(owned_context, monkeypatch, confirmed):
    from agent_bridge.session_manager import RemoteHostRecoveryPendingError

    ctx = owned_context
    record = _remote_record(ctx)
    entered, release = asyncio.Event(), asyncio.Event()

    async def reap(*_args):
        entered.set()
        await release.wait()
        return confirmed

    remote = AsyncMock(side_effect=reap)
    monkeypatch.setattr(ctx.manager, "_remote_reap", remote)
    ctx.manager._schedule_remote_reap(record, "existing cleanup")
    await asyncio.wait_for(entered.wait(), 2)
    end = asyncio.create_task(ctx.manager.end_session(record.session_id, force=True))
    await asyncio.sleep(0)
    assert not end.done()
    assert remote.await_count == 1
    release.set()
    if confirmed:
        await asyncio.wait_for(end, 2)
        assert ctx.manager.get_session(record.session_id) is None
    else:
        with pytest.raises(RemoteHostRecoveryPendingError, match="inconclusive"):
            await asyncio.wait_for(end, 2)
        assert ctx.manager._host_index.get(record.session_id) == record
    assert remote.await_count == 1


async def test_shutdown_retries_retained_failed_remote_reap(owned_context, monkeypatch):
    from agent_bridge.session_teardown import detach_for_restart

    ctx = owned_context
    record = _remote_record(ctx)
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def reap(*_args):
        nonlocal calls
        calls += 1
        if calls == 1:
            return False
        entered.set()
        await release.wait()
        return True

    monkeypatch.setattr(ctx.manager, "_remote_reap", reap)
    monkeypatch.setattr("agent_bridge.session_teardown._SHUTDOWN_CLEANUP_RETRY_SECONDS", 0.01)
    ctx.manager._schedule_remote_reap(record, "existing cleanup")
    pending = next(iter(ctx.manager._remote_reaps_by_session[record.session_id]))
    assert await pending is False
    await asyncio.sleep(0)
    shutdown = asyncio.create_task(detach_for_restart(ctx.manager))
    await asyncio.wait_for(entered.wait(), 2)
    assert not shutdown.done()
    assert ctx.manager._host_index.get(record.session_id) == record
    release.set()
    await asyncio.wait_for(shutdown, 2)
    assert calls == 2
    assert not ctx.manager._remote_reap_pending(record.session_id)
    assert ctx.manager._host_index.get(record.session_id) is None


@pytest.mark.parametrize("failed_attempts", [1, 2])
async def test_shutdown_retries_orphan_remote_reap(owned_context, monkeypatch, failed_attempts):
    from agent_bridge.session_teardown import detach_for_restart

    ctx = owned_context
    record = _remote_record(ctx)
    ctx.manager._sessions.pop(record.session_id)
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def reap(*_args):
        nonlocal calls
        calls += 1
        if calls <= failed_attempts:
            return False
        entered.set()
        await release.wait()
        return True

    monkeypatch.setattr(ctx.manager, "_remote_reap", reap)
    monkeypatch.setattr("agent_bridge.session_teardown._SHUTDOWN_CLEANUP_RETRY_SECONDS", 0.01)
    ctx.manager._schedule_remote_reap(record, "orphan authority")
    shutdown = asyncio.create_task(detach_for_restart(ctx.manager))
    await asyncio.wait_for(entered.wait(), 2)
    assert not shutdown.done()
    assert ctx.manager._host_index.get(record.session_id) == record
    assert ctx.manager._remote_reap_pending(record.session_id)
    release.set()
    await asyncio.wait_for(shutdown, 2)
    assert calls == failed_attempts + 1
    assert ctx.manager._host_index.get(record.session_id) is None
    assert not ctx.manager._remote_reap_pending(record.session_id)


async def test_shutdown_fails_for_unconfirmed_orphan_without_record(owned_context, monkeypatch):
    from agent_bridge.session_manager import RemoteHostRecoveryPendingError
    from agent_bridge.session_teardown import detach_for_restart

    ctx = owned_context
    record = _remote_record(ctx)
    ctx.manager._sessions.pop(record.session_id)
    monkeypatch.setattr(ctx.manager, "_remote_reap", AsyncMock(return_value=False))
    ctx.manager._schedule_remote_reap(record, "orphan authority")
    task = next(iter(ctx.manager._remote_reaps_by_session[record.session_id]))
    assert await task is False
    await asyncio.sleep(0)
    ctx.manager._host_index.remove(record.session_id)
    with pytest.raises(RemoteHostRecoveryPendingError, match="no authority record"):
        await detach_for_restart(ctx.manager)
    assert ctx.manager._remote_reap_pending(record.session_id)


@pytest.mark.parametrize("failure_stage", ["marker", "index"])
async def test_dead_authority_metadata_failure_retains_container_lock(
    owned_context, monkeypatch, failure_stage,
):
    ctx = owned_context
    record = _remote_record(ctx)
    session_id = record.session_id
    ctx.session.target.container = {"name": "example-container", "launch_pending_session_id": session_id}
    lock = Mock()
    ctx.manager._container_locks["example-container"] = (lock, session_id)
    ctx.manager._container_lock_sessions[session_id] = "example-container"
    spawner = SimpleNamespace(
        boundary="container", can_inspect_without_wake=AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        "agent_bridge.session_host.container_transport.build_container_spawner",
        Mock(return_value=spawner),
    )
    if failure_stage == "marker":
        monkeypatch.setattr(ctx.db, "update_session_target", Mock(side_effect=OSError("metadata failed")))
    else:
        monkeypatch.setattr(ctx.manager._host_index, "_flush", Mock(side_effect=OSError("metadata failed")))
    with pytest.raises(OSError, match="metadata failed"):
        await ctx.manager._recover_remote_host_records(session_ids={session_id})
    assert ctx.manager._host_index.get(session_id) == record
    assert ctx.session.target.container["launch_pending_session_id"] == session_id
    assert ctx.manager._container_lock_sessions[session_id] == "example-container"
    lock.release.assert_not_called()


async def test_resume_nudge_clear_can_retry_failed_persistence(owned_context, monkeypatch):
    from agent_bridge.session_host.host_index import HostIndex

    ctx = owned_context
    record = _remote_record(ctx)
    ctx.manager._host_index.set_resume_flag(record.session_id, True)
    flush = ctx.manager._host_index._flush
    monkeypatch.setattr(ctx.manager._host_index, "_flush", Mock(side_effect=OSError("flag failed")))
    with pytest.raises(OSError, match="flag failed"):
        ctx.manager._host_index.set_resume_flag(record.session_id, False)
    assert record.resume_on_reattach is True
    monkeypatch.setattr(ctx.manager._host_index, "_flush", flush)
    assert ctx.manager._host_index.set_resume_flag(record.session_id, False)
    assert record.resume_on_reattach is False
    assert not HostIndex(ctx.manager._host_index._path).get(record.session_id).resume_on_reattach


async def test_maintenance_candidate_rejects_equal_value_republication(owned_context):
    from dataclasses import replace

    ctx = owned_context
    record = _remote_record(ctx)
    candidate = next(item for item in ctx.manager._host_candidates() if item[0].session_id == record.session_id)
    assert ctx.manager._host_candidate_current(*candidate)
    ctx.manager._host_index.register(replace(record))
    assert not ctx.manager._host_candidate_current(*candidate)


@pytest.mark.parametrize("change", ["generation", "stopped", "stranded"])
async def test_maintenance_fence_rechecks_lifecycle_and_eligibility(owned_context, change):
    ctx = owned_context
    record = _remote_record(ctx)
    candidate = next(item for item in ctx.manager._host_candidates() if item[0].session_id == record.session_id)
    async with ctx.session._turn_start_lock, ctx.session._lifecycle_lock:
        assert ctx.manager._host_candidate_current(*candidate)
        if change == "generation":
            ctx.session._lifecycle_generation += 1
        elif change == "stopped":
            ctx.session.status = SessionStatus.STOPPED
            ctx.session.restart_status = None
        else:
            ctx.manager._sessions.pop(record.session_id)
            assert ctx.manager._host_candidate_current(candidate[0], None, None, candidate[3])
        assert ctx.manager._host_index.get(record.session_id) == candidate[0]
        assert ctx.manager._host_index.revision(record.session_id) == candidate[3]
        assert not ctx.manager._host_candidate_current(*candidate)


@pytest.mark.parametrize("restart_status", [None, "idle"])
async def test_failed_scheduled_reap_preserves_only_existing_restart_intent(
    owned_context, monkeypatch, restart_status,
):
    ctx = owned_context
    record = _remote_record(ctx)
    ctx.session.status = SessionStatus.STOPPED
    ctx.session.restart_status = restart_status
    ctx.db.update_session_status(
        record.session_id, "stopped", time.time(), restart_status=restart_status,
    )
    remote = AsyncMock(return_value=False)
    monkeypatch.setattr(ctx.manager, "_remote_reap", remote)
    ctx.manager._schedule_remote_reap(record, "existing cleanup")
    task = next(iter(ctx.manager._remote_reaps_by_session[record.session_id]))
    assert await task is False
    await asyncio.sleep(0)
    assert ctx.session.status == SessionStatus.FAILED
    assert ctx.session.restart_status == restart_status
    assert ctx.db.get_session(record.session_id)["restart_status"] == restart_status
    restarted = SessionManager(ctx.db)
    restored = restarted.get_session(record.session_id)
    assert restored.restart_status == restart_status
    assert not restarted._background_recovery_allowed(restored)
    remote.return_value = True
    await ctx.manager.stop_session(record.session_id, reap_host=True, for_restart=True)
    assert ctx.session.status == SessionStatus.STOPPED
    assert ctx.session.restart_status == restart_status
    assert ctx.manager._background_recovery_allowed(ctx.session) is (restart_status is not None)


async def test_authority_result_rejects_equal_value_republication(owned_context, monkeypatch):
    from dataclasses import replace

    ctx = owned_context
    record = _remote_record(ctx)
    replacement = replace(record)

    async def recover(_session_id):
        ctx.manager._host_index.register(replacement)
        return replace(record)

    spawner = SimpleNamespace(
        can_inspect_without_wake=AsyncMock(return_value=True),
        recover_record=AsyncMock(side_effect=recover),
    )
    monkeypatch.setattr(
        "agent_bridge.session_host.codespace_transport.build_codespace_spawner",
        Mock(return_value=spawner),
    )
    monkeypatch.setattr("agent_bridge.relay_state.get_live_relay_port", lambda: None)
    assert await ctx.manager._recover_remote_host_records(background=True) == 0
    assert ctx.manager._host_index.get(record.session_id) is replacement


@pytest.mark.parametrize("removal", ["remove", "prune"])
async def test_absent_authority_probe_rejects_add_remove_aba(owned_context, monkeypatch, removal):
    ctx = owned_context
    record = _remote_record(ctx)
    index = ctx.manager._host_index
    index.remove(record.session_id)
    absent_revision = index.revision(record.session_id)

    async def recover(_session_id):
        index.register(record)
        published_revision = index.revision(record.session_id)
        if removal == "remove":
            index.remove(record.session_id)
        else:
            index.prune_dead(lambda _pid: False)
        assert index.revision(record.session_id) > published_revision
        return record

    spawner = SimpleNamespace(
        can_inspect_without_wake=AsyncMock(return_value=True),
        recover_record=AsyncMock(side_effect=recover),
    )
    monkeypatch.setattr(
        "agent_bridge.session_host.codespace_transport.build_codespace_spawner",
        Mock(return_value=spawner),
    )
    monkeypatch.setattr("agent_bridge.relay_state.get_live_relay_port", lambda: None)
    assert await ctx.manager._recover_remote_host_records(background=True) == 0
    assert index.get(record.session_id) is None
    assert index.revision(record.session_id) > absent_revision


@pytest.mark.parametrize("removal", ["remove", "prune"])
async def test_removal_tombstone_is_published_only_after_flush(owned_context, monkeypatch, removal):
    ctx = owned_context
    record = _remote_record(ctx)
    index = ctx.manager._host_index
    revision = index.revision(record.session_id)
    flush = index._flush
    monkeypatch.setattr(index, "_flush", Mock(side_effect=OSError("removal write failed")))

    def remove():
        if removal == "remove":
            return index.remove(record.session_id)
        return index.prune_dead(lambda _pid: False)

    with pytest.raises(OSError, match="removal write failed"):
        remove()
    assert index.get(record.session_id) == record
    assert index.revision(record.session_id) == revision
    monkeypatch.setattr(index, "_flush", flush)
    remove()
    assert index.get(record.session_id) is None
    assert index.revision(record.session_id) > revision


async def test_end_preserves_removed_identity_when_index_write_fails(owned_context, monkeypatch):
    ctx = owned_context
    record = _remote_record(ctx)
    record.boundary = "container"
    ctx.manager._host_index.register(record)
    ctx.session.target = SpawnTarget(
        type="command",
        container={"name": "example-container", "authoritative_identity_removed": True},
    )
    lock = Mock()
    ctx.manager._container_locks["example-container"] = (lock, record.session_id)
    ctx.manager._container_lock_sessions[record.session_id] = "example-container"
    flush = ctx.manager._host_index._flush
    monkeypatch.setattr(ctx.manager._host_index, "_flush", Mock(side_effect=OSError("remove failed")))
    with pytest.raises(OSError, match="remove failed"):
        await ctx.manager.end_session(record.session_id, force=True)
    assert ctx.manager.get_session(record.session_id) is ctx.session
    assert ctx.db.get_session(record.session_id) is not None
    assert ctx.manager._host_index.get(record.session_id) == record
    lock.release.assert_not_called()
    monkeypatch.setattr(ctx.manager._host_index, "_flush", flush)
    await ctx.manager.end_session(record.session_id, force=True)
    assert ctx.manager.get_session(record.session_id) is None
    lock.release.assert_called_once()


async def test_scheduled_reap_without_endpoint_retains_failed_cleanup(owned_context, monkeypatch):
    ctx = owned_context
    record = _remote_record(ctx)
    record.endpoint = {}
    ctx.manager._host_index.register(record)
    remote = AsyncMock(side_effect=AssertionError("missing endpoint must not be probed"))
    monkeypatch.setattr(ctx.manager, "_remote_reap", remote)
    ctx.manager._schedule_remote_reap(record, "missing endpoint")
    task = next(iter(ctx.manager._remote_reaps_by_session[record.session_id]))
    assert await task is False
    await asyncio.sleep(0)
    assert ctx.manager._remote_reap_pending(record.session_id)
    assert ctx.manager._host_index.get(record.session_id) == record
    assert ctx.session.status == SessionStatus.FAILED
    remote.assert_not_awaited()


@pytest.mark.parametrize("relay_fails", [False, True])
async def test_stop_contains_independent_channels_after_process_failure(
    owned_context, monkeypatch, relay_fails,
):
    ctx = owned_context
    process = SimpleNamespace(alive=True, pid=123, proc=Mock())
    process.kill = AsyncMock(side_effect=PermissionError("process cleanup failed"))
    ctx.session._owned_process = process
    relay = SimpleNamespace(stop=AsyncMock(
        side_effect=OSError("relay cleanup failed") if relay_fails else None,
    ))
    forward = SimpleNamespace(cancel=AsyncMock())
    ctx.manager._relays[ctx.session.session_id] = [relay]
    ctx.manager._forwards[ctx.session.session_id] = forward
    with pytest.raises((PermissionError, OSError)):
        await ctx.manager.stop_session(ctx.session.session_id)
    relay.stop.assert_awaited_once()
    forward.cancel.assert_awaited_once()
    assert ctx.session.session_id not in ctx.manager._forwards
    assert ctx.session._owned_process is process
    assert ctx.session.status == SessionStatus.FAILED
    assert (ctx.session.session_id in ctx.manager._relays) is relay_fails


async def test_background_authority_probe_requires_turn_admission(owned_context, monkeypatch):
    ctx = owned_context
    record = _remote_record(ctx)
    entered, release = asyncio.Event(), asyncio.Event()

    async def inspect():
        assert ctx.session._turn_start_lock.locked()
        assert ctx.session._lifecycle_lock.locked()
        entered.set()
        await release.wait()
        return True

    spawner = SimpleNamespace(
        can_inspect_without_wake=AsyncMock(side_effect=inspect),
        recover_record=AsyncMock(return_value=record),
    )
    monkeypatch.setattr(
        "agent_bridge.session_host.codespace_transport.build_codespace_spawner",
        Mock(return_value=spawner),
    )
    monkeypatch.setattr("agent_bridge.relay_state.get_live_relay_port", lambda: None)
    async with ctx.session._turn_start_lock:
        assert await ctx.manager._recover_remote_host_records(background=True) == 0
    spawner.can_inspect_without_wake.assert_not_awaited()
    recovery = asyncio.create_task(ctx.manager._recover_remote_host_records(background=True))
    await asyncio.wait_for(entered.wait(), 2)
    stop = asyncio.create_task(ctx.manager.stop_session(record.session_id))
    await asyncio.sleep(0)
    assert not stop.done()
    release.set()
    await asyncio.wait_for(asyncio.gather(recovery, stop), 2)
    assert ctx.session.status == SessionStatus.STOPPED
    assert await ctx.manager._recover_remote_host_records(background=True) == 0
    spawner.can_inspect_without_wake.assert_awaited_once()
    spawner.recover_record.assert_awaited_once()


async def test_stale_reap_callback_cannot_remove_replacement_task_set(owned_context, monkeypatch):
    ctx = owned_context
    record = _remote_record(ctx)
    callbacks = []
    completed = Mock()
    completed.cancelled.return_value = False
    completed.exception.return_value = None
    completed.result.return_value = True
    completed.done.return_value = True
    completed.add_done_callback.side_effect = callbacks.append

    def create_task(operation):
        operation.close()
        return completed

    with monkeypatch.context() as patch:
        patch.setattr(
            "agent_bridge.session_manager.asyncio.get_running_loop",
            lambda: SimpleNamespace(create_task=create_task),
        )
        ctx.manager._schedule_remote_reap(record, "old cleanup")
    old_set = ctx.manager._remote_reaps_by_session.pop(record.session_id)
    newer = Mock()
    newer.done.return_value = False
    replacement = {newer}
    ctx.manager._remote_reaps_by_session[record.session_id] = replacement
    ctx.manager._remote_reap_tasks.add(newer)
    callbacks[0](completed)
    assert old_set == set()
    assert ctx.manager._remote_reaps_by_session[record.session_id] is replacement
    assert ctx.manager._remote_reap_pending(record.session_id)


@pytest.mark.parametrize("channel", ["relay", "forward"])
async def test_remote_end_retains_failed_channel_before_reaping(owned_context, monkeypatch, channel):
    ctx = owned_context
    record = _remote_record(ctx)
    relay = SimpleNamespace(stop=AsyncMock())
    forward = SimpleNamespace(cancel=AsyncMock())
    ctx.manager._relays[record.session_id] = [relay]
    ctx.manager._forwards[record.session_id] = forward
    failing = relay.stop if channel == "relay" else forward.cancel
    failing.side_effect = OSError("channel cleanup failed")
    remote = AsyncMock(return_value=True)
    monkeypatch.setattr(ctx.manager, "_remote_reap", remote)
    with pytest.raises(OSError, match="channel cleanup failed"):
        await ctx.manager.end_session(record.session_id, force=True)
    remote.assert_not_awaited()
    assert ctx.manager._host_index.get(record.session_id) == record
    assert ctx.manager.get_session(record.session_id) is ctx.session
    assert (record.session_id in ctx.manager._relays) is (channel == "relay")
    assert (record.session_id in ctx.manager._forwards) is (channel == "forward")
    failing.side_effect = None
    await ctx.manager.end_session(record.session_id, force=True)
    remote.assert_awaited_once()
    assert ctx.manager.get_session(record.session_id) is None


async def test_shutdown_retries_strict_acp_client_ownership(owned_context, monkeypatch):
    from agent_bridge.acp_client import AcpClient
    from agent_bridge.session_teardown import detach_for_restart

    ctx = owned_context
    client = AcpClient()
    client._host_mode = True
    entered, release = asyncio.Event(), asyncio.Event()
    attempts = 0

    async def close():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("ACP close failed")
        entered.set()
        await release.wait()

    client._connection = SimpleNamespace(close=close)
    client._host_closer = AsyncMock()
    ctx.session.client = client
    ctx.session.status = SessionStatus.IDLE
    monkeypatch.setattr("agent_bridge.session_teardown._SHUTDOWN_CLEANUP_RETRY_SECONDS", 0.01)
    shutdown = asyncio.create_task(detach_for_restart(ctx.manager))
    await asyncio.wait_for(entered.wait(), 2)
    assert not shutdown.done()
    assert ctx.session.client is client
    assert ctx.session.status == SessionStatus.FAILED
    release.set()
    await asyncio.wait_for(shutdown, 2)
    assert attempts == 2
    assert ctx.session.client is None
    assert ctx.session.status == SessionStatus.STOPPED
    assert ctx.session.restart_status == "idle"


@pytest.mark.parametrize("failure", [None, "client", "relay", "forward"])
async def test_committed_host_resume_cleanup_retains_failed_owners(
    owned_context, monkeypatch, failure,
):
    from agent_bridge.session_ownership import cleanup_resume_attempt

    ctx = owned_context
    record = _remote_record(ctx)
    client = SimpleNamespace(shutdown=AsyncMock())
    relay = SimpleNamespace(stop=AsyncMock())
    forward = SimpleNamespace(cancel=AsyncMock())
    ctx.session.client = client
    ctx.manager._relays[record.session_id] = [relay]
    ctx.manager._forwards[record.session_id] = forward
    release = Mock()
    remote = AsyncMock(side_effect=AssertionError("committed host must remain resumable"))
    monkeypatch.setattr(ctx.manager, "_release_container_lock", release)
    monkeypatch.setattr(ctx.manager, "_remote_reap", remote)
    operations = {"client": client.shutdown, "relay": relay.stop, "forward": forward.cancel}
    if failure is not None:
        operations[failure].side_effect = OSError("cleanup failed")
        with pytest.raises(OSError, match="cleanup failed"):
            await cleanup_resume_attempt(ctx.manager, ctx.session, client)
        assert ctx.session.status == SessionStatus.FAILED
        assert (ctx.session.client is client) is (failure == "client")
        assert (record.session_id in ctx.manager._relays) is (failure == "relay")
        assert (record.session_id in ctx.manager._forwards) is (failure == "forward")
        assert ctx.manager._host_index.get(record.session_id) == record
        release.assert_not_called()
        operations[failure].side_effect = None
        await cleanup_resume_attempt(ctx.manager, ctx.session, ctx.session.client)
    else:
        await cleanup_resume_attempt(ctx.manager, ctx.session, client)
        client.shutdown.assert_awaited_once_with(strict=True)
    assert ctx.session.client is None
    assert ctx.manager._forwards == {}
    assert ctx.manager._relays == {}
    assert ctx.manager._host_index.get(record.session_id) == record
    release.assert_not_called()
    remote.assert_not_awaited()


@pytest.mark.parametrize("gate", ["_draining", "_shutting_down"])
async def test_queued_prompt_rechecks_gate_after_admission_wait(owned_context, gate):
    from agent_bridge.session_manager import DaemonDrainingError

    ctx = owned_context
    async with ctx.session._turn_start_lock:
        submit = asyncio.create_task(ctx.manager.submit_prompt(ctx.session.session_id, "queued caller"))
        await asyncio.sleep(0)
        assert not submit.done()
        setattr(ctx.manager, gate, True)
    with pytest.raises(DaemonDrainingError):
        await asyncio.wait_for(submit, 2)
    assert ctx.db.get_turns(ctx.session.session_id) == []
    assert ctx.processes == []


async def test_newly_scheduled_reap_fences_legacy_resume_replacement(owned_context, monkeypatch):
    from agent_bridge.session_manager import RemoteHostRecoveryPendingError

    ctx = owned_context
    record = _remote_record(ctx)
    record.extra = {}
    ctx.manager._host_index.register(record)
    ctx.session.status = SessionStatus.STOPPED
    release = asyncio.Event()
    tasks = []
    forward = SimpleNamespace(refresh=AsyncMock(), cancel=AsyncMock())
    ctx.manager._forwards[record.session_id] = forward

    async def reap(*_args):
        await release.wait()
        return True

    async def attach(*_args, **_kwargs):
        ctx.manager._schedule_remote_reap(record, "child exit during reattach")
        tasks.extend(ctx.manager._remote_reaps_by_session[record.session_id])
        return False

    monkeypatch.setattr(ctx.manager, "_remote_reap", reap)
    monkeypatch.setattr(ctx.manager, "_reattach_one", attach)
    monkeypatch.setattr(
        ctx.manager, "_try_reattach_live_host",
        SessionManager._try_reattach_live_host.__get__(ctx.manager),
    )
    try:
        with pytest.raises(RemoteHostRecoveryPendingError, match="replacement spawn"):
            await ctx.manager.resume_session(record.session_id, drain=False)
        ctx.manager._resume_via_new_remote_host.assert_not_awaited()
        with pytest.raises(RemoteHostRecoveryPendingError, match="replacement channel"):
            await ctx.manager._ensure_forward(record)
        forward.refresh.assert_not_awaited()
        assert ctx.processes == []
    finally:
        release.set()
        await asyncio.gather(*tasks)


async def test_remote_reap_does_not_close_replacement_channels(owned_context, monkeypatch):
    from dataclasses import replace
    from agent_bridge.session_teardown import reap_remote_record

    ctx = owned_context
    record = _remote_record(ctx)
    replacement = replace(record, nonce="new-authority")
    old_forward = SimpleNamespace(cancel=AsyncMock())
    new_forward = SimpleNamespace(cancel=AsyncMock())
    new_relay = SimpleNamespace(stop=AsyncMock())
    new_relays = [new_relay]

    async def close_old_relay():
        ctx.manager._host_index.register(replacement)
        ctx.manager._forwards[record.session_id] = new_forward
        ctx.manager._relays[record.session_id] = new_relays

    old_relay = SimpleNamespace(stop=AsyncMock(side_effect=close_old_relay))
    ctx.manager._forwards[record.session_id] = old_forward
    ctx.manager._relays[record.session_id] = [old_relay]
    monkeypatch.setattr(ctx.manager, "_remote_reap", AsyncMock(return_value=True))
    assert await reap_remote_record(ctx.manager, record)
    old_relay.stop.assert_awaited_once()
    old_forward.cancel.assert_awaited_once()
    new_relay.stop.assert_not_awaited()
    new_forward.cancel.assert_not_awaited()
    assert ctx.manager._host_index.get(record.session_id) is replacement
    assert ctx.manager._forwards[record.session_id] is new_forward
    assert ctx.manager._relays[record.session_id] is new_relays
