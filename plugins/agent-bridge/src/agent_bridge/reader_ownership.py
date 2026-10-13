"""Retain provider-native reads and samples independently of cache publication."""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import Future
from typing import Any

from agent_procutil.metrics import CpuObservation

log = logging.getLogger("agent-bridge")


class ProviderReaderOwnership:
    """Exact native holders, including providers no longer in the live roster."""

    def __init__(self) -> None:
        self.reads: set[asyncio.Task[Any]] = set()
        self.samples: set[Future[CpuObservation]] = set()

    def retain(self, resolver: object | None) -> None:
        sample = getattr(resolver, "pending_cpu_sample_future", None)
        if sample is not None and not sample.done() and sample not in self.samples:
            self.samples.add(sample)
            loop = asyncio.get_running_loop()

            def completed(future: Future[CpuObservation]) -> None:
                # The native thread completes this future, not the event loop.
                try:
                    loop.call_soon_threadsafe(self.samples.discard, future)
                except RuntimeError:
                    log.debug("CPU sample completed after its cache event loop closed")

            sample.add_done_callback(completed)
        native_task = getattr(resolver, "pending_list_task", None)
        if native_task is not None and not native_task.done() and native_task not in self.reads:
            self.reads.add(native_task)

            def read_completed(task: asyncio.Task[Any]) -> None:
                self.retain(resolver)
                self.reads.discard(task)

            native_task.add_done_callback(read_completed)

    async def wait(self, *, timeout: float) -> None:
        """Bound shutdown joins without cancelling native tasks or raw futures."""
        native_reads = [task for task in self.reads if not task.done()]
        native_results = await asyncio.gather(
            *(asyncio.wait_for(asyncio.shield(task), timeout=timeout) for task in native_reads),
            return_exceptions=True,
        )
        for result in native_results:
            if isinstance(result, (TimeoutError, asyncio.TimeoutError)):
                log.warning(
                    "Agent roster cache: native provider read still running at shutdown; "
                    "retaining ownership until its execution timeout or completion",
                )
            elif isinstance(result, BaseException):
                log.warning("Agent roster cache: native provider read ended with %r", result)
        samples = [future for future in self.samples if not future.done()]
        sample_results = await asyncio.gather(
            *(
                asyncio.wait_for(asyncio.shield(asyncio.wrap_future(future)), timeout=timeout)
                for future in samples
            ),
            return_exceptions=True,
        )
        for result in sample_results:
            if isinstance(result, (TimeoutError, asyncio.TimeoutError)):
                log.warning(
                    "Agent roster cache: native CPU sample still running at shutdown; "
                    "retaining ownership until completion",
                )
            elif isinstance(result, BaseException):
                log.warning("Agent roster cache: native CPU sample ended with %r", result)
