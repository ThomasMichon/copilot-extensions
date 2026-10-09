"""A conversation resumed in a new process keeps its session id: a dead row for
that id (``expired``, or ``live`` past its heartbeat lease) is revived by the new
incarnation, while a live row is never taken over by another process."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from agent_bridge import db_live_session_aliases as aliases
from agent_bridge.app import create_app
from agent_bridge.db import Database
from agent_bridge.db_core import LIVE_SESSION_STALE_SECONDS
from agent_bridge.models import ServiceConfig


def _register(db: Database, sid: str, now: float, *, pid: int, started: float) -> str:
    return db.register_live_session(
        sid, machine="codespaces-1", cwd="/workspaces/repo", worktree_id="wt-1", repo="repo",
        branch="main", pid=pid, role=None, now=now, process_started_at=started,
    )


def test_an_expired_row_is_revived_by_its_resumed_conversation(tmp_db: Database) -> None:
    t0 = time.time() - 3600
    assert _register(tmp_db, "conv-1", t0, pid=100, started=t0) == "live"
    tmp_db.execute_write("UPDATE live_sessions SET status='expired' WHERE session_id=?", ("conv-1",))
    now = time.time()
    assert _register(tmp_db, "conv-1", now, pid=200, started=now) == "live"
    row = tmp_db.get_live_session("conv-1")
    assert (row["status"], row["pid"]) == ("live", 200)


def test_a_live_row_past_its_lease_is_revived_before_the_sweep_marks_it(tmp_db: Database, monkeypatch) -> None:
    monkeypatch.setattr(aliases, "local_pid_alive", lambda pid: False)
    now = time.time()
    lapsed = now - LIVE_SESSION_STALE_SECONDS - 5
    assert _register(tmp_db, "conv-2", lapsed, pid=100, started=lapsed) == "live"
    assert _register(tmp_db, "conv-2", now, pid=200, started=now) == "live"
    assert tmp_db.get_live_session("conv-2")["pid"] == 200


def test_a_lapsed_row_whose_local_process_still_runs_is_not_taken_over(tmp_db: Database, monkeypatch) -> None:
    """The reaper's rule: a lapsed local row whose pid is alive is wedged, not
    dead, so admission does not depend on whether the sweep ran yet."""
    monkeypatch.setattr(aliases, "local_pid_alive", lambda pid: True)
    now = time.time()
    lapsed = now - LIVE_SESSION_STALE_SECONDS - 5
    assert _register(tmp_db, "conv-5", lapsed, pid=100, started=lapsed) == "live"
    assert _register(tmp_db, "conv-5", now, pid=200, started=now) == "incarnation_mismatch"
    assert tmp_db.get_live_session("conv-5")["pid"] == 100


@pytest.mark.parametrize("alive, admitted", [(False, True), (True, False), (None, False)])
def test_a_wedged_row_is_revived_only_once_its_process_is_provably_gone(tmp_db: Database, monkeypatch, alive,
                                                                      admitted) -> None:
    """A row the sweep found wedged (its pid alive then) whose process has since
    exited: the resumed process need not wait for the next sweep."""
    monkeypatch.setattr(aliases, "local_pid_alive", lambda pid: alive)
    now = time.time()
    assert _register(tmp_db, "conv-6", now - 600, pid=100, started=now - 600) == "live"
    tmp_db.execute_write("UPDATE live_sessions SET status='wedged' WHERE session_id=?", ("conv-6",))
    got = _register(tmp_db, "conv-6", now, pid=200, started=now)
    assert got == ("live" if admitted else "incarnation_mismatch")


def test_a_fresh_live_row_is_never_taken_over_by_another_process(tmp_db: Database) -> None:
    now = time.time()
    assert _register(tmp_db, "conv-3", now, pid=100, started=now) == "live"
    assert _register(tmp_db, "conv-3", now + 1, pid=200, started=now + 1) == "incarnation_mismatch"
    assert _register(tmp_db, "conv-3", now + 2, pid=100, started=now) == "live"  # its own heartbeat
    assert tmp_db.get_live_session("conv-3")["pid"] == 100


def test_a_revived_row_is_the_worktrees_newest_incarnation(tmp_db: Database) -> None:
    """A resume first starts a provisional session, which the same launch
    registers in the worktree moments before the resumed conversation revives
    its own row. The revived row is the current incarnation (delivery goes to
    the newest ``registered_at``), never superseded by that provisional one."""
    t0 = time.time() - 3600
    assert _register(tmp_db, "conv-7", t0, pid=100, started=t0) == "live"
    tmp_db.execute_write("UPDATE live_sessions SET status='expired' WHERE session_id=?", ("conv-7",))
    now = time.time()
    assert _register(tmp_db, "provisional", now - 3, pid=300, started=now - 3) == "live"
    assert _register(tmp_db, "conv-7", now, pid=200, started=now) == "live"
    assert tmp_db.get_live_session("conv-7")["registered_at"] == pytest.approx(now)
    assert tmp_db.current_live_session_for_worktree("wt-1", now=now) == "conv-7"
    message_id, reason = tmp_db.enqueue_live_message_if_fresh(
        "conv-7", sender="board", body="hi", now=now, expected_session_id="conv-7")
    assert reason is None and message_id


def test_a_heartbeat_keeps_the_rows_registration_time(tmp_db: Database) -> None:
    now = time.time()
    assert _register(tmp_db, "conv-8", now - 60, pid=100, started=now - 60) == "live"
    assert _register(tmp_db, "conv-8", now, pid=100, started=now - 60) == "live"
    assert tmp_db.get_live_session("conv-8")["registered_at"] == pytest.approx(now - 60)


def test_the_route_admits_a_resumed_process_for_an_expired_row(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_WORKTREES_PROJECTS_YAML", str(tmp_path / "none.yaml"))
    app = create_app(config=ServiceConfig(port=0, bind="127.0.0.1", db_path=str(tmp_path / "t.db")),
                     token="test-token")
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer test-token"
        body = {"session_id": "conv-4", "machine": "codespaces-1", "cwd": "/w", "worktree_id": "wt-4",
                "repo": "repo", "branch": "main", "pid": 100, "process_started_at": time.time() - 600}
        assert client.post("/api/v1/live-sessions", json=body).status_code == 200
        app.state.db.execute_write("UPDATE live_sessions SET status='expired' WHERE session_id=?", ("conv-4",))
        resumed = {**body, "pid": 200, "process_started_at": time.time()}
        response = client.post("/api/v1/live-sessions", json=resumed)
        assert response.status_code == 200, response.text
        assert response.json()["pid"] == 200
        # A second, different process while that one is live is still refused.
        other = {**body, "pid": 300, "process_started_at": time.time() + 1}
        refused = client.post("/api/v1/live-sessions", json=other)
        assert refused.status_code == 409 and refused.json()["detail"]["reason"] == "incarnation_mismatch"
