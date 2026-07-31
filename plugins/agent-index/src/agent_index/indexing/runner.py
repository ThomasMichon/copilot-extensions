"""Task runner — async worker loop that drains the task queue.

Modelled after analysis-feed's ``EngineService`` worker loop and
permanent-record's ``TaskRunner``.  Runs in a persistent asyncio task,
executing one indexing job at a time.  Progress is throttled for SQLite
writes but streamed immediately via the EventBus.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from typing import TYPE_CHECKING, Any

from agent_index.indexing.task_store import TaskStatus

if TYPE_CHECKING:
    from agent_index.indexing.task_store import TaskRecord, TaskStore
    from agent_index.server.event_bus import EventBus

log = logging.getLogger(__name__)

# Throttle interval for persisting progress to SQLite (seconds).
# SSE events are always emitted immediately.
_PROGRESS_PERSIST_INTERVAL = 5.0


class IndexingCancelled(Exception):
    """Raised when a running task is cancelled via the API."""


class ProgressCallback:
    """Bridge between the indexing engine and the task runner.

    Called from a worker thread.  Throttles SQLite writes to every
    ``_PROGRESS_PERSIST_INTERVAL`` seconds (or on stage change), while
    streaming SSE events immediately via ``run_coroutine_threadsafe``.
    """

    def __init__(
        self,
        task_id: str,
        store: TaskStore,
        event_bus: EventBus,
        loop: asyncio.AbstractEventLoop,
        cancelled: threading.Event,
    ) -> None:
        self._task_id = task_id
        self._store = store
        self._event_bus = event_bus
        self._loop = loop
        self._cancelled = cancelled
        self._last_persist: float = 0.0
        self._last_stage: str = ""
        # Running counters (updated in-thread)
        self._stage: str = "queued"
        self._pct: float = 0.0
        self._msg: str = ""

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled.is_set()

    def check_cancelled(self) -> None:
        """Raise IndexingCancelled if the task was cancelled."""
        if self._cancelled.is_set():
            raise IndexingCancelled(f"Task {self._task_id} cancelled")

    def phase(self, stage: str, msg: str = "", *, pct: float = 0.0) -> None:
        """Report a phase transition (discovering, chunking, embedding)."""
        self._update(stage, pct, msg, force_persist=True)

    def source_started(self, source: str) -> None:
        self._update("discovering", 0.0, f"Discovering {source}", force_persist=True)

    def source_complete(self, source: str, chunks: int) -> None:
        self._update(
            "source_complete", self._pct,
            f"{source}: {chunks} chunks",
            force_persist=True,
        )

    def file_discovered(self, count: int, total: int = 0) -> None:
        pct = 5.0  # discovery is early
        msg = f"Discovered {count} files" + (f" / {total}" if total else "")
        self._update("discovering", pct, msg)

    def batch_complete(self, chunks_done: int, chunks_total: int | None = None) -> None:
        if chunks_total and chunks_total > 0:
            pct = min(95.0, 10.0 + 85.0 * (chunks_done / chunks_total))
        else:
            pct = min(95.0, self._pct + 1.0)
        msg = f"Embedded {chunks_done}"
        if chunks_total:
            msg += f" / {chunks_total}"
        msg += " chunks"
        self._update("embedding", pct, msg)

    def _update(
        self,
        stage: str,
        pct: float,
        msg: str,
        *,
        force_persist: bool = False,
    ) -> None:
        self._stage = stage
        self._pct = pct
        self._msg = msg

        now = time.monotonic()
        stage_changed = stage != self._last_stage
        self._last_stage = stage

        # Always persist on stage change or forced; otherwise throttle
        if (
            force_persist
            or stage_changed
            or (now - self._last_persist) >= _PROGRESS_PERSIST_INTERVAL
        ):
            self._last_persist = now
            self._store.update_progress(self._task_id, stage, pct, msg)

        # SSE is always immediate
        self._event_bus.publish("task_progress", {
            "task_id": self._task_id,
            "stage": stage,
            "percent": round(pct, 1),
            "message": msg,
        })


class TaskRunner:
    """Async worker loop that drains the task queue sequentially.

    Runs as a persistent asyncio task.  When the queue is empty, waits
    on an ``asyncio.Event`` until awakened by ``notify()``.
    """

    def __init__(
        self,
        store: TaskStore,
        event_bus: EventBus,
    ) -> None:
        self.store = store
        self.event_bus = event_bus
        self._index_fn: Callable[..., None] | None = None
        self._post_index_fn: Callable[[], None] | None = None
        self._task: asyncio.Task[None] | None = None
        self._shutdown = False
        self._paused = False
        self._wake = asyncio.Event()
        # Cancellation per running task
        self._cancel_events: dict[str, threading.Event] = {}
        self._cancel_lock = threading.Lock()
        # Per-source write lock (set by server at startup)
        self._source_lock_acquire: Callable[..., Any] | None = None
        # Observable state
        self.running = False
        self.active_task_id: str | None = None
        self.tasks_completed: int = 0
        self.tasks_failed: int = 0

    def set_index_fn(self, fn: Callable[..., None]) -> None:
        """Register the indexing function (called by the server at startup)."""
        self._index_fn = fn

    def set_post_index_fn(self, fn: Callable[[], None]) -> None:
        """Register a callback run after each successful indexing task."""
        self._post_index_fn = fn

    def set_source_lock_fn(self, fn: Callable[..., Any]) -> None:
        """Register the per-source lock acquire function.

        ``fn(source) → asyncio.Lock`` — called before entering the
        executor so the lock is held in the async context.
        """
        self._source_lock_acquire = fn

    # ── Lifecycle ────────────────────────────────────────────

    async def start(self) -> None:
        """Initialize DB, recover from crash, start the worker loop."""
        interrupted = await asyncio.to_thread(self.store.mark_interrupted)
        if interrupted:
            self.event_bus.publish("queue_update", {"reason": "recovery"})

        pending = await asyncio.to_thread(self.store.get_pending_count)
        if pending > 0:
            log.info("Startup recovery: %d queued task(s)", pending)

        self._shutdown = False
        self._task = asyncio.create_task(self._worker_loop())
        log.info("Task runner started")

    async def stop(self) -> None:
        """Gracefully stop the worker loop."""
        self._shutdown = True
        self._wake.set()
        if self._task:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
        log.info("Task runner stopped")

    async def drain(self, *, timeout: float = 300.0, poll: float = 0.5) -> bool:
        """Pause dequeueing and wait for the current task to finish."""
        self._paused = True
        self._wake.set()
        deadline = time.monotonic() + max(0.0, timeout)
        while self.running or self.active_task_id is not None:
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(max(0.05, poll))
        return True

    async def resume(self) -> None:
        """Resume dequeueing queued tasks after a drain rollback."""
        self._paused = False
        self._wake.set()

    def notify(self) -> None:
        """Wake the worker loop (call after enqueueing)."""
        self._wake.set()

    # ── Cancel ───────────────────────────────────────────────

    def cancel_task(self, task_id: str) -> bool:
        """Cancel a queued or running task. Returns True if cancelled."""
        # Try store-level cancel first (queued tasks)
        if self.store.cancel(task_id):
            self.event_bus.publish("task_cancelled", {"task_id": task_id})
            return True

        # Signal running task
        with self._cancel_lock:
            evt = self._cancel_events.get(task_id)
            if evt:
                evt.set()
                return True

        return False

    # ── Status ───────────────────────────────────────────────

    def status(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "paused": self._paused,
            "active_task_id": self.active_task_id,
            "stats": {
                "completed": self.tasks_completed,
                "failed": self.tasks_failed,
            },
        }

    # ── Worker loop ──────────────────────────────────────────

    async def _worker_loop(self) -> None:
        log.info("Worker loop started")
        # Small initial delay to let the service fully start
        await asyncio.sleep(2)

        while not self._shutdown:
            if self._paused:
                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=10.0)
                except TimeoutError:
                    continue
                continue

            task = await asyncio.to_thread(self.store.dequeue_next)
            if task is None:
                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=10.0)
                except TimeoutError:
                    continue
                continue

            self.running = True
            self.active_task_id = task.id
            self.event_bus.publish("task_started", {
                "task_id": task.id,
                "source": task.source,
                "full": task.full,
                "trigger_source": task.trigger_source,
            })

            try:
                await self._process_task(task)
            except Exception:
                log.exception("Unhandled error processing task %s", task.id)

            self.running = False
            self.active_task_id = None

        log.info("Worker loop exited")

    async def _process_task(self, task: TaskRecord) -> None:
        """Run a single indexing task in an executor thread.

        Acquires a per-source write lock (if configured) to prevent
        concurrent writes from ingest endpoints.
        """
        loop = asyncio.get_running_loop()

        cancel_event = threading.Event()
        with self._cancel_lock:
            self._cancel_events[task.id] = cancel_event

        callback = ProgressCallback(
            task_id=task.id,
            store=self.store,
            event_bus=self.event_bus,
            loop=loop,
            cancelled=cancel_event,
        )

        def _run() -> dict[str, int] | None:
            if self._index_fn is None:
                raise RuntimeError("No index function registered — call set_index_fn()")

            return self._index_fn(
                full=task.full,
                source=task.source if task.source != "all" else None,
                progress_cb=callback,
            )

        # Acquire per-source lock before entering executor
        source_lock = None
        if self._source_lock_acquire is not None:
            source_key = task.source if task.source != "all" else "__all__"
            source_lock = await self._source_lock_acquire(source_key)

        try:
            result_stats = await loop.run_in_executor(None, _run)

            # Persist crawl stats
            if result_stats and isinstance(result_stats, dict):
                self.store.set_result_stats(task.id, result_stats)

            # Force final progress persist
            self.store.update_progress(task.id, "complete", 100.0, "Indexing complete")
            await asyncio.to_thread(
                self.store.update_status, task.id, TaskStatus.COMPLETE.value,
            )
            self.tasks_completed += 1
            log.info("Task %s completed", task.id)
            self.event_bus.publish("task_complete", {
                "task_id": task.id,
                "success": True,
                "result_stats": result_stats,
            })

            # Run post-indexing hook (e.g. rebuild FTS index)
            if self._post_index_fn is not None:
                try:
                    await asyncio.to_thread(self._post_index_fn)
                except Exception:
                    log.warning("Post-index hook failed", exc_info=True)

        except IndexingCancelled:
            await asyncio.to_thread(
                self.store.update_status, task.id, TaskStatus.CANCELLED.value,
            )
            log.info("Task %s cancelled", task.id)
            self.event_bus.publish("task_complete", {
                "task_id": task.id,
                "success": False,
                "reason": "cancelled",
            })

        except Exception as e:
            error_msg = f"{type(e).__name__}: {e}"
            await asyncio.to_thread(
                self.store.update_status, task.id, TaskStatus.FAILED.value, error_msg,
            )
            self.tasks_failed += 1
            log.error("Task %s failed: %s", task.id, error_msg, exc_info=True)
            self.event_bus.publish("task_complete", {
                "task_id": task.id,
                "success": False,
                "error": error_msg,
            })

        finally:
            if source_lock is not None:
                source_lock.release()
            with self._cancel_lock:
                self._cancel_events.pop(task.id, None)
