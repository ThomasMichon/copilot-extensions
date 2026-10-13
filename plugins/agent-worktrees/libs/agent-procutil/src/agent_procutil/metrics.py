"""Read-only, identity-pinned CPU lower bounds for one caller-owned tree."""

from __future__ import annotations

import asyncio
import importlib
import logging
import math
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass
from typing import Literal, Protocol, cast

log = logging.getLogger(__name__)


class _CpuTimes(Protocol):
    user: float
    system: float


class _SampleProcess(Protocol):
    pid: int

    def create_time(self) -> float: ...
    def ppid(self) -> int: ...
    def cpu_times(self) -> _CpuTimes: ...
    def children(self, *, recursive: bool) -> list[_SampleProcess]: ...


class _Backend(Protocol):
    AccessDenied: type[Exception]
    NoSuchProcess: type[Exception]
    ZombieProcess: type[Exception]

    def Process(self, pid: int) -> _SampleProcess: ...

@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    created_at: float | None


@dataclass(frozen=True)
class CpuIssue:
    pid: int
    reason: str


@dataclass(frozen=True)
class CpuObservation:
    observed_cpu_seconds: float | None
    identities: tuple[ProcessIdentity, ...]
    issues: tuple[CpuIssue, ...]
    truncated: bool
    coverage: Literal["observed_lower_bound"] = "observed_lower_bound"


@dataclass
class _Record:
    identity: ProcessIdentity
    maximum_cpu: float | None = None


def _load_backend() -> _Backend:
    return cast(_Backend, importlib.import_module("psutil"))


class OwnedProcessCpuSampler:
    """One optional sample holder, no resident loop and no termination API.

    The caller must supply the root identity captured at its own spawn seam.
    A completed sample is reused for one second. Cancellation detaches only
    that caller; the native sampling thread remains the single-flight holder.
    """

    def __init__(
        self, root: ProcessIdentity, *, max_identities: int = 128,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if root.pid <= 0:
            raise ValueError("root PID must be positive")
        if root.created_at is not None and (
            not math.isfinite(root.created_at) or root.created_at <= 0
        ):
            raise ValueError("root creation time must be finite and positive")
        if not 1 <= max_identities <= 128:
            raise ValueError("identity cap must be between 1 and 128")
        self._root = root
        self._cap = max_identities
        self._clock = clock
        self._records = {root.pid: _Record(root)}
        self._truncated = False
        self._last: CpuObservation | None = None
        self._last_at: float | None = None
        self._pending: Future[CpuObservation] | None = None
        self._lock = threading.Lock()

    @property
    def pending_sample_future(self) -> Future[CpuObservation] | None:
        with self._lock:
            pending = self._pending
            return pending if pending is not None and not pending.done() else None

    async def sample(self) -> CpuObservation:
        now = self._clock()
        start_worker = False
        with self._lock:
            pending = self._pending
            if pending is None or pending.done():
                if (
                    self._last is not None and self._last_at is not None
                    and now - self._last_at < 1.0
                ):
                    return self._last
                pending = Future()
                pending.set_running_or_notify_cancel()
                self._pending = pending
                start_worker = True
        if start_worker:
            try:
                worker = threading.Thread(
                    target=self._run_sample, args=(pending,), name="owned-cpu-sample", daemon=True,
                )
                worker.start()
            except RuntimeError as error:
                pending.set_exception(error)
                log.error("Owned CPU sampling worker could not start", exc_info=error)
        return await asyncio.shield(asyncio.wrap_future(pending))

    def _run_sample(self, pending: Future[CpuObservation]) -> None:
        try:
            observation = self._sample_sync()
            completed_at = self._clock()
            with self._lock:
                self._last = observation
                self._last_at = completed_at
        except BaseException as error:
            pending.set_exception(error)
            log.error("Owned CPU sample failed", exc_info=error)
        else:
            pending.set_result(observation)

    def _observation(self, issues: list[CpuIssue]) -> CpuObservation:
        values = [
            record.maximum_cpu for record in self._records.values()
            if record.maximum_cpu is not None
        ]
        return CpuObservation(
            observed_cpu_seconds=sum(values) if values else None,
            identities=tuple(record.identity for record in self._records.values()),
            issues=tuple(issues), truncated=self._truncated,
        )

    def _sample_sync(self) -> CpuObservation:
        if self._root.created_at is None:
            return self._observation([CpuIssue(self._root.pid, "root_identity_unavailable")])
        try:
            backend = _load_backend()
        except ModuleNotFoundError as error:
            if error.name != "psutil":
                raise
            return self._observation([CpuIssue(self._root.pid, "metrics_unavailable")])

        issues: list[CpuIssue] = []
        queue = list(self._records)
        visited: set[int] = set()
        discovery_attempts = 0
        while queue:
            pid = queue.pop(0)
            if pid in visited:
                continue
            visited.add(pid)
            record = self._records[pid]
            expected = record.identity.created_at
            try:
                process = backend.Process(pid)
                if process.create_time() != expected:
                    issues.append(CpuIssue(pid, "identity_mismatch"))
                    continue
                times = process.cpu_times()
                cpu = float(times.user + times.system)
                children = process.children(recursive=False)
                # A fresh object is required: psutil caches create_time on
                # an existing Process instance, which cannot detect reuse.
                if backend.Process(pid).create_time() != expected:
                    issues.append(CpuIssue(pid, "identity_mismatch"))
                    continue
            except backend.AccessDenied:
                issues.append(CpuIssue(pid, "access_denied"))
                continue
            except (backend.NoSuchProcess, backend.ZombieProcess):
                issues.append(CpuIssue(pid, "process_unavailable"))
                continue
            if not math.isfinite(cpu) or cpu < 0:
                issues.append(CpuIssue(pid, "invalid_cpu_counter"))
            elif record.maximum_cpu is not None and cpu < record.maximum_cpu:
                issues.append(CpuIssue(pid, "cpu_counter_regressed"))
            else:
                record.maximum_cpu = cpu
            for child in children:
                if discovery_attempts >= self._cap:
                    self._truncated = True
                    break
                discovery_attempts += 1
                if child.pid in self._records:
                    continue
                if len(self._records) >= self._cap:
                    self._truncated = True
                    break
                try:
                    created_at = child.create_time()
                    if (
                        child.ppid() != pid
                        or backend.Process(child.pid).create_time() != created_at
                        or backend.Process(pid).create_time() != expected
                    ):
                        issues.append(CpuIssue(child.pid, "descendant_identity_unavailable"))
                        continue
                except backend.AccessDenied:
                    issues.append(CpuIssue(child.pid, "access_denied"))
                    continue
                except (backend.NoSuchProcess, backend.ZombieProcess):
                    issues.append(CpuIssue(child.pid, "process_unavailable"))
                    continue
                if (
                    not math.isfinite(created_at)
                    or created_at < self._root.created_at
                ):
                    issues.append(CpuIssue(child.pid, "descendant_identity_unavailable"))
                    continue
                identity = ProcessIdentity(child.pid, created_at)
                self._records[child.pid] = _Record(identity)
                queue.append(child.pid)
        return self._observation(issues)
