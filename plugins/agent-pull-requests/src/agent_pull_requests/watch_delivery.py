"""Independent, generation-fenced at-least-once callback retry workers."""

from __future__ import annotations

import subprocess
import threading
import time
from collections import deque

from .watch_notification import event_payload
from .watch_storage import StateCommitUncertain


class Deliveries:
    def __init__(self, registry, notify, persist, transaction, shutdown) -> None:
        self.registry = registry
        self.notify = notify
        self.persist = persist
        self.transaction = transaction
        self.shutdown = shutdown
        self.workers: dict[str, threading.Thread] = {}
        self.lock = threading.Lock()
        self.legacy_queue = deque()
        self.scheduler: threading.Thread | None = None
        self.max_workers = 8

    def resume(self) -> None:
        with self.lock:
            if self.shutdown.is_set():
                return
            if self.scheduler is None or not self.scheduler.is_alive():
                self.scheduler = threading.Thread(
                    target=self._schedule, name="pr-notify-scheduler", daemon=True,
                )
                self.scheduler.start()

    def _schedule(self) -> None:
        while not self.shutdown.is_set():
            with self.transaction:
                pending = sorted(
                    self.registry.pending_events(),
                    key=lambda e: e.subscriber.pending["next_attempt"],
                )
            with self.lock:
                if self.shutdown.is_set():
                    return
                available = self.max_workers - len(self.workers)
                candidates = []
                while self.legacy_queue and len(candidates) < available:
                    candidates.append((self.legacy_queue.popleft(), self._run_legacy))
                for event in pending:
                    identity = event.subscriber.registration_id
                    if (
                        len(candidates) >= available
                        or event.subscriber.pending["next_attempt"] > time.time()
                        or identity in self.workers
                    ):
                        continue
                    candidates.append((event, self._run))
                for event, target in candidates:
                    identity = event.subscriber.registration_id
                    thread = threading.Thread(
                        target=target, args=(event,), name=f"pr-notify:{identity}", daemon=True,
                    )
                    self.workers[identity] = thread
                    thread.start()
            self.shutdown.wait(0.05)

    def legacy(self, event) -> None:
        """Keep legacy best-effort delivery from blocking observation or other callbacks."""
        with self.lock:
            if self.shutdown.is_set():
                return
            self.legacy_queue.append(event)
        self.resume()

    def _run_legacy(self, event) -> None:
        try:
            self.notify(event)
        except Exception:
            pass
        finally:
            with self.lock:
                self.workers.pop(event.subscriber.registration_id, None)

    def _run(self, event) -> None:
        sub = event.subscriber
        try:
            if not self.shutdown.is_set():
                with self.transaction:
                    if not self.registry.is_current(event):
                        return
                    delay = max(0.0, sub.pending["next_attempt"] - time.time())
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
                            entries = [
                                e for e in self.registry.snapshot_state()
                                if e.get("registration_id") != sub.registration_id
                            ]
                            try:
                                self.persist(entries)
                            except StateCommitUncertain:
                                self.registry.unregister(event.key, sub.subscriber_id)
                                return
                            self.registry.unregister(event.key, sub.subscriber_id)
                            return
                        pending = sub.pending
                        pending["attempts"] += 1
                        pending["last_error"] = error
                        pending["next_attempt"] = time.time() + min(
                            60.0, 2.0 ** min(pending["attempts"] - 1, 6),
                        )
                        self.persist()
                except OSError:
                    # The persisted event remains authoritative; never acknowledge a failed write.
                    if sub.pending is not None:
                        sub.pending["next_attempt"] = time.time() + 1.0
        finally:
            with self.lock:
                if self.workers.get(sub.registration_id) is threading.current_thread():
                    del self.workers[sub.registration_id]

    def status(self) -> list[dict]:
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
                for event in self.registry.pending_events()
            ]

    def close(self, deadline: float) -> list[str]:
        scheduler = self.scheduler
        if scheduler is not None:
            scheduler.join(max(0.0, deadline - time.monotonic()))
        while True:
            with self.lock:
                while self.legacy_queue and len(self.workers) < self.max_workers:
                    event = self.legacy_queue.popleft()
                    identity = event.subscriber.registration_id
                    thread = threading.Thread(
                        target=self._run_legacy, args=(event,),
                        name=f"pr-notify:{identity}", daemon=True,
                    )
                    self.workers[identity] = thread
                    thread.start()
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
        live = [worker.name for worker in workers if worker.is_alive()]
        if scheduler is not None and scheduler.is_alive():
            live.append(scheduler.name)
        return live
