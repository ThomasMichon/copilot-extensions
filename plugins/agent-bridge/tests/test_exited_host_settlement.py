"""Ordinary interrupted work settles under its original Host's exit check."""

from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest
from zdd import process_exit

from agent_bridge.db import Database
from agent_bridge.session_host.host_index import HostIndex, HostRecord
from agent_bridge.status_store import SelectedAgent, Selection, StatusStore


@pytest.mark.parametrize("case", ["exited", "live-child", "unobservable", "native-held", "feed-failed"])
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
        return index.release_exited_local(
            record, settle=lambda: tmp_db.settle_exited_inherited_host(observed, record.child_pid),
        )

    if case == "feed-failed":
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
    if case == "exited":
        assert session["status"] == "stopped" and not session["background_recovery_enabled"]
        assert turn["completed_at"] and turn["stop_reason"] == "interrupted"
        assert [e["event_type"] for e in tmp_db.get_events(sid)] == ["turn_complete", "session_state_changed"]
        assert [e["kind"] for e in page["events"]] == ["idle"]
        assert sub["unread"] == page["events"][0]["id"] > sub["acked"] and sub["notice_epoch"] == 1
        assert HostIndex(tmp_path / "index.json").get(sid) is None
    else:
        assert session["status"] == "running" and turn["completed_at"] is None
        assert tmp_db.get_events(sid) == [] and not page["events"] and sub["unread"] == sub["acked"]
        assert HostIndex(tmp_path / "index.json").get(sid) == record
