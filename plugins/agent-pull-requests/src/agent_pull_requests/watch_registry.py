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
import copy
from collections.abc import Iterable
from dataclasses import dataclass, field, replace

from .watch_contract import Baseline, PRSnapshot, advance_baseline, compute_transitions
from .watch_notification import ACKNOWLEDGED_NOTIFICATIONS, event_payload
from .watch_state import RegistryState


@dataclass(frozen=True)
class WatchKey:
    repo: str
    number: int
    worktrees_command: str = ""

    def __str__(self) -> str:
        prefix = f"native:{self.worktrees_command}:" if self.worktrees_command else ""
        return f"{prefix}{self.repo}#{self.number}"


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
    deadline_at: float | None = None
    continuous: bool = False
    queued: dict | None = None
    terminal_observed: bool = False
    target_id: str = ""
    registered_revision: int = 0


@dataclass(frozen=True)
class FiredEvent:
    key: WatchKey
    subscriber: Subscriber
    transitions: tuple[str, ...]
    snapshot: PRSnapshot | None
    timed_out: bool = False
    frozen_payload: dict | None = None


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
        continuous: bool = False,
        target_id: str = "",
        baseline: Baseline | None = None,
    ) -> Subscriber:
        """Add (or replace, same id) a subscriber. Returns the stored record."""
        deadline = time.monotonic() + timeout if timeout else None
        sub = Subscriber(
            subscriber_id=subscriber_id, until=until, notify=notify, deadline=deadline,
            acknowledged=acknowledged,
            deadline_at=time.time() + timeout if acknowledged and timeout else None,
            continuous=continuous,
            target_id=target_id,
            baseline=baseline,
        )
        with self._lock:
            self._subscribers.setdefault(key, {})[subscriber_id] = sub
            self._revision += 1
            sub.registered_revision = self._revision
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

    def admit_terminal(
        self, key: WatchKey, subscriber_id: str, snap: PRSnapshot,
    ) -> FiredEvent | None:
        """Retain a validated admission observation without touching other waiters."""
        if not (snap.merged or snap.closed_unmerged):
            return None
        with self._lock:
            bucket = self._subscribers.get(key, {})
            sub = bucket.get(subscriber_id)
            if (
                sub is None or not sub.acknowledged or sub.terminal_observed
                or sub.pending is not None and not sub.continuous
            ):
                return None
            transitions = compute_transitions(Baseline(), snap, sub.until)
            if not transitions:
                return None
            if sub.continuous:
                sub.terminal_observed = True
            return self._fire(FiredEvent(key, sub, transitions, snap), bucket)

    def restore_admission(
        self, key: WatchKey, sub: Subscriber, pending: dict | None,
        queued: dict | None, terminal_observed: bool,
    ) -> None:
        with self._lock:
            if self._subscribers.get(key, {}).get(sub.subscriber_id) is sub:
                sub.pending, sub.queued, sub.terminal_observed = pending, queued, terminal_observed
                self._revision += 1

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

    def target_matches(self, key: WatchKey, target: str) -> bool:
        with self._lock:
            return all(sub.target_id == target for sub in self._subscribers.get(key, {}).values())

    def subscriber_count(self, key: WatchKey) -> int:
        with self._lock:
            return len(self._subscribers.get(key, {}))

    def watching_count(self, key: WatchKey) -> int:
        with self._lock:
            return sum(
                sub.pending is None or (sub.continuous and not sub.terminal_observed)
                for sub in self._subscribers.get(key, {}).values()
            )

    def status(self) -> dict:
        with self._lock:
            return {
                str(key): sorted(bucket.keys()) for key, bucket in self._subscribers.items()
            }

    def pending_events(self) -> tuple[FiredEvent, ...]:
        with self._lock:
            return tuple(
                FiredEvent(key, sub, tuple(sub.pending["payload"]["transitions"]), None,
                           sub.pending["payload"]["timed_out"],
                           copy.deepcopy(sub.pending["payload"]))
                for key, bucket in self._subscribers.items() for sub in bucket.values()
                if sub.pending is not None
            )

    def is_current(self, event: FiredEvent) -> bool:
        with self._lock:
            return self._current_unlocked(event)

    def _current_unlocked(self, event: FiredEvent) -> bool:
        sub = event.subscriber
        return (
            self._subscribers.get(event.key, {}).get(sub.subscriber_id) is sub
            and (
                event.frozen_payload is None
                or sub.pending is not None
                and sub.pending["event_id"] == event.frozen_payload["event_id"]
            )
        )

    def _fire(self, event: FiredEvent, bucket: dict) -> FiredEvent:
        self._revision += 1
        sub = event.subscriber
        if sub.continuous and event.timed_out:
            sub.terminal_observed = True
        if not sub.acknowledged:
            del bucket[sub.subscriber_id]
            return event
        event_id = uuid.uuid4().hex
        payload = event_payload(event, current=True)
        payload.update(
            notification_protocol=ACKNOWLEDGED_NOTIFICATIONS,
            event_id=event_id, registration_id=sub.registration_id,
        )
        pending = {
            "event_id": event_id, "payload": payload, "attempts": 0,
            "next_attempt": 0.0, "last_error": None,
        }
        if sub.pending is not None:
            if sub.queued is not None and not event.timed_out:
                payload["transitions"] = sorted(set(
                    sub.queued["payload"]["transitions"] + payload["transitions"],
                ))
            payload["coalesced"] = True
            sub.queued = pending
        else:
            sub.pending = pending
        return replace(event, frozen_payload=copy.deepcopy(payload))

    def acknowledge(self, event: FiredEvent) -> None:
        """Apply a durably committed ACK while retaining a continuous registration."""
        with self._lock:
            sub = event.subscriber
            if not self._current_unlocked(event):
                return
            if sub.continuous and (
                sub.queued is not None or not sub.terminal_observed
            ):
                sub.pending, sub.queued = sub.queued, None
                self._revision += 1
            else:
                bucket = self._subscribers[event.key]
                del bucket[sub.subscriber_id]
                if not bucket:
                    del self._subscribers[event.key]
                self._revision += 1

    def acknowledged_state(self, event: FiredEvent) -> list[dict]:
        """Prepare the successor state without acknowledging anything in memory."""
        entries = self.snapshot_state()
        sub = event.subscriber
        with self._lock:
            if not self._current_unlocked(event):
                return entries
        for entry in list(entries):
            if entry.get("registration_id") != sub.registration_id:
                continue
            if sub.continuous and (
                sub.queued is not None or not sub.terminal_observed
            ):
                entry["pending"] = entry["queued"]
                entry["queued"] = None
            else:
                entries.remove(entry)
        return entries

    def apply_snapshot(
        self, key: WatchKey, snap: PRSnapshot, *, fetch_revision: int | None = None,
    ) -> tuple[FiredEvent, ...]:
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
                if (
                    key.worktrees_command and fetch_revision is not None
                    and fetch_revision < sub.registered_revision
                ):
                    continue
                if sub.pending is not None and not sub.continuous:
                    continue
                if sub.terminal_observed:
                    continue
                if sub.baseline is None:
                    zero = Baseline()
                    transitions = compute_transitions(zero, snap, sub.until)
                    if transitions:
                        if sub.continuous and (snap.merged or snap.closed_unmerged):
                            sub.terminal_observed = True
                        fired.append(FiredEvent(key, sub, transitions, snap))
                        fired[-1] = self._fire(fired[-1], bucket)
                        continue
                    sub.baseline = Baseline.from_snapshot(snap)
                    self._revision += 1
                    if sub.acknowledged and sub.deadline is not None and now >= sub.deadline:
                        fired.append(FiredEvent(key, sub, (), snap, timed_out=True))
                        fired[-1] = self._fire(fired[-1], bucket)
                    continue
                transitions = compute_transitions(sub.baseline, snap, sub.until)
                baseline = (
                    Baseline.from_snapshot(snap) if sub.continuous
                    else advance_baseline(sub.baseline, snap)
                )
                if baseline != sub.baseline:
                    self._revision += 1
                sub.baseline = baseline
                expired = sub.continuous and sub.deadline is not None and now >= sub.deadline
                if transitions and (not expired or snap.merged or snap.closed_unmerged):
                    if sub.continuous and (snap.merged or snap.closed_unmerged):
                        sub.terminal_observed = True
                    fired.append(FiredEvent(key, sub, transitions, snap))
                    fired[-1] = self._fire(fired[-1], bucket)
                    continue
                if sub.deadline is not None and now >= sub.deadline:
                    fired.append(FiredEvent(key, sub, (), snap, timed_out=True))
                    fired[-1] = self._fire(fired[-1], bucket)
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
                    if sub.terminal_observed or (sub.pending is not None and not sub.continuous):
                        continue
                    if sub.deadline is not None and now >= sub.deadline:
                        fired.append(FiredEvent(key, sub, (), None, timed_out=True))
                        fired[-1] = self._fire(fired[-1], bucket)
                if not bucket:
                    del self._subscribers[key]
            return tuple(fired)


__all__ = ["FiredEvent", "Subscriber", "WatchKey", "WatchRegistry"]
