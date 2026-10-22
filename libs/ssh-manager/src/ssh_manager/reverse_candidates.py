"""Finite reverse forwarding through the existing SSH process/supervisor owner.

The observer is supplied by the trusted transport integration. It must verify
the actual attempt, not merely connect to a possibly foreign listening port.
This module neither authenticates enrollment nor publishes fleet discovery.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
import math
import logging
import os
import uuid
from typing import Literal

from .config_sources import SSHConfig
from .forward import build_forward_ssh_args
from .proxy import create_ssh_subprocess
from .relay_channel import (
    SupervisedRelayForward, _FORWARD_DIAGNOSTIC_TAIL, _OWNER_ENV, _VenueStopped,
)

MAX_CANDIDATES = 16
_DIAGNOSTIC_READS = 16
log = logging.getLogger("ssh-manager.reverse-candidates")


@dataclass(frozen=True)
class ReverseEndpoint:
    host: str
    port: int

    def __post_init__(self) -> None:
        if self.host not in ("127.0.0.1", "::1", "localhost"):
            raise ValueError("reverse endpoint must be an exact loopback literal")
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ValueError("reverse endpoint requires a positive TCP port")

    def specification(self, target_port: int) -> str:
        host = "[::1]" if self.host == "::1" else self.host
        return f"{host}:{self.port}:127.0.0.1:{target_port}"


@dataclass(frozen=True)
class ReverseAttempt:
    request_id: str
    attempt_id: str
    endpoint: ReverseEndpoint
    target_port: int
    process_pid: int
    process_settled: bool


@dataclass(frozen=True)
class ReverseEvidence:
    attempt_id: str
    state: Literal["ready", "occupied", "refused", "unknown"]
    reason: str = "unclassified"

    def __post_init__(self) -> None:
        if self.state not in ("ready", "occupied", "refused", "unknown"):
            raise ValueError("invalid reverse evidence state")
        if self.reason not in (
            "unclassified", "collision", "authentication", "host-key", "permission",
            "transport", "readiness",
        ):
            raise ValueError("invalid reverse evidence reason")


@dataclass(frozen=True)
class ReverseStatus:
    request_id: str
    attempt: ReverseAttempt | None
    state: Literal["ready", "unavailable", "refused", "unknown", "stopped"]
    reason: str


class ReverseForwardError(ConnectionError):
    def __init__(self, status: ReverseStatus) -> None:
        self.status = status
        super().__init__(f"reverse forward {status.state}: {status.reason}")


class FiniteReverseForward(SupervisedRelayForward):
    """Opt-in finite selection; fixed-port SupervisedRelayForward is unchanged.

    ``observe`` must bind authenticated server-side evidence to the supplied
    attempt. ``occupied`` is actionable only after SSH refused that forward,
    and must distinguish an existing listener from a permission/auth failure.
    Unknown evidence latches this instance: neither establish nor its existing
    monitor allocates another resource. The caller must reconcile the original
    correlation through its own authority, not create a new request to retry.
    """

    def __init__(
        self,
        config: SSHConfig,
        candidates: tuple[ReverseEndpoint, ...],
        *,
        request_id: str,
        observe: Callable[[ReverseAttempt], Awaitable[ReverseEvidence]],
        ready_timeout: float = 40.0,
        monitor_interval: float = 15.0,
        host_port_resolver: Callable[[], int] | None = None,
        reconnect_gate: Callable[[], Awaitable[bool]] | None = None,
    ) -> None:
        if (type(candidates) is not tuple or not 1 <= len(candidates) <= MAX_CANDIDATES
                or any(not isinstance(c, ReverseEndpoint) for c in candidates)
                or len(set(candidates)) != len(candidates)):
            raise ValueError("a bounded unique tuple of reverse endpoints is required")
        if (not isinstance(request_id, str) or not 1 <= len(request_id) <= 128
                or any(ord(c) < 33 or ord(c) > 126 for c in request_id)):
            raise ValueError("a bounded printable operation correlation is required")
        if not callable(observe):
            raise ValueError("candidate-specific serving observer is required")
        if (type(ready_timeout) not in (int, float) or not math.isfinite(ready_timeout)
                or not 0 < ready_timeout <= 300):
            raise ValueError("reverse operation timeout must be finite and bounded")
        if (type(monitor_interval) not in (int, float) or not math.isfinite(monitor_interval)
                or monitor_interval <= 0):
            raise ValueError("reverse monitor interval must be positive and finite")
        if ((host_port_resolver is not None and not callable(host_port_resolver))
                or (reconnect_gate is not None and not callable(reconnect_gate))):
            raise ValueError("reverse owner hooks must be callable")
        super().__init__(
            config, candidates[0].port, ready_timeout=ready_timeout,
            monitor_interval=monitor_interval, host_port_resolver=host_port_resolver,
            reconnect_gate=reconnect_gate,
        )
        self._candidates = candidates
        self._default_target_port = candidates[0].port
        self._request_id = request_id
        self._observe_candidate = observe
        self._selection_lock = asyncio.Lock()
        self._lifecycle_task: asyncio.Task | None = None
        self._lifecycle_released = asyncio.Event()
        self._lifecycle_released.set()
        self._stop_requests = 0
        self._shutdown_future: asyncio.Future | None = None
        self._retiring_process = None
        self._retirement_task: asyncio.Task | None = None
        self._status = ReverseStatus(request_id, None, "unavailable", "not-started")

    @property
    def reverse_status(self) -> ReverseStatus:
        if self._status.state == "ready" and not self.is_alive:
            return replace(self._status, state="unavailable", reason="process-exited")
        return self._status

    def _fail(self, state, reason, attempt=None) -> ReverseForwardError:
        self._status = ReverseStatus(self._request_id, attempt, state, reason)
        return ReverseForwardError(self._status)

    def _resolve_host_port(self) -> int:
        if self._host_port_resolver is None:
            return self._default_target_port
        try:
            value = self._host_port_resolver()
        except Exception:
            raise ValueError("reverse target-port resolver unavailable") from None
        if type(value) is not int or not 1 <= value <= 65535:
            raise ValueError("reverse target-port resolver returned an invalid port")
        return value

    async def _reconnect_allowed(self, *, timeout: float | None = None) -> bool | None:
        if self._stopped:
            return False
        gate = self._reconnect_gate
        if gate is None:
            return True
        budget = self._ready_timeout if timeout is None else min(timeout, self._ready_timeout)
        try:
            value = await asyncio.wait_for(gate(), max(0.0, budget))
        except asyncio.CancelledError:
            raise
        except Exception:
            log.warning("Reverse reconnect gate inconclusive; no connection attempted")
            return None
        if self._stopped:
            return False
        if type(value) is not bool:
            log.warning("Reverse reconnect gate returned an invalid result; no connection attempted")
            return None
        return value

    @asynccontextmanager
    async def _lifecycle(self):
        task = asyncio.current_task()
        if self._lifecycle_task is task:
            yield
            return
        if self._selection_lock.locked():
            raise ReverseForwardError(ReverseStatus(
                self._request_id, None, "unavailable", "lifecycle-busy",
            ))
        await self._selection_lock.acquire()
        self._lifecycle_released.clear()
        self._lifecycle_task = task
        try:
            yield
        finally:
            self._lifecycle_task = None
            self._selection_lock.release()
            self._lifecycle_released.set()

    async def _spawn_owned(self, args: list[str], remaining: float):
        pending = asyncio.create_task(create_ssh_subprocess(
            *args, config=self._config,
            env={**os.environ, _OWNER_ENV: self._owner_token},
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        ))
        try:
            return await asyncio.wait_for(asyncio.shield(pending), remaining)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            self._status = ReverseStatus(
                self._request_id, None, "unknown", "interrupted-spawn",
            )
            cleanup = asyncio.create_task(self._retire_pending_spawn(pending))
            self._cleanup_tasks.add(cleanup)
            cleanup.add_done_callback(self._cleanup_tasks.discard)
            try:
                await asyncio.wait_for(asyncio.shield(cleanup), 5)
            except asyncio.TimeoutError:
                log.warning("Reverse forward spawn cleanup remains pending")
            raise

    async def _retire_pending_spawn(self, pending: asyncio.Task) -> None:
        try:
            proc = await pending
        except Exception:
            log.warning("Interrupted reverse spawn failed before ownership was returned")
            return
        self._proc = proc
        try:
            await self._retire_process()
        except ReverseForwardError:
            log.error("Interrupted reverse spawn retains uncleaned process custody")

    def _ensure_cleanup_settled(self) -> None:
        if any(not task.done() for task in self._cleanup_tasks):
            raise ReverseForwardError(ReverseStatus(
                self._request_id, self._status.attempt, "unavailable", "cleanup-pending",
            ))

    async def establish(self) -> None:
        async with self._lifecycle():
            if self._status.state == "unknown":
                raise ReverseForwardError(self._status)
            self._ensure_cleanup_settled()
            if self._stopped:
                raise self._fail("stopped", "owner-stopped", self._status.attempt)
            if self.is_alive:
                return
            await self._retire_process()
            loop = asyncio.get_running_loop()
            deadline = loop.time() + self._ready_timeout
            try:
                host_port = self._resolve_host_port()
            except ValueError:
                raise self._fail("refused", "target-port-unavailable") from None
            for endpoint in self._candidates:
                if self._stopped:
                    raise self._fail("stopped", "owner-stopped")
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise self._fail("unavailable", "operation-deadline")
                if self._gated_establish:
                    allowed = await self._reconnect_allowed(timeout=remaining)
                    if allowed is not True:
                        if allowed is False:
                            raise _VenueStopped("reverse reconnect gate stopped")
                        raise ConnectionError("reverse reconnect gate inconclusive")
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise self._fail("unavailable", "operation-deadline")
                args = build_forward_ssh_args(
                    self._config, None, None,
                    reverse_forwards=[endpoint.specification(host_port)],
                    exit_on_forward_failure=True,
                )
                try:
                    proc = await self._spawn_owned(args, remaining)
                except (OSError, asyncio.TimeoutError):
                    raise self._fail("unknown", "spawn-outcome") from None
                self._proc = proc
                self._notify_pid_change()
                if self._stopped:
                    await self._retire_process()
                    raise self._fail("stopped", "owner-stopped")
                attempt = ReverseAttempt(
                    self._request_id, uuid.uuid4().hex, endpoint, host_port, proc.pid, False,
                )
                try:
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        raise asyncio.TimeoutError
                    settled = await asyncio.wait_for(self._wait_settled(proc), remaining)
                    attempt = replace(attempt, process_settled=settled.ready)
                    if self._stopped or self._proc is not proc:
                        raise self._fail("stopped", "owner-stopped", attempt)
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        raise asyncio.TimeoutError
                    try:
                        evidence = await asyncio.wait_for(self._observe_candidate(attempt), remaining)
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        raise self._fail("unknown", "observer-outcome", attempt) from None
                    if (not isinstance(evidence, ReverseEvidence)
                            or evidence.attempt_id != attempt.attempt_id):
                        raise self._fail("unknown", "uncorrelated-evidence", attempt)
                    if self._stopped or self._proc is not proc:
                        raise self._fail("stopped", "owner-stopped", attempt)
                    if (evidence.state == "ready" and settled.ready
                            and proc.returncode is None and loop.time() < deadline):
                        self._relay_port = endpoint.port
                        self._established_host_port = host_port
                        self._status = ReverseStatus(
                            self._request_id, attempt, "ready", "owner-verified",
                        )
                        self._notify_pid_change()
                        return
                    forward_failed = settled.remote_forward_failed
                    if (evidence.state == "occupied" and not forward_failed
                            and loop.time() < deadline):
                        diagnostic = (settled.raw_tail or settled.stderr)[-_FORWARD_DIAGNOSTIC_TAIL:]
                        for _ in range(_DIAGNOSTIC_READS):
                            if self._remote_forward_failed(diagnostic):
                                forward_failed = True
                                break
                            remaining = deadline - loop.time()
                            if remaining <= 0:
                                break
                            chunk = await self._read_stderr_available(
                                proc, timeout=min(0.01, remaining),
                            )
                            if not chunk:
                                break
                            combined = diagnostic + chunk
                            if self._remote_forward_failed(combined):
                                forward_failed = True
                                break
                            diagnostic = combined[-_FORWARD_DIAGNOSTIC_TAIL:]
                    if evidence.state == "occupied" and forward_failed:
                        await self._retire_process()
                        continue
                    state = "refused" if evidence.state == "refused" else "unknown"
                    raise self._fail(state, evidence.reason, attempt)
                except asyncio.CancelledError:
                    self._status = ReverseStatus(
                        self._request_id, attempt, "unknown", "interrupted-attempt",
                    )
                    await self._retire_process()
                    raise
                except ReverseForwardError:
                    await self._retire_process()
                    raise
                except Exception:
                    error = self._fail("unknown", "observer-outcome", attempt)
                    await self._retire_process()
                    raise error from None
            raise self._fail("unavailable", "candidates-exhausted")

    def _retry_establish_failure(self, exc: Exception) -> bool:
        return not isinstance(exc, ReverseForwardError)

    async def _restart_with_backoff(self, reason: str) -> bool:
        async with self._lifecycle():
            return await super()._restart_with_backoff(reason)

    async def start(self) -> None:
        async with self._lifecycle():
            self._ensure_cleanup_settled()
            if self._stop_requests:
                raise ReverseForwardError(ReverseStatus(
                    self._request_id, None, "stopped", "owner-stopping",
                ))
            await super().start()

    async def _cancel_process(self) -> None:
        async with self._lifecycle():
            await self._retire_process()

    async def _retire_process(self) -> None:
        if self._status.state == "ready":
            self._status = replace(self._status, state="unavailable", reason="transport-retired")
        proc = self._proc
        if proc is None:
            return
        cleanup = self._queue_retirement(proc)
        try:
            retired = await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            self._status = replace(self._status, state="unknown", reason="interrupted-cleanup")
            raise
        if not retired:
            raise ReverseForwardError(self._status)

    def _queue_retirement(self, proc) -> asyncio.Task:
        task = self._retirement_task
        if task is not None and not task.done():
            if self._retiring_process is not proc:
                raise self._fail("unknown", "cleanup-custody-conflict")
            return task
        task = asyncio.create_task(self._retire_captured(proc))
        self._retiring_process = proc
        self._retirement_task = task
        self._cleanup_tasks.add(task)
        task.add_done_callback(self._cleanup_tasks.discard)
        return task

    async def _retire_captured(self, proc) -> bool:
        try:
            await self._terminate(proc)
        except asyncio.CancelledError:
            self._status = replace(self._status, state="unknown", reason="interrupted-cleanup")
            log.error("Reverse forward process cleanup was interrupted; custody retained")
            return False
        except Exception:
            self._status = replace(self._status, state="unknown", reason="cleanup-failed")
            log.error("Reverse forward owned process cleanup failed")
            return False
        if proc.returncode is None:
            self._status = replace(self._status, state="unknown", reason="cleanup-unconfirmed")
            log.error("Reverse forward termination is unconfirmed; process custody retained")
            return False
        if self._proc is proc:
            self._proc = None
            self._notify_pid_change()
        return True

    async def stop(self) -> None:
        previous = self._shutdown_future
        if previous is not None and not previous.done():
            await asyncio.shield(previous)
            return
        completed = asyncio.get_running_loop().create_future()
        completed.add_done_callback(
            lambda future: None if future.cancelled() else future.exception(),
        )
        self._shutdown_future = completed
        self._stop_requests += 1
        try:
            self._stopped = True
            active = self._lifecycle_task
            if active is not None and active is not asyncio.current_task():
                active.cancel()
                await self._lifecycle_released.wait()
            async with self._lifecycle():
                await super().stop()
                if self._status.state != "unknown":
                    self._status = replace(self._status, state="stopped", reason="owner-stopped")
        except BaseException as exc:
            completed.set_exception(exc)
            raise
        else:
            completed.set_result(None)
        finally:
            self._stop_requests -= 1

    async def stop_attempt(self, attempt_id: str) -> ReverseStatus | None:
        """Retire only the matching owned attempt; stale release is inert.

        This fences local transport cleanup, not publication or enrollment.
        A returned unknown status remains unsettled after owned cleanup.
        """
        shutdown = self._shutdown_future
        if shutdown is not None and not shutdown.done():
            await asyncio.shield(shutdown)
            async with self._lifecycle():
                attempt = self._status.attempt
                if attempt is None or attempt.attempt_id != attempt_id:
                    return None
                return self.reverse_status
        async with self._lifecycle():
            attempt = self._status.attempt
            if attempt is None or attempt.attempt_id != attempt_id:
                return None
            await self.stop()
            return self.reverse_status

    def stop_nowait(self) -> None:
        if self._shutdown_future is not None and not self._shutdown_future.done():
            self._stopped = True
            return
        if self._status.state == "ready":
            self._status = replace(self._status, state="stopped", reason="owner-stopped")
        self._stopped = True
        monitor = self._monitor_task
        self._monitor_task = None
        if monitor is not None and not monitor.done():
            monitor.cancel()
        active = self._lifecycle_task
        try:
            current = asyncio.current_task()
        except RuntimeError:
            current = None
        if active is not None and active is not current:
            active.cancel()
        proc = self._proc
        if proc is not None:
            self._kill_tree_nowait(proc)
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                self._status = replace(self._status, state="unknown", reason="cleanup-unavailable")
                log.warning("Reverse forward cleanup requires its asynchronous owner")
            else:
                self._queue_retirement(proc)
