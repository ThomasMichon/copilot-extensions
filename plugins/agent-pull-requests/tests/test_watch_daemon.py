"""Tests for ``WatchDaemon`` -- the poller/notification side, with a fake
fetch and fake notify so no real network/subprocess is involved."""

from __future__ import annotations

import threading
import time

from agent_pull_requests.watch_contract import CLOSED, MERGED, PRSnapshot
from agent_pull_requests.watch_daemon import WatchDaemon


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


def test_register_spawns_poller_and_fires_on_merge():
    fetcher = _FakeFetcher()
    fetcher.set("o/n", 1, PRSnapshot(pr_state="open"))
    events: list[dict] = []
    daemon = WatchDaemon(
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


def test_unregister_before_fire_stops_notification():
    fetcher = _FakeFetcher()
    daemon = WatchDaemon(
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


def test_two_subscribers_same_pr_share_one_poller_thread():
    fetcher = _FakeFetcher()
    daemon = WatchDaemon(
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


def test_idle_poller_exits_after_last_subscriber_fires():
    fetcher = _FakeFetcher()
    fetcher.set("o/n", 4, PRSnapshot(pr_state="closed", merged=True))
    daemon = WatchDaemon(
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
