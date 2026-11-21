"""Independent, generation-fenced at-least-once callback retry workers."""

from __future__ import annotations

import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Any, Protocol

from .watch_notification import event_payload
from .watch_registry import FiredEvent, WatchRegistry
from .watch_storage import StateCommitUncertain


class Persist(Protocol):
    def __call__(self, entries: list[dict[str, Any]] | None = None) -> None: ...


class Deliveries:
    def __init__(
        self, registry: WatchRegistry, notify: Callable[[FiredEvent], int | None],
        persist: Persist, transaction: threading.RLock, shutdown: threading.Event,
        *, before_notify: Callable[[], None] | None = None,
        retained_legacy: Deliveries | None = None,
    ) -> None:
        self.registry = registry
        self.notify = notify
        self.persist = persist
        self.transaction = transaction
        self.shutdown = shutdown
        self.before_notify = before_notify
        self.workers: dict[str, threading.Thread] = {}
        self._native_threads: set[threading.Thread] = set()
        self.in_flight: dict[str, dict[str, str | int]] = {}
        self.lock = threading.Lock()
        self.legacy_queue: deque[FiredEvent] = deque()
        self.scheduler: threading.Thread | None = None
        self.max_workers = 8
        self.changed = threading.Event()
        self.prefer_legacy = True
        self.start_error: str | None = None
        self.legacy_outcomes: deque[dict[str, str | int | bool | None]] = deque(maxlen=128)
        self.legacy_evicted = 0
        self.legacy_fenced = False
        if retained_legacy is not None:
            with retained_legacy.lock:
                if any(thread.is_alive() for thread in retained_legacy._native_threads):
                    raise RuntimeError("legacy work cannot transfer while delivery workers are live")
                self.legacy_queue.extend(retained_legacy.legacy_queue)
                self.legacy_outcomes.extend(retained_legacy.legacy_outcomes)
                self.legacy_evicted = retained_legacy.legacy_evicted
                self.max_workers = retained_legacy.max_workers
                self.legacy_fenced = retained_legacy.legacy_fenced
                retained_legacy.legacy_queue.clear()
                retained_legacy.legacy_fenced = True

    def fence_legacy(self) -> None:
        with self.lock:
            self.legacy_fenced = True
        self.changed.set()

    def unfence_legacy(self) -> None:
        with self.lock:
            self.legacy_fenced = False
        self.changed.set()

    def has_legacy_pending(self) -> bool:
        with self.lock:
            return bool(self.legacy_queue)

    def resume(self) -> None:
        with self.lock:
            if self.shutdown.is_set():
                return
            self.changed.set()
            if self.scheduler is None or not self.scheduler.is_alive():
                self.scheduler = threading.Thread(
                    target=self._schedule, name="pr-notify-scheduler", daemon=True,
                )
                self._native_threads.add(self.scheduler)
                try:
                    self.scheduler.start()
                except RuntimeError:
                    self.scheduler = None
                    self.start_error = "worker_start_failed"
                    raise

    def _schedule(self) -> None:
        while not self.shutdown.is_set():
            self.changed.clear()
            start_failed = False
            with self.transaction:
                pending = sorted(
                    (
                        (event, event.subscriber.pending["next_attempt"],
                         event.subscriber.registration_id)
                        for event in self.registry.pending_events(delivery_mode="callback")
                        if event.subscriber.pending is not None
                    ),
                    key=lambda item: item[1],
                )
            with self.transaction, self.lock:
                if self.shutdown.is_set():
                    return
                self._native_threads.intersection_update(threading.enumerate())
                available = self.max_workers - len(self.workers)
                candidates: list[tuple[FiredEvent, Callable[[FiredEvent], None]]] = []
                ready = deque(
                    event for event, next_attempt, identity in pending
                    if next_attempt <= time.time()
                    and identity not in self.workers and self.registry.is_current(event)
                )
                while len(candidates) < available and (
                    ready or self.legacy_queue and not self.legacy_fenced
                ):
                    if self.legacy_queue and not self.legacy_fenced and (
                        self.prefer_legacy or not ready
                    ):
                        candidates.append((self.legacy_queue.popleft(), self._run_legacy))
                    else:
                        candidates.append((ready.popleft(), self._run))
                    self.prefer_legacy = not self.prefer_legacy
                for event, target in candidates:
                    if target == self._run and not self.registry.is_current(event):
                        continue
                    identity = event.subscriber.registration_id
                    thread = threading.Thread(
                        target=target, args=(event,), name=f"pr-notify:{identity}", daemon=True,
                    )
                    self.workers[identity] = thread
                    self._native_threads.add(thread)
                    self.in_flight[identity] = {
                        "registration_id": identity, "subscriber_id": event.subscriber.subscriber_id,
                        "repo": event.key.repo, "number": event.key.number,
                    }
                    try:
                        thread.start()
                    except RuntimeError:
                        del self.workers[identity]
                        self.in_flight.pop(identity, None)
                        if target == self._run_legacy:
                            self.legacy_queue.appendleft(event)
                        self.start_error = "worker_start_failed"
                        start_failed = True
                    else:
                        self.start_error = None
                future = [
                    next_attempt for event, next_attempt, identity in pending
                    if identity not in self.workers and next_attempt > time.time()
                    and self.registry.is_current(event)
                ]
            delay = min(60.0, max(0.0, min(future) - time.time())) if future else None
            if start_failed:
                delay = 1.0
            self.changed.wait(delay)

    def legacy(self, event: FiredEvent) -> None:
        """Keep legacy best-effort delivery from blocking observation or other callbacks."""
        with self.lock:
            self.legacy_queue.append(event)
        self.resume()

    def _run_legacy(self, event: FiredEvent) -> None:
        outcome = "unknown"
        error: str | None = "callback_result_unknown"
        attempted = False
        try:
            try:
                if self.before_notify is not None:
                    self.before_notify()
            except Exception:
                self.shutdown.set()
                with self.lock:
                    self.legacy_fenced = True
                    self.legacy_queue.appendleft(event)
                return
            with self.lock:
                if self.legacy_fenced:
                    self.legacy_queue.appendleft(event)
                    return
            attempted = True
            result = self.notify(event)
            if type(result) is int:
                outcome = "completed" if result == 0 else "failed"
                error = None if result == 0 else "callback_nonzero"
        except subprocess.TimeoutExpired:
            error = "callback_timeout"
        except Exception:
            error = "callback_error"
        finally:
            with self.lock:
                if attempted:
                    if len(self.legacy_outcomes) == self.legacy_outcomes.maxlen:
                        self.legacy_evicted += 1
                    self.legacy_outcomes.append({
                        "repo": event.key.repo[:256], "number": event.key.number,
                        "subscriber_id": event.subscriber.subscriber_id[:256],
                        "registration_id": event.subscriber.registration_id,
                        "identity_truncated": (
                            len(event.key.repo) > 256 or len(event.subscriber.subscriber_id) > 256
                        ),
                        "outcome": outcome, "error": error,
                    })
                self.workers.pop(event.subscriber.registration_id, None)
                self.in_flight.pop(event.subscriber.registration_id, None)
            self.changed.set()

    def legacy_status(self) -> dict[str, Any]:
        with self.lock:
            return {
                "protocol": "legacy_callback_history/v1",
                "retention": "process-local",
                "limit": self.legacy_outcomes.maxlen,
                "evicted": self.legacy_evicted,
                "outcomes": [dict(item) for item in self.legacy_outcomes],
            }

    def _run(self, event: FiredEvent) -> None:
        sub = event.subscriber
        try:
            if not self.shutdown.is_set():
                with self.transaction:
                    if not self.registry.is_current(event):
                        return
                    pending = sub.pending
                    if pending is None:
                        return
                    delay = max(0.0, pending["next_attempt"] - time.time())
                if delay:
                    return
                try:
                    with self.transaction:
                        if not self.registry.is_current(event):
                            return
                        # Persist BEFORE subprocess execution, including after a failed write.
                        self.persist()
                    if self.shutdown.is_set():
                        return
                    try:
                        if self.before_notify is not None:
                            self.before_notify()
                    except Exception:
                        self.shutdown.set()
                        self.fence_legacy()
                        return
                    try:
                        result = self.notify(event)
                        error = None if type(result) is int and result == 0 else "callback_nonzero"
                    except subprocess.TimeoutExpired:
                        error = "callback_timeout"
                    except Exception:
                        error = "callback_error"
                    with self.transaction:
                        if not self.registry.is_current(event):
                            return
                        if error is None:
                            # Write the ACK before deleting in memory. A failed local ACK write
                            # leaves both copies pending, even after a successful remote commit.
                            entries = self.registry.acknowledged_state(event)
                            try:
                                self.persist(entries)
                            except StateCommitUncertain:
                                self.registry.acknowledge(event)
                                return
                            self.registry.acknowledge(event)
                            return
                        pending = sub.pending
                        if pending is None:
                            return
                        pending["attempts"] += 1
                        pending["last_error"] = error
                        pending["next_attempt"] = time.time() + min(
                            60.0, 2.0 ** min(pending["attempts"] - 1, 6),
                        )
                        try:
                            self.persist()
                        except StateCommitUncertain:
                            return
                except (OSError, ValueError):
                    # The persisted event remains authoritative; never acknowledge a failed write.
                    with self.transaction:
                        if self.registry.is_current(event) and sub.pending is not None:
                            sub.pending["next_attempt"] = time.time() + 1.0
        finally:
            with self.lock:
                if self.workers.get(sub.registration_id) is threading.current_thread():
                    del self.workers[sub.registration_id]
                    self.in_flight.pop(sub.registration_id, None)
            self.changed.set()

    def status(self) -> list[dict[str, Any]]:
        with self.transaction:
            return [
                {
                    "repo": event.key.repo, "number": event.key.number,
                    "subscriber_id": event.subscriber.subscriber_id,
                    "registration_id": event.subscriber.registration_id,
                    "event_id": event_payload(event)["event_id"],
                    "attempts": event.subscriber.pending["attempts"],
                    "last_error": event.subscriber.pending["last_error"],
                    "next_attempt": event.subscriber.pending["next_attempt"],
                }
                for event in self.registry.pending_events(delivery_mode="callback")
                if event.subscriber.pending is not None
            ]

    def running(self) -> list[dict[str, str | int]]:
        with self.lock:
            return [dict(item) for item in self.in_flight.values()]

    def close(
        self, deadline: float, *, bounded: bool = False, preserve_queued: bool = False,
    ) -> list[str]:
        self.changed.set()
        scheduler = self.scheduler
        if scheduler is not None:
            scheduler.join(max(0.0, deadline - time.monotonic()))
        while True:
            with self.lock:
                while (
                    self.legacy_queue and not self.legacy_fenced
                    and len(self.workers) < self.max_workers
                    and (not bounded or time.monotonic() < deadline)
                ):
                    event = self.legacy_queue.popleft()
                    identity = event.subscriber.registration_id
                    thread = threading.Thread(
                        target=self._run_legacy, args=(event,),
                        name=f"pr-notify:{identity}", daemon=True,
                    )
                    self.workers[identity] = thread
                    self._native_threads.add(thread)
                    try:
                        thread.start()
                    except RuntimeError:
                        del self.workers[identity]
                        self.legacy_queue.appendleft(event)
                        self.start_error = "worker_start_failed"
                        raise
                    if not bounded:
                        deadline = max(deadline, time.monotonic() + 35.0)
                workers = list(self.workers.values())
                queued = bool(self.legacy_queue)
            if not workers or time.monotonic() >= deadline:
                break
            if queued:
                self.shutdown.wait(0.01)
                time.sleep(0.01)
            else:
                for worker in workers:
                    worker.join(max(0.0, deadline - time.monotonic()))
                break
        if preserve_queued:
            with self.lock:
                retained_threads = list(self._native_threads)
            for worker in retained_threads:
                if worker.ident is not None and worker is not threading.current_thread():
                    worker.join(max(0.0, deadline - time.monotonic()))
        with self.lock:
            native = threading.enumerate()
            live = [worker.name for worker in self._native_threads if worker in native]
            self._native_threads.intersection_update(native)
            if self.legacy_queue and not (preserve_queued and self.legacy_fenced):
                live.append(f"pr-notify:legacy-queued({len(self.legacy_queue)})")
        return live
