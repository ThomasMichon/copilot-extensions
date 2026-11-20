"""Passive history preparation does not move a reader onto an inconsistent cursor."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from collections.abc import AsyncGenerator
from contextlib import aclosing, closing
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from agent_bridge.db import Database
from agent_bridge import passive_refresh
from agent_bridge.events import EventLog, SseEvent
from agent_bridge.passive_refresh import InheritedRefresh, RefreshTarget
from agent_bridge.routes.session_observations import observations
from agent_bridge.routes.sessions import _sse_event_stream, wait_for_attention
from agent_bridge.models import AttentionReason
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
    get_events = passive_refresh._read_session_events

    def finish_write(db: Database, key: str) -> list[SseEvent]:
        rows = get_events(db, key)
        if change == "append":
            writer.append("agent_message", {"text": "PROGRESS: build=done"}, timestamp=3.0)
        elif change == "rebuild":
            writer.rebuild([("agent_message", {"text": "replacement"})])
        else:
            tmp_db.update_session_stopped(sid, 4.0, False)
        return rows

    with monkeypatch.context() as racing:
        racing.setattr(passive_refresh, "_read_session_events", finish_write)
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
async def test_attention_wait_exits_when_its_inherited_source_retires(
    tmp_db: Database, monkeypatch: pytest.MonkeyPatch,
) -> None:
    sid = "worker"
    tmp_db.create_session(sid, sid, None, ".", "local", "running", 1.0)
    manager = _passive_manager(tmp_db)
    session = manager.get_session(sid)
    assert session is not None and session.event_log is not None
    session.awaiting_maintenance_refresh = True
    log = session.event_log
    wait = log.wait_for_events
    waiting = asyncio.Event()

    async def once(after: int, timeout: float) -> list[SseEvent]:
        assert not waiting.is_set(), "retired attention wait retried instead of terminating"
        waiting.set()
        return await wait(after, timeout)

    monkeypatch.setattr(log, "wait_for_events", once)
    req = Mock()
    req.app.state.session_manager = manager
    task = asyncio.create_task(wait_for_attention(sid, req, [AttentionReason.INPUT_REQUIRED], None, 30))
    try:
        await asyncio.wait_for(waiting.wait(), 2)
        tmp_db.delete_session(sid)
        manager._rehydrate(merge=True, session_id=sid,
                           prepared=InheritedRefresh(None, None, target=RefreshTarget.capture(session)))
        with pytest.raises(HTTPException) as unavailable:
            await asyncio.wait_for(task, 2)
        assert unavailable.value.status_code == 410
        detail: object = getattr(unavailable.value, "detail")
        assert isinstance(detail, dict) and detail["code"] == "source_unavailable"
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_real_predecessor_end_reports_missing_source_to_attached_reader(tmp_db: Database) -> None:
    sid = "worker"
    tmp_db.create_session(sid, sid, None, ".", "local", "stopped", 1.0,
                          target_json='{"type":"local","cwd":".","worktree_id":"tree"}')
    writer = EventLog(db=tmp_db, session_id=sid)
    writer.append("agent_message", {"text": "first"})
    predecessor = _passive_manager(tmp_db)
    successor = SessionManager(tmp_db, read_only_rehydrate=True)
    inherited = successor.get_session(sid)
    assert inherited is not None and inherited.event_log is not None
    inherited.awaiting_maintenance_refresh = True
    req = Mock()
    req.app.state.uvicorn_server = None
    req.app.state.session_manager = successor
    req.app.state.db = tmp_db
    req.is_disconnected = AsyncMock(return_value=False)
    read = ReadRequest.parse(sid, [("contract_version", "1"), ("worktree_id", "tree"),
                                   ("follow", "true")], history=False)
    async with aclosing(cast(AsyncGenerator[bytes, None], observations(req, read))) as stream:
        first = decode((await anext(stream)).split(b"data: ", 1)[1].strip())
        assert first["kind"] == "snapshot" and inherited.subscriber_count == 1
        raw = _sse_event_stream(inherited, 0, server=None, is_disconnected=None, mgr=successor)
        async with aclosing(cast(AsyncGenerator[str, None], raw)) as events:
            assert "id: 1\n" in await anext(events)
            await predecessor.end_session(sid)
            assert tmp_db.get_session(sid) is None and not tmp_db.get_events(sid)
            missing = InheritedRefresh(None, None, target=RefreshTarget.capture(inherited))
            successor._rehydrate(merge=True, session_id=sid, prepared=missing)
            assert successor.get_session(sid) is None
            assert "source_unavailable" in await asyncio.wait_for(anext(events), 1)
        result = decode((await asyncio.wait_for(anext(stream), 1)).split(b"data: ", 1)[1].strip())
        assert result["code"] == "source-unavailable" and result["availability"] == "unavailable"
    assert inherited.subscriber_count == 0


@pytest.mark.parametrize("retained", ["row-reappeared", "local-client", "log-advanced"])
def test_stale_absence_cannot_retire_a_retained_projection(tmp_db: Database, retained: str) -> None:
    sid = "worker"
    tmp_db.create_session(sid, sid, None, ".", "local", "stopped", 1.0)
    mgr = _passive_manager(tmp_db)
    session = mgr.get_session(sid)
    assert session is not None and session.event_log is not None
    session.awaiting_maintenance_refresh = True
    tmp_db.delete_session(sid)
    missing = InheritedRefresh(None, None, target=RefreshTarget.capture(session))
    if retained == "row-reappeared":
        tmp_db.create_session(sid, sid, None, ".", "local", "starting", 2.0)
    elif retained == "local-client":
        session.client = Mock()
    else:
        session.event_log.wake_observers()
    mgr._rehydrate(merge=True, session_id=sid, prepared=missing)
    assert mgr.get_session(sid) is session and not session.event_log.source_unavailable


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
        assert following["kind"] == "gap" and following["code"] == "source-changed"
        assert following["reconcile"] == "new-snapshot"
    async with aclosing(cast(AsyncGenerator[bytes, None], observations(req, read))) as fresh:
        snapshot = decode((await anext(fresh)).split(b"data: ", 1)[1].strip())
        validate_frame(snapshot, read)
        assert snapshot["kind"] == "snapshot" and snapshot["source"]["durable_session_id"] == "assigned"
        assert read_position(snapshot["position"], snapshot["source"]) == writer.latest_id
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


@pytest.mark.parametrize("same_session", [True, False])
def test_dequeued_event_fences_only_its_session(
    tmp_db: Database, monkeypatch: pytest.MonkeyPatch, same_session: bool,
) -> None:
    sid = "worker"
    tmp_db.create_session("other", "other", None, ".", "local", "running", 1.0)
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
        tmp_db.append_event(sid if same_session else "other", 2 if same_session else 1,
                            "turn_complete", {"stop_reason": "done"}, 3.0)
        try:
            assert dequeued.wait(2)
            if not same_session:
                prepared = InheritedRefresh.read(tmp_db, row, None, target=RefreshTarget.capture(session))
                assert prepared is not None
            mgr._rehydrate(merge=True, session_id=sid, prepared=prepared)
            assert session.awaiting_maintenance_refresh == same_session and reader.latest_id == 1
        finally:
            release.set()
            tmp_db.flush()
    if not same_session:
        assert tmp_db.get_events("other")[0]["event_type"] == "turn_complete"
        assert reader.append("agent_message", {"text": "next"}).id == 2
        return
    retry = InheritedRefresh.read(tmp_db, row, None, target=RefreshTarget.capture(session))
    assert retry is not None
    mgr._rehydrate(merge=True, session_id=sid, prepared=retry)
    assert not session.awaiting_maintenance_refresh and reader.get_events(1)[0].event == "turn_complete"
    assert reader.append("agent_message", {"text": "next"}).id == 3
