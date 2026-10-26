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
from dataclasses import dataclass, replace
from typing import Literal, Protocol, cast
from uuid import uuid4

from .metrics_accounting import SampleAccounting, _read_clock, _SampleTimer

log = logging.getLogger(__name__)


class _CpuTimes(Protocol):
    user: float
    system: float


class _SampleProcess(Protocol):
    pid: int

    def create_time(self) -> float: ...
    def ppid(self) -> int: ...
    def cpu_times(self) -> _CpuTimes: ...


class _Backend(Protocol):
    AccessDenied: type[Exception]
    NoSuchProcess: type[Exception]
    ZombieProcess: type[Exception]

    def Process(self, pid: int) -> _SampleProcess: ...

    def _ppid_map(self) -> dict[int, int]: ...

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
    sample: SampleAccounting | None = None


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
        clock: Callable[[], float | None] = time.monotonic,
        thread_clock: Callable[[], float | None] = time.thread_time,
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
        self._thread_clock = thread_clock
        self._instance = uuid4().hex
        self._sequence = 0
        self._records = {root.pid: _Record(root)}
        self._truncated = False
        self._last: CpuObservation | None = None
        self._last_at: float | None = None
        self._pending: Future[CpuObservation] | None = None
        self._accounting_future: Future[SampleAccounting] | None = None
        self._current_sample: SampleAccounting | None = None
        self._lock = threading.Lock()

    @property
    def current_sample(self) -> SampleAccounting | None:
        """Return only the current/last sample, including pending or failed work."""
        with self._lock:
            return self._current_sample

    @property
    def sample_accounting_future(self) -> Future[SampleAccounting] | None:
        """Pin accounting to this native attempt, even if its result raises."""
        with self._lock:
            return self._accounting_future

    @property
    def pending_sample_future(self) -> Future[CpuObservation] | None:
        with self._lock:
            pending = self._pending
            return pending if pending is not None and not pending.done() else None

    async def sample(self) -> CpuObservation:
        now, _ = _read_clock(self._clock, "cache")
        new_sample: tuple[Future[SampleAccounting], SampleAccounting] | None = None
        with self._lock:
            pending = self._pending
            if pending is None or pending.done():
                if (
                    self._last is not None and self._last_at is not None and now is not None
                    and 0 <= now - self._last_at < 1.0
                ):
                    return self._last
                pending = Future()
                pending.set_running_or_notify_cancel()
                self._pending = pending
                if self._sequence == (1 << 63) - 1:
                    self._instance = uuid4().hex
                    self._sequence = 0
                self._sequence += 1
                accounting: Future[SampleAccounting] = Future()
                accounting.set_running_or_notify_cancel()
                self._accounting_future = accounting
                sample = SampleAccounting(self._instance, self._sequence, "pending")
                self._current_sample = sample
                new_sample = accounting, sample
        if new_sample is not None:
            accounting, sample = new_sample
            try:
                worker = threading.Thread(
                    target=self._run_sample, args=(pending, accounting, sample),
                    name="owned-cpu-sample", daemon=True,
                )
                worker.start()
            except Exception as error:
                failed = replace(sample, state="failed", issues=("worker_start_failed",))
                with self._lock:
                    self._current_sample = failed
                    self._last_at = None
                accounting.set_result(failed)
                pending.set_exception(error)
                log.error("Owned CPU sampling worker could not start", exc_info=error)
        wrapped = asyncio.wrap_future(pending)
        wrapped.add_done_callback(
            lambda future: None if future.cancelled() else future.exception(),
        )
        # Cancellation must neither cancel native work nor orphan a shield error.
        await asyncio.wait((wrapped,))
        return wrapped.result()

    def _run_sample(
        self, pending: Future[CpuObservation], accounting: Future[SampleAccounting],
        sample: SampleAccounting,
    ) -> None:
        timer = _SampleTimer(
            sample.instance, sample.sequence, self._clock, self._thread_clock,
        )
        with self._lock:
            self._current_sample = timer.pending
        try:
            observation = self._sample_sync()
        except BaseException as error:
            completed = timer.complete("failed")
            with self._lock:
                self._current_sample = completed
                self._last_at = None
            accounting.set_result(completed)
            pending.set_exception(error)
            log.error("Owned CPU sample failed", exc_info=error)
        else:
            completed = timer.complete("succeeded")
            observation = replace(observation, sample=completed)
            with self._lock:
                self._current_sample = completed
                self._last = observation
                self._last_at = completed.completed_at
            accounting.set_result(completed)
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
        children_by_parent: dict[int, list[int]] | None = None
        topology_error: Exception | None = None
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
                if children_by_parent is None and topology_error is None:
                    try:
                        # Share psutil's own children() topology helper for this
                        # attempt only; retained/reparented roots use it too.
                        children_by_parent = {}
                        for child_pid, parent_pid in backend._ppid_map().items():
                            children_by_parent.setdefault(parent_pid, []).append(child_pid)
                    except (
                        backend.AccessDenied, backend.NoSuchProcess, backend.ZombieProcess,
                    ) as error:
                        topology_error = error
                if topology_error is not None:
                    raise topology_error
                assert children_by_parent is not None
                children = children_by_parent.get(pid, ())
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
            for child_pid in children:
                if discovery_attempts >= self._cap:
                    self._truncated = True
                    break
                discovery_attempts += 1
                if child_pid in self._records:
                    continue
                if len(self._records) >= self._cap:
                    self._truncated = True
                    break
                try:
                    child = backend.Process(child_pid)
                    created_at = child.create_time()
                    if (
                        child.ppid() != pid
                        or backend.Process(child.pid).create_time() != created_at
                        or backend.Process(pid).create_time() != expected
                    ):
                        issues.append(CpuIssue(child.pid, "descendant_identity_unavailable"))
                        continue
                except backend.AccessDenied:
                    issues.append(CpuIssue(child_pid, "access_denied"))
                    continue
                except (backend.NoSuchProcess, backend.ZombieProcess):
                    issues.append(CpuIssue(child_pid, "process_unavailable"))
                    continue
                if (
                    not math.isfinite(created_at)
                    or created_at < expected
                    or created_at < self._root.created_at
                ):
                    issues.append(CpuIssue(child.pid, "descendant_identity_unavailable"))
                    continue
                identity = ProcessIdentity(child.pid, created_at)
                self._records[child.pid] = _Record(identity)
                queue.append(child.pid)
        return self._observation(issues)
