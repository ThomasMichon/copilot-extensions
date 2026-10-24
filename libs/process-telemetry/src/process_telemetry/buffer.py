"""Opt-in bounded adapter for the existing dict/callable telemetry seam."""

from __future__ import annotations

import math
import threading
from collections import deque
from itertools import count

from .emission import TelemetrySink


class _Counter:
    def __init__(self):
        self._events = count()
        self._reads = count()
        self._reader_lock = threading.Lock()

    def __next__(self):
        return next(self._events)

    def read(self):
        # Reading advances the iterator too; subtract those advances rather than
        # depending on the deprecated itertools pickle/copy representation.
        with self._reader_lock:
            return next(self._events) - next(self._reads)


def snapshot(event: dict, *, max_bytes: int = 4096) -> dict:
    """Copy a bounded primitive event without JSON, arbitrary repr or user hooks."""
    remaining = max_bytes

    def copy(value, depth=0):
        nonlocal remaining
        remaining -= 32
        if remaining < 0 or depth > 3:
            raise ValueError("event limit")
        if value is None or type(value) is bool:
            return value
        if type(value) is int and -(2**63) <= value < 2**63:
            return value
        if type(value) is float and math.isfinite(value):
            return value
        if type(value) is str and len(value) <= 512:
            remaining -= len(value.encode("utf-8")) * 6
            if remaining < 0:
                raise ValueError("event limit")
            return value
        if type(value) is list and len(value) <= 32:
            return [copy(item, depth + 1) for item in value]
        if type(value) is dict and len(value) <= 64:
            result = {}
            for key, item in value.items():
                if type(key) is not str:
                    raise ValueError("non-string key")
                result[copy(key, depth + 1)] = copy(item, depth + 1)
            return result
        raise ValueError("unsupported event value")

    if type(event) is not dict:
        raise ValueError("event must be a dict")
    return copy(event)


class BufferedSink:
    """One owner-started writer; producers drop rather than wait for a sink.

    Counts are atomic iterator advances on supported CPython runtimes. A stats
    snapshot is observational, not transactionally consistent across counters.
    No writer is replaced automatically after a blocked or failed sink.
    """

    def __init__(
        self, sink: TelemetrySink, *, capacity: int = 128, max_bytes: int = 4096,
        batch_size: int = 32, batch_sink: bool = False,
        on_start=None,
        on_iteration=None,
    ) -> None:
        if not callable(sink):
            raise ValueError("sink must be callable")
        if on_start is not None and not callable(on_start):
            raise ValueError("on_start must be callable")
        if on_iteration is not None and not callable(on_iteration):
            raise ValueError("on_iteration must be callable")
        self._on_start = on_start
        self._on_iteration = on_iteration
        if any(type(value) is not int for value in (capacity, max_bytes, batch_size)):
            raise ValueError("queue bounds must be integers")
        if type(batch_sink) is not bool:
            raise ValueError("batch_sink must be boolean")
        if not 1 <= capacity <= 4096 or not 128 <= max_bytes <= 65536:
            raise ValueError("invalid queue bounds")
        if not 1 <= batch_size <= capacity:
            raise ValueError("invalid batch size")
        self._sink = sink
        self._capacity = capacity
        self._max_bytes = max_bytes
        self._batch_size = batch_size
        self._batch_sink = batch_sink
        self._queue: deque[dict] = deque()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._started = False
        self._closed = False
        self._counts = {name: _Counter() for name in (
            "accepted", "delivered", "invalid", "full", "contended",
            "inactive", "sink_failed", "abandoned",
        )}

    def start(self) -> BufferedSink:
        """Start once at explicit owner initialization, never from emission."""
        with self._lock:
            if self._closed:
                raise RuntimeError("sink closed")
            if self._started:
                return self
            thread = threading.Thread(
                target=self._run, name="diagnostic-telemetry", daemon=True,
            )
            self._thread = thread
            thread.start()
            self._started = True
        return self

    def __call__(self, event: dict) -> None:
        if not self._started or self._closed:
            next(self._counts["inactive"])
            return
        try:
            record = snapshot(event, max_bytes=self._max_bytes)
        except (ValueError, UnicodeError, RuntimeError):
            next(self._counts["invalid"])
            return
        if not self._lock.acquire(blocking=False):
            next(self._counts["contended"])
            return
        try:
            if self._closed:
                next(self._counts["inactive"])
            elif len(self._queue) >= self._capacity:
                next(self._counts["full"])
            else:
                self._queue.append(record)
                next(self._counts["accepted"])
        finally:
            self._lock.release()

    def close(self) -> None:
        """Reject new events immediately; abandon queued records off-path."""
        self._closed = True
        self._stop.set()

    @property
    def closed(self) -> bool:
        """Owner closure, not proof that the worker has exited."""
        return self._closed

    def join(self, timeout: float = 0.0) -> bool:
        """Optional bounded off-path wait; never flush or call sink shutdown."""
        if not math.isfinite(timeout) or not 0 <= timeout <= 5:
            raise ValueError("join timeout must be between zero and five seconds")
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
        return thread is None or not thread.is_alive()

    def stats(self) -> dict[str, int]:
        """Read cumulative counts without acquiring a writer-held lock."""
        return {name: counter.read() for name, counter in self._counts.items()}

    def _run(self) -> None:
        if self._on_start is not None and not self._closed:
            try:
                self._on_start()
            except Exception:
                next(self._counts["sink_failed"])
        while not self._closed:
            with self._lock:
                batch = [
                    self._queue.popleft()
                    for _ in range(min(self._batch_size, len(self._queue)))
                ]
            if self._batch_sink and batch and not self._closed:
                try:
                    self._sink(batch)
                except Exception:
                    for _ in batch:
                        next(self._counts["sink_failed"])
                else:
                    for _ in batch:
                        next(self._counts["delivered"])
                batch = []
            for event in batch:
                if self._closed:
                    next(self._counts["abandoned"])
                    continue
                try:
                    self._sink(event)
                except Exception:
                    next(self._counts["sink_failed"])
                else:
                    next(self._counts["delivered"])
            if self._on_iteration is not None and not self._closed:
                try:
                    self._on_iteration()
                except Exception:
                    next(self._counts["sink_failed"])
            self._stop.wait(0.025)
        with self._lock:
            while self._queue:
                self._queue.popleft()
                next(self._counts["abandoned"])
