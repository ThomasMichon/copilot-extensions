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
        return await manager._connect_via_session_host(
            current.target,
            tracker=ConnectTracker(current.event_log.append, session_id=current.session_id),
            session_id=current.session_id,
            on_acp_event=on_acp_event, permission_callback=permission_callback,
            spawner=spawner, remote_child_argv=["example-agent"],
            remote_cwd="/example/workspace", load_session_id=current.acp_session_id,
        )

    monkeypatch.setattr(manager, "_resume_via_new_remote_host", host_resume)
    task = asyncio.create_task(manager.resume_session(session.session_id, drain=False))
    await asyncio.wait_for(entered.wait(), 2)
    if phase != "spawn":
        assert manager._host_index.get(session.session_id) is not None
        assert session.session_id in manager._pending_host_launches
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
        if abort_confirmed else RemoteSpawnCleanupPendingError
    )
    with pytest.raises(error):
        await asyncio.wait_for(task, 3)
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
        await manager.stop_session(session.session_id)
        restarted = SessionManager(tmp_db, session_host_state_dir=state_dir)
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
    attach.assert_not_awaited()
    assert manager._host_index.get(session_id) is None
    assert manager.get_session(session_id).target.container["launch_pending_session_id"] == session_id


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
async def test_partial_host_reaping_releases_only_confirmed_container_claim(
    tmp_db, tmp_path, monkeypatch, indexed, confirmed,
):
    from agent_bridge.session_host_ownership import _remember

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
    if confirmed:
        await manager.stop_session(session_id, reap_host=True)
        lock.release.assert_called_once()
        assert session_id not in manager._container_lock_sessions
        assert manager._host_index.get(session_id) is None
        assert session_id not in manager._pending_host_launches
        assert "launch_pending_session_id" not in session.target.container
        assert session.status == SessionStatus.STOPPED
    else:
        with pytest.raises(RemoteSpawnCleanupPendingError, match="cleanup is inconclusive"):
            await manager.stop_session(session_id, reap_host=True)
        lock.release.assert_not_called()
        assert manager._container_lock_sessions[session_id] == "example-container"
        assert session_id in manager._pending_host_launches
        assert session.target.container["launch_pending_session_id"] == session_id
        assert session.status == SessionStatus.FAILED
    spawner.abort_spawned.assert_awaited_once()
    reap.assert_not_awaited()
