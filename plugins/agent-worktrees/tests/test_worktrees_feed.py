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


def _test_budget_factor():
    value = os.environ.get("WORKTREES_TEST_BUDGET_FACTOR", "1")
    if value not in {"1", "2", "3"}:
        raise ValueError("WORKTREES_TEST_BUDGET_FACTOR must be 1, 2 or 3")
    return int(value)


@pytest.mark.parametrize("value,expected", [(None, 1), ("1", 1), ("2", 2), ("3", 3)])
def test_test_budget_factor_explicit_values(monkeypatch, value, expected):
    monkeypatch.delenv("WORKTREES_TEST_BUDGET_FACTOR", raising=False)
    if value is not None:
        monkeypatch.setenv("WORKTREES_TEST_BUDGET_FACTOR", value)
    assert _test_budget_factor() == expected


@pytest.mark.parametrize("value", ["", "0", "4", "-1", "1.5", "nan", "2.0"])
def test_test_budget_factor_rejects_other_values(monkeypatch, value):
    monkeypatch.setenv("WORKTREES_TEST_BUDGET_FACTOR", value)
    with pytest.raises(ValueError, match="must be 1, 2 or 3"):
        _test_budget_factor()


# The documented meta-path protocol requires find_spec, not ABC inheritance.
# https://docs.python.org/3/reference/import.html#the-meta-path
_GUARDED_MODULE_SCRIPT = """
import runpy, sys
class Guard:
    entries = 0
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'agent_worktrees.__main__':
            self.entries += 1
            if self.entries > 1:
                raise ImportError('dispatcher import back-edge')
        if fullname in {
            'agent_worktrees.activity', 'agent_worktrees.front_door_cli',
            'agent_worktrees.list_cli', 'agent_worktrees.status_monitor_runtime',
        }:
            raise ImportError('broad command/recovery import: ' + fullname)
sys.meta_path.insert(0, Guard())
sys.argv = ['agent-worktrees', *sys.argv[1:]]
runpy.run_module('agent_worktrees', run_name='__main__')
"""


def _guard_from_native_script():
    import ast

    classes = [node for node in ast.parse(_GUARDED_MODULE_SCRIPT).body
               if isinstance(node, ast.ClassDef)]
    assert len(classes) == 1
    namespace = {}
    exec(compile(ast.Module(body=classes, type_ignores=[]), "<native-guard>", "exec"), namespace)
    assert namespace["Guard"].__bases__ == (object,)
    return namespace["Guard"]()


@pytest.mark.parametrize("forbidden", [
    "agent_worktrees.activity",
    "agent_worktrees.front_door_cli",
    "agent_worktrees.list_cli",
    "agent_worktrees.status_monitor_runtime",
    "agent_worktrees.__main__",
])
def test_guarded_import_blocks_before_following_finder_effect(monkeypatch, forbidden):
    import importlib

    guard = _guard_from_native_script()
    if forbidden == "agent_worktrees.__main__":
        assert guard.find_spec(forbidden, None) is None
    effects = []

    class EffectFinder:
        def find_spec(self, fullname, path=None, target=None):
            if fullname == forbidden:
                effects.append(fullname)
                raise AssertionError("forbidden import reached a following finder")

    with monkeypatch.context() as scoped:
        scoped.delitem(sys.modules, forbidden, raising=False)
        scoped.setattr(sys, "meta_path", [guard, EffectFinder(), *sys.meta_path])
        message = "dispatcher import back-edge" if forbidden.endswith(".__main__") else (
            "broad command/recovery import"
        )
        with pytest.raises(ImportError, match=message):
            importlib.import_module(forbidden)
    assert effects == []


def test_guarded_import_allows_normal_finder_delegation(monkeypatch):
    import importlib

    guard = _guard_from_native_script()
    with monkeypatch.context() as scoped:
        scoped.delitem(sys.modules, "math", raising=False)
        scoped.setattr(sys, "meta_path", [guard, *sys.meta_path])
        allowed = importlib.import_module("math")
        assert allowed.sqrt(4) == 2
    assert guard.entries == 0


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


@pytest.mark.parametrize("started", [True, False])
def test_cold_feed_without_an_advisory_roster_retries_only_admitted_recovery(
    monkeypatch, tmp_path, capsys, started,
):
    from agent_worktrees import __main__ as core
    from agent_worktrees import config as cfg
    from agent_worktrees import list_cache, worktrees_client

    monkeypatch.setattr(cfg, "install_dir", lambda: tmp_path)
    monkeypatch.setattr(cfg, "project_name", lambda: "project")
    monkeypatch.setattr(worktrees_client.monitor, "_aw_runtime_home", lambda: tmp_path)
    monkeypatch.setattr(worktrees_client.monitor, "_status_monitor_enabled", lambda: True)
    monkeypatch.setattr(worktrees_client.monitor, "_ensure_status_monitor", lambda: started)
    monkeypatch.setattr(worktrees_client, "RECOVERY_WAIT", 0)
    monkeypatch.setattr(list_cache, "cache_key", lambda *args, **kwargs: "fixture")
    monkeypatch.setattr(list_cache, "note_demand", lambda *args, **kwargs: None)
    args = core.build_parser().parse_args(["list", "--current", "--follow", "--all"])

    assert worktrees_client.emit_current(args) == 1
    error = json.loads(capsys.readouterr().out)["error"]
    assert error["retry"] is started
    assert error["code"] == ("unready" if started else "malformed-peer")
    assert "no usable advisory roster" in error["message"]


@pytest.mark.parametrize("reply", ["unready", "malformed", "incompatible", "superseded"])
@pytest.mark.parametrize("first_read", [False, True])
def test_cold_feed_validates_initial_and_recovery_resident_errors_before_retrying(
    monkeypatch, tmp_path, capsys, reply, first_read,
):
    from agent_worktrees import __main__ as core
    from agent_worktrees import config as cfg
    from agent_worktrees import list_cache, worktrees_client

    calls = []

    def resident(payload):
        calls.append(payload)
        if not first_read and len(calls) == 1:
            raise ConnectionRefusedError("stopped resident")
        response = {
            "contract_version": 3 if reply == "incompatible" else 1,
            "ok": False,
            "error": {"code": reply, "message": "" if reply == "malformed" else "not published"},
        }
        return worktrees_client.validate_result(response, payload)

    ticks = iter([10.0, 10.1, 10.6])
    monkeypatch.setattr(worktrees_client, "_resident", resident)
    monkeypatch.setattr(worktrees_client, "time", SimpleNamespace(
        monotonic=lambda: next(ticks), sleep=lambda _: None,
    ))
    monkeypatch.setattr(cfg, "install_dir", lambda: tmp_path)
    monkeypatch.setattr(cfg, "project_name", lambda: "project")
    monkeypatch.setattr(worktrees_client.monitor, "_aw_runtime_home", lambda: tmp_path)
    monkeypatch.setattr(worktrees_client.monitor, "_status_monitor_enabled", lambda: True)
    def ensure():
        assert not first_read, "a live unready response must not start another resident"
        return True

    monkeypatch.setattr(worktrees_client.monitor, "_ensure_status_monitor", ensure)
    monkeypatch.setattr(list_cache, "cache_key", lambda *a, **kw: "fixture")
    monkeypatch.setattr(list_cache, "note_demand", lambda *a, **kw: None)
    args = core.build_parser().parse_args(["list", "--current", "--follow", "--all"])

    assert worktrees_client.emit_current(args) == 1
    error = json.loads(capsys.readouterr().out)["error"]
    assert len(calls) == (1 if first_read else 2)
    assert error["retry"] is (reply == "unready")
    assert error["code"] == ("unready" if reply == "unready" else "malformed-peer")
    assert "no usable advisory roster" in error["message"]


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
    monkeypatch.setattr(control, "control_token_path", lambda runtime_home=None: token)
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


def test_worktrees_feed_confirmed_dead_route_does_not_wait_for_socket_timeout(
    owner, monkeypatch, tmp_path,
):
    from agent_worktrees import locks, worktrees_client
    from zdd import process_exit

    routes = iter([
        SimpleNamespace(pid=12345, process_start_time="12345", base_url="http://127.0.0.1:1"),
        SimpleNamespace(pid=23456, process_start_time="23456", base_url="http://127.0.0.1:2"),
    ])
    token = tmp_path / "token"
    token.write_text("fixture", encoding="utf-8")
    monkeypatch.setattr(control, "control_token_path", lambda runtime_home=None: token)
    monkeypatch.setattr(worktrees_client.routing, "read_active_endpoint", lambda *a, **kw: next(routes))
    monkeypatch.setattr(locks, "lock_is_live", lambda identity: True)
    monkeypatch.setattr(locks.platform, "system", lambda: "Windows")
    monkeypatch.setattr(process_exit, "observe_original_exit", lambda pid, birth: pid == 12345)
    recovery = []
    monkeypatch.setattr(
        worktrees_client, "read_current", lambda payload: recovery.append(payload) or owner.handle(payload),
    )

    def health(self, *args, **kwargs):
        assert self.port == 2, "confirmed dead route was dialed"
        return owner.health()

    monkeypatch.setattr(control.ControlClient, "_request", health)
    assert client._client(request()).port == 2
    assert recovery == [request()]


@pytest.mark.parametrize("failure", [TimeoutError("slow"), ValueError("malformed")])
def test_worktrees_feed_live_health_failure_cannot_trigger_recovery(owner, monkeypatch, tmp_path, failure):
    from zdd import routing

    from agent_worktrees import worktrees_client

    token = tmp_path / "token"
    token.write_text("fixture", encoding="utf-8")
    monkeypatch.setattr(control, "control_token_path", lambda runtime_home=None: token)
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


@pytest.mark.parametrize("entry_mode", ["module", "console", "guarded-module"])
def test_worktrees_feed_actual_cli_full_roster_load_update_and_cutover(
    tmp_path, monkeypatch, entry_mode,
):
    from agent_procutil import no_window_flags

    from agent_worktrees import config as cfg

    factor = _test_budget_factor()
    initial_budget = 2.0 * factor
    step_budget = 2.0 * factor
    cleanup_budget = 1.0 * factor
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
    received_requests = []

    def handle(action, payload):
        received_requests.append((action, round(time.monotonic() - started, 3)))
        return runtime.health() if action == "health" else runtime.handle(payload)

    def stream_handle(action, payload, disconnected):
        received_requests.append((action, round(time.monotonic() - started, 3)))
        return runtime.feed.stream(payload, disconnected)

    server = control.ControlServer(
        handle,
        token="fixture",
        stream_handle=stream_handle,
    )
    server.start()
    control.publish_route(server.port, pid=os.getpid(), version="fixture")
    frames = queue.Queue()
    command = [sys.executable, "-m", "agent_worktrees"]
    if entry_mode == "console":
        from importlib.metadata import entry_points
        from pathlib import Path

        declared = list(entry_points(group="console_scripts", name="agent-worktrees"))
        assert len(declared) == 1
        assert declared[0].value == "agent_worktrees.current_roster_entry:console_entry"
        executable = Path(sys.executable).with_name(
            "agent-worktrees.exe" if os.name == "nt" else "agent-worktrees"
        )
        assert executable.is_file(), "source-owned test environment needs regenerated console scripts"
        command = [str(executable)]
    elif entry_mode == "guarded-module":
        command = [sys.executable, "-c", _GUARDED_MODULE_SCRIPT]
    started = time.monotonic()
    proc = subprocess.Popen(
        [
            *command, "--project", "project",
            "list", "--json", "--current", "--stream", "--follow",
            "--include-other-platforms", "--all",
        ],
        cwd=checkout, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL, text=True, encoding="utf-8",
        creationflags=no_window_flags(),
        env={**os.environ, "PYTHONPROFILEIMPORTTIME": "1"}
        if os.environ.get("WORKTREES_FEED_IMPORT_PROFILE") == "1" else None,
    )
    stderr_lines = []
    raw_stdout = []

    def read_stderr():
        for line in proc.stderr:
            stderr_lines.append(line)
            if len(stderr_lines) > 2048:
                del stderr_lines[:1024]

    def read_stdout():
        for line in proc.stdout:
            raw_stdout.append(line)
            frame = json.loads(line)
            if frame["type"] in {"begin", "done", "commit"}:
                received_requests.append((frame["type"], round(time.monotonic() - started, 3)))
            frames.put(frame)

    reader = threading.Thread(target=read_stdout)
    error_reader = threading.Thread(target=read_stderr)
    reader.start()
    error_reader.start()
    qualified = False
    contended_checks_completed = False
    elapsed = None
    cleanup_failure = None
    try:
        initial = []
        try:
            while not initial or initial[-1]["type"] != "done":
                initial.append(frames.get(
                    timeout=max(0.001, initial_budget - (time.monotonic() - started)),
                ))
        except queue.Empty:
            missed_frames = len(initial)
            # Eventual diagnostic completion never changes the admitted verdict.
            diagnostic_deadline = started + 10 * factor
            while proc.poll() is None and time.monotonic() < diagnostic_deadline:
                try:
                    item = frames.get(timeout=0.1)
                except queue.Empty:
                    continue
                if item["type"] in {"done", "error"}:
                    break
            exited = proc.poll()
            if exited is None:
                proc.terminate()
                proc.wait(timeout=step_budget)
            error_reader.join(timeout=cleanup_budget)
            import_times = []
            for line in stderr_lines:
                if line.startswith("import time:"):
                    fields = line.removeprefix("import time:").split("|")
                    if len(fields) == 3 and fields[0].strip().isdigit():
                        import_times.append((int(fields[1]), int(fields[0]), fields[2].strip()))
            ranked = sorted(import_times, reverse=True)[:12]
            f1_imports = [item for item in import_times if "worktrees_observation" in item[2]]
            pytest.fail(
                f"CLI initial roster missed {initial_budget:g}s "
                f"(nominal 2s, factor {factor}): {missed_frames} frames, exit={exited}, "
                f"requests={received_requests!r}, import_cumulative_self_us={ranked!r}, "
                f"F1_imports={f1_imports!r}, "
                f"stderr={''.join(line for line in stderr_lines if not line.startswith('import time:'))[-8192:]!r}",
            )
        elapsed = time.monotonic() - started
        assert elapsed < initial_budget
        receiver = client.FeedReceiver(request())
        current = apply(receiver, initial)
        assert len(current["worktrees"]) == 1100
        assert current["projection"]["selected_count"] == 1100
        assert current["worktrees"][-1]["claims_assets"]["held_count"] == 1000
        rows[-1] = {**rows[-1], "dirty": 9}
        runtime.publish("project", rows, observed_at=2)
        changed = []
        while not changed or changed[-1]["type"] != "commit":
            changed.append(frames.get(timeout=step_budget))
        assert len([f for f in changed if f["type"] == "worktree"]) == 1
        assert apply(receiver, changed)["worktrees"][-1]["dirty"] == 9
        runtime.close_admission("update")
        notice = frames.get(timeout=step_budget)
        assert notice["type"] == "going-away" and notice["reason"] == "update"
        proc.wait(timeout=step_budget)
        reader.join(timeout=cleanup_budget)
        assert proc.returncode == 0 and not reader.is_alive()
        assert runtime.feed.wait_closed(cleanup_budget)
        print(
            f"actual CLI full current roster: 1100 rows in {elapsed * 1000:.2f}ms, "
            f"test factor {factor}; nominal qualification is separate"
        )
        contended_checks_completed = True
        qualified = factor == 1
    finally:
        failure = sys.exc_info()[1]
        try:
            runtime.close_admission()
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=step_budget)
            reader.join(timeout=cleanup_budget)
            error_reader.join(timeout=cleanup_budget)
            server.close()
            runtime.shutdown()
            if contended_checks_completed:
                assert not reader.is_alive() and not error_reader.is_alive()
        except BaseException as exc:
            cleanup_failure = exc
            raise
        finally:
            if report_dir := os.environ.get("F1_NATIVE_REPORT_DIR"):
                import hashlib
                from pathlib import Path

                report = Path(report_dir).resolve()
                repo = Path(__file__).resolve().parents[3]
                assert report.is_relative_to(repo / ".test-venvs") and report.is_dir()
                (report / f"native-{entry_mode}.json").write_text(json.dumps({
                    "source_commit": os.environ.get("F1_SOURCE_COMMIT"),
                    "production_ancestor": os.environ.get("F1_PRODUCTION_ANCESTOR"),
                    "environment_reference": os.environ.get("F1_ENVIRONMENT_REFERENCE"),
                    "test_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                    "entry_mode": entry_mode, "argv": proc.args,
                    "console_declaration": declared[0].value if entry_mode == "console" else None,
                    "interpreter": sys.executable, "python_version": sys.version,
                    "started_monotonic": started,
                    "budget_factor": factor,
                    "nominal_initial_deadline_seconds": 2.0,
                    "initial_deadline_seconds": initial_budget,
                    "step_budget_seconds": step_budget,
                    "cleanup_budget_seconds": cleanup_budget,
                    "diagnostic_window_seconds": 10 * factor,
                    "initial_elapsed_seconds": elapsed,
                    "nominal_initial_threshold_met": elapsed is not None and elapsed < 2.0,
                    "nominal_run_exercised": factor == 1,
                    "fixture_row_count": 1100,
                    "qualified": qualified and cleanup_failure is None,
                    "nominal_qualified": qualified and cleanup_failure is None,
                    "contended_checks_completed": contended_checks_completed,
                    "contended_test_pass": (
                        contended_checks_completed and failure is None
                        and cleanup_failure is None
                    ),
                    "failure": None if failure is None else {
                        "type": type(failure).__name__, "message": str(failure),
                    },
                    "phases": received_requests,
                    "cleanup_failure": None if cleanup_failure is None else {
                        "type": type(cleanup_failure).__name__, "message": str(cleanup_failure),
                    },
                    "stdout": "".join(raw_stdout), "stderr": "".join(stderr_lines),
                    "returncode": proc.returncode,
                    "readers_joined": not reader.is_alive() and not error_reader.is_alive(),
                }, indent=2), encoding="utf-8")
