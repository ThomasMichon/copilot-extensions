"""Coordinator-owned waiter cadence, including one explicitly selected replay lane."""

from __future__ import annotations

import asyncio
import logging
import math
import time

from .run_waiter_recovery import (
    DEFAULT_RUN_WAITER_ARM_GRACE_SECONDS,
    maintain_delegated_recovery,
)

log = logging.getLogger("agent-dispatch.waiter-maintenance")


class WaiterMaintenance:
    """Keep remote IO off native recovery and retain one cycle until shutdown.

    A trusted replay factory receives the queue and a synchronous admission
    checkpoint for its ReplayConsumer. It owns authenticated scope/configuration,
    not scheduling. Nothing imports PR/Fleet unless that factory is selected.
    """

    def __init__(
        self, queue, recover, prepare_signal, *, gate, is_active=None,
        replay_factory=None, interval=60.0, clock=time.monotonic,
    ):
        if not math.isfinite(interval) or interval <= 0:
            raise ValueError("waiter maintenance interval must be finite and positive")
        self.queue, self.recover, self.prepare_signal = queue, recover, prepare_signal
        self.gate, self.is_active = gate, is_active
        self.interval, self.clock = interval, clock
        self._closed = False
        self._tasks = []
        self._replay_task = None
        self._next_due = 0.0
        self._failures = 0
        self.status = {"state": "disabled", "cycles": 0, "failures": 0, "last_result": None}
        self.consumer = None
        if replay_factory is not None:
            from .delegated_replay_consumer import ReplayConsumer

            checkpoint = self.check_current
            self.consumer = replay_factory(queue, checkpoint)
            if (
                not isinstance(self.consumer, ReplayConsumer) or self.consumer.queue is not queue
                or self.consumer._checkpoint is not checkpoint
            ):
                raise ValueError("replay factory must use this queue and admission checkpoint")
            self.status["state"] = "idle"

    def check_current(self):
        """Stop later work after known drain, retirement or authority loss."""
        if self._closed or self.gate.draining:
            raise ValueError("replay coordinator is draining")
        try:
            active = self.is_active is None or self.is_active()
        except Exception:
            raise ValueError("replay coordinator authority unavailable") from None
        if not active:
            raise ValueError("replay coordinator is not active")

    def start(self):
        """Start the existing startup and coalesced preparing-waiter owners."""
        if self._closed or self._tasks:
            raise RuntimeError("waiter maintenance already started or closed")
        self._tasks = [
            asyncio.create_task(self._startup()),
            asyncio.create_task(self._after_prepare()),
        ]

    async def _startup(self):
        await self.recover()
        await self.tick()
        await asyncio.sleep(DEFAULT_RUN_WAITER_ARM_GRACE_SECONDS)
        await self.recover()
        await self.tick()
        await maintain_delegated_recovery(
            self.queue, self.recover, interval=self.interval, on_tick=self.tick,
        )

    async def _after_prepare(self):
        while True:
            await self.prepare_signal.get()
            await asyncio.sleep(DEFAULT_RUN_WAITER_ARM_GRACE_SECONDS)
            await self.recover()

    async def tick(self):
        """Admit at most one finite remote cycle without delaying native recovery."""
        if (
            self.consumer is None or self._closed
            or self._replay_task is not None or self.clock() < self._next_due
        ):
            return
        try:
            self.check_current()
        except ValueError:
            self.status["state"] = "unavailable"
            return
        self.status["state"] = "running"
        self._replay_task = asyncio.create_task(self._cycle())
        self._replay_task.add_done_callback(self._completed)

    async def _cycle(self):
        with self.gate.track_claim():
            self.check_current()
            return await self.consumer.cycle()

    def _completed(self, task):
        self._replay_task = None
        if task.cancelled():
            return
        failure = task.exception()
        result = None if failure is not None else task.result()
        blocked = failure is not None or result["blocked"]
        self.status["cycles"] += 1
        self.status["last_result"] = result
        self._failures = min(self._failures + 1, 4) if blocked else 0
        if blocked:
            self.status["failures"] += 1
            log.warning("replay cycle unavailable; original waiter identities retained")
        self._next_due = self.clock() + min(900.0, self.interval * 2 ** self._failures)
        if not self._closed:
            self.status["state"] = "blocked" if blocked else "idle"

    async def close(self):
        """Close admission first, then cancel and await every owned coroutine."""
        self._closed = True
        self.status["state"] = "closed"
        tasks = [*self._tasks]
        if self._replay_task is not None:
            tasks.append(self._replay_task)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
