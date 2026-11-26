"""Compose native inbound SSH beneath the embedding application's lifetime.

The embedding owns durable installation/startup and the authenticated management
transport. This role owns only its listener, fixed reverse channel and periodic
public-membership catch-up; it never enrolls, installs outbound profiles or
turns process health into independent inbound authentication evidence.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from copy import deepcopy
from datetime import datetime, timezone
import math
import subprocess
from typing import Any, TypeVar

from ssh_manager.config_sources import SSHConfig
from ssh_manager.relay_channel import SupervisedRelayForward

from . import gateway_native, gateway_sshd

T = TypeVar("T")


async def _settle(task: asyncio.Future[T]) -> T:
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
    return task.result()


async def _local(call: Callable[..., T], *args: Any) -> T:
    task = asyncio.create_task(asyncio.to_thread(call, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            await _settle(task)
        except Exception:
            pass
        raise


class NativeReceiver:
    """One foreground receiving role for an existing durable application owner.

    ``observe`` must use the independently selected, authenticated Gateway
    management transport, with bounded output and cancellation cleanup.
    ``transport`` must authenticate that same Gateway using this receiver's
    existing local identity and strict host pin. A forwarding-only credential
    is not a management credential. Neither comes from synchronized code.

    Only Windows currently supplies attributed foreground listener custody.
    The embedding serializes this role against other versions and must await
    ``run`` through cleanup before reporting retirement or releasing its lease.
    """

    def __init__(
        self, config: dict[str, Any], transport: SSHConfig, *,
        observe: Callable[[], Awaitable[gateway_native.Observation]],
        refresh_interval: float = 30,
        observation_timeout: float = 30,
        listener_timeout: float = 10,
    ) -> None:
        if type(config.get("version")) is not int or config["version"] != 2:
            raise ValueError("Native receiver requires the selected native ingress contract")
        for value in (refresh_interval, observation_timeout, listener_timeout):
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= 300:
                raise ValueError("Native receiver requires finite positive cadence and deadlines")
        self.config = deepcopy(config)
        self.descriptor = gateway_native._descriptor(self.config["native_gateway"])
        if self.descriptor["machine"] != self.config["machine"]:
            raise ValueError("Native receiver differs from the selected Gateway target")
        self.observe = observe
        self.refresh_interval = refresh_interval
        self.observation_timeout = observation_timeout
        self.listener_timeout = listener_timeout
        self.listener: gateway_sshd.ForegroundIngress | None = None
        self.forward = SupervisedRelayForward(
            transport, self.descriptor["relay_port"], remote_bind_host="127.0.0.1",
            host_port=self.config["port"],
        )
        self.membership_current = False
        self.membership_observed_at: str | None = None
        self.started = asyncio.Event()
        self.running = False
        self._serving = False
        self._closing = False

    async def _observation(self) -> gateway_native.Observation:
        result = await asyncio.wait_for(self.observe(), self.observation_timeout)
        if not isinstance(result, gateway_native.Observation):
            raise ValueError("Native receiver requires an authenticated public observation")
        return result.validated(self.descriptor)

    def _accepted(self) -> None:
        self.membership_current = True
        self.membership_observed_at = datetime.now(timezone.utc).isoformat()

    async def _start_listener(self, initial: gateway_native.Observation) -> None:
        task = asyncio.create_task(asyncio.to_thread(
            gateway_sshd.start, self.config, update=initial,
        ))
        try:
            self.listener = await asyncio.shield(task)
        except asyncio.CancelledError:
            # A native spawn already in progress must return custody before
            # cancellation can enter the common retirement path.
            listener = await _settle(task)
            self.listener = listener
            raise

    async def _serve(self) -> None:
        initial = await self._observation()
        await self._start_listener(initial)
        assert self.listener is not None
        self._accepted()
        deadline = asyncio.get_running_loop().time() + self.listener_timeout
        while not await _local(self.listener.ready):
            if self.listener.process.poll() is not None:
                raise RuntimeError("Native receiver listener exited")
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError("Native receiver listener attribution was not confirmed")
            await asyncio.sleep(0.1)
        start = asyncio.create_task(self.forward.start())
        try:
            await asyncio.shield(start)
        except asyncio.CancelledError:
            start.cancel()
            try:
                await _settle(start)
            except asyncio.CancelledError:
                pass
            raise
        self.started.set()
        next_refresh = asyncio.get_running_loop().time() + self.refresh_interval
        while True:
            if not await _local(self.listener.ready):
                raise RuntimeError("Native receiver lost its attributed listener")
            if asyncio.get_running_loop().time() >= next_refresh:
                try:
                    update = await self._observation()
                    await _local(gateway_sshd.prepare_update, self.config, update)
                except (OSError, ValueError, RuntimeError, TimeoutError,
                        asyncio.TimeoutError, subprocess.TimeoutExpired):
                    self.membership_current = False
                else:
                    self._accepted()
                next_refresh = asyncio.get_running_loop().time() + self.refresh_interval
            await asyncio.sleep(min(1, self.refresh_interval))

    async def _close(self) -> None:
        try:
            await self.forward.stop()
        finally:
            if self.listener is not None:
                await _local(self.listener.close)
                self.listener = None

    async def close(self) -> None:
        """Reconcile retained cleanup after failure; stop an active run via its event."""
        if self._serving or self._closing:
            raise RuntimeError("Native receiver still owns an active operation")
        self._closing = True
        try:
            await _settle(asyncio.create_task(self._close()))
            self.running = False
        finally:
            self._closing = False

    async def run(self, stop: asyncio.Event) -> None:
        """Serve until stopped or failed, retaining native custody through cleanup."""
        if self.running or self._closing:
            raise RuntimeError("Native receiver is already running")
        if stop.is_set():
            return
        self.running = True
        self._serving = True
        self.started.clear()
        self.membership_current = False
        worker = asyncio.create_task(self._serve())
        stopping = asyncio.create_task(stop.wait())
        try:
            done, _ = await asyncio.wait({worker, stopping}, return_when=asyncio.FIRST_COMPLETED)
            if worker in done:
                await worker
        finally:
            worker.cancel()
            stopping.cancel()
            # Join work and any in-flight native publication before children
            # retire. A second cancellation cannot release the outer owner early.
            joined = asyncio.ensure_future(asyncio.gather(worker, stopping, return_exceptions=True))
            results = await _settle(joined)
            self._serving = False
            self.started.clear()
            self.membership_current = False
            await self.close()
            for result in results:
                if isinstance(result, Exception):
                    raise result
