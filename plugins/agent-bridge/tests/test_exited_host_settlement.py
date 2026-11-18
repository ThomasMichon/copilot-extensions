"""Ordinary interrupted work settles under its original Host's exit check."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sqlite3
from unittest.mock import patch

import pytest
from zdd import process_exit

from agent_bridge.db import Database
from agent_bridge.events import EventLog
from agent_bridge.session_host.host_index import HostIndex, HostRecord
from agent_bridge.status_store import SelectedAgent, Selection, StatusStore


@pytest.mark.parametrize("case", [
    "exited", "live-child", "unobservable", "native-held", "feed-failed", "flush-failed",
    "replacement", "completed", "missing-idle", "persisted-acked", "persisted-missing",
    "unanchored", "no-origin", "evicted", "persisted-no-history",
])
def test_exit_settles_turn_and_subscription_or_preserves_both(
    tmp_path: Path, tmp_db: Database, monkeypatch: pytest.MonkeyPatch, case: str,
) -> None:
    sid = "worker"
    tmp_db.create_session(sid, sid, None, str(tmp_path), "local", "running", 1.0,
                          acp_session_id="11111111-2222-4333-8444-555555555555")
    tmp_db.create_turn(sid, 0, "unfinished", 2.0, delivery_id="delivery" if case == "native-held" else None)
    observed = tmp_db.get_session(sid)
    assert observed is not None
    store = StatusStore(tmp_db)
    store.subscribe("watch", Selection(watcher="reader", agents=[SelectedAgent(session_id=sid)], events=["idle"]))
    index = HostIndex(tmp_path / "index.json")
    record = HostRecord(sid, 9001, 21, 22, extra={"host_process_start": "100", "child_process_start": "102"})
    index.register(record)

    def exited(pid: int, birth: str) -> bool:
        assert (pid, birth) in {(21, "100"), (22, "102")}
        if case == "unobservable":
            raise PermissionError("missing observation")
        return (case, pid) != ("live-child", 22)

    monkeypatch.setattr(process_exit, "observe_original_exit", exited)

    def retire() -> bool:
        snapshot = observed
        assert snapshot is not None
        return index.release_exited_local(
            record, settle=lambda locked: tmp_db.settle_exited_inherited_host(snapshot, locked),
        )

    persisted = case in {"persisted-acked", "persisted-missing", "persisted-no-history"}
    completion = persisted or case in {"completed", "missing-idle"}
    missing = case in {"missing-idle", "persisted-missing"}
    if completion:
        log = EventLog(db=tmp_db, session_id=sid)
        log.append("session_state_changed", {"status": "running", "turn_index": 0}, timestamp=2.0)
        acknowledged = None
        if missing:
            with patch.object(StatusStore, "publish", side_effect=RuntimeError("feed unavailable")):
                with pytest.raises(RuntimeError, match="feed unavailable"):
                    log.append("turn_complete", {"stop_reason": "end_turn"}, timestamp=3.0)
            tmp_db.flush()
            assert not store.page(after=0, epoch=None)["events"]
        else:
            if case == "persisted-no-history":
                # The feed commits, but the queued history write does not survive the crash.
                with patch.object(tmp_db, "append_event"):
                    log.append("turn_complete", {"stop_reason": "end_turn"}, timestamp=3.0)
            else:
                log.append("turn_complete", {"stop_reason": "end_turn"}, timestamp=3.0)
            page = store.page(after=0, epoch=None, subscription_id="watch")
            acknowledged = store.acknowledge("watch", page["events"][0]["id"], page["epoch"])
        if persisted:
            tmp_db.update_turn(sid, 0, response_text="original reply", stop_reason="end_turn", completed_at=4.0)
            tmp_db.update_session_status(sid, "idle", 5.0)
            log.append("session_state_changed", {"status": "idle"}, timestamp=5.0)
            observed = tmp_db.get_session(sid)
        if case == "persisted-no-history":
            saved = tmp_db.get_turn(sid, 0)
            history = tmp_db.get_events(sid)
            assert not any(e["event_type"] == "turn_complete" for e in history)
            assert not retire()
            assert tmp_db.get_turn(sid, 0) == saved and tmp_db.get_session(sid) == observed
            assert tmp_db.get_events(sid) == history
            assert store.page(after=0, epoch=None, subscription_id="watch")["subscription"] == acknowledged
            assert HostIndex(tmp_path / "index.json").get(sid) == record
            return
        assert retire()
        if acknowledged is not None:
            assert store.page(after=0, epoch=None, subscription_id="watch")["subscription"] == acknowledged
    elif case in {"no-origin", "evicted"}:
        after = 0
        if case == "no-origin":
            tmp_db.append_event(sid, 2, "session_state_changed", {"status": "running", "turn_index": 0}, 2.0)
            tmp_db.append_event(sid, 3, "turn_complete", {"stop_reason": "end_turn"}, 3.0)
            tmp_db.flush()
        else:
            monkeypatch.setattr("agent_bridge.status_store.RETENTION", 1)
            log = EventLog(db=tmp_db, session_id=sid)
            log.append("session_state_changed", {"status": "running", "turn_index": 0}, timestamp=2.0)
            log.append("turn_complete", {"stop_reason": "end_turn"}, timestamp=3.0)
            page = store.page(after=0, epoch=None, subscription_id="watch")
            after = page["events"][0]["id"]
            store.acknowledge("watch", after, page["epoch"])
            store.publish("other-worker", "idle", "another-completion")
        before = store.page(after=after, epoch=None, subscription_id="watch")
        assert not retire()
        assert store.page(after=after, epoch=None, subscription_id="watch") == before
        assert tmp_db.get_session(sid) == observed
        assert HostIndex(tmp_path / "index.json").get(sid) == record
        return
    elif case == "unanchored":
        tmp_db.append_event(sid, 2, "turn_complete", {"stop_reason": "end_turn"}, 3.0)
        tmp_db.flush()
        assert not retire()
    elif case in {"flush-failed", "replacement"}:
        def fail_flush(records: dict[str, HostRecord]) -> None:
            assert sid not in records
            raise OSError("index unavailable")

        with monkeypatch.context() as failing_index:
            failing_index.setattr(index, "_flush", fail_flush)
            with pytest.raises(OSError, match="index unavailable"):
                retire()
        index = HostIndex(tmp_path / "index.json")
        assert index.get(sid) == record
        observed = tmp_db.get_session(sid)
        assert observed is not None and observed["status"] == "stopped"
        if case == "replacement":
            record = replace(record, nonce="new-host")
            index.register(record)
        assert retire() is (case == "flush-failed")
    elif case == "feed-failed":
        tmp_db.execute_write(
            "CREATE TEMP TRIGGER fail_feed BEFORE INSERT ON bridge_status_events "
            "BEGIN SELECT RAISE(ABORT, 'feed failure'); END"
        )
        with pytest.raises(sqlite3.IntegrityError, match="feed failure"):
            retire()
    elif case == "unobservable":
        with pytest.raises(PermissionError, match="missing observation"):
            retire()
    else:
        assert retire() is (case == "exited")
        assert not retire()

    turn, session = tmp_db.get_turn(sid, 0), tmp_db.get_session(sid)
    assert turn is not None and session is not None
    page = store.page(after=0, epoch=None, subscription_id="watch")
    sub = page["subscription"]
    if completion:
        assert turn["stop_reason"] == "end_turn" and turn["completed_at"] == (4.0 if persisted else 3.0)
        if persisted:
            assert turn["response_text"] == "original reply"
        assert [e["event_type"] for e in tmp_db.get_events(sid)].count("turn_complete") == 1
        assert [e["kind"] for e in page["events"]] == ["idle"]
        assert (sub["unread"] > sub["acked"]) is missing
    elif case == "unanchored":
        assert turn["completed_at"] is None and session["status"] == "running"
        assert len(tmp_db.get_events(sid)) == 1 and HostIndex(tmp_path / "index.json").get(sid) == record
    elif case in {"exited", "flush-failed", "replacement"}:
        assert session["status"] == "stopped" and not session["background_recovery_enabled"]
        assert turn["completed_at"] and turn["stop_reason"] == "interrupted"
        assert [e["event_type"] for e in tmp_db.get_events(sid)] == ["turn_complete", "session_state_changed"]
        assert [e["kind"] for e in page["events"]] == ["idle"]
        assert sub["unread"] == page["events"][0]["id"] > sub["acked"] and sub["notice_epoch"] == 1
        assert HostIndex(tmp_path / "index.json").get(sid) == (record if case == "replacement" else None)
    else:
        assert session["status"] == "running" and turn["completed_at"] is None
        assert tmp_db.get_events(sid) == [] and not page["events"] and sub["unread"] == sub["acked"]
        assert HostIndex(tmp_path / "index.json").get(sid) == record
