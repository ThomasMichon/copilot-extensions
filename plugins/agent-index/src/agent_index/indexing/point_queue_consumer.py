"""Caller-scheduled, exact-scope consumption of explicitly selected point work."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from types import TracebackType
from typing import TYPE_CHECKING

from agent_index.indexing.point_hints import PointChangeHints
from agent_index.indexing.point_worker_host import PointWorkerHost
from agent_index.indexing.task_store import TaskRecord, TaskStore
from agent_index.store.source_layout import PointScope, _point_scope_json

if TYPE_CHECKING:
    from agent_index.index_config import IndexConfig


class PointQueueConsumer:
    """Consume bounded FIFO batches, pausing on failure rather than retrying.

    One event loop and caller-owned connector/client lifetime surround this
    single-use context. New instances start paused; scheduling and explicit
    recovery decisions remain with the caller, not a timer or detached runner.
    """

    def __init__(
        self, *, config: IndexConfig, point_scope: PointScope,
        point_executor: Callable[[TaskRecord], object],
    ) -> None:
        if _point_scope_json(point_scope) is None:
            raise ValueError("Point queue consumer requires an exact point scope")
        if not callable(point_executor):
            raise ValueError("Point queue consumer requires an explicit point executor")
        self._scope = point_scope
        self._tasks = TaskStore(config.data_dir / "tasks.db")

        def execute(task: TaskRecord) -> object:
            if type(task.change_hints) is not PointChangeHints or task.change_hints.scope != self._scope:
                raise ValueError("Selected point task does not match the consumer scope")
            return point_executor(task)

        self._host = PointWorkerHost(config=config, point_executor=execute)
        self._entered = False
        self._paused = True
        self._stopped = False
        self._running = False

    @property
    def paused(self) -> bool:
        """Whether dispatch requires explicit resume."""
        return self._paused

    @property
    def tasks(self) -> TaskStore:
        """The isolated queue used by both configured intake and selection."""
        return self._tasks

    async def __aenter__(self) -> PointQueueConsumer:
        await self._host.__aenter__()
        self._entered = True
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None,
        exc: BaseException | None, tb: TracebackType | None,
    ) -> None:
        await self.stop()

    def _require_open(self) -> None:
        if not self._entered or self._stopped:
            raise RuntimeError("Point queue consumer is not accepting work")

    async def run_pending(
        self, *, max_tasks: int = 1, admit: Callable[[], bool] | None = None,
    ) -> int:
        """Complete at most max_tasks claims; raise and pause on any failure."""
        self._require_open()
        if type(max_tasks) is not int or max_tasks < 1:
            raise ValueError("Point queue batch requires a positive integer task limit")
        if self._paused:
            raise RuntimeError("Point queue consumer requires explicit resume")
        if self._running:
            raise RuntimeError("Point queue consumer already has an active batch")
        self._running = True
        completed = 0
        try:
            while completed < max_tasks and not self._paused and not self._stopped:
                task = await asyncio.to_thread(
                    self._tasks.next_point_task, point_scope=self._scope,
                )
                if task is None or self._paused or self._stopped:
                    break
                if admit is not None and not admit():
                    break
                result = await self._host.run(task.id)
                if result != 0:
                    raise RuntimeError(f"Point queue dispatch {task.id} exited with code {result}")
                completed += 1
            return completed
        except BaseException:
            self._paused = True
            raise
        finally:
            self._running = False

    def pause(self) -> None:
        """Close admission synchronously, including during off-loop selection."""
        self._paused = True

    async def drain(self, *, timeout: float = 300.0) -> bool:
        """Pause this consumer and observe its joined worker without cancelling it."""
        self.pause()
        return await self._host.drain(timeout=timeout)

    async def resume(self) -> None:
        """Explicitly admit a new batch or retry after retained-state inspection."""
        self._require_open()
        if self._running:
            raise RuntimeError("Point queue consumer still has an active batch")
        await self._host.resume()
        self._paused = False

    async def stop(self) -> None:
        """Close admission and join native effects before clients may close."""
        self._stopped = True
        self._paused = True
        await self._host.stop()
