"""Bounded, nonblocking delivery of optional fleet placement-decision audits.

The supervisor cycle only enqueues a decision; one owner-scoped writer thread
posts it to the coordinator. A slow, stalled or unavailable sink therefore
cannot delay spawn settlement or the next cycle, and it cannot multiply writer
threads: a full queue drops the record and counts it. A coordinator without
the route disables the outbox, and a transport failure omits records for a
short backoff instead of retrying. The audit is diagnostic evidence only; the
reservation's own bookkeeping stays authoritative.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable
from typing import Any

from .client_errors import DispatchError

log = logging.getLogger(__name__)

PLACEMENT_AUDIT_CAPACITY = 32
PLACEMENT_AUDIT_UNAVAILABLE_BACKOFF_SECONDS = 60.0

Record = Callable[[str, dict[str, Any]], object]
_STOP = object()


class PlacementAuditOutbox:
    """Single-writer outbox whose ``submit`` never blocks the caller."""

    def __init__(
        self,
        record: Record,
        *,
        capacity: int = PLACEMENT_AUDIT_CAPACITY,
        backoff_seconds: float = PLACEMENT_AUDIT_UNAVAILABLE_BACKOFF_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._record = record
        self._queue: queue.Queue[object] = queue.Queue(maxsize=max(1, capacity))
        self._backoff_seconds = backoff_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._closed = False
        self._disabled = False
        self._unavailable_until = 0.0
        self.submitted = 0
        self.delivered = 0
        self.dropped = 0

    def submit(self, key: str, decision: dict[str, Any]) -> bool:
        """Queue one decision for delivery; return ``False`` when it was omitted."""
        with self._lock:
            if self._closed or self._disabled or self._clock() < self._unavailable_until:
                self.dropped += 1
                return False
            try:
                self._queue.put_nowait((key, decision))
            except queue.Full:
                self.dropped += 1
                return False
            self.submitted += 1
            if self._thread is None:
                self._thread = threading.Thread(
                    target=self._run, daemon=True, name="placement-audit-writer",
                )
                self._thread.start()
            return True

    def drain(self, timeout: float) -> bool:
        """Wait up to ``timeout`` for queued records to settle (tests/shutdown only)."""
        deadline = time.monotonic() + timeout
        while self._queue.unfinished_tasks:
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.01)
        return True

    def close(self) -> None:
        """Stop accepting records and let the writer exit without waiting for it."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            thread = self._thread
        if thread is None:
            return
        try:
            self._queue.put_nowait(_STOP)
        except queue.Full:
            pass  # the daemon writer exits with the process; never block shutdown

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item is _STOP:
                    return
                key, decision = item  # type: ignore[misc]
                if self._disabled or self._clock() < self._unavailable_until:
                    with self._lock:
                        self.dropped += 1
                    continue
                self._deliver(key, decision)
            finally:
                self._queue.task_done()

    def _deliver(self, key: str, decision: dict[str, Any]) -> None:
        try:
            self._record(key, decision)
        except DispatchError as exc:
            if exc.status_code == 405 or (exc.status_code == 404 and exc.detail == "Not Found"):
                # An older coordinator lacks the route: omit the audit from now on.
                self._disabled = True
            log.warning("placement decision for reservation %s not recorded: %s", key, exc)
            return
        except Exception as exc:  # noqa: BLE001 - an optional audit sink never aborts its owner
            self._unavailable_until = self._clock() + self._backoff_seconds
            log.warning("placement decision for reservation %s not recorded: %s", key, exc)
            return
        with self._lock:
            self.delivered += 1
