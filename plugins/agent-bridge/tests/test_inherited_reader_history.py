"""Passive history preparation does not move a reader onto an inconsistent cursor."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

from agent_bridge.db import Database
from agent_bridge.events import EventLog
from agent_bridge.passive_refresh import InheritedRefresh
from agent_bridge.session_manager import SessionManager
from agent_bridge.session_status import SessionStatus


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["append", "rebuild", "stop"])
async def test_existing_reader_retries_on_changed_history_or_session(
    tmp_db: Database, monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    sid = "worker"
    tmp_db.create_session(sid, sid, None, ".", "local", "running", 1.0)
    tmp_db.create_turn(sid, 0, "work", 1.0)
    writer = EventLog(db=tmp_db, session_id=sid)
    writer.append("agent_message", {"text": "first"}, timestamp=2.0)
    reader = EventLog.from_db(tmp_db, sid)
    row = tmp_db.get_session(sid)
    assert row is not None
    before = reader.snapshot_history()
    get_events = tmp_db.get_events

    def finish_write(key: str, *, after: int = 0) -> list[dict[str, Any]]:
        rows = get_events(key, after=after)
        if change == "append":
            writer.append("agent_message", {"text": "PROGRESS: build=done"}, timestamp=3.0)
        elif change == "rebuild":
            writer.rebuild([("agent_message", {"text": "replacement"})])
        else:
            tmp_db.update_session_stopped(sid, 4.0, False)
        return rows

    with monkeypatch.context() as racing:
        racing.setattr(tmp_db, "get_events", finish_write)
        assert InheritedRefresh.read(tmp_db, row, None) is None
    assert reader.snapshot_history() == before

    row = tmp_db.get_session(sid)
    assert row is not None
    prepared = InheritedRefresh.read(tmp_db, row, None)
    assert prepared is not None and prepared.history is not None and prepared.turns == 1
    assert prepared.last_output == writer.get_events()[-1].timestamp
    if change == "append":
        assert prepared.progress == {"build": "done"}
    revision = reader.observation_revision
    waiter = asyncio.create_task(reader.wait_for_observation(reader.latest_id, timeout=5.0, revision=revision))
    await asyncio.sleep(0)
    reader.refresh_subscribed_history(prepared.history)
    await asyncio.wait_for(waiter, 1.0)
    assert reader.snapshot_history() == writer.snapshot_history()
    assert reader.observation_revision != revision
    assert reader.append("agent_message", {"text": "next"}).id == writer.latest_id + 1


def test_missing_history_cannot_advance_reader(tmp_db: Database) -> None:
    sid = "worker"
    tmp_db.create_session(sid, sid, None, ".", "local", "running", 1.0)
    tmp_db.append_event(sid, 1, "agent_message", {"text": "first"}, 2.0)
    tmp_db.append_event(sid, 3, "agent_message", {"text": "after missing event"}, 3.0)
    row = tmp_db.get_session(sid)
    assert row is not None and InheritedRefresh.read(tmp_db, row, None) is None


def _passive_manager(db: Database) -> SessionManager:
    (Path(db.db_path).parent / "hosts").mkdir()
    return SessionManager(db, read_only_rehydrate=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("final_status", [SessionStatus.STOPPED, SessionStatus.ENDED])
async def test_prepared_merge_preserves_a_waiting_reader(tmp_db: Database, final_status: SessionStatus) -> None:
    sid = "watched"
    tmp_db.create_session(sid, sid, None, ".", "local", "starting", 1.0)
    writer = EventLog(db=tmp_db, session_id=sid)
    writer.append("agent_message", {"text": "first"}, timestamp=2.0)
    mgr = _passive_manager(tmp_db)
    session = mgr.get_session(sid)
    assert session is not None and session.event_log is not None
    reader = session.event_log
    mgr.add_subscriber(sid)
    session.awaiting_maintenance_refresh = True
    waiter = asyncio.create_task(reader.wait_for_observation(1, timeout=5.0, revision=reader.observation_revision))
    await asyncio.sleep(0)
    try:
        writer.append("agent_message", {"text": "PROGRESS: build=done"}, timestamp=3.0)
        tmp_db.create_turn(sid, 0, "work", 3.0)
        if final_status == SessionStatus.STOPPED:
            tmp_db.update_session_stopped(sid, 4.0, False)
        else:
            tmp_db.update_session_status(sid, final_status.value, 4.0)
        row = tmp_db.get_session(sid)
        assert row is not None
        prepared = InheritedRefresh.read(tmp_db, row, None)
        assert prepared is not None
        mgr._rehydrate(merge=True, session_id=sid)
        assert session.status.value == "starting" and reader.latest_id == 1
        mgr._rehydrate(merge=True, session_id=sid, prepared=prepared)
        await asyncio.wait_for(waiter, 1.0)
        assert mgr.get_session(sid) is session and session.event_log is reader
        assert session.status == final_status and session.restart_status == final_status.value
        assert not session.awaiting_maintenance_refresh
        assert session.progress == {"build": "done"} and session.last_output_at == 3.0
        assert session.turn_count == 1 and tmp_db.get_turns(sid)[0]["completed_at"] is None
        assert reader.snapshot_history() == writer.snapshot_history()
        assert tmp_db.get_session(sid) == row
        if final_status == SessionStatus.STOPPED:
            assert not session.background_recovery_enabled
            assert reader.append("agent_message", {"text": "next"}).id == 3
    finally:
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)
        mgr.remove_subscriber(sid)


@pytest.mark.asyncio
@pytest.mark.parametrize("busy", ["client", "prompt", "lifecycle", "not-inherited"])
async def test_prepared_merge_does_not_replace_local_work(tmp_db: Database, busy: str) -> None:
    sid = "worker"
    tmp_db.create_session(sid, sid, None, ".", "local", "running", 1.0)
    mgr = _passive_manager(tmp_db)
    session = mgr.get_session(sid)
    assert session is not None and session.event_log is not None
    reader = session.event_log
    session.awaiting_maintenance_refresh = busy != "not-inherited"
    tmp_db.update_session_stopped(sid, 2.0, False)
    EventLog(db=tmp_db, session_id=sid).append("agent_message", {"text": "later"})
    row = tmp_db.get_session(sid)
    assert row is not None
    prepared = InheritedRefresh.read(tmp_db, row, None)
    assert prepared is not None
    unblock = asyncio.Event()
    if busy == "client":
        session.client = Mock()
    elif busy == "prompt":
        async def pending_prompt() -> None:
            await unblock.wait()

        session._prompt_task = asyncio.create_task(pending_prompt())
    elif busy == "lifecycle":
        await session._lifecycle_lock.acquire()
    try:
        mgr._rehydrate(merge=True, session_id=sid, prepared=prepared)
        assert session.status == SessionStatus.RUNNING and session.event_log is reader and reader.latest_id == 0
        assert session.awaiting_maintenance_refresh == (busy != "not-inherited")
    finally:
        if busy == "lifecycle":
            session._lifecycle_lock.release()
        if session._prompt_task is not None:
            unblock.set()
            await session._prompt_task
