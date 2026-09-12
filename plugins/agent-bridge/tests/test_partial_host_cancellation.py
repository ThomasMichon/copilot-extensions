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
@pytest.mark.parametrize("phase", ["spawn", "connect", "attach", "streams", "initialize", "load"])
@pytest.mark.parametrize("abort_confirmed", [False, True])
async def test_cancelled_host_launch_aborts_or_retains_retry_authority(
    tmp_db, tmp_path, monkeypatch, phase, abort_confirmed,
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
    task.cancel()
    if phase == "spawn":
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
    error = asyncio.CancelledError if abort_confirmed else RemoteSpawnCleanupPendingError
    with pytest.raises(error):
        await asyncio.wait_for(task, 3)
    spawner.abort_spawned.assert_awaited_once()
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
        assert session.status == SessionStatus.FAILED
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
