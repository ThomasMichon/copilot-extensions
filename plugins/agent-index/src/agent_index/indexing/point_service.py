"""Explicit point binding for the service's existing task-runner loop."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager, AsyncExitStack
from contextvars import copy_context
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar, cast

from .point_hints import AcquisitionHints, PointChangeHints
from .point_queue_consumer import PointQueueConsumer
from .point_service_contract import (
    point_scope_fingerprint, require_point_service, unavailable_point_service,
)
from .runner import _settle
from .task_store import TaskRecord, TaskStore

if TYPE_CHECKING:
    from agent_index.index_config import IndexConfig
    from agent_index.indexing.component_read import ComponentRead
    from agent_index.store.source_layout import PointScope

log = logging.getLogger(__name__)
PointExecutor = Callable[[TaskRecord], object]
PointReader = Callable[[str], "ComponentRead"]
_T = TypeVar("_T")


class PointAdmissionRefused(RuntimeError):
    """The selected point owner refused before starting a queue effect."""


@dataclass(frozen=True)
class PointRuntime:
    """One explicitly composed executor and qualified reader selection."""

    config: IndexConfig
    point_scope: PointScope
    execute: PointExecutor
    read_component: PointReader


class _ConfiguredPointRuntime(PointRuntime):
    """Owned production composition; custom callable pairs remain unadvertised."""


class _CleanupTask(asyncio.Task[_T]):
    """Keep joined service operations running through loop-wide cancellation."""

    def cancel(self, msg: object = None) -> bool:
        return False


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
        executor_context: Callable[[], AbstractAsyncContextManager[PointExecutor]] | None = None,
        runtime_context: Callable[[], AbstractAsyncContextManager[PointRuntime]] | None = None,
    ) -> None:
        if (executor_context is None) == (runtime_context is None):
            raise ValueError("Select exactly one point executor or paired runtime context")
        self._config = config
        self._scope = point_scope
        self._executor_context = executor_context
        self._runtime_context = runtime_context
        self._reader: PointReader | None = None
        self._runtime: PointRuntime | None = None
        self._reads: set[asyncio.Future[ComponentRead]] = set()
        self._intakes: set[asyncio.Task[TaskRecord]] = set()
        self._consumer: PointQueueConsumer | None = None
        self._resources: AsyncExitStack | None = None
        self._stop_result: asyncio.Future[None] | None = None
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
            runtime = None
            reader = None
            if self._runtime_context is not None:
                runtime = await resources.enter_async_context(self._runtime_context())
                if (type(runtime) not in (PointRuntime, _ConfiguredPointRuntime)
                        or runtime.config is not self._config
                        or runtime.point_scope != self._scope
                        or not callable(runtime.execute) or not callable(runtime.read_component)):
                    raise ValueError("Point runtime requires its configured executor and reader binding")
                executor, reader = runtime.execute, runtime.read_component
            else:
                assert self._executor_context is not None
                executor = await resources.enter_async_context(self._executor_context())
            consumer = await asyncio.to_thread(
                PointQueueConsumer, config=self._config, point_scope=self._scope,
                point_executor=executor,
            )
            await resources.enter_async_context(consumer)
        except BaseException:
            await _settle(_CleanupTask(resources.aclose()))
            raise
        self._resources = resources
        self._consumer = consumer
        self._reader = reader
        self._runtime = runtime

    def contract(self, *, draining: bool = False) -> dict[str, object]:
        """Observe installed owners, never open or qualify their retained state."""
        if self._stopped:
            return unavailable_point_service("stopped", stopped=True, failed=self._failed)
        if self._resources is None:
            return unavailable_point_service(
                "startup-incomplete" if self._started else "not-started",
            )
        if self._runtime is None:
            return unavailable_point_service("executor-only")
        if type(self._runtime) is not _ConfiguredPointRuntime:
            return unavailable_point_service("unqualified-runtime")
        closed = self._draining or draining
        return {
            "id": "agent-index.point-service", "version": 1, "paired": True,
            "required_admission_versions": [1],
            "scope_fingerprint": point_scope_fingerprint(self._scope),
            "representation": "point-components", "representation_version": 1,
            "coverage": "named-target-only", "hints_versions": [2],
            "read_boundary": "in-process-only",
            "state": (
                "draining" if closed else "failed" if self._failed
                else "active" if self._admitted else "paused"
            ),
            "failed": self._failed,
            "execution_admitted": self._admitted and not self._failed and not closed,
            "read_admitted": self._reader is not None and not closed,
        }

    async def enqueue_required(
        self, hints: AcquisitionHints, enqueue: Callable[[TaskStore], Awaitable[TaskRecord]],
    ) -> TaskRecord:
        """Admit and own one exact selected queue effect through settled cleanup."""
        if not self.matches(hints):
            raise PointAdmissionRefused("Required point-service scope does not match the selected pair")
        try:
            require_point_service(
                {"point_service_contract": self.contract()}, self._scope, operation="execute",
            )
        except RuntimeError as exc:
            raise PointAdmissionRefused(str(exc)) from None
        store = self.store

        async def admitted() -> TaskRecord:
            return await enqueue(store)

        operation = _CleanupTask(admitted())
        self._intakes.add(operation)
        operation.add_done_callback(self._intakes.discard)
        return cast("TaskRecord", await _settle(operation))

    async def read_component(self, path: str) -> ComponentRead:
        """Qualify one named target, including while execution remains paused."""
        if self._reader is None or self._stopped or self._draining:
            raise RuntimeError("Point service is not accepting qualified reads")
        reader = self._reader
        operation = asyncio.get_running_loop().run_in_executor(
            None, copy_context().run, reader, path,
        )
        self._reads.add(operation)
        operation.add_done_callback(self._reads.discard)
        return cast("ComponentRead", await _settle(operation))

    async def activate(self) -> None:
        """Explicitly admit inspected retained work; never called by HTTP control."""
        if self._consumer is None or self._stopped or self._draining:
            raise RuntimeError("Point service is not accepting activation")
        await self._consumer.resume()
        self._failed = False
        self._admitted = True

    async def run_pending(self, *, admit: Callable[[], bool] | None = None) -> int:
        consumer = self._consumer
        if consumer is None or self._stopped or self._draining or not self._admitted:
            return 0
        self.running = True
        try:
            if consumer.paused:
                await consumer.resume()
            return await consumer.run_pending(max_tasks=1, admit=admit)
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
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        admitted: set[asyncio.Future[ComponentRead] | asyncio.Task[TaskRecord]] = {
            *self._reads, *self._intakes,
        }
        if admitted:
            _, pending = await asyncio.wait(admitted, timeout=max(0, timeout))
            if pending:
                return False
        if self._consumer is None:
            return True
        return await self._consumer.drain(timeout=max(0, deadline - loop.time()))

    async def resume(self) -> None:
        """Rollback drain without activating reconstructed or failed work."""
        self._draining = False

    async def stop(self) -> None:
        self._stopped = True
        self._admitted = False
        self.pause()
        if self._stop_result is not None:
            await _settle(self._stop_result)
            return
        resources = self._resources
        if resources is None:
            return
        async def close() -> None:
            try:
                admitted: set[asyncio.Future[ComponentRead] | asyncio.Task[TaskRecord]] = {
                    *self._reads, *self._intakes,
                }
                if admitted:
                    await _settle(asyncio.gather(*admitted, return_exceptions=True))
            finally:
                await resources.aclose()

        self._stop_result = _CleanupTask(close())
        await _settle(self._stop_result)

    def status(self) -> dict[str, bool]:
        return {
            "admitted": self._admitted,
            "paused": self._consumer is None or self._consumer.paused or self._draining,
            "failed": self._failed,
            "running": self.running,
        }
