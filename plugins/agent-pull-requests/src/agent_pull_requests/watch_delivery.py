"""Independent, generation-fenced at-least-once callback retry workers."""

from __future__ import annotations

import subprocess
import threading
import time

from .watch_notification import event_payload


class Deliveries:
    def __init__(self, registry, notify, persist, transaction, shutdown) -> None:
        self.registry = registry
        self.notify = notify
        self.persist = persist
        self.transaction = transaction
        self.shutdown = shutdown
        self.workers: dict[str, threading.Thread] = {}
        self.lock = threading.Lock()

    def resume(self) -> None:
        with self.lock:
            if self.shutdown.is_set():
                return
            for event in self.registry.pending_events():
                identity = event.subscriber.registration_id
                worker = self.workers.get(identity)
                if worker is not None and worker.is_alive():
                    continue
                thread = threading.Thread(
                    target=self._run, args=(event,), name=f"pr-notify:{identity}", daemon=True,
                )
                self.workers[identity] = thread
                thread.start()

    def legacy(self, event) -> None:
        """Keep legacy best-effort delivery from blocking observation or other callbacks."""
        with self.lock:
            thread = threading.Thread(
                target=self._run_legacy, args=(event,),
                name=f"pr-notify:{event.subscriber.registration_id}", daemon=True,
            )
            self.workers[event.subscriber.registration_id] = thread
            thread.start()

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
            while not self.shutdown.is_set():
                with self.transaction:
                    if not self.registry.is_current(event):
                        return
                    delay = max(0.0, sub.pending["next_attempt"] - time.time())
                if delay:
                    self.shutdown.wait(min(delay, 0.5))
                    continue
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
                            self.persist(entries)
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
                    self.shutdown.wait(1.0)
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
        with self.lock:
            workers = list(self.workers.values())
        for worker in workers:
            worker.join(max(0.0, deadline - time.monotonic()))
        return [worker.name for worker in workers if worker.is_alive()]
