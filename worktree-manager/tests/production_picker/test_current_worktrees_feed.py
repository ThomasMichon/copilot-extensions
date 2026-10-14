"""Current feed admission through the actual background loader and child stdout."""

from __future__ import annotations

import sys
import threading
import time

import pytest

from worktree_manager import engine_client
from worktree_manager.production_picker.picker_tui import data_ssh
from worktree_manager.production_picker.picker_tui import loader_current_feed as loader_feed
from worktree_manager.worktree_feed_contract import CurrentFeed, FeedUnavailable


def head(revision=1, count=1, generation="a" * 32):
    return {
        "schema_version": 1, "quality": "current", "generation": generation,
        "revision": revision, "observed_at": revision, "roster_count": count,
        "selected_count": count,
    }


def snapshot(rows, *, generation="a" * 32):
    projection = head(count=len(rows), generation=generation)
    return [
        {"type": "begin", "contract_version": 1, "projection": projection,
         "selected_count": len(rows), "cursor": 1, "base_cursor": None},
        *({"type": "worktree", "projection": projection, "wt": row} for row in rows),
        {"type": "done", "projection": projection, "cursor": 1},
    ]


def update(rows, *, deleted=(), revision=2, count=None, order=None):
    projection = head(revision, count=len(rows) if count is None else count)
    return [
        {"type": "update", "contract_version": 1, "projection": projection,
         "selected_count": projection["selected_count"], "cursor": revision,
         "base_cursor": revision - 1},
        *({"type": "worktree", "projection": projection, "wt": row} for row in rows),
        *({"type": "delete", "projection": projection, "id": wid} for wid in deleted),
        *(
            {"type": "order", "projection": projection, "ids": identities[start:start + 128]}
            for identities in [[row["id"] for row in rows] if order is None else order]
            for start in range(0, len(identities), 128)
        ),
        {"type": "commit", "projection": projection, "cursor": revision},
    ]


def receive(receiver, frames):
    rows = None
    for frame in frames:
        rows = receiver.receive(frame)
    return rows


def wait_until(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    pytest.fail("background feed did not reach its expected state")


def test_current_feed_initial_update_deletes_and_empty_are_atomic():
    receiver = CurrentFeed()
    assert receive(receiver, snapshot([{"id": "one", "dirty": 0}]))[0]["dirty"] == 0
    pending = update([{"id": "two", "dirty": 1}], deleted=["one"])
    for frame in pending[:-1]:
        assert receiver.receive(frame) is None
        assert receiver.rows[0]["id"] == "one"
    assert receiver.receive(pending[-1])[0]["id"] == "two"
    assert receive(receiver, update([], deleted=["two"], revision=3)) == []
    assert receiver.head["revision"] == 3


def test_current_feed_preserves_reorder_and_front_insertion_without_reemitting_unchanged_rows():
    receiver = CurrentFeed()
    initial = [{"id": f"row-{i}"} for i in range(300)]
    receive(receiver, snapshot(initial))
    reversed_ids = [row["id"] for row in reversed(initial)]
    rows = receive(receiver, update([], count=300, order=reversed_ids))
    assert [row["id"] for row in rows] == reversed_ids
    front_ids = ["front", *reversed_ids]
    rows = receive(
        receiver, update([{"id": "front"}], revision=3, count=301, order=front_ids),
    )
    assert [row["id"] for row in rows] == front_ids


def test_current_feed_refreshes_time_fields_for_reused_and_changed_rows(monkeypatch):
    from datetime import datetime, timedelta

    from worktree_manager.production_picker.picker_tui import derive

    clock = [datetime(2026, 10, 14, 0, 25, 10)]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0]

    monkeypatch.setattr(loader_feed._dt, "datetime", Clock)
    monkeypatch.setattr(derive, "NOW", datetime(2026, 10, 13))
    source = data_ssh.Source("host", "Win", None, local=True)
    loader = data_ssh.LiveLoader([source])
    receiver = CurrentFeed()
    base = {
        "status": "active", "state": "wip", "dirty": 3,
        "started_at": "2026-10-14T00:00:00",
        "last_resumed_at": "2026-10-14T00:20:00",
        "live_intent": "Working", "live_intent_at": "2026-10-14T00:25:00",
    }
    rows = [{"id": "one", **base}, {"id": "two", **base}]
    norm = derive.norm
    normalized = []

    def counted(row, *args, **kwargs):
        normalized.append(row["id"])
        return norm(row, *args, **kwargs)

    monkeypatch.setattr(derive, "norm", counted)
    loader._feed_commit(source, 0, receive(receiver, snapshot(rows)))
    before = loader.records()[0]
    assert (before["age"], before["used"], before["live_pulse"]) == ("25m", "5m", "fresh")
    normalized.clear()
    clock[0] += timedelta(minutes=40)
    changed = {**rows[1], "title": "Changed"}
    loader._feed_commit(
        source, 0, receive(receiver, update([changed], count=2, order=["one", "two"])),
    )
    assert normalized == ["two"]
    for row in loader.records():
        assert (row["age"], row["used"], row["live_pulse"]) == ("1h", "45m", "stale")
        assert row["age_secs"] == before["age_secs"] + 2400
        assert row["raw"]["dirty"] == 3 and row["raw"]["state"] == "wip"
    assert loader.records()[0]["asset_hints"] is before["asset_hints"]


@pytest.mark.parametrize("order", [[], ["unknown"], ["one", "one"], [False]])
def test_current_feed_rejects_invalid_order_and_preserves_last_good_rows(order):
    receiver = CurrentFeed()
    before = receive(receiver, snapshot([{"id": "one"}]))
    with pytest.raises(ValueError):
        receive(receiver, update([{"id": "one", "title": "after"}], order=order))
    assert receiver.rows == before
    assert receiver.head["revision"] == 1


@pytest.mark.parametrize("bad", ["base", "revision", "generation", "duplicate", "missing", "mixed", "history"])
def test_current_feed_rejects_broken_updates_and_retains_last_commit(bad):
    receiver = CurrentFeed()
    receive(receiver, snapshot([{"id": "one"}]))
    frames = update([{"id": "one", "title": "after"}])
    if bad == "base":
        frames[0]["base_cursor"] = 0
    elif bad == "revision":
        frames[0]["cursor"] = 1
    elif bad == "generation":
        frames[0]["projection"]["generation"] = "b" * 32
    elif bad == "duplicate":
        frames.insert(2, frames[1])
    elif bad == "missing":
        frames.pop(1)
        frames[0]["projection"]["selected_count"] = 2
        frames[0]["projection"]["roster_count"] = 2
        frames[0]["selected_count"] = 2
    elif bad == "mixed":
        frames[1]["projection"] = head(3)
    else:
        frames[1]["wt"]["resources"] = [{"kind": "history"}]
    with pytest.raises((TypeError, ValueError)):
        receive(receiver, frames)
    assert receiver.rows[0]["id"] == "one" and receiver.head["revision"] == 1


def test_current_feed_availability_and_cutover_do_not_rederive_domain_state():
    receiver = CurrentFeed()
    receive(receiver, snapshot([{"id": "one", "dirty": 7, "state": "wip"}]))
    rows = receiver.receive({
        "type": "availability", "projection": head(),
        "availability": {"status": "stale", "reason": "owner unavailable"},
    })
    assert rows[0]["dirty"] == 7 and rows[0]["state"] == "wip"
    receiver.receive({"type": "heartbeat", "generation": "a" * 32, "cursor": 1})
    assert receiver.receive({
        "type": "going-away", "generation": "a" * 32, "cursor": 1, "reason": "update",
    }) is None
    assert receiver.notice == "update" and receiver.rows[0]["dirty"] == 7


@pytest.mark.parametrize("local", [True, False])
def test_current_feed_actual_loader_subprocess_commits_both_sources_and_cancels(monkeypatch, local):
    monkeypatch.delenv("AGENT_WORKTREES_PICKER_STREAM", raising=False)
    initial = snapshot([
        {"id": "one", "title": "before", "status": "active", "dirty": 0},
        {"id": "other", "title": "unchanged", "status": "active", "dirty": 0},
    ])
    changed = update(
        [{"id": "two", "title": "after", "status": "active", "dirty": 3}],
        deleted=["one"], count=2, order=["two", "other"],
    )
    script = (
        "import json,time\n"
        f"initial={initial!r}\nchanged={changed!r}\n"
        "for frame in initial: print(json.dumps(frame),flush=True)\n"
        "time.sleep(.1)\n"
        "for frame in changed: print(json.dumps(frame),flush=True)\n"
        "time.sleep(10)\n"
    )
    monkeypatch.setattr(loader_feed, "feed_argv", lambda source: [sys.executable, "-u", "-c", script])
    source = data_ssh.Source("host", "Win", None, local=local)
    loader = data_ssh.LiveLoader([source])
    worker = threading.Thread(target=loader._load_one, args=(source, 0))
    worker.start()
    try:
        wait_until(lambda: bool(loader.records()) and loader.records()[0]["id"] == "two")
        row = loader.records()[0]
        assert [record["id"] for record in loader.records()] == ["two", "other"]
        assert row["raw"]["dirty"] == 3
        assert row["raw"]["_projection"]["revision"] == 2
        assert source.source_id in loader.authoritative_source_ids()
        assert loader.repoll_silent() == 0
        assert loader.reconcile_remote_prs() == 0
        assert loader._held_sources == {source.cache_key}
    finally:
        loader.cancel()
        worker.join(timeout=3)
    assert not worker.is_alive()
    assert loader._procs == [] and loader._held_sources == set()


@pytest.mark.parametrize("graceful", [True, False])
def test_current_feed_loader_reconnect_resolves_new_generation_without_empty_overwrite(
    monkeypatch, tmp_path, graceful,
):
    marker = tmp_path / "attempt"
    first = snapshot([{"id": "one", "title": "first", "dirty": 2}])
    second = snapshot([{"id": "two", "title": "second", "dirty": 2}], generation="b" * 32)
    notice = {"type": "going-away", "generation": "a" * 32, "cursor": 1, "reason": "update"}
    script = (
        "import json,time,pathlib\n"
        f"path=pathlib.Path({str(marker)!r})\n"
        "second=path.exists()\npath.write_text('started')\n"
        f"frames={second!r} if second else {first!r}\n"
        "if second: time.sleep(.1)\n"
        "for frame in frames: print(json.dumps(frame),flush=True)\n"
        f"if not second and {graceful!r}: print(json.dumps({notice!r}),flush=True)\n"
        "if second: time.sleep(10)\n"
    )
    calls = []

    def argv(source):
        calls.append(source.source_id)
        return [sys.executable, "-u", "-c", script]

    monkeypatch.setattr(loader_feed, "feed_argv", argv)
    source = data_ssh.Source("host", "Win", None, local=True)
    loader = data_ssh.LiveLoader([source])
    worker = threading.Thread(target=loader._load_one, args=(source, 0))
    worker.start()
    try:
        wait_until(lambda: bool(loader.error_for_source(source.source_id)))
        expected = "going away: update" if graceful else "without a going-away notice"
        assert expected in loader.error_for_source(source.source_id)
        assert loader.records()[0]["id"] == "one"
        assert loader.records()[0]["title"].startswith("[stale]")
        wait_until(lambda: loader.records()[0]["id"] == "two")
        assert len(calls) == 2
        assert loader.records()[0]["raw"]["_projection"]["generation"] == "b" * 32
        assert not loader.error_for_source(source.source_id)
    finally:
        loader.cancel()
        worker.join(timeout=3)
    assert not worker.is_alive() and not loader._procs


@pytest.mark.parametrize("shell", ["bash", "pwsh"])
def test_current_feed_local_and_remote_argv_preserve_authority(monkeypatch, shell):
    monkeypatch.setattr(data_ssh, "_project", lambda: "project")
    monkeypatch.setattr(engine_client, "engine_base_command", lambda: ["selected-engine"])
    source = data_ssh.Source("host", "Win", None, local=True)
    argv = loader_feed.feed_argv(source)
    assert argv[:3] == ["selected-engine", "--project", "project"]
    assert {"--current", "--stream", "--follow"} <= set(argv)
    assert "--include-other-platforms" not in argv
    remote = data_ssh.Source("peer", "Linux", [], alias="peer", shell=shell)
    original = data_ssh._stream_argv(remote)
    argv = loader_feed.feed_argv(remote)
    assert argv[0] == "ssh"
    assert argv[:-1] == original[:-1]
    assert argv[-2] == "peer"
    assert data_ssh._remote_cmd_str(argv) == data_ssh._remote_cmd_str(original).replace(
        " --stream", " --stream --follow",
    )
    command = data_ssh._remote_cmd_str(argv)
    assert "--current" in command and "--stream --follow" in command
    assert command.count("--follow") == 1


def test_current_feed_unsupported_is_actionable_not_legacy_fallback(monkeypatch):
    source = data_ssh.Source("host", "Win", None, local=True)
    loader = data_ssh.LiveLoader([source])
    error = {
        "type": "error", "error": {"code": "unavailable", "message": "resident lacks worktrees-feed-v1; update the engine"},
    }
    script = f"import json; print(json.dumps({error!r}),flush=True)"
    monkeypatch.setattr(loader_feed, "feed_argv", lambda source: [sys.executable, "-u", "-c", script])
    monkeypatch.setattr(
        loader, "_load_local_two_phase", lambda *a: pytest.fail("feed fell back to caller polling")
    )
    loader._load_one(source, 0)
    assert loader._state[source.cache_key] == "failed"
    assert "update the engine" in loader.error_for_source(source.source_id)
    assert not loader._procs


def test_current_feed_one_shot_can_be_explicitly_selected(monkeypatch):
    monkeypatch.setenv("AGENT_WORKTREES_PICKER_STREAM", "0")
    assert data_ssh._stream_enabled() is False
    monkeypatch.delenv("AGENT_WORKTREES_PICKER_STREAM", raising=False)
    assert data_ssh._stream_enabled() is True


def test_current_feed_error_retry_policy():
    receiver = CurrentFeed()
    with pytest.raises(FeedUnavailable) as error:
        receiver.receive({"type": "error", "error": {"code": "unready", "message": "not published"}})
    assert error.value.retry is True


@pytest.mark.parametrize(
    ("code", "retry"), [("timeout", True), ("malformed-peer", False), ("unavailable", False)],
)
def test_current_feed_terminal_errors_stop_actual_child_respawn(monkeypatch, code, retry):
    source = data_ssh.Source("host", "Win", None, local=True)
    loader = data_ssh.LiveLoader([source])
    error = {
        "type": "error", "error": {"code": code, "message": "fixture terminal peer", "retry": retry},
    }
    script = f"import json,time; print(json.dumps({error!r}),flush=True); time.sleep(10)"
    calls = []

    def argv(source):
        calls.append(source.source_id)
        return [sys.executable, "-u", "-c", script]

    monkeypatch.setattr(loader_feed, "feed_argv", argv)
    worker = threading.Thread(target=loader._load_one, args=(source, 0))
    worker.start()
    try:
        wait_until(lambda: bool(loader.error_for_source(source.source_id)))
        worker.join(timeout=1)
        assert not worker.is_alive()
        assert calls == [source.source_id]
        assert loader._state[source.cache_key] == "failed"
        assert "fixture terminal peer" in loader.error_for_source(source.source_id)
        assert loader._procs == [] and loader._held_sources == set()
    finally:
        loader.cancel()
        worker.join(timeout=3)
    assert not worker.is_alive()


@pytest.mark.parametrize("retry", [None, 0, "false"])
def test_current_feed_rejects_non_boolean_retry_policy(retry):
    with pytest.raises(ValueError, match="retry policy"):
        CurrentFeed().receive({
            "type": "error", "error": {"code": "unready", "message": "fixture", "retry": retry},
        })
