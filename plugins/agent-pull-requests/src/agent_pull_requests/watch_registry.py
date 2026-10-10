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
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field

from .watch_contract import Baseline, PRSnapshot, advance_baseline, compute_transitions
from .watch_notification import ACKNOWLEDGED_NOTIFICATIONS, event_payload
from .watch_state import RegistryState


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
    acknowledged: bool = False
    registration_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    pending: dict | None = None


@dataclass(frozen=True)
class FiredEvent:
    key: WatchKey
    subscriber: Subscriber
    transitions: tuple[str, ...]
    snapshot: PRSnapshot | None
    timed_out: bool = False


class WatchRegistry(RegistryState):
    """Owns the subscriber map; callers drive polling and notification."""

    def __init__(self) -> None:
        self._subscribers: dict[WatchKey, dict[str, Subscriber]] = {}
        self._lock = threading.Lock()
        self._revision = 0

    def revision(self) -> int:
        with self._lock:
            return self._revision

    def register(
        self,
        key: WatchKey,
        subscriber_id: str,
        *,
        until: tuple[str, ...],
        notify: dict,
        timeout: float | None = None,
        acknowledged: bool = False,
    ) -> Subscriber:
        """Add (or replace, same id) a subscriber. Returns the stored record."""
        deadline = time.monotonic() + timeout if timeout else None
        sub = Subscriber(
            subscriber_id=subscriber_id, until=until, notify=notify, deadline=deadline,
            acknowledged=acknowledged,
        )
        with self._lock:
            self._subscribers.setdefault(key, {})[subscriber_id] = sub
            self._revision += 1
        return sub

    def unregister(
        self, key: WatchKey, subscriber_id: str, *, registration_id: str | None = None,
    ) -> bool:
        with self._lock:
            bucket = self._subscribers.get(key)
            if not bucket or subscriber_id not in bucket:
                return False
            if registration_id is not None and bucket[subscriber_id].registration_id != registration_id:
                return False
            del bucket[subscriber_id]
            self._revision += 1
            if not bucket:
                del self._subscribers[key]
            return True

    def subscriber(self, key: WatchKey, subscriber_id: str) -> Subscriber | None:
        with self._lock:
            return self._subscribers.get(key, {}).get(subscriber_id)

    def restore_registration(
        self, key: WatchKey, attempted: Subscriber, previous: Subscriber | None,
    ) -> None:
        with self._lock:
            bucket = self._subscribers.get(key, {})
            if bucket.get(attempted.subscriber_id) is not attempted:
                return
            if previous is not None:
                bucket[attempted.subscriber_id] = previous
            else:
                del bucket[attempted.subscriber_id]
                if not bucket:
                    del self._subscribers[key]

    def active_keys(self) -> tuple[WatchKey, ...]:
        with self._lock:
            return tuple(self._subscribers.keys())

    def subscriber_count(self, key: WatchKey) -> int:
        with self._lock:
            return len(self._subscribers.get(key, {}))

    def watching_count(self, key: WatchKey) -> int:
        with self._lock:
            return sum(sub.pending is None for sub in self._subscribers.get(key, {}).values())

    def status(self) -> dict:
        with self._lock:
            return {
                str(key): sorted(bucket.keys()) for key, bucket in self._subscribers.items()
            }

    def pending_events(self) -> tuple[FiredEvent, ...]:
        with self._lock:
            return tuple(
                FiredEvent(key, sub, tuple(sub.pending["payload"]["transitions"]), None,
                           sub.pending["payload"]["timed_out"])
                for key, bucket in self._subscribers.items() for sub in bucket.values()
                if sub.pending is not None
            )

    def is_current(self, event: FiredEvent) -> bool:
        with self._lock:
            return self._subscribers.get(event.key, {}).get(
                event.subscriber.subscriber_id
            ) is event.subscriber

    def _fire(self, event: FiredEvent, bucket: dict) -> None:
        self._revision += 1
        sub = event.subscriber
        if not sub.acknowledged:
            del bucket[sub.subscriber_id]
            return
        event_id = uuid.uuid4().hex
        payload = event_payload(event)
        payload.update(
            notification_protocol=ACKNOWLEDGED_NOTIFICATIONS,
            event_id=event_id, registration_id=sub.registration_id,
        )
        sub.pending = {
            "event_id": event_id, "payload": payload, "attempts": 0,
            "next_attempt": 0.0, "last_error": None,
        }

    def apply_snapshot(self, key: WatchKey, snap: PRSnapshot) -> tuple[FiredEvent, ...]:
        """Diff ``snap`` against every subscriber; retain opted-in fired events.

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
                if sub.pending is not None:
                    continue
                if sub.baseline is None:
                    zero = Baseline()
                    transitions = compute_transitions(zero, snap, sub.until)
                    if transitions:
                        fired.append(FiredEvent(key, sub, transitions, snap))
                        self._fire(fired[-1], bucket)
                        continue
                    sub.baseline = Baseline.from_snapshot(snap)
                    self._revision += 1
                    if sub.acknowledged and sub.deadline is not None and now >= sub.deadline:
                        fired.append(FiredEvent(key, sub, (), snap, timed_out=True))
                        self._fire(fired[-1], bucket)
                    continue
                transitions = compute_transitions(sub.baseline, snap, sub.until)
                baseline = advance_baseline(sub.baseline, snap)
                if baseline != sub.baseline:
                    self._revision += 1
                sub.baseline = baseline
                if transitions:
                    fired.append(FiredEvent(key, sub, transitions, snap))
                    self._fire(fired[-1], bucket)
                    continue
                if sub.deadline is not None and now >= sub.deadline:
                    fired.append(FiredEvent(key, sub, (), snap, timed_out=True))
                    self._fire(fired[-1], bucket)
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
                    if sub.pending is not None:
                        continue
                    if sub.deadline is not None and now >= sub.deadline:
                        fired.append(FiredEvent(key, sub, (), None, timed_out=True))
                        self._fire(fired[-1], bucket)
                if not bucket:
                    del self._subscribers[key]
            return tuple(fired)


__all__ = ["FiredEvent", "Subscriber", "WatchKey", "WatchRegistry"]
