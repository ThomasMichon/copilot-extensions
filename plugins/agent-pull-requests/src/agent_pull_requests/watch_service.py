"""Owned foreground PR service lifecycle over the existing serving/observer core."""

from __future__ import annotations

import asyncio
import os
import threading
from collections.abc import Awaitable, Callable
from typing import Any

from fleet_manager.gateway_service_connector import ServiceConnector
from fleet_manager.gateway_service_handler import RequestDisconnected
from single_instance_lease import OperationLockedError, SingleInstance

from .watch_gateway import WatchGateway, WatchScopeBinding
from .watch_notification import positive_seconds
from .watch_observer import NativeObservationBinding, ReplayObserver
from .watch_replay_store import ReplayStore


class WatchService:
    """One host-local writer, connector and bounded, non-overlapping observation.

    The embedding placement owner supplies current authority and credentials.
    This class does not enroll, provision storage, activate a candidate, transfer
    a volume or fence another host. A timed-out/cancelled drain keeps the lease;
    retry ``close`` on this same instance before attempting a replacement.
    """

    def __init__(
        self, store: ReplayStore, *, fleet: str, gateway: str, slot: str,
        bindings: tuple[WatchScopeBinding, ...],
        observations: tuple[NativeObservationBinding, ...],
        gateway_origin: str, access_token_callback: Callable[[], Awaitable[str]],
        proof_key: Any, authority: Callable[[], int], checkpoint: Callable[[], None],
        gateway_ca: str | None = None, interval: float = 5,
    ) -> None:
        if not callable(authority) or not callable(checkpoint):
            raise ValueError("current service authority is required")
        self.store = store
        self.interval = positive_seconds(interval)
        self._authority, self._checkpoint = authority, checkpoint
        self._lease = SingleInstance(store.root, service="agent-pull-requests-watch")
        self._pid = os.getpid()
        self._stopping = threading.Event()
        self._stop = asyncio.Event()
        self._closing = asyncio.Lock()
        self._started = False
        self._closed = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._last_cycle: dict | None = None
        self._disk: asyncio.Task | None = None
        self._tasks: tuple[asyncio.Task, ...] = ()
        self.gateway = WatchGateway(
            store, fleet=fleet, gateway=gateway, slot=slot, bindings=bindings,
            checkpoint=self._check,
        )
        self.observer = ReplayObserver(store, bindings=observations)
        if any(
            (scope.account, scope.repo) not in self.observer.bindings
            for scope in self.gateway.bindings.values()
        ):
            raise ValueError("serving scope has no native observation binding")
        self.connector = ServiceConnector(
            slot=slot, gateway_origin=gateway_origin,
            access_token_callback=access_token_callback, proof_key=proof_key,
            gateway_ca=gateway_ca, handler=self.gateway.handler_binding(),
        )

    def _check(self) -> None:
        if os.getpid() != self._pid or not self._lease.held or self._stopping.is_set():
            raise RequestDisconnected()
        try:
            self.store.check_current(self._authority(), self._checkpoint)
        except Exception:
            # Loss is not evidence that an already-started durable write failed.
            raise RequestDisconnected() from None

    async def _observe(self) -> None:
        while not self._stop.is_set():
            self._check()
            self._disk = asyncio.create_task(asyncio.to_thread(
                self.observer.cycle, expires_at=self._authority(), checkpoint=self._check,
            ))
            try:
                self._last_cycle = await asyncio.shield(self._disk)
            except OperationLockedError:
                self._last_cycle = None
            except RequestDisconnected:
                if self._stop.is_set():
                    return
                raise
            if self._last_cycle is not None and self._last_cycle["status"] != "observed":
                raise RuntimeError("watch observation outcome uncertain")
            try:
                await asyncio.wait_for(self._stop.wait(), self.interval)
            except asyncio.TimeoutError:
                pass

    def _withdraw(self) -> None:
        self._stopping.set()
        self.gateway.stop_admission()
        self._stop.set()

    def _begin_start(self) -> None:
        if self._started or self._closed:
            raise RuntimeError("watch service instance already used")
        if os.getpid() != self._pid:
            raise RuntimeError("watch service belongs to another process")
        self._loop = asyncio.get_running_loop()
        self._lease.acquire()
        self._started = True

    async def _adopt(self, stop: asyncio.Event | None = None) -> None:
        try:
            if stop is not None and stop.is_set():
                self._withdraw()
                return
            if (self.store.root / "watch-subscriptions.json").exists():
                raise ValueError("native and replay state roots must be separate")
            self._check()
            self._disk = asyncio.create_task(asyncio.to_thread(
                self.store.observation_targets,
                expires_at=self._authority(), checkpoint=self._check,
            ))
            await asyncio.shield(self._disk)
            if stop is not None and stop.is_set():
                self._withdraw()
                return
            self._check()
        except BaseException:
            self._withdraw()
            if self._disk is None or self._disk.done():
                if self._disk is not None:
                    await asyncio.gather(self._disk, return_exceptions=True)
                self._lease.release()
                self._closed = True
            raise
        self._tasks = (
            asyncio.create_task(self.connector.run(self._stop)),
            asyncio.create_task(self._observe()),
        )

    async def start(self) -> None:
        """Adopt existing state before starting either owned unit; never create it."""
        self._begin_start()
        await self._adopt()

    def status(self) -> dict:
        """Report local readiness, not placement, provider or activation evidence."""
        current = True
        try:
            self._check()
        except RequestDisconnected:
            current = False
        cycle = self._last_cycle
        ready = bool(
            self._started and not self._closed and current
            and self.connector.connected.is_set()
            and all(not task.done() for task in self._tasks)
            and cycle is not None and cycle["status"] == "observed"
            and cycle["unavailable"] == 0 and cycle["unbound"] == 0
        )
        return {
            "ready": ready, "writer_held": self._lease.held,
            "draining": self._stopping.is_set(), "closed": self._closed,
        }

    async def close(self, *, timeout: float) -> None:
        """Stop later work and join actual disk/native work before releasing custody."""
        timeout = positive_seconds(timeout)
        if os.getpid() != self._pid or (
            self._loop is not None and asyncio.get_running_loop() is not self._loop
        ):
            raise RuntimeError("watch service belongs to another execution owner")
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        self._withdraw()
        acquisition = asyncio.create_task(self._closing.acquire())
        try:
            await asyncio.wait_for(acquisition, timeout=timeout)
        except BaseException:
            if acquisition.done() and not acquisition.cancelled() and (
                acquisition.exception() is None and acquisition.result()
            ):
                self._closing.release()
            raise
        drain = None
        try:
            tasks = set(self._tasks)
            if self._disk is not None:
                tasks.add(self._disk)
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TimeoutError("watch service is not drained")
            drain = asyncio.create_task(self.gateway.drain(timeout=remaining))
            tasks.add(drain)
            _, pending = await asyncio.wait(tasks, timeout=max(0, deadline - loop.time()))
            if pending:
                raise TimeoutError("watch service is not drained")
            # Consume failures after every unit settled; none permits early release.
            await asyncio.gather(*tasks, return_exceptions=True)
            await drain
            self._lease.release()
            self._closed = True
        finally:
            # The gateway retains its disk-task set; don't leak a drain waiter.
            try:
                if drain is not None:
                    if not drain.done():
                        drain.cancel()
                    await asyncio.gather(drain, return_exceptions=True)
            finally:
                self._closing.release()

    async def run(self, stop: asyncio.Event, *, drain_timeout: float = 60) -> None:
        """Foreground entry for the existing placement/supervision owner."""
        drain_timeout = positive_seconds(drain_timeout)
        if self._started or self._closed:
            raise RuntimeError("watch service instance already used")
        if stop.is_set():
            return
        # Reserve ownership before yielding to either startup or another caller.
        self._begin_start()
        startup = asyncio.create_task(self._adopt(stop))
        stopped = asyncio.create_task(stop.wait())
        try:
            await asyncio.wait({startup, stopped}, return_when=asyncio.FIRST_COMPLETED)
            if stop.is_set():
                return
            await startup
            done, _ = await asyncio.wait(
                {*self._tasks, stopped}, return_when=asyncio.FIRST_COMPLETED,
            )
            if stopped not in done:
                for task in done:
                    await task
                raise RuntimeError("watch service unit stopped")
        finally:
            self._withdraw()
            loop = asyncio.get_running_loop()
            deadline = loop.time() + drain_timeout
            for task in (startup, stopped):
                if not task.done():
                    task.cancel()
            done, pending = await asyncio.wait({startup, stopped}, timeout=drain_timeout)
            await asyncio.gather(*done, return_exceptions=True)
            remaining = deadline - loop.time()
            if pending or remaining <= 0:
                raise TimeoutError("watch service is not drained")
            await self.close(timeout=remaining)
