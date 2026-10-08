"""Tests for ``WatchDaemon`` -- the poller/notification side, with a fake
fetch and fake notify so no real network/subprocess is involved."""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_pull_requests.watch_contract import CLOSED, MERGED, PRSnapshot
from agent_pull_requests.watch_daemon import WatchDaemon, _atomic_write_json


@pytest.fixture
def daemon_factory():
    daemons = []

    def create(**kwargs):
        daemon = WatchDaemon(**kwargs)
        daemons.append(daemon)
        return daemon

    yield create
    for daemon in reversed(daemons):
        daemon.close()
        assert not daemon._pollers


class _FakeFetcher:
    """Thread-safe queue of snapshots to hand back, keyed by (repo, number)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._snapshots: dict[tuple[str, int], PRSnapshot] = {}

    def set(self, repo: str, number: int, snap: PRSnapshot) -> None:
        with self._lock:
            self._snapshots[(repo, number)] = snap

    def __call__(self, repo: str, number: int) -> PRSnapshot:
        with self._lock:
            return self._snapshots.get((repo, number), PRSnapshot(pr_state="open"))


def _wait_until(predicate, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def test_register_spawns_poller_and_fires_on_merge(daemon_factory):
    fetcher = _FakeFetcher()
    fetcher.set("o/n", 1, PRSnapshot(pr_state="open"))
    events: list[dict] = []
    daemon = daemon_factory(
        fetch=fetcher,
        notify=lambda event: events.append(
            {"id": event.subscriber.subscriber_id, "transitions": event.transitions}
        ),
        poll_interval=0.02,
        idle_exit=0.2,
    )
    result = daemon.compute(
        "register",
        {
            "repo": "o/n",
            "number": 1,
            "subscriber_id": "sub-1",
            "until": [MERGED, CLOSED],
            "notify": {},
        },
    )
    assert result == {"registered": True}
    assert _wait_until(lambda: daemon.status()["subscribers"].get("o/n#1") == ["sub-1"])

    fetcher.set("o/n", 1, PRSnapshot(pr_state="closed", merged=True))
    assert _wait_until(lambda: len(events) == 1)
    assert events[0]["id"] == "sub-1"
    assert events[0]["transitions"] == (MERGED,)
    # Subscriber removed from status after firing.
    assert daemon.status()["subscribers"] == {}


def test_unregister_before_fire_stops_notification(daemon_factory):
    fetcher = _FakeFetcher()
    daemon = daemon_factory(
        fetch=fetcher, notify=lambda event: None, poll_interval=0.02, idle_exit=0.2
    )
    daemon.compute(
        "register",
        {"repo": "o/n", "number": 2, "subscriber_id": "sub-x", "until": [MERGED], "notify": {}},
    )
    assert _wait_until(lambda: "o/n#2" in daemon.status()["subscribers"])
    result = daemon.compute(
        "unregister", {"repo": "o/n", "number": 2, "subscriber_id": "sub-x"}
    )
    assert result == {"unregistered": True}
    assert "o/n#2" not in daemon.status()["subscribers"]


def test_two_subscribers_same_pr_share_one_poller_thread(daemon_factory):
    fetcher = _FakeFetcher()
    daemon = daemon_factory(
        fetch=fetcher, notify=lambda event: None, poll_interval=0.02, idle_exit=0.2
    )
    daemon.compute(
        "register",
        {"repo": "o/n", "number": 3, "subscriber_id": "a", "until": [MERGED], "notify": {}},
    )
    daemon.compute(
        "register",
        {"repo": "o/n", "number": 3, "subscriber_id": "b", "until": [MERGED], "notify": {}},
    )
    assert _wait_until(
        lambda: sorted(daemon.status()["subscribers"].get("o/n#3", [])) == ["a", "b"]
    )
    # Exactly one poller thread for this key.
    assert sum(1 for t in threading.enumerate() if t.name == "pr-watch:o/n#3") == 1


def test_idle_poller_exits_after_last_subscriber_fires(daemon_factory):
    fetcher = _FakeFetcher()
    fetcher.set("o/n", 4, PRSnapshot(pr_state="closed", merged=True))
    daemon = daemon_factory(
        fetch=fetcher, notify=lambda event: None, poll_interval=0.02, idle_exit=0.1
    )
    daemon.compute(
        "register",
        {"repo": "o/n", "number": 4, "subscriber_id": "sub-1", "until": [MERGED], "notify": {}},
    )
    assert _wait_until(lambda: daemon.status()["subscribers"] == {})
    assert _wait_until(
        lambda: not any(t.name == "pr-watch:o/n#4" for t in threading.enumerate()), timeout=2.0
    )


def test_a_restarted_daemon_reattaches_persisted_subscriptions(
    tmp_path, monkeypatch, daemon_factory
):
    """The stop/restart (update) path: a brand-new ``WatchDaemon`` process
    (simulated here by constructing a second instance against the same
    durable state dir) must resume every subscription the first one was
    still tracking -- without the caller ever re-subscribing."""
    monkeypatch.setenv("AGENT_PULL_REQUESTS_HOME", str(tmp_path))
    fetcher = _FakeFetcher()
    fetcher.set("o/n", 7, PRSnapshot(pr_state="open"))

    predecessor_events = []
    first = daemon_factory(
        fetch=fetcher, notify=predecessor_events.append, poll_interval=0.02
    )
    first.compute(
        "register",
        {"repo": "o/n", "number": 7, "subscriber_id": "carried-over", "until": [MERGED]},
    )
    assert _wait_until(lambda: first.status()["subscribers"].get("o/n#7") == ["carried-over"])

    first.compute("shutdown", {})
    first.close()
    assert not first._pollers

    # Reattachment happens in __init__, before any register call.
    events: list[dict] = []
    second = daemon_factory(
        fetch=fetcher,
        notify=lambda event: events.append({"id": event.subscriber.subscriber_id}),
        poll_interval=0.02,
    )
    assert second.status()["subscribers"].get("o/n#7") == ["carried-over"]

    # And the reattached subscription is genuinely live, not just listed --
    # a real merge fires it without any new subscribe call.
    fetcher.set("o/n", 7, PRSnapshot(pr_state="closed", merged=True))
    assert _wait_until(lambda: len(events) == 1)
    assert events[0]["id"] == "carried-over"
    assert predecessor_events == []


def test_atomic_writers_use_independent_temporary_files(tmp_path, monkeypatch):
    path = tmp_path / "subscriptions.json"
    ready = threading.Barrier(2)
    dump = json.dump
    sources = []

    def simultaneous_dump(data, handle):
        dump(data, handle)
        sources.append(Path(handle.name))
        ready.wait(timeout=3)

    monkeypatch.setattr(json, "dump", simultaneous_dump)
    entries = [{"subscriber": "first"}, {"subscriber": "second"}]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(_atomic_write_json, path, entry) for entry in entries]
        for future in futures:
            future.result(timeout=5)

    assert len(set(sources)) == 2
    assert json.loads(path.read_text(encoding="utf-8")) in entries
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("failure", ["replace", "serialization"])
def test_failed_atomic_write_preserves_state_and_cleans_temp(failure, tmp_path, monkeypatch):
    path = tmp_path / "subscriptions.json"
    _atomic_write_json(path, {"subscriber": "original"})

    def fail_replace(source, target):
        raise OSError("replacement failed")

    if failure == "replace":
        monkeypatch.setattr(Path, "replace", fail_replace)
        with pytest.raises(OSError, match="replacement failed"):
            _atomic_write_json(path, {"subscriber": "new"})
    else:
        with pytest.raises(TypeError, match="JSON serializable"):
            _atomic_write_json(path, {"subscriber": object()})

    assert json.loads(path.read_text(encoding="utf-8")) == {"subscriber": "original"}
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("mode", ["active", "idle", "fetch-error"])
def test_shutdown_wakes_and_joins_pollers(mode, daemon_factory):
    fetched = threading.Event()

    def fetch(repo, number):
        fetched.set()
        if mode == "fetch-error":
            raise OSError("fetch failed")
        return PRSnapshot(pr_state="open")

    daemon = daemon_factory(
        fetch=fetch, notify=lambda event: None, poll_interval=60, idle_exit=60,
        persist=False,
    )
    daemon.compute(
        "register",
        {"repo": "o/n", "number": 8, "subscriber_id": "waiting", "until": [MERGED]},
    )
    assert fetched.wait(timeout=3)
    if mode == "idle":
        daemon.compute("unregister", {"repo": "o/n", "number": 8, "subscriber_id": "waiting"})
    daemon.compute("shutdown", {})
    daemon.close(timeout=1)
    assert not daemon._pollers
    daemon.close(timeout=1)


def test_close_reports_inflight_fetch_timeout_and_can_be_retried(daemon_factory):
    started = threading.Event()
    finish = threading.Event()

    def fetch(repo, number):
        started.set()
        assert finish.wait(timeout=3)
        return PRSnapshot(pr_state="open")

    daemon = daemon_factory(fetch=fetch, persist=False, poll_interval=60)
    try:
        daemon.compute(
            "register",
            {"repo": "o/n", "number": 9, "subscriber_id": "waiting", "until": [MERGED]},
        )
        assert started.wait(timeout=1)
        with pytest.raises(TimeoutError, match="pr-watch:o/n#9"):
            daemon.close(timeout=0.01)
    finally:
        finish.set()
        daemon.close()
    assert not daemon._pollers


@pytest.mark.parametrize("interrupted", [False, True])
def test_serve_joins_pollers_before_releasing_lease(interrupted, monkeypatch):
    import single_instance_lease
    import work_coalescing_singleton
    from agent_pull_requests import __main__ as cli, watch_daemon

    lifecycle = []

    class Lease:
        def __init__(self, *args, **kwargs):
            pass

        def acquire(self):
            lifecycle.append("acquire")

        def release(self):
            lifecycle.append("release")

    class Daemon:
        def __init__(self, **kwargs):
            pass

        def compute(self, kind, payload):
            return {}

        def wait_for_shutdown(self):
            if interrupted:
                raise KeyboardInterrupt

        def close(self):
            lifecycle.append("join")

    class Server:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            pass

        def rendezvous(self):
            return {"endpoint": "example"}

        def close(self):
            lifecycle.append("stop-listener")

    monkeypatch.setattr(single_instance_lease, "SingleInstance", Lease)
    monkeypatch.setattr(work_coalescing_singleton, "CoalescingServer", Server)
    monkeypatch.setattr(watch_daemon, "WatchDaemon", Daemon)
    monkeypatch.setattr(watch_daemon, "rendezvous_fields", lambda server: {})
    monkeypatch.setattr(watch_daemon, "write_lock_data", lambda data: None)

    assert cli._cmd_serve(SimpleNamespace(poll_interval=30)) == 0
    assert lifecycle == ["acquire", "stop-listener", "join", "release"]
