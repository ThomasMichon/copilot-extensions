"""Passive history preparation does not move a reader onto an inconsistent cursor."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from agent_bridge.db import Database
from agent_bridge.events import EventLog
from agent_bridge.passive_refresh import InheritedRefresh


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
