"""A represented session that lost its history in a bridge restart gets it back
from its own transcript, replayed by its extension, in order."""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

from agent_bridge.app import create_app
from agent_bridge.live_backfill import REPLAY_HISTORY_CONTROL, REPLAY_WAIT_SECONDS, HistoryBackfill
from agent_bridge.live_representation import LiveEventStore
from agent_bridge.models import ServiceConfig


def _msg(i: int, text: str, ts: float | None = None) -> dict:
    ev = {"type": "assistant.message", "id": f"e{i}", "data": {"content": text}}
    if ts is not None:
        ev["timestamp"] = ts
    return ev


class _Db:
    def __init__(self, reason=None):
        self.reason, self.enqueued = reason, []

    def enqueue_live_message_if_fresh(self, sid, **kw):
        self.enqueued.append((sid, kw["kind"]))
        return (1, None) if self.reason is None else (None, self.reason)


def _texts(store: LiveEventStore, sid: str) -> list[str]:
    log = store.get(sid)
    return [e.data.get("text") for e in log.snapshot_history()[1]] if log else []


def _row(sid="s1", registered_at=100.0, status="live"):
    return {"session_id": sid, "registered_at": registered_at, "status": status}


def test_a_session_from_an_earlier_generation_replays_before_its_held_live_events() -> None:
    store, db, bf = LiveEventStore(), _Db(), HistoryBackfill(started_at=200.0)
    assert bf.maybe_start(db, store, _row(), now=300.0)
    assert db.enqueued == [("s1", REPLAY_HISTORY_CONTROL)]
    assert bf.ingest(store, "s1", [_msg(3, "new")], worktree_id=None) == 0  # held, not landed
    assert store.get("s1") is None
    assert bf.replay(store, "s1", [_msg(1, "old"), _msg(2, "older-but-later"), _msg(3, "new")],
                     worktree_id=None) == 3  # the duplicate of the held event is dropped
    assert _texts(store, "s1") == ["old", "older-but-later", "new"]
    assert not bf.maybe_start(db, store, _row(), now=301.0)  # once per generation


def test_only_a_pre_restart_live_session_without_a_log_is_asked() -> None:
    store, db, bf = LiveEventStore(), _Db(), HistoryBackfill(started_at=200.0)
    assert not bf.maybe_start(db, store, _row(registered_at=250.0), now=300.0)  # registered this generation
    assert not bf.maybe_start(db, store, _row(status="expired"), now=300.0)
    store.ingest("s2", [_msg(1, "x")])
    assert not bf.maybe_start(db, store, _row(sid="s2"), now=300.0)  # already has a log
    assert not bf.maybe_start(_Db(reason="not_found"), store, _row(sid="s3"), now=300.0)
    assert bf.ingest(store, "s3", [_msg(1, "live")], worktree_id=None) == 1  # nothing to wait for: never held
    assert db.enqueued == []


def test_no_replay_in_time_releases_the_held_events_and_a_late_one_is_ignored() -> None:
    store, db, bf = LiveEventStore(), _Db(), HistoryBackfill(started_at=200.0)
    bf.maybe_start(db, store, _row(), now=300.0)
    bf.ingest(store, "s1", [_msg(5, "live-1")], worktree_id=None)
    bf.ingest(store, "s1", [_msg(6, "live-2")], worktree_id=None)
    bf.release_expired(store, now=300.0 + REPLAY_WAIT_SECONDS - 1)
    assert store.get("s1") is None
    bf.release_expired(store, now=300.0 + REPLAY_WAIT_SECONDS)
    assert _texts(store, "s1") == ["live-1", "live-2"]
    assert bf.replay(store, "s1", [_msg(1, "old")], worktree_id=None) == 0  # it would land out of order
    assert _texts(store, "s1") == ["live-1", "live-2"]


def test_a_replayed_event_keeps_its_own_time() -> None:
    store = LiveEventStore()
    store.ingest("s1", [_msg(1, "old", ts=1_000.0), _msg(2, "live")])
    events = store.get("s1").snapshot_history()[1]
    assert events[0].timestamp == 1_000.0 and events[1].timestamp > 1_000.0


def test_the_routes_ask_for_the_replay_hold_live_events_and_land_it_first(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AGENT_WORKTREES_PROJECTS_YAML", str(tmp_path / "none.yaml"))
    app = create_app(config=ServiceConfig(port=0, bind="127.0.0.1", db_path=str(tmp_path / "t.db")),
                     token="test-token")
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer test-token"
        body = {"session_id": "live-1", "machine": "m", "cwd": "/w", "worktree_id": "wt", "pid": 7}
        assert client.post("/api/v1/live-sessions", json=body).status_code == 200
        # The daemon restarts: a new generation, with no represented history.
        app.state.history_backfill = HistoryBackfill(started_at=time.time() + 1)
        app.state.live_event_store = LiveEventStore()
        assert client.post("/api/v1/live-sessions", json=body).status_code == 200  # its heartbeat
        controls = client.get("/api/v1/live-sessions/live-1/controls").json()["messages"]
        assert [c["kind"] for c in controls] == [REPLAY_HISTORY_CONTROL]
        held = client.post("/api/v1/live-sessions/live-1/events", json={"events": [_msg(9, "live")]}).json()
        assert held["ingested"] == 0
        replay = {"events": [_msg(1, "first", ts=10.0), _msg(2, "second", ts=20.0)]}
        assert client.post("/api/v1/live-sessions/live-1/events?replay=true", json=replay).json()["ingested"] == 3
        assert _texts(app.state.live_event_store, "live-1") == ["first", "second", "live"]
        # A replayed history never re-derives the session's current state.
        assert client.get("/api/v1/live-sessions/live-1").json().get("latest_progress") is None
