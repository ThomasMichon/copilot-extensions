"""Joined async ownership for explicitly configured in-process point workers."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextvars import copy_context
from functools import partial
from types import TracebackType
from typing import TYPE_CHECKING, cast

from agent_index.indexing.runner import _settle
from agent_index.indexing.worker import run_worker

if TYPE_CHECKING:
    from agent_index.index_config import IndexConfig
    from agent_index.indexing.task_store import TaskRecord


class PointWorkerHost:
    """Keep caller-owned clients alive until synchronous dispatch has settled.

    One event loop owns this single-use context. It neither dequeues work nor
    participates in the ordinary detached TaskRunner's admission or adoption.
    """

    def __init__(
        self, *, config: IndexConfig, point_executor: Callable[[TaskRecord], object],
    ) -> None:
        if not callable(point_executor):
            raise ValueError("Point worker host requires an explicit point executor")
        self._config = config
        self._point_executor = point_executor
        self._operation: asyncio.Future[int] | None = None
        self._entered = False
        self._paused = False
        self._stopped = False

    async def __aenter__(self) -> PointWorkerHost:
        if self._entered or self._stopped:
            raise RuntimeError("Point worker host cannot be entered again")
        self._entered = True
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None,
        exc: BaseException | None, tb: TracebackType | None,
    ) -> None:
        await self.stop()

    async def run(self, task_id: str) -> int:
        """Dispatch one exact task; defer cancellation until its thread returns."""
        if not self._entered or self._stopped or self._paused:
            raise RuntimeError("Point worker host is not accepting work")
        if self._operation is not None:
            raise RuntimeError("Point worker host already has an active operation")
        call = partial(
            run_worker, task_id, config=self._config, point_executor=self._point_executor,
        )
        # A Future, unlike a to_thread Task, is not cancelled by loop-wide
        # task shutdown while the native thread is still running.
        operation = asyncio.get_running_loop().run_in_executor(None, copy_context().run, call)
        self._operation = operation
        try:
            return cast(int, await _settle(operation))
        finally:
            self._operation = None

    async def drain(self, *, timeout: float = 300.0) -> bool:
        """Pause admission, returning False if active dispatch cannot settle in time."""
        self._paused = True
        operation = self._operation
        if operation is None:
            return True
        # Unlike wait_for, wait does not cancel the effects-running thread task
        # on timeout or cancellation of the drain observer.
        done, _ = await asyncio.wait((operation,), timeout=max(0.0, timeout))
        return bool(done)

    async def resume(self) -> None:
        if not self._entered or self._stopped:
            raise RuntimeError("Point worker host is not accepting work")
        self._paused = False

    async def stop(self) -> None:
        """Close admission and join active dispatch, including on repeated cancellation."""
        self._stopped = True
        operation = self._operation
        if operation is not None:
            await _settle(operation)
