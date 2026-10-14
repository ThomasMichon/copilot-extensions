"""Held feed races and lifecycle use the resident's actual authenticated socket."""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from agent_worktrees import status_monitor_cutover as control
from agent_worktrees import worktrees_feed as feed
from agent_worktrees import worktrees_feed_client as client
from agent_worktrees.worktrees_projection_index import ProjectionUnavailable
from agent_worktrees.worktrees_runtime import WorktreesRuntime


def request(**selection):
    return {"contract_version": 1, "project": "project", **selection}


@pytest.fixture
def owner(tmp_path):
    runtime = WorktreesRuntime(tmp_path / "current.sqlite3")
    runtime.activate()
    runtime.publish("project", [{"id": "one", "title": "before"}], observed_at=1)
    try:
        yield runtime
    finally:
        runtime.shutdown()


def transaction(frames):
    result = []
    for frame in frames:
        result.append(frame)
        if frame["type"] in {"done", "commit"}:
            return result
    pytest.fail("feed ended before commit")


def apply(receiver, frames):
    admitted = None
    for frame in frames:
        admitted = receiver.receive(frame)
    assert admitted is not None
    return receiver.current


@pytest.mark.parametrize(
    ("failure", "code", "retry"),
    [
        (TimeoutError("slow live peer"), "timeout", False),
        (ValueError("malformed live peer"), "malformed-peer", False),
        (ProjectionUnavailable("invalid live transaction"), "malformed-peer", False),
        (ConnectionResetError("peer crashed"), "disconnected", True),
        (client.FeedFailure("not published", code="unready", retry=True), "unready", True),
        (client.FeedFailure("update needed", code="incompatible"), "incompatible", False),
    ],
)
def test_worktrees_feed_cli_preserves_structured_retry_policy(
    monkeypatch, capsys, failure, code, retry,
):
    from agent_worktrees import __main__ as core
    from agent_worktrees import config as cfg
    from agent_worktrees import list_cache, worktrees_client

    monkeypatch.setattr(cfg, "project_name", lambda: "project")
    monkeypatch.setattr(list_cache, "cache_key", lambda *args, **kwargs: "fixture")
    monkeypatch.setattr(list_cache, "note_demand", lambda *args, **kwargs: None)

    def fail(request):
        raise failure

    monkeypatch.setattr(client, "relay", fail)
    monkeypatch.setattr(
        worktrees_client, "read_current", lambda _: pytest.fail("feed failure recomputed a read"),
    )
    args = core.build_parser().parse_args(["list", "--current", "--follow", "--all"])
    assert worktrees_client.emit_current(args) == 1
    frame = json.loads(capsys.readouterr().out)
    assert frame["error"] == {"code": code, "message": str(failure), "retry": retry}


def test_worktrees_feed_barrier_replays_mutation_during_initial_snapshot(owner):
    stream = owner.feed.stream(request(), lambda: False)
    receiver = client.FeedReceiver(request())
    initial = [next(stream)]
    owner.publish("project", [{"id": "two", "title": "after"}], observed_at=2)
    initial.extend(transaction(stream))
    assert apply(receiver, initial)["worktrees"] == [{"id": "one", "title": "before"}]
    update = transaction(stream)
    assert update[0]["base_cursor"] == 1 and update[0]["cursor"] == 2
    assert [f["type"] for f in update] == ["update", "worktree", "delete", "order", "commit"]
    assert apply(receiver, update)["worktrees"] == [{"id": "two", "title": "after"}]
    stream.close()
    assert owner.feed.active_count() == 0


def test_worktrees_feed_coalesces_burst_without_queued_row_copies(owner):
    stream = owner.feed.stream(request(), lambda: False)
    receiver = client.FeedReceiver(request())
    apply(receiver, transaction(stream))
    for value in range(100):
        owner.publish("project", [{"id": "one", "title": str(value)}], observed_at=value)
    update = transaction(stream)
    assert update[0]["cursor"] == 101
    assert len(update) == 4
    assert apply(receiver, update)["worktrees"] == [{"id": "one", "title": "99"}]
    assert owner.feed._subscribers
    assert all(set(s) == {"project", "closed"} for s in owner.feed._subscribers.values())
    stream.close()


def test_worktrees_feed_filter_membership_and_deletes_are_committed(owner):
    stream = owner.feed.stream(request(status_filter="active"), lambda: False)
    receiver = client.FeedReceiver(request(status_filter="active"))
    assert apply(receiver, transaction(stream))["worktrees"] == []
    owner.publish("project", [{"id": "one", "status": "active"}], observed_at=2)
    assert apply(receiver, transaction(stream))["worktrees"] == [{"id": "one", "status": "active"}]
    owner.publish("project", [{"id": "one", "status": "done"}], observed_at=3)
    assert apply(receiver, transaction(stream))["worktrees"] == []
    stream.close()


def test_worktrees_feed_heartbeat_never_redecodes_the_unchanged_roster(owner, monkeypatch):
    monkeypatch.setattr(feed, "HEARTBEAT_SECONDS", 0.001)
    original = owner.handle
    calls = []

    def counted(payload):
        calls.append(payload)
        return original(payload)

    monkeypatch.setattr(owner, "handle", counted)
    stream = owner.feed.stream(request(), lambda: False)
    transaction(stream)
    for _ in range(5):
        assert next(stream)["type"] == "heartbeat"
    assert len(calls) == 1
    stream.close()


def test_worktrees_feed_active_demand_survives_linger_then_releases(owner):
    clock = [1.0]
    owner._now = lambda: clock[0]
    stream = owner.feed.stream(request(), lambda: False)
    transaction(stream)
    clock[0] = 100
    assert owner.demand_projects() == {"project"}
    stream.close()
    assert owner.demand_projects() == {"project"}
    clock[0] = 131
    assert owner.demand_projects() == set()


def test_worktrees_feed_bounded_admission_and_notice_before_release(owner, monkeypatch):
    monkeypatch.setattr(feed, "MAX_SUBSCRIBERS", 2)
    streams = [owner.feed.stream(request(), lambda: False) for _ in range(3)]
    transaction(streams[0])
    transaction(streams[1])
    refused = next(streams[2])
    assert refused["type"] == "error" and refused["error"]["code"] == "subscriber-limit"
    assert owner.feed.active_count() == 2
    owner.close_admission("superseded")
    for stream in streams[:2]:
        notice = next(stream)
        assert notice == {
            "type": "going-away", "reason": "superseded",
            "generation": owner.store.generation, "cursor": 1,
        }
        assert owner.feed.active_count() > 0
        with pytest.raises(StopIteration):
            next(stream)
    assert owner.feed.wait_closed(0.01)
    for stream in streams:
        stream.close()


def test_worktrees_feed_availability_notifies_without_recomputing_rows(owner):
    stream = owner.feed.stream(request(), lambda: False)
    receiver = client.FeedReceiver(request())
    apply(receiver, transaction(stream))
    owner.publication_failed("project", OSError("fixture producer unavailable"))
    frame = next(stream)
    assert frame["type"] == "availability"
    receiver.receive(frame)
    assert receiver.current["availability"]["status"] == "stale"
    assert receiver.current["projection"]["revision"] == 1
    owner.publish("project", [{"id": "one", "title": "before"}], observed_at=2)
    assert apply(receiver, transaction(stream)).get("availability") is None
    stream.close()


@pytest.mark.parametrize("mode", ["notice", "cancel", "wrong-token"])
def test_worktrees_feed_real_transport_lifecycle(owner, monkeypatch, mode):
    monkeypatch.setattr(feed, "HEARTBEAT_SECONDS", 0.02)
    server = control.ControlServer(
        lambda action, payload: owner.health(),
        token="fixture",
        stream_handle=lambda action, payload, disconnected: owner.feed.stream(payload, disconnected),
    )
    server.start()
    reader = control.ControlClient(
        f"http://127.0.0.1:{server.port}",
        token="wrong" if mode == "wrong-token" else "fixture",
        timeout=1,
    )
    stream = client.transport(reader, request())
    try:
        if mode == "wrong-token":
            with pytest.raises(ProjectionUnavailable, match="disconnected"):
                next(stream)
            assert owner.feed.active_count() == 0
            return
        initial = transaction(stream)
        assert initial[-1]["cursor"] == 1
        assert owner.feed.active_count() == 1
        if mode == "notice":
            owner.close_admission("draining")
            assert next(stream)["type"] == "going-away"
            with pytest.raises(StopIteration):
                next(stream)
        stream.close()
        assert owner.feed.wait_closed(1)
        assert owner.feed.active_count() == 0
    finally:
        stream.close()
        owner.close_admission()
        server.close()


def test_worktrees_feed_transport_preserves_snapshot_to_update_barrier(owner):
    server = control.ControlServer(
        lambda action, payload: owner.health(), token="fixture",
        stream_handle=lambda action, payload, disconnected: owner.feed.stream(payload, disconnected),
    )
    server.start()
    reader = control.ControlClient(f"http://127.0.0.1:{server.port}", token="fixture", timeout=1)
    stream = client.transport(reader, request())
    receiver = client.FeedReceiver(request())
    try:
        apply(receiver, transaction(stream))
        owner.publish("project", [{"id": "one", "title": "real-update"}], observed_at=2)
        assert apply(receiver, transaction(stream))["worktrees"][0]["title"] == "real-update"
        owner.close_admission("update")
        assert next(stream)["reason"] == "update"
    finally:
        stream.close()
        owner.close_admission()
        owner.feed.wait_closed(1)
        server.close()


def test_worktrees_feed_real_transport_preserves_reorder_and_front_insertion(owner):
    server = control.ControlServer(
        lambda action, payload: owner.health(), token="fixture",
        stream_handle=lambda action, payload, disconnected: owner.feed.stream(payload, disconnected),
    )
    server.start()
    reader = control.ControlClient(f"http://127.0.0.1:{server.port}", token="fixture", timeout=1)
    initial = [{"id": f"row-{i}"} for i in range(300)]
    owner.publish("project", initial, observed_at=2)
    stream = client.transport(reader, request())
    receiver = client.FeedReceiver(request())
    try:
        assert apply(receiver, transaction(stream))["worktrees"] == initial
        for revision, rows in enumerate(
            [list(reversed(initial)), [{"id": "front"}, *initial]], start=3,
        ):
            owner.publish("project", rows, observed_at=revision)
            frames = transaction(stream)
            assert all(len(f["ids"]) <= 128 for f in frames if f["type"] == "order")
            assert sum(f["type"] == "worktree" for f in frames) == (revision == 4)
            assert apply(receiver, frames)["worktrees"] == owner.handle(request())["worktrees"]
    finally:
        stream.close()
        owner.close_admission()
        owner.feed.wait_closed(1)
        server.close()


@pytest.mark.parametrize("bad", ["missing", "duplicate", "unknown", "oversized"])
def test_worktrees_feed_rejects_invalid_identity_order_without_replacing_commit(owner, bad):
    receiver = client.FeedReceiver(request())
    before = owner.handle(request())
    apply(receiver, list(feed.publication_frames(before)))
    committed = receiver.current
    owner.publish("project", [{"id": "one", "title": "after"}], observed_at=2)
    frames = list(feed.publication_frames(owner.handle(request()), before))
    order = next(frame for frame in frames if frame["type"] == "order")
    if bad == "missing":
        frames.remove(order)
    else:
        order["ids"] = {
            "duplicate": ["one", "one"], "unknown": ["unknown"], "oversized": ["one"] * 129,
        }[bad]
    with pytest.raises(ProjectionUnavailable):
        apply(receiver, frames)
    assert receiver.current == committed


@pytest.mark.parametrize("change", ["generation", "base", "revision", "missing", "duplicate"])
def test_worktrees_feed_receiver_rejects_broken_incremental_commit(owner, change):
    receiver = client.FeedReceiver(request())
    result = owner.handle(request())
    apply(receiver, list(feed.publication_frames(result)))
    owner.publish("project", [{"id": "one", "title": "after"}], observed_at=2)
    update = list(feed.publication_frames(owner.handle(request()), result))
    if change == "generation":
        update[0] = {**update[0], "projection": {**update[0]["projection"], "generation": "a" * 32}}
    elif change == "base":
        update[0]["base_cursor"] = 0
    elif change == "revision":
        update[0]["cursor"] = 1
    elif change == "missing":
        update[0]["selected_count"] = 2
    else:
        update.insert(2, update[1])
    with pytest.raises(ProjectionUnavailable):
        apply(receiver, update)
    assert receiver.current["projection"]["revision"] == 1


def test_worktrees_feed_graceful_close_race_still_emits_notice(owner, monkeypatch):
    original = owner.handle

    def closing(payload):
        owner.close_admission("update")
        return original(payload)

    monkeypatch.setattr(owner, "handle", closing)
    stream = owner.feed.stream(request(), lambda: False)
    notice = next(stream)
    assert notice["type"] == "going-away" and notice["cursor"] is None
    assert client.FeedReceiver(request()).receive(notice) == [notice]
    stream.close()


def test_worktrees_feed_revoked_election_closes_held_reader(owner, monkeypatch):
    monkeypatch.setattr(feed, "HEARTBEAT_SECONDS", 0.001)
    elected = [True]
    owner._elected = lambda: elected[0]
    stream = owner.feed.stream(request(), lambda: False)
    transaction(stream)
    elected[0] = False
    assert next(stream)["reason"] == "superseded"
    stream.close()


def test_worktrees_feed_retained_crashed_route_runs_bounded_recovery(owner, monkeypatch, tmp_path):
    from zdd import routing

    from agent_worktrees import worktrees_client

    routes = []
    recovery = []
    token = tmp_path / "token"
    token.write_text("fixture", encoding="utf-8")
    monkeypatch.setattr(control, "control_token_path", lambda: token)
    monkeypatch.setattr(
        control, "load_or_create_control_token", lambda: pytest.fail("reader minted a token")
    )

    def route(*args, **kwargs):
        routes.append(1)
        return SimpleNamespace(base_url="http://127.0.0.1:1" if len(routes) == 1 else "http://127.0.0.1:2")

    def health(self, *args, **kwargs):
        if self.port == 1:
            raise ConnectionRefusedError("fixture crashed")
        return owner.health()

    monkeypatch.setattr(routing, "read_active_endpoint", route)
    monkeypatch.setattr(control.ControlClient, "_request", health)
    monkeypatch.setattr(
        worktrees_client, "read_current", lambda payload: recovery.append(payload) or owner.handle(payload)
    )
    restored = client._client(request())
    assert restored.port == 2 and recovery == [request()] and len(routes) == 2


@pytest.mark.parametrize("failure", [TimeoutError("slow"), ValueError("malformed")])
def test_worktrees_feed_live_health_failure_cannot_trigger_recovery(owner, monkeypatch, tmp_path, failure):
    from zdd import routing

    from agent_worktrees import worktrees_client

    token = tmp_path / "token"
    token.write_text("fixture", encoding="utf-8")
    monkeypatch.setattr(control, "control_token_path", lambda: token)
    monkeypatch.setattr(
        routing, "read_active_endpoint", lambda *a, **kw: SimpleNamespace(base_url="http://127.0.0.1:1")
    )
    monkeypatch.setattr(
        control.ControlClient, "_request", lambda *a, **kw: (_ for _ in ()).throw(failure)
    )
    monkeypatch.setattr(
        worktrees_client, "read_current", lambda _: pytest.fail("live failure triggered recovery")
    )
    with pytest.raises(type(failure)):
        client._client(request())


def test_worktrees_feed_drain_accounts_point_read_until_transport_completion(owner, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = owner.store.read

    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(owner.store, "read", blocked)
    server = control.ControlServer(lambda action, payload: owner.handle(payload), token="fixture")
    server.start()
    reader = control.ControlClient(f"http://127.0.0.1:{server.port}", token="fixture", timeout=2)
    result = []
    worker = threading.Thread(target=lambda: result.append(reader.worktrees_read(request(worktree_id="one"))))
    worker.start()
    try:
        assert entered.wait(1)
        owner.close_admission()
        assert server.current_read_count() == 1
        assert owner.handle(request())["error"]["code"] == "draining"
        release.set()
        worker.join(1)
        assert not worker.is_alive() and result[0]["ok"]
        deadline = time.monotonic() + 1
        while server.current_read_count() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.current_read_count() == 0
    finally:
        release.set()
        worker.join(1)
        server.close()


def test_worktrees_feed_actual_cli_full_roster_load_update_and_cutover(tmp_path, monkeypatch):
    from agent_procutil import no_window_flags

    from agent_worktrees import config as cfg

    monkeypatch.setenv("AGENT_HOME", str(tmp_path / "agent-home"))
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / ".git").write_text("gitdir: fixture")
    config_path = cfg.project_dir("project") / "config.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps({"repos": {"project": {"anchor": str(checkout)}}}), encoding="utf-8",
    )
    token_path = control.control_token_path()
    assert token_path.is_relative_to(tmp_path)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text("fixture", encoding="utf-8")
    runtime = WorktreesRuntime(tmp_path / "current.sqlite3")
    runtime.activate()
    rows = [
        {
            "id": f"worktree-{index:04d}", "title": f"Current task {index}",
            "status": "active", "state": "wip", "dirty": index % 3,
            "platform": "windows", "checkout_present": True,
            "claims_assets": {"held_count": 1000, "by_kind": {"worktree": 1000}},
        }
        for index in range(1100)
    ]
    runtime.publish("project", rows, observed_at=1)
    server = control.ControlServer(
        lambda action, payload: runtime.health() if action == "health" else runtime.handle(payload),
        token="fixture",
        stream_handle=lambda action, payload, disconnected: runtime.feed.stream(payload, disconnected),
    )
    server.start()
    control.publish_route(server.port, pid=os.getpid(), version="fixture")
    frames = queue.Queue()
    started = time.monotonic()
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "agent_worktrees", "--project", "project",
            "list", "--json", "--current", "--stream", "--follow",
            "--include-other-platforms", "--all",
        ],
        cwd=checkout, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL, text=True, encoding="utf-8",
        creationflags=no_window_flags(),
    )

    def read_stdout():
        for line in proc.stdout:
            frames.put(json.loads(line))

    reader = threading.Thread(target=read_stdout)
    reader.start()
    try:
        initial = []
        try:
            while not initial or initial[-1]["type"] != "done":
                initial.append(frames.get(timeout=max(0.001, 2 - (time.monotonic() - started))))
        except queue.Empty:
            exited = proc.poll()
            if exited is None:
                proc.terminate()
                proc.wait(timeout=2)
            pytest.fail(
                f"CLI initial roster missed 2s: {len(initial)} frames, exit={exited}, "
                f"stderr={proc.stderr.read(8192)!r}",
            )
        elapsed = time.monotonic() - started
        assert elapsed < 2
        receiver = client.FeedReceiver(request())
        current = apply(receiver, initial)
        assert len(current["worktrees"]) == 1100
        assert current["projection"]["selected_count"] == 1100
        assert current["worktrees"][-1]["claims_assets"]["held_count"] == 1000
        rows[-1] = {**rows[-1], "dirty": 9}
        runtime.publish("project", rows, observed_at=2)
        changed = []
        while not changed or changed[-1]["type"] != "commit":
            changed.append(frames.get(timeout=2))
        assert len([f for f in changed if f["type"] == "worktree"]) == 1
        assert apply(receiver, changed)["worktrees"][-1]["dirty"] == 9
        runtime.close_admission("update")
        notice = frames.get(timeout=2)
        assert notice["type"] == "going-away" and notice["reason"] == "update"
        proc.wait(timeout=2)
        reader.join(timeout=1)
        assert proc.returncode == 0 and not reader.is_alive()
        assert runtime.feed.wait_closed(1)
        print(f"actual CLI full current roster: 1100 rows in {elapsed * 1000:.2f}ms")
    finally:
        runtime.close_admission()
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=2)
        reader.join(timeout=1)
        server.close()
        runtime.shutdown()
