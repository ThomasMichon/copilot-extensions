"""Explicit point binding for the service's existing task-runner loop."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, AsyncExitStack
from pathlib import Path
from typing import TYPE_CHECKING

from .point_hints import AcquisitionHints, PointChangeHints
from .point_queue_consumer import PointQueueConsumer
from .runner import _settle
from .task_store import TaskRecord, TaskStore

if TYPE_CHECKING:
    from agent_index.index_config import IndexConfig
    from agent_index.store.source_layout import PointScope

log = logging.getLogger(__name__)
PointExecutor = Callable[[TaskRecord], object]


def require_point_isolation(config: IndexConfig, legacy_data_dir: Path) -> None:
    """Require existing point owners in a directory disjoint from legacy state."""
    root, legacy = config.data_dir.resolve(), legacy_data_dir.resolve()
    if root.is_relative_to(legacy) or legacy.is_relative_to(root):
        raise ValueError("Point runtime requires a separate legacy data directory")
    for path in (
        config.lance_dir, config.clusters_db, config.state_file,
        config.data_dir / "tasks.db", config.data_dir / "path_index.db",
    ):
        if not path.resolve().is_relative_to(root):
            raise ValueError("Point runtime state must remain inside its selected data directory")
    if not all((root / name).is_file() for name in ("tasks.db", "path_index.db")):
        raise ValueError("Point runtime requires existing configured queue and path owners")


class PointService:
    """Hold a caller's executor/client context through joined service shutdown.

    Construction and every new start are inert with respect to dispatch.
    Activation is an explicit in-process recovery decision, never an intake,
    wakeup, drain rollback or reconstruction side effect.
    """

    def __init__(
        self, *, config: IndexConfig, point_scope: PointScope,
        executor_context: Callable[[], AbstractAsyncContextManager[PointExecutor]],
    ) -> None:
        self._config = config
        self._scope = point_scope
        self._executor_context = executor_context
        self._consumer: PointQueueConsumer | None = None
        self._resources: AsyncExitStack | None = None
        self._stop_task: asyncio.Task[None] | None = None
        self._started = False
        self._stopped = False
        self._admitted = False
        self._draining = False
        self._failed = False
        self.running = False

    @property
    def store(self) -> TaskStore:
        if self._consumer is None:
            raise RuntimeError("Point service has not started")
        return self._consumer.tasks

    def matches(self, hints: AcquisitionHints) -> bool:
        return type(hints) is PointChangeHints and hints.scope == self._scope

    async def start(self, *, legacy_data_dir: Path) -> None:
        if self._started or self._stopped:
            raise RuntimeError("Point service cannot be started again")
        self._started = True
        await asyncio.to_thread(require_point_isolation, self._config, legacy_data_dir)
        resources = AsyncExitStack()
        try:
            executor = await resources.enter_async_context(self._executor_context())
            consumer = await asyncio.to_thread(
                PointQueueConsumer, config=self._config, point_scope=self._scope,
                point_executor=executor,
            )
            await resources.enter_async_context(consumer)
        except BaseException:
            await _settle(asyncio.create_task(resources.aclose()))
            raise
        self._resources = resources
        self._consumer = consumer

    async def activate(self) -> None:
        """Explicitly admit inspected retained work; never called by HTTP control."""
        if self._consumer is None or self._stopped or self._draining:
            raise RuntimeError("Point service is not accepting activation")
        await self._consumer.resume()
        self._failed = False
        self._admitted = True

    async def run_pending(self) -> int:
        consumer = self._consumer
        if consumer is None or self._stopped or self._draining or not self._admitted:
            return 0
        self.running = True
        try:
            if consumer.paused:
                await consumer.resume()
            return await consumer.run_pending(max_tasks=1)
        except BaseException as exc:
            self._admitted = False
            self._failed = True
            if not isinstance(exc, Exception):
                raise
            log.exception("Point service paused; retained-state inspection required")
            return 0
        finally:
            self.running = False

    def pause(self) -> None:
        """Close admission before a runner starts asynchronous shutdown."""
        self._draining = True
        if self._consumer is not None:
            self._consumer.pause()

    async def drain(self, *, timeout: float) -> bool:
        self.pause()
        if self._consumer is None:
            return True
        return await self._consumer.drain(timeout=timeout)

    async def resume(self) -> None:
        """Rollback drain without activating reconstructed or failed work."""
        self._draining = False

    async def stop(self) -> None:
        self._stopped = True
        self._admitted = False
        self.pause()
        if self._stop_task is None:
            resources = self._resources
            if resources is None:
                return
            self._stop_task = asyncio.create_task(resources.aclose())
        await _settle(self._stop_task)

    def status(self) -> dict[str, bool]:
        return {
            "admitted": self._admitted,
            "paused": self._consumer is None or self._consumer.paused or self._draining,
            "failed": self._failed,
            "running": self.running,
        }
