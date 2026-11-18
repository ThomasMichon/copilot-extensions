"""Passive promotion refreshes inherited projections without disrupting local readers."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import aclosing
import os
from pathlib import Path
import threading
import time
from typing import Any, cast
from unittest.mock import Mock

import pytest
from zdd import diagnostics, process_exit

from agent_bridge.db import Database
from agent_bridge.events import EventLog
from agent_bridge.passive_maintenance import refresh_inherited_sessions
from agent_bridge.routes.sessions import _sse_event_stream
from agent_bridge.session_host.host_index import HostRecord
from agent_bridge.session_manager import SessionManager
from agent_bridge.session_status import SessionStatus


@pytest.mark.asyncio
@pytest.mark.parametrize("interrupt", ["stop-notice", "cancel"])
async def test_history_read_yields_serving_loop_and_cannot_publish_after_interruption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, interrupt: str,
) -> None:
    (tmp_path / "hosts").mkdir()
    db = Database(str(tmp_path / "sessions.db"))
    release = threading.Event()
    try:
        db.create_session("worker", "worker", None, str(tmp_path), "local", "running", time.time())
        manager = SessionManager(db, read_only_rehydrate=True)
        session = manager.get_session("worker")
        assert session is not None
        session.awaiting_maintenance_refresh = True
        db.update_session_stopped("worker", time.time(), False)
        entered = asyncio.Event()
        loop = asyncio.get_running_loop()
        read_events = db.get_events

        def delayed(sid: str, *, after: int = 0) -> list[dict[str, Any]]:
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(5), "the serving loop did not run during a blocked history read"
            return read_events(sid, after=after)

        monkeypatch.setattr(db, "get_events", delayed)
        refresh = asyncio.create_task(refresh_inherited_sessions(manager, lambda: True))
        cancelled = False
        try:
            await asyncio.wait_for(entered.wait(), 3)
            assert session.status == SessionStatus.RUNNING and session.awaiting_maintenance_refresh
            if interrupt == "cancel":
                cancelled = refresh.cancel()
                await asyncio.sleep(0)
                assert session._lifecycle_lock.locked() and not refresh.done()
            else:
                session._stop_notices += 1
        finally:
            release.set()
            if cancelled:
                with pytest.raises(asyncio.CancelledError):
                    await refresh
            else:
                await refresh
        assert session.awaiting_maintenance_refresh and not session._lifecycle_lock.locked()
        assert session.status == SessionStatus.RUNNING
        await refresh_inherited_sessions(manager, lambda: True)
        assert not session.awaiting_maintenance_refresh and session.status == SessionStatus.STOPPED
    finally:
        release.set()
        db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("final_status", [SessionStatus.STOPPED, SessionStatus.ENDED])
async def test_subscribed_session_refreshes_lifecycle_and_durable_cursor(
    tmp_path: Path, final_status: SessionStatus,
) -> None:
    (tmp_path / "hosts").mkdir()
    db = Database(str(tmp_path / "sessions.db"))
    try:
        db.create_session("watched", "watched", None, str(tmp_path), "local", "running", time.time())
        writer = EventLog(db=db, session_id="watched")
        writer.append("agent_message", {"text": "first"})
        manager = SessionManager(db, read_only_rehydrate=True)
        session = manager.get_session("watched")
        assert session is not None and session.event_log is not None
        session.awaiting_maintenance_refresh = True
        subscribed_log = session.event_log
        stream = _sse_event_stream(
            session, 0, server=None, mgr=manager, is_disconnected=None, signal_gaps=True,
        )
        async with aclosing(cast(AsyncGenerator[str, None], stream)) as reader:
            assert "id: 1\n" in await asyncio.wait_for(anext(reader), 2)
            assert session.subscriber_count == 1
            second = asyncio.create_task(anext(reader))
            await asyncio.sleep(0)
            writer.append("agent_message", {"text": "second"})
            if final_status == SessionStatus.STOPPED:
                db.update_session_stopped("watched", time.time(), False)
            else:
                db.update_session_status("watched", final_status.value, time.time())
                assert "watched" not in db.gc_retained_relationship_session_ids()
            await refresh_inherited_sessions(manager, lambda: True)
            assert session.status == final_status
            assert session.event_log is subscribed_log and not session.awaiting_maintenance_refresh
            delivered = await asyncio.wait_for(second, 2)
            assert "id: 2\n" in delivered and "second" in delivered
            if final_status == SessionStatus.STOPPED:
                assert not session.background_recovery_enabled
                assert subscribed_log.append("agent_message", {"text": "third"}).id == 3
                assert "id: 3\n" in await asyncio.wait_for(anext(reader), 2)
            else:
                assert subscribed_log.latest_id == db.get_max_event_id("watched") == 2
        assert session.subscriber_count == 0
    finally:
        db.close()


@pytest.mark.asyncio
async def test_missing_durable_row_evicts_only_unused_projection(tmp_path: Path) -> None:
    (tmp_path / "hosts").mkdir()
    db = Database(str(tmp_path / "sessions.db"))
    try:
        for sid in ("removed", "locally-owned"):
            db.create_session(sid, sid, None, str(tmp_path), "local", "running", time.time())
        manager = SessionManager(db, read_only_rehydrate=True)
        for session in manager.list_sessions():
            session.awaiting_maintenance_refresh = True
            db.delete_session(session.session_id)
        owned = manager.get_session("locally-owned")
        assert owned is not None
        owned.client = Mock()
        await refresh_inherited_sessions(manager, lambda: True)
        assert manager.get_session("removed") is None
        assert manager.list_sessions() == [owned] and owned.client is not None
        assert db.list_sessions() == []
    finally:
        db.close()


@pytest.mark.asyncio
async def test_refresh_isolates_ineligible_hosts_from_compatible_workers_and_stops(tmp_path: Path) -> None:
    (tmp_path / "hosts").mkdir()
    db = Database(str(tmp_path / "sessions.db"))
    try:
        for sid in ("partial", "stranded", "released", "stopped"):
            db.create_session(sid, sid, None, str(tmp_path), "local", "running", time.time())
        manager = SessionManager(db, read_only_rehydrate=True)
        records = [
            HostRecord("partial", 9001, 21, 22, owner_generation="prior"),
            HostRecord("stranded", 9002, 31, 32, protocol_version=2),
            HostRecord("released", 9003, os.getpid(), os.getpid(), extra={
                "host_process_start": diagnostics.process_start_time(os.getpid()),
                "child_process_start": diagnostics.process_start_time(os.getpid()),
            }),
        ]
        for record in records:
            manager._host_index.register(record)
        for session in manager.list_sessions():
            session.awaiting_maintenance_refresh = True
        db.update_session_stopped("stopped", time.time(), False)
        await refresh_inherited_sessions(manager, lambda: True)
        assert {s.session_id for s in manager.list_sessions() if s.awaiting_maintenance_refresh} == {
            "partial", "stranded",
        }
        assert manager._host_index.get("partial") == records[0]
        assert manager._host_index.get("stranded") == records[1]
        assert manager._host_index.get("released").owner_generation == manager._generation_id
        stopped = manager.get_session("stopped")
        assert stopped is not None and stopped.status == SessionStatus.STOPPED
        assert not stopped.background_recovery_enabled
    finally:
        db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("evidence", ["both-exited", "child-still-live", "denied", "native-held", "starting"])
async def test_refresh_preserves_or_settles_original_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, evidence: str,
) -> None:
    (tmp_path / "hosts").mkdir()
    db = Database(str(tmp_path / "sessions.db"))
    try:
        sid = "worker"
        status = "starting" if evidence == "starting" else "running"
        db.create_session(sid, sid, None, str(tmp_path), "local", status, time.time(),
                          acp_session_id="11111111-2222-4333-8444-555555555555")
        db.create_turn(sid, 0, "unfinished", time.time(),
                       delivery_id="original-delivery" if evidence == "native-held" else None)
        manager = SessionManager(db, read_only_rehydrate=True)
        session = manager.get_session(sid)
        assert session is not None
        session.awaiting_maintenance_refresh = True
        manager._host_index.register(HostRecord(sid, 9001, 21, 22, extra={
            "host_process_start": "100", "child_process_start": "102",
        }))
        monkeypatch.setattr(manager, "_rec_host_alive", lambda _: False)

        def exited(pid: int, token: str) -> bool:
            assert (pid, token) in {(21, "100"), (22, "102")}
            if evidence == "denied":
                raise PermissionError("fixture observation unavailable")
            return (pid, evidence) != (22, "child-still-live")

        monkeypatch.setattr(process_exit, "observe_original_exit", exited)
        manager._prune_dead_hosts()
        assert manager._host_index.get(sid) is not None
        for _ in range(3):
            await refresh_inherited_sessions(manager, lambda: True)
        turn = db.get_turn(sid, 0)
        assert turn is not None
        if evidence == "both-exited":
            assert manager._host_index.get(sid) is None
            assert session.status == SessionStatus.STOPPED and not session.background_recovery_enabled
            assert turn["completed_at"] and turn["stop_reason"] == "interrupted"
            assert [e["event_type"] for e in db.get_events(sid)] == ["turn_complete", "session_state_changed"]
        else:
            assert manager._host_index.get(sid) is not None and session.awaiting_maintenance_refresh
            assert session.status.value == status and turn["completed_at"] is None
            assert db.get_events(sid) == []
    finally:
        db.close()
