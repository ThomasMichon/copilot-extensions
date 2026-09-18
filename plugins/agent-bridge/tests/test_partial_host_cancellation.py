"""Partial Session Host ownership survives every pre-client cancellation window."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from agent_bridge.connect import ConnectTracker
from agent_bridge.events import EventLog
from agent_bridge.models import SessionStatus
from agent_bridge.session_host.protocol import PROTOCOL_VERSION
from agent_bridge.session_host.spawner import RemoteSpawnCleanupPendingError, SpawnedHost
from agent_bridge.session_manager import Session, SessionManager
from agent_bridge.transport import SpawnTarget


@pytest.mark.asyncio
@pytest.mark.parametrize("phase,fault", [
    ("spawn", "cancel"), ("connect", "cancel"), ("attach", "cancel"),
    ("streams", "cancel"), ("initialize", "cancel"), ("load", "cancel"),
    ("connect", "error"), ("attach", "error"), ("streams", "error"),
    ("committed", "cancel"),
])
@pytest.mark.parametrize("abort_confirmed", [False, True])
async def test_cancelled_host_launch_aborts_or_retains_retry_authority(
    tmp_db, tmp_path, monkeypatch, phase, fault, abort_confirmed,
):
    state_dir = str(tmp_path / "hosts")
    manager = SessionManager(tmp_db, session_host_state_dir=state_dir)
    target = SpawnTarget(type="local", cwd=str(tmp_path))
    session = Session("example-session", "example-agent", target)
    session.status = SessionStatus.STOPPED
    session.acp_session_id = "example-acp"
    if phase == "committed":
        session.acp_session_id = None
    tmp_db.create_session(
        session.session_id, session.name, None, str(tmp_path), "local", "stopped",
        time.time(), target_json=target.to_json(),
    )
    tmp_db.update_session_acp_id(session.session_id, session.acp_session_id)
    session.event_log = EventLog(db=tmp_db, session_id=session.session_id)
    manager._sessions[session.session_id] = session
    entered, release = asyncio.Event(), asyncio.Event()
    forward = SimpleNamespace(cancel=AsyncMock())
    relay = SimpleNamespace(stop=AsyncMock())
    spawned = SpawnedHost(
        local_port=51000, host_pid=123, child_pid=456,
        protocol_version=PROTOCOL_VERSION, boundary="codespace",
        nonce="example-nonce", state_file="/example/host.json",
        endpoint={"kind": "codespace", "codespace": "example-space"},
        forward=forward, relay=relay,
    )

    async def stage(name):
        if phase == name:
            entered.set()
            await release.wait()
            if fault == "error":
                raise OSError("handshake failure")

    async def spawn(*_args, **_kwargs):
        await stage("spawn")
        return spawned

    spawner = SimpleNamespace(
        boundary="codespace",
        spawn=AsyncMock(side_effect=spawn),
        abort_spawned=AsyncMock(return_value=abort_confirmed),
    )
    sock = SimpleNamespace(terminate=AsyncMock(), close=AsyncMock())

    async def connect(**_kwargs):
        await stage("connect")
        return sock

    async def attach(*_args, **_kwargs):
        await stage("attach")

    sock.attach = AsyncMock(side_effect=attach)
    streams = SimpleNamespace(reader=Mock(), writer=Mock(), child_exit_code=None, aclose=AsyncMock())

    async def open_streams(_sock):
        await stage("streams")
        return streams

    async def initialize(*_args, **_kwargs):
        await stage("initialize")

    async def load(**_kwargs):
        await stage("load")

    client = SimpleNamespace(
        start_streams=AsyncMock(side_effect=initialize),
        load_session=AsyncMock(side_effect=load),
        new_session=AsyncMock(return_value="new-acp"),
        shutdown=AsyncMock(),
        mark_transport_lost=Mock(),
        mark_host_child_exited=Mock(),
    )
    monkeypatch.setattr(
        "agent_bridge.session_host.client.SessionHostClient.connect", connect,
    )
    monkeypatch.setattr("agent_bridge.session_host.acp_adapter.open_acp_streams", open_streams)
    monkeypatch.setattr("agent_bridge.session_manager.AcpClient", lambda **_kwargs: client)
    monkeypatch.setattr(manager, "_try_reattach_live_host", AsyncMock(return_value=False))
    monkeypatch.setattr(manager, "_refresh_provider_target", AsyncMock())
    monkeypatch.setattr("agent_bridge.session_manager._MAX_RESUME_ROUNDS", 1)
    monkeypatch.setattr("agent_bridge.session_manager._cleanup_worktree", AsyncMock())

    async def host_resume(current, *, on_acp_event, permission_callback, load_existing):
        result = await manager._connect_via_session_host(
            current.target,
            tracker=ConnectTracker(current.event_log.append, session_id=current.session_id),
            session_id=current.session_id,
            on_acp_event=on_acp_event, permission_callback=permission_callback,
            spawner=spawner, remote_child_argv=["example-agent"],
            remote_cwd="/example/workspace", load_session_id=current.acp_session_id,
        )
        # Container resume can await preparation cleanup after the host commits.
        await stage("committed")
        return result

    monkeypatch.setattr(manager, "_resume_via_new_remote_host", host_resume)
    task = asyncio.create_task(manager.resume_session(
        session.session_id, drain=False, allow_recreate=phase == "committed",
    ))
    await asyncio.wait_for(entered.wait(), 2)
    if phase != "spawn":
        assert manager._host_index.get(session.session_id) is not None
        assert (session.session_id in manager._pending_host_launches) is (phase != "committed")
    if fault == "cancel":
        task.cancel()
        if phase == "spawn":
            await asyncio.sleep(0)
            assert not task.done()
            release.set()
    else:
        release.set()
    error = (
        (asyncio.CancelledError if fault == "cancel" else Exception)
        if abort_confirmed or phase == "committed" else RemoteSpawnCleanupPendingError
    )
    with pytest.raises(error):
        await asyncio.wait_for(task, 3)
    if phase == "committed":
        spawner.abort_spawned.assert_not_awaited()
        client.shutdown.assert_awaited_once()
        assert session.client is None
        assert session.status == SessionStatus.STOPPED
        assert session.acp_session_id == "new-acp"
        assert tmp_db.get_session(session.session_id)["acp_session_id"] == "new-acp"
        assert not manager._host_index.get(session.session_id).extra.get("launch_cleanup_pending")
        restarted = SessionManager(tmp_db, session_host_state_dir=state_dir)
        assert restarted.get_session(session.session_id).acp_session_id == "new-acp"
        assert restarted._host_index.get(session.session_id) is not None
        return
    # Inconclusive handshake rollback is retried by the outer resume cleanup.
    expected_aborts = 2 if not abort_confirmed and phase != "spawn" else 1
    assert spawner.abort_spawned.await_count == expected_aborts
    assert manager._forwards == {}
    assert manager._relays == {}
    if abort_confirmed:
        assert manager._host_index.get(session.session_id) is None
        assert manager._pending_host_launches == {}
        assert session.status == SessionStatus.STOPPED
    else:
        record = manager._host_index.get(session.session_id)
        assert record.extra["launch_cleanup_pending"] is True
        assert session.session_id in manager._pending_host_launches
        assert session.status in (SessionStatus.FAILED, SessionStatus.STOPPED)
        assert not manager._background_recovery_allowed(session)
        with pytest.raises(RemoteSpawnCleanupPendingError, match="cleanup is inconclusive"):
            await manager.stop_session(session.session_id)
        restarted = SessionManager(tmp_db, session_host_state_dir=state_dir)
        await restarted.stop_session(session.session_id)
        with pytest.raises(RemoteSpawnCleanupPendingError, match="cleanup is pending"):
            await restarted.resume_session(session.session_id, drain=False)
        assert spawner.spawn.await_count == 1
        spawner.abort_spawned.return_value = True
        await manager.end_session(session.session_id, force=True)
        assert manager._host_index.get(session.session_id) is None
        assert manager._pending_host_launches == {}


@pytest.mark.asyncio
async def test_durable_container_marker_blocks_resume_without_host_index(
    tmp_db, tmp_path, monkeypatch,
):
    session_id = "example-session"
    target = SpawnTarget(
        type="command",
        container={"name": "example-container", "launch_pending_session_id": session_id},
    )
    tmp_db.create_session(
        session_id, "example-agent", None, ".", "command", "stopped",
        time.time(), target_json=target.to_json(),
    )
    tmp_db.update_session_acp_id(session_id, "example-acp")
    manager = SessionManager(tmp_db, session_host_state_dir=str(tmp_path / "hosts"))
    attach = AsyncMock(side_effect=AssertionError("pending host must not be resumed"))
    monkeypatch.setattr(manager, "_try_reattach_live_host", attach)
    with pytest.raises(RemoteSpawnCleanupPendingError, match="cleanup is pending"):
        await manager.resume_session(session_id)
    with pytest.raises(RemoteSpawnCleanupPendingError, match="cleanup is pending"):
        await manager.resync_session(session_id)
    with pytest.raises(RemoteSpawnCleanupPendingError, match="cleanup is pending"):
        await manager.stop_session(session_id, reap_host=True)
    attach.assert_not_awaited()
    assert manager._host_index.get(session_id) is None
    assert manager.get_session(session_id).target.container["launch_pending_session_id"] == session_id
    assert manager.get_session(session_id).status == SessionStatus.FAILED
    assert tmp_db.get_session(session_id)["status"] == "failed"


@pytest.mark.asyncio
async def test_restarted_partial_container_never_enters_background_recovery(
    tmp_db, tmp_path, monkeypatch,
):
    session_id = "example-session"
    target = SpawnTarget(
        type="command",
        container={"name": "example-container", "launch_pending_session_id": session_id},
    )
    tmp_db.create_session(
        session_id, "example-agent", None, ".", "command", "starting",
        time.time(), target_json=target.to_json(),
    )
    tmp_db.update_session_acp_id(session_id, "example-acp")
    manager = SessionManager(tmp_db, session_host_state_dir=str(tmp_path / "hosts"))
    factory = Mock(side_effect=AssertionError("partial launch must not probe a provider"))
    monkeypatch.setattr("agent_bridge.session_host.container_transport.build_container_spawner", factory)
    session = manager.get_session(session_id)
    assert session.restart_status == "starting"
    assert not manager._background_recovery_allowed(session)
    assert await manager._recover_remote_host_records(background=True) == 0
    assert await manager.reattach_session_hosts() == 0
    factory.assert_not_called()
    assert manager._host_index.get(session_id) is None
    assert session.target.container["launch_pending_session_id"] == session_id


def test_dead_host_pruning_retains_partial_launch_authority(tmp_db, tmp_path, monkeypatch):
    from agent_bridge.session_host.host_index import HostRecord

    session_id = "example-session"
    tmp_db.create_session(
        session_id, "example-agent", None, ".", "local", "starting", time.time(),
    )
    manager = SessionManager(tmp_db, session_host_state_dir=str(tmp_path / "hosts"))
    record = HostRecord(
        session_id=session_id, port=51000, host_pid=123, child_pid=456,
        extra={"launch_cleanup_pending": True},
    )
    manager._host_index.register(record)
    alive = Mock(return_value=False)
    monkeypatch.setattr(manager, "_rec_host_alive", alive)
    manager._prune_dead_hosts()
    alive.assert_not_called()
    assert manager._host_index.get(session_id) == record


@pytest.mark.asyncio
@pytest.mark.parametrize("indexed", [False, True])
@pytest.mark.parametrize("confirmed", [False, True])
@pytest.mark.parametrize("operation", ["stop", "ordinary-stop", "rollback"])
async def test_partial_host_reaping_releases_only_confirmed_container_claim(
    tmp_db, tmp_path, monkeypatch, indexed, confirmed, operation,
):
    from agent_bridge.session_host_ownership import _remember, abort_pending_host_launch

    manager = SessionManager(tmp_db, session_host_state_dir=str(tmp_path / "hosts"))
    session_id = "example-session"
    target = SpawnTarget(type="command", container={"name": "example-container"})
    session = Session(session_id, "example-agent", target)
    session.status = SessionStatus.STOPPED
    tmp_db.create_session(
        session_id, session.name, None, ".", "command", "stopped",
        time.time(), target_json=target.to_json(),
    )
    manager._sessions[session_id] = session
    lock = Mock()
    manager._container_locks["example-container"] = (lock, session_id)
    manager._container_lock_sessions[session_id] = "example-container"
    spawned = SpawnedHost(
        local_port=51000, host_pid=123, child_pid=456,
        protocol_version=PROTOCOL_VERSION, boundary="container",
        nonce="example-nonce", state_file="/example/host.json",
        endpoint={"kind": "container", "container": "example-container"},
    )
    spawner = SimpleNamespace(abort_spawned=AsyncMock(return_value=confirmed))
    _remember(manager, session_id, spawner, spawned)
    if not indexed:
        manager._host_index.remove(session_id)
    reap = AsyncMock(side_effect=AssertionError("partial launch must use its abort handle"))
    monkeypatch.setattr(manager, "_remote_reap", reap)
    cleanup = (
        manager.stop_session(session_id, reap_host=operation == "stop")
        if operation != "rollback" else abort_pending_host_launch(manager, session_id)
    )
    if confirmed:
        await cleanup
        lock.release.assert_called_once()
        assert session_id not in manager._container_lock_sessions
        assert manager._host_index.get(session_id) is None
        assert session_id not in manager._pending_host_launches
        assert "launch_pending_session_id" not in session.target.container
        assert session.status == SessionStatus.STOPPED
        if operation == "ordinary-stop":
            from agent_bridge.session_teardown import detach_for_restart

            probe = Mock(side_effect=AssertionError("stopped target must remain dormant"))
            monkeypatch.setattr("agent_bridge.session_host.container_transport.build_container_spawner", probe)
            assert await manager.reattach_session_hosts() == 0
            assert await manager.recover_disconnected_hosts() == 0
            await detach_for_restart(manager)
            probe.assert_not_called()
    else:
        with pytest.raises(RemoteSpawnCleanupPendingError, match="cleanup is inconclusive"):
            await cleanup
        lock.release.assert_not_called()
        assert manager._container_lock_sessions[session_id] == "example-container"
        assert session_id in manager._pending_host_launches
        assert session.target.container["launch_pending_session_id"] == session_id
        assert session.status == (SessionStatus.STOPPED if operation == "rollback" else SessionStatus.FAILED)
    spawner.abort_spawned.assert_awaited_once()
    reap.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("persistence_stage", ["index", "marker"])
@pytest.mark.parametrize("confirmed", [False, True])
async def test_host_persistence_failure_aborts_before_returning(
    tmp_db, tmp_path, monkeypatch, persistence_stage, confirmed,
):
    from agent_bridge.session_host_ownership import spawn_host_owned
    from agent_bridge.session_teardown import detach_for_restart

    manager = SessionManager(tmp_db, session_host_state_dir=str(tmp_path / "hosts"))
    session = Session(
        "example-session", "example-agent",
        SpawnTarget(type="command", container={"name": "example-container"}),
    )
    tmp_db.create_session(
        session.session_id, session.name, None, ".", "command", "starting",
        time.time(), target_json=session.target.to_json(),
    )
    manager._sessions[session.session_id] = session
    spawned = SpawnedHost(
        local_port=51000, host_pid=123, child_pid=456, boundary="container",
        protocol_version=PROTOCOL_VERSION, nonce="example-nonce",
        state_file="/example/host.json",
        endpoint={"kind": "container", "container": "example-container"},
    )
    spawner = SimpleNamespace(abort_spawned=AsyncMock(return_value=confirmed))
    if persistence_stage == "index":
        monkeypatch.setattr(
            manager._host_index, "register", Mock(side_effect=OSError("persist failed")),
        )
    else:
        set_marker = manager._set_container_launch_pending

        def fail_marker(session_id, pending):
            if pending:
                raise OSError("persist failed")
            set_marker(session_id, pending)

        monkeypatch.setattr(manager, "_set_container_launch_pending", fail_marker)
    error = RuntimeError if confirmed else RemoteSpawnCleanupPendingError
    with pytest.raises(error):
        await spawn_host_owned(manager, session.session_id, spawner, AsyncMock(return_value=spawned)())
    spawner.abort_spawned.assert_awaited_once()
    if confirmed:
        assert manager._pending_host_launches == {}
        assert manager._host_index.get(session.session_id) is None
    else:
        assert not manager._pending_host_launches[session.session_id].durable
        monkeypatch.setattr("agent_bridge.session_teardown._SHUTDOWN_CLEANUP_RETRY_SECONDS", 0.01)
        entered, release = asyncio.Event(), asyncio.Event()

        async def abort(_spawned, _session_id):
            entered.set()
            await release.wait()
            return True

        spawner.abort_spawned.side_effect = abort
        shutdown = asyncio.create_task(detach_for_restart(manager))
        await asyncio.wait_for(entered.wait(), 2)
        assert not shutdown.done()
        assert session.session_id in manager._pending_host_launches
        release.set()
        await asyncio.wait_for(shutdown, 2)
        assert manager._pending_host_launches == {}


@pytest.mark.parametrize("indexed", [False, True])
def test_dormant_container_retains_launch_fence_after_restart(
    tmp_db, tmp_path, monkeypatch, indexed,
):
    from agent_bridge.session_host.host_index import HostRecord

    session_id = "example-session"
    target = SpawnTarget(type="command", container={"name": "example-container"})
    if not indexed:
        target.container["launch_pending_session_id"] = session_id
    tmp_db.create_session(
        session_id, "example-agent", None, ".", "command", "stopped",
        time.time(), target_json=target.to_json(),
    )
    state_dir = str(tmp_path / "hosts")
    manager = SessionManager(tmp_db, session_host_state_dir=state_dir)
    if indexed:
        manager._host_index.register(HostRecord(
            session_id=session_id, port=51000, host_pid=123, child_pid=456,
            boundary="container",
        ))
    restarted = SessionManager(tmp_db, session_host_state_dir=state_dir)
    lock = Mock()
    factory = Mock(return_value=lock)
    monkeypatch.setattr("ssh_manager.TargetLock", factory)
    with pytest.raises(RuntimeError, match="retains host ownership"):
        restarted._acquire_container_lock("another-session", "example-container")
    factory.assert_not_called()
    restarted._acquire_container_lock(session_id, "example-container")
    lock.acquire.assert_called_once()
    restarted._release_container_lock(session_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("flush_before_failure", [False, True])
async def test_completed_host_persistence_failure_remains_rollbackable(
    tmp_db, tmp_path, monkeypatch, flush_before_failure,
):
    from agent_bridge.session_host_ownership import (
        _remember, abort_pending_host_launch, complete_host_launch,
    )

    state_dir = str(tmp_path / "hosts")
    manager = SessionManager(tmp_db, session_host_state_dir=state_dir)
    session = Session("example-session", "example-agent", SpawnTarget(type="command"))
    tmp_db.create_session(
        session.session_id, session.name, None, ".", "command", "starting", time.time(),
    )
    manager._sessions[session.session_id] = session
    spawned = SpawnedHost(
        local_port=51000, host_pid=123, child_pid=456, boundary="container",
        protocol_version=PROTOCOL_VERSION, nonce="example-nonce",
        state_file="/example/host.json",
        endpoint={"kind": "container", "container": "example-container"},
    )
    spawner = SimpleNamespace(abort_spawned=AsyncMock(return_value=True))
    pending = _remember(manager, session.session_id, spawner, spawned)
    client = SimpleNamespace(shutdown=AsyncMock())
    pending.client = client
    flush = manager._host_index._flush

    def fail():
        if flush_before_failure:
            flush()
        raise OSError("index write failed")

    monkeypatch.setattr(manager._host_index, "_flush", fail)
    with pytest.raises(OSError, match="index write failed"):
        complete_host_launch(manager, session.session_id, client, "example-acp")
    assert manager._host_index.get(session.session_id) == pending.record
    with pytest.raises(OSError, match="index write failed"):
        manager._host_index.remove(session.session_id)
    assert manager._host_index.get(session.session_id) == pending.record
    monkeypatch.setattr(manager._host_index, "_flush", flush)
    await abort_pending_host_launch(manager, session.session_id)
    assert manager._pending_host_launches == {}
    assert manager._host_index.get(session.session_id) is None
    restarted = SessionManager(tmp_db, session_host_state_dir=state_dir)
    assert restarted._host_index.get(session.session_id) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["terminate", "streams", "socket", "client", "spawned", "forward"])
async def test_partial_rollback_retains_failed_channel_until_retry(tmp_db, tmp_path, monkeypatch, phase):
    from agent_bridge.session_host_ownership import _remember, abort_pending_host_launch

    manager = SessionManager(tmp_db, session_host_state_dir=str(tmp_path / "hosts"))
    session = Session(
        "example-session", "example-agent",
        SpawnTarget(type="command", container={"name": "example-container"}),
    )
    tmp_db.create_session(
        session.session_id, session.name, None, ".", "command", "starting",
        time.time(), target_json=session.target.to_json(),
    )
    manager._sessions[session.session_id] = session
    forward = SimpleNamespace(cancel=AsyncMock())
    spawned = SpawnedHost(
        local_port=51000, host_pid=123, child_pid=456, boundary="container",
        protocol_version=PROTOCOL_VERSION, nonce="example-nonce",
        state_file="/example/host.json",
        endpoint={"kind": "container", "container": "example-container"},
        forward=forward,
    )
    spawner = SimpleNamespace(abort_spawned=AsyncMock(return_value=True))
    pending = _remember(manager, session.session_id, spawner, spawned)
    sock = SimpleNamespace(terminate=AsyncMock(), close=AsyncMock())
    streams = SimpleNamespace(aclose=AsyncMock())
    client = SimpleNamespace(shutdown=AsyncMock())
    pending.sock, pending.streams, pending.client = sock, streams, client
    close_spawned = AsyncMock()
    monkeypatch.setattr(spawned, "aclose", close_spawned)
    lock = Mock()
    manager._container_locks["example-container"] = (lock, session.session_id)
    manager._container_lock_sessions[session.session_id] = "example-container"
    stages = {
        "terminate": sock.terminate, "streams": streams.aclose,
        "socket": sock.close, "client": client.shutdown,
        "spawned": close_spawned, "forward": forward.cancel,
    }
    stages[phase].side_effect = OSError("local cleanup failed")
    with pytest.raises(RemoteSpawnCleanupPendingError, match="cleanup is inconclusive"):
        await abort_pending_host_launch(manager, session.session_id)
    for operation in stages.values():
        operation.assert_awaited_once()
    spawner.abort_spawned.assert_awaited_once()
    assert manager._host_index.get(session.session_id) == pending.record
    assert manager._pending_host_launches[session.session_id] is pending
    assert session.target.container["launch_pending_session_id"] == session.session_id
    lock.release.assert_not_called()
    stages[phase].side_effect = None
    await abort_pending_host_launch(manager, session.session_id)
    assert manager._host_index.get(session.session_id) is None
    assert manager._pending_host_launches == {}
    assert "launch_pending_session_id" not in session.target.container
    lock.release.assert_called_once()
    if phase == "terminate":
        sock.terminate.assert_awaited_once()


@pytest.mark.asyncio
async def test_shutdown_retries_durable_partial_launch_until_abort_confirmed(tmp_db, tmp_path, monkeypatch):
    from agent_bridge.session_host_ownership import _remember
    from agent_bridge.session_teardown import detach_for_restart

    manager = SessionManager(tmp_db, session_host_state_dir=str(tmp_path / "hosts"))
    session = Session(
        "example-session", "example-agent",
        SpawnTarget(type="command", container={"name": "example-container"}),
    )
    session.status = SessionStatus.STARTING
    tmp_db.create_session(
        session.session_id, session.name, None, ".", "command", "starting",
        time.time(), target_json=session.target.to_json(),
    )
    manager._sessions[session.session_id] = session
    spawned = SpawnedHost(
        local_port=51000, host_pid=123, child_pid=456, boundary="container",
        protocol_version=PROTOCOL_VERSION, nonce="example-nonce",
        state_file="/example/host.json",
        endpoint={"kind": "container", "container": "example-container"},
    )
    entered, release = asyncio.Event(), asyncio.Event()
    attempts = 0

    async def abort(*_args):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return False
        entered.set()
        await release.wait()
        return True

    spawner = SimpleNamespace(abort_spawned=AsyncMock(side_effect=abort))
    pending = _remember(manager, session.session_id, spawner, spawned)
    assert pending.durable
    monkeypatch.setattr("agent_bridge.session_teardown._SHUTDOWN_CLEANUP_RETRY_SECONDS", 0.01)
    shutdown = asyncio.create_task(detach_for_restart(manager))
    await asyncio.wait_for(entered.wait(), 2)
    assert not shutdown.done()
    assert manager._pending_host_launches[session.session_id] is pending
    assert manager._host_index.get(session.session_id) is not None
    release.set()
    await asyncio.wait_for(shutdown, 2)
    assert attempts == 2
    assert manager._pending_host_launches == {}
    assert manager._host_index.get(session.session_id) is None
    assert session.status == SessionStatus.STOPPED
