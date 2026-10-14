"""Push-first satellite intake, with one serialized queue reconciler.

The bounded dirty bit carries no task data or authority. The reader only wakes
the existing authoritative intake, including after the subscription barrier.
Presence belongs to FederationRunner and never waits for discovery or spawn.
"""

from __future__ import annotations

import asyncio
import logging
import math
import threading
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from .queue_common import machine_matches

if TYPE_CHECKING:
    from .client import DispatchClient
    from .satellite_work_intake import SatelliteWorkIntake

log = logging.getLogger(__name__)
POLL_SECONDS = 60.0
MIN_RECONCILE_SECONDS = 1.0
READY_TIMEOUT_SECONDS = 5.0
MAX_BACKOFF_SECONDS = 30.0
STABLE_STREAM_SECONDS = 20.0
_NO_WAKE = frozenset({"task.heartbeat", "task.activity_updated", "task.progress"})


def assignment_event_matches(event: dict, machine: str, repo: str | None) -> bool:
    """Task snapshots filter by affinity; aggregate reconciles wake all lanes."""
    kind = event.get("type", "")
    if not isinstance(kind, str) or not kind.startswith("task.") or kind in _NO_WAKE:
        return False
    task = event.get("task")
    if not isinstance(task, dict):
        return kind in {"task.reconciled", "task.reaped", "task.recovered"}
    target = task.get("target_machine")
    return (
        isinstance(target, str) and machine_matches(target, machine)
        and (repo is None or task.get("repo") == repo)
    )


class AssignmentIntake:
    """Own reader + intake threads; stop joins both before releasing transport."""

    def __init__(
        self,
        client: DispatchClient,
        intake: SatelliteWorkIntake,
        *,
        machine: str,
        repo: str | None = None,
        enabled: Callable[[], bool],
        reconcile: Callable[[], dict] | None = None,
        poll_seconds: float = POLL_SECONDS,
        min_reconcile_seconds: float = MIN_RECONCILE_SECONDS,
        ready_timeout: float = READY_TIMEOUT_SECONDS,
    ) -> None:
        timings = (poll_seconds, min_reconcile_seconds, ready_timeout)
        if any(value <= 0 or not math.isfinite(value) for value in timings):
            raise ValueError("assignment intake timing must be positive")
        self._client = client
        self._reconcile = reconcile or intake.tick
        self._machine = machine
        self._repo = repo
        self._enabled = enabled
        self._poll = poll_seconds
        self._minimum = min_reconcile_seconds
        self._ready_timeout = ready_timeout
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._reader_task: asyncio.Task | None = None
        self._health = {"state": "connecting", "error": None, "reconciles": 0}
        self._worker = threading.Thread(target=self._work, daemon=True)
        self._reader = threading.Thread(target=self._read, daemon=True)

    def start(self) -> None:
        self._wake.set()
        try:
            self._worker.start()
            self._reader.start()
        except Exception:
            self.stop()
            self._state("stopped")
            raise

    def status(self) -> dict:
        with self._lock:
            return dict(self._health)

    def _state(self, state: str, error: str | None = None) -> None:
        with self._lock:
            self._health.update(state=state, error=error)

    def _work(self) -> None:
        next_poll = time.monotonic()
        next_allowed = 0.0
        while not self._stop.is_set():
            now = time.monotonic()
            if now >= next_poll or self._wake.is_set():
                if self._stop.wait(max(0.0, next_allowed - now)):
                    break
                # Clear BEFORE the read: an event during reconcile is trailing work.
                self._wake.clear()
                if self._stop.is_set() or not self._enabled():
                    break
                try:
                    result = self._reconcile()
                    with self._lock:
                        self._health["reconciles"] += 1
                        self._health["intake_error"] = result.get("error")
                except Exception as exc:
                    with self._lock:
                        self._health["intake_error"] = type(exc).__name__
                    log.warning("Assignment reconciliation failed (%s)", type(exc).__name__)
                now = time.monotonic()
                next_allowed = now + self._minimum
                next_poll = now + self._poll
            self._wake.wait(max(0.0, next_poll - time.monotonic()))

    def _read(self) -> None:
        asyncio.run(self._subscribe())

    async def _subscribe(self) -> None:
        with self._lock:
            self._loop = asyncio.get_running_loop()
            self._reader_task = asyncio.current_task()
        backoff = 1.0
        try:
            while not self._stop.is_set() and self._enabled():
                stream = self._client.async_stream_events()
                connected_at = None
                try:
                    self._state("connecting")
                    first = await asyncio.wait_for(
                        anext(stream), timeout=self._ready_timeout
                    )
                    if first.get("type") != "ready":
                        raise RuntimeError("missing subscription barrier")
                    if self._stop.is_set() or not self._enabled():
                        break
                    self._state("connected")
                    connected_at = time.monotonic()
                    self._wake.set()
                    async for event in stream:
                        if self._stop.is_set() or not self._enabled():
                            return
                        if assignment_event_matches(event, self._machine, self._repo):
                            self._wake.set()
                    raise ConnectionError("assignment stream ended")
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    if (
                        connected_at is not None
                        and time.monotonic() - connected_at >= STABLE_STREAM_SECONDS
                    ):
                        backoff = 1.0
                    self._state("degraded", type(exc).__name__)
                    log.warning("Assignment subscription degraded (%s)", type(exc).__name__)
                finally:
                    await stream.aclose()
                await asyncio.sleep(backoff)
                backoff = min(MAX_BACKOFF_SECONDS, backoff * 2)
        except asyncio.CancelledError:
            pass
        finally:
            self._state("stopped")
            with self._lock:
                self._loop = None
                self._reader_task = None

    def stop(self, timeout: float = 5.0) -> bool:
        """False means a bounded synchronous intake is still retiring: keep owner."""
        with self._lock:
            stopping = self._stop.is_set()
            self._stop.set()
            self._wake.set()
            if not stopping and self._loop is not None and self._reader_task is not None:
                self._loop.call_soon_threadsafe(self._reader_task.cancel)
        deadline = time.monotonic() + timeout
        for thread in (self._reader, self._worker):
            if thread.ident is not None:
                thread.join(timeout=max(0.0, deadline - time.monotonic()))
        return not (self._reader.is_alive() or self._worker.is_alive())
