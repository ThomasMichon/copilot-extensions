"""Exercise passive admission with durable session and Host records."""

from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path

import pytest
from zdd import process_exit

from agent_bridge.db import Database
from agent_bridge.passive_maintenance import may_refresh_inherited
from agent_bridge.session_host.host_index import HostIndex, HostRecord


@pytest.mark.parametrize("case", ["same", "replacement", "explicit-stop"])
def test_stopped_row_admits_metadata_but_retires_only_original_host(
    tmp_path: Path, tmp_db: Database, monkeypatch: pytest.MonkeyPatch, case: str,
) -> None:
    sid = "worker"
    tmp_db.create_session(sid, sid, None, str(tmp_path), "local", "running", 1.0,
                          acp_session_id="11111111-2222-4333-8444-555555555555")
    tmp_db.create_turn(sid, 0, "unfinished", 2.0)
    path = tmp_path / "index.json"
    index = HostIndex(path)
    record = HostRecord(sid, 9001, 21, 22, extra={"host_process_start": "100", "child_process_start": "102"})
    index.register(record)
    acquired: list[HostRecord] = []
    monkeypatch.setattr(process_exit, "observe_original_exit", lambda *_: True)
    row = tmp_db.get_session(sid)
    assert row is not None
    with monkeypatch.context() as once:
        def fail_flush(records: dict[str, HostRecord]) -> None:
            assert sid not in records and index.get(sid) == record
            raise OSError("index write failed")

        once.setattr(index, "_flush", fail_flush)
        assert not may_refresh_inherited(tmp_db, index, "successor", row, acquired.append)
    assert index.get(sid) == HostIndex(path).get(sid) == record
    history = tmp_db.get_events(sid)
    assert [event["event_type"] for event in history] == ["turn_complete", "session_state_changed"]
    if case == "replacement":
        record = replace(record, nonce="replacement")
        index.register(record)
    elif case == "explicit-stop":
        tmp_db.request_cold_recovery_stop(sid)
    row = tmp_db.get_session(sid)
    assert row is not None and row["status"] == "stopped"
    assert may_refresh_inherited(tmp_db, index, "successor", row, acquired.append)
    assert HostIndex(path).get(sid) == (None if case == "same" else record)
    assert tmp_db.get_events(sid) == history and not acquired


@pytest.mark.parametrize("case", ["released", "owner-live", "partial-exit", "stop-requested", "unknown"])
def test_live_host_claim_needs_matching_owner_and_no_pending_stop(
    tmp_path: Path, tmp_db: Database, monkeypatch: pytest.MonkeyPatch, case: str,
) -> None:
    sid = "worker"
    tmp_db.create_session(sid, sid, None, str(tmp_path), "local", "running", 1.0)
    index = HostIndex(tmp_path / "index.json")
    record = HostRecord(sid, 9001, 21, 22, extra={"host_process_start": "100", "child_process_start": "102"})
    if case == "owner-live":
        record = replace(record, owner_generation="prior", owner_pid=os.getpid())
    if case == "stop-requested":
        tmp_db.request_cold_recovery_stop(sid)
    index.register(record)

    def exited(pid: int, birth: str) -> bool:
        assert (pid, birth) in {(21, "100"), (22, "102")}
        if case == "unknown":
            raise PermissionError("missing observation")
        return case == "partial-exit" and pid == 21

    monkeypatch.setattr(process_exit, "observe_original_exit", exited)
    acquired: list[HostRecord] = []
    row = tmp_db.get_session(sid)
    assert row is not None
    assert may_refresh_inherited(tmp_db, index, "successor", row, acquired.append) is (case == "released")
    current = HostIndex(tmp_path / "index.json").get(sid)
    if case == "released":
        assert current == replace(record, owner_generation="successor", owner_pid=os.getpid()) and acquired == [current]
        assert may_refresh_inherited(tmp_db, index, "successor", row, acquired.append)
        assert acquired == [current]
    else:
        assert current == record and not acquired
    assert tmp_db.get_session(sid) == row and not tmp_db.get_events(sid)
