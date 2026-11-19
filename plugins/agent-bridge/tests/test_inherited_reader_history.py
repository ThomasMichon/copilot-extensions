"""Passive history preparation does not move a reader onto an inconsistent cursor."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from collections.abc import AsyncGenerator
from contextlib import aclosing, closing
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

import pytest

from agent_bridge.db import Database
from agent_bridge.events import EventLog
from agent_bridge.passive_refresh import InheritedRefresh, RefreshTarget
from agent_bridge.routes.session_observations import observations
from agent_bridge.session_manager import SessionManager
from agent_bridge.session_observation_contract import ReadRequest, decode, read_position, validate_frame
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
@pytest.mark.parametrize("change", ["first-id", "replacement-id", "rebuilt-journal"])
async def test_follow_through_inherited_native_id_assignment(tmp_db: Database, change: str) -> None:
    sid = "worker"
    tmp_db.create_session(sid, sid, None, ".", "local", "starting", 1.0,
                          target_json='{"type":"local","cwd":".","worktree_id":"tree"}',
                          acp_session_id="previous" if change == "replacement-id" else None)
    writer = EventLog(db=tmp_db, session_id=sid)
    writer.append("agent_message", {"text": "first"})
    mgr = _passive_manager(tmp_db)
    session = mgr.get_session(sid)
    assert session is not None
    session.awaiting_maintenance_refresh = True
    req = Mock()
    req.app.state.uvicorn_server = None
    req.app.state.session_manager = mgr
    req.is_disconnected = AsyncMock(return_value=False)
    read = ReadRequest.parse(sid, [("contract_version", "1"), ("worktree_id", "tree"),
                                   ("follow", "true")], history=False)
    async with aclosing(cast(AsyncGenerator[bytes, None], observations(req, read))) as stream:
        first = decode((await anext(stream)).split(b"data: ", 1)[1].strip())
        assert read_position(first["position"], first["source"]) == 1 and session.subscriber_count == 1
        tmp_db.update_session_acp_id(sid, "assigned")
        if change == "rebuilt-journal":
            writer.rebuild([("agent_message", {"text": "different"})])
        else:
            writer.append("agent_message", {"text": "second"})
        row = tmp_db.get_session(sid)
        assert row is not None
        prepared = InheritedRefresh.read(tmp_db, row, None, target=RefreshTarget.capture(session))
        assert prepared is not None
        mgr._rehydrate(merge=True, session_id=sid, prepared=prepared)
        assert not session.awaiting_maintenance_refresh
        following = decode((await asyncio.wait_for(anext(stream), 2)).split(b"data: ", 1)[1].strip())
        validate_frame(following, read)
        if change == "first-id":
            assert following["kind"] == "delta" and following["source"]["durable_session_id"] == "assigned"
            assert following["base_position"] == first["position"]
            assert [event["native_event_id"] for event in following["entries"]] == [2]
            assert read_position(following["position"], following["source"]) == 2
        else:
            assert following["kind"] == "gap" and following["code"] == "source-changed"
    assert session.subscriber_count == 0


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
        prepared = InheritedRefresh.read(tmp_db, row, None, target=RefreshTarget.capture(session))
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
    prepared = InheritedRefresh.read(tmp_db, row, None, target=RefreshTarget.capture(session))
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


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["local-stop", "predecessor-append"])
async def test_do_not_install_history_superseded_after_preparation(tmp_db: Database, change: str) -> None:
    sid = "worker"
    tmp_db.create_session(sid, sid, None, ".", "local", "running", 1.0)
    writer = EventLog(db=tmp_db, session_id=sid)
    writer.append("agent_message", {"text": "first"})
    mgr = _passive_manager(tmp_db)
    session = mgr.get_session(sid)
    assert session is not None and session.event_log is not None
    reader = session.event_log
    session.awaiting_maintenance_refresh = True
    row = tmp_db.get_session(sid)
    assert row is not None
    prepared = InheritedRefresh.read(tmp_db, row, None, target=RefreshTarget.capture(session))
    assert prepared is not None
    if change == "local-stop":
        await mgr.stop_session(sid)
        assert not session.background_recovery_enabled and reader.latest_id > 1
    else:
        writer.append("agent_message", {"text": "last"})
        tmp_db.flush()
    before = reader.snapshot_history()
    mgr._rehydrate(merge=True, session_id=sid, prepared=prepared)
    if change == "local-stop":
        assert session.status == SessionStatus.STOPPED and not session.background_recovery_enabled
    assert reader.snapshot_history() == before and session.awaiting_maintenance_refresh
    latest = tmp_db.get_session(sid)
    assert latest is not None
    retry = InheritedRefresh.read(tmp_db, latest, None, target=RefreshTarget.capture(session))
    assert retry is not None
    mgr._rehydrate(merge=True, session_id=sid, prepared=retry)
    assert not session.awaiting_maintenance_refresh and session.event_log is reader
    if change == "local-stop":
        assert session.status == SessionStatus.STOPPED and reader.snapshot_history() == before
    else:
        assert reader.snapshot_history() == writer.snapshot_history()


def test_defer_prepared_merge_while_another_writer_is_active(tmp_db: Database) -> None:
    sid = "worker"
    tmp_db.create_session(sid, sid, None, ".", "local", "running", 1.0)
    mgr = _passive_manager(tmp_db)
    session = mgr.get_session(sid)
    assert session is not None
    session.awaiting_maintenance_refresh = True
    row = tmp_db.get_session(sid)
    assert row is not None
    prepared = InheritedRefresh.read(tmp_db, row, None, target=RefreshTarget.capture(session))
    assert prepared is not None
    with closing(sqlite3.connect(tmp_db.db_path)) as writer:
        writer.execute("BEGIN IMMEDIATE")
        mgr._rehydrate(merge=True, session_id=sid, prepared=prepared)
        assert session.awaiting_maintenance_refresh
        writer.rollback()
    mgr._rehydrate(merge=True, session_id=sid, prepared=prepared)
    assert not session.awaiting_maintenance_refresh


def test_dequeued_event_is_not_a_settled_write(tmp_db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    sid = "worker"
    tmp_db.create_session(sid, sid, None, ".", "local", "running", 1.0)
    tmp_db.append_event(sid, 1, "agent_message", {"text": "first"}, 2.0)
    mgr = _passive_manager(tmp_db)
    session = mgr.get_session(sid)
    assert session is not None and session.event_log is not None
    reader = session.event_log
    session.awaiting_maintenance_refresh = True
    row = tmp_db.get_session(sid)
    assert row is not None
    prepared = InheritedRefresh.read(tmp_db, row, None, target=RefreshTarget.capture(session))
    assert prepared is not None
    dequeued, release = threading.Event(), threading.Event()
    write = tmp_db._write_event_batch

    def held(batch: list[tuple[str, int, str, str, float]]) -> None:
        dequeued.set()
        assert release.wait(5)
        write(batch)

    with monkeypatch.context() as patch:
        patch.setattr(tmp_db, "_write_event_batch", held)
        tmp_db.append_event(sid, 2, "turn_complete", {"stop_reason": "done"}, 3.0)
        try:
            assert dequeued.wait(2)
            mgr._rehydrate(merge=True, session_id=sid, prepared=prepared)
            assert session.awaiting_maintenance_refresh and reader.latest_id == 1
        finally:
            release.set()
            tmp_db.flush()
    retry = InheritedRefresh.read(tmp_db, row, None, target=RefreshTarget.capture(session))
    assert retry is not None
    mgr._rehydrate(merge=True, session_id=sid, prepared=retry)
    assert not session.awaiting_maintenance_refresh and reader.get_events(1)[0].event == "turn_complete"
    assert reader.append("agent_message", {"text": "next"}).id == 3
