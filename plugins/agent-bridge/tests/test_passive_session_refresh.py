"""Passive promotion refreshes inherited projections without disrupting local readers."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import aclosing
from pathlib import Path
import time
from typing import cast
from unittest.mock import Mock

import pytest

from agent_bridge.db import Database
from agent_bridge.events import EventLog
from agent_bridge.passive_maintenance import refresh_inherited_sessions
from agent_bridge.routes.sessions import _sse_event_stream
from agent_bridge.session_host.host_index import HostRecord
from agent_bridge.session_manager import SessionManager
from agent_bridge.session_status import SessionStatus


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
            refresh_inherited_sessions(manager)
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


def test_missing_durable_row_evicts_only_unused_projection(tmp_path: Path) -> None:
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
        refresh_inherited_sessions(manager)
        assert manager.get_session("removed") is None
        assert manager.list_sessions() == [owned] and owned.client is not None
        assert db.list_sessions() == []
    finally:
        db.close()


def test_refresh_isolates_ineligible_hosts_from_compatible_workers_and_stops(tmp_path: Path) -> None:
    (tmp_path / "hosts").mkdir()
    db = Database(str(tmp_path / "sessions.db"))
    try:
        for sid in ("partial", "stranded", "released", "stopped"):
            db.create_session(sid, sid, None, str(tmp_path), "local", "running", time.time())
        manager = SessionManager(db, read_only_rehydrate=True)
        records = [
            HostRecord("partial", 9001, 21, 22, owner_generation="prior"),
            HostRecord("stranded", 9002, 31, 32, protocol_version=2),
            HostRecord("released", 9003, 41, 42),
        ]
        for record in records:
            manager._host_index.register(record)
        for session in manager.list_sessions():
            session.awaiting_maintenance_refresh = True
        db.update_session_stopped("stopped", time.time(), False)
        refresh_inherited_sessions(manager)
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
