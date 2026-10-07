"""In-memory subscriber registry multiplexing one poll loop per ``(repo,
number)`` key over any number of independent waiters.

Pure logic, no I/O and no threads -- the daemon (``watch_daemon.py``) owns
fetching snapshots and dispatching notifications; this module only tracks
*who is waiting for what* and decides, given a fresh snapshot, who just got
their answer. Kept separate so the subscriber bookkeeping is fast to test
without a real network or clock.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass, field

from .watch_contract import Baseline, PRSnapshot, advance_baseline, compute_transitions


def _baseline_to_dict(baseline: Baseline | None) -> dict | None:
    if baseline is None:
        return None
    return {
        "merged": baseline.merged,
        "closed": baseline.closed,
        "review_decision": baseline.review_decision,
        "mergeable": baseline.mergeable,
        "checks_state": baseline.checks_state,
    }


def _baseline_from_dict(data: dict | None) -> Baseline | None:
    if data is None:
        return None
    return Baseline(
        merged=bool(data.get("merged", False)),
        closed=bool(data.get("closed", False)),
        review_decision=data.get("review_decision"),
        mergeable=data.get("mergeable"),
        checks_state=data.get("checks_state"),
    )


@dataclass(frozen=True)
class WatchKey:
    repo: str
    number: int

    def __str__(self) -> str:
        return f"{self.repo}#{self.number}"


@dataclass
class Subscriber:
    """One waiter's registration against a single :class:`WatchKey`.

    ``baseline=None`` means auto-baseline: the first poll this subscriber
    observes becomes its reference (mirrors ``pr-watch``'s own "notify me of
    changes from now on" default) -- except an already-terminal snapshot at
    that first poll still fires immediately, handled by the registry rather
    than duplicated per-subscriber.
    """

    subscriber_id: str
    until: tuple[str, ...]
    notify: dict
    baseline: Baseline | None = None
    deadline: float | None = None  # time.monotonic() epoch; None = no timeout
    registered_at: float = field(default_factory=time.monotonic)


@dataclass(frozen=True)
class FiredEvent:
    key: WatchKey
    subscriber: Subscriber
    transitions: tuple[str, ...]
    snapshot: PRSnapshot | None
    timed_out: bool = False


class WatchRegistry:
    """Owns the subscriber map; callers drive polling and notification."""

    def __init__(self) -> None:
        self._subscribers: dict[WatchKey, dict[str, Subscriber]] = {}
        self._lock = threading.Lock()

    def register(
        self,
        key: WatchKey,
        subscriber_id: str,
        *,
        until: tuple[str, ...],
        notify: dict,
        timeout: float | None = None,
    ) -> Subscriber:
        """Add (or replace, same id) a subscriber. Returns the stored record."""
        deadline = time.monotonic() + timeout if timeout else None
        sub = Subscriber(
            subscriber_id=subscriber_id, until=until, notify=notify, deadline=deadline,
        )
        with self._lock:
            self._subscribers.setdefault(key, {})[subscriber_id] = sub
        return sub

    def unregister(self, key: WatchKey, subscriber_id: str) -> bool:
        with self._lock:
            bucket = self._subscribers.get(key)
            if not bucket or subscriber_id not in bucket:
                return False
            del bucket[subscriber_id]
            if not bucket:
                del self._subscribers[key]
            return True

    def active_keys(self) -> tuple[WatchKey, ...]:
        with self._lock:
            return tuple(self._subscribers.keys())

    def subscriber_count(self, key: WatchKey) -> int:
        with self._lock:
            return len(self._subscribers.get(key, {}))

    def status(self) -> dict:
        with self._lock:
            return {
                str(key): sorted(bucket.keys()) for key, bucket in self._subscribers.items()
            }

    def snapshot_state(self) -> list[dict]:
        """Durable, process-independent dump of every live subscriber.

        Deadlines are stored as **remaining seconds** (not an absolute
        ``time.monotonic()`` epoch, which is meaningless across a process
        restart) so :meth:`restore_state` can recompute a correct deadline
        against the new process's own clock.
        """
        with self._lock:
            now = time.monotonic()
            out: list[dict] = []
            for key, bucket in self._subscribers.items():
                for sub in bucket.values():
                    out.append(
                        {
                            "repo": key.repo,
                            "number": key.number,
                            "subscriber_id": sub.subscriber_id,
                            "until": list(sub.until),
                            "notify": sub.notify,
                            "baseline": _baseline_to_dict(sub.baseline),
                            "remaining_timeout": (
                                max(0.0, sub.deadline - now) if sub.deadline is not None else None
                            ),
                        }
                    )
            return out

    def restore_state(self, entries: list[dict]) -> int:
        """Reload a :meth:`snapshot_state` dump (e.g. after a daemon
        restart). Returns the number of subscribers restored. Never raises
        on a malformed individual entry -- a corrupt/partial state file
        should lose at most the bad entries, never block startup."""
        restored = 0
        with self._lock:
            for entry in entries:
                try:
                    key = WatchKey(repo=str(entry["repo"]), number=int(entry["number"]))
                    sub = Subscriber(
                        subscriber_id=str(entry["subscriber_id"]),
                        until=tuple(entry["until"]),
                        notify=dict(entry.get("notify") or {}),
                        baseline=_baseline_from_dict(entry.get("baseline")),
                    )
                    remaining = entry.get("remaining_timeout")
                    if remaining is not None:
                        sub.deadline = time.monotonic() + float(remaining)
                    self._subscribers.setdefault(key, {})[sub.subscriber_id] = sub
                    restored += 1
                except (KeyError, TypeError, ValueError):
                    continue
        return restored

    def apply_snapshot(self, key: WatchKey, snap: PRSnapshot) -> tuple[FiredEvent, ...]:
        """Diff ``snap`` against every subscriber of ``key``; fire and
        remove those whose transition (or timeout) condition is met.

        A subscriber with ``baseline is None`` adopts ``snap`` as its
        reference now, UNLESS ``snap`` is already terminal (merged/closed)
        for one of its own ``until`` transitions, in which case it fires
        immediately against a zero baseline -- the "already merged before I
        started watching" case.
        """
        with self._lock:
            bucket = self._subscribers.get(key)
            if not bucket:
                return ()
            fired: list[FiredEvent] = []
            now = time.monotonic()
            for subscriber_id in list(bucket.keys()):
                sub = bucket[subscriber_id]
                if sub.baseline is None:
                    zero = Baseline()
                    transitions = compute_transitions(zero, snap, sub.until)
                    if transitions:
                        fired.append(FiredEvent(key, sub, transitions, snap))
                        del bucket[subscriber_id]
                        continue
                    sub.baseline = Baseline.from_snapshot(snap)
                    continue
                transitions = compute_transitions(sub.baseline, snap, sub.until)
                sub.baseline = advance_baseline(sub.baseline, snap)
                if transitions:
                    fired.append(FiredEvent(key, sub, transitions, snap))
                    del bucket[subscriber_id]
                    continue
                if sub.deadline is not None and now >= sub.deadline:
                    fired.append(FiredEvent(key, sub, (), snap, timed_out=True))
                    del bucket[subscriber_id]
            if not bucket:
                del self._subscribers[key]
            return tuple(fired)

    def sweep_timeouts(self, keys: Iterable[WatchKey] | None = None) -> tuple[FiredEvent, ...]:
        """Fire timeout events for subscribers whose deadline passed without
        a poll happening to observe it (e.g. the key currently has no other
        reason to poll). Does not require a snapshot."""
        with self._lock:
            now = time.monotonic()
            fired: list[FiredEvent] = []
            target_keys = tuple(keys) if keys is not None else tuple(self._subscribers.keys())
            for key in target_keys:
                bucket = self._subscribers.get(key)
                if not bucket:
                    continue
                for subscriber_id in list(bucket.keys()):
                    sub = bucket[subscriber_id]
                    if sub.deadline is not None and now >= sub.deadline:
                        fired.append(FiredEvent(key, sub, (), None, timed_out=True))
                        del bucket[subscriber_id]
                if not bucket:
                    del self._subscribers[key]
            return tuple(fired)


__all__ = ["FiredEvent", "Subscriber", "WatchKey", "WatchRegistry"]
