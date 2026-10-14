from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest

from agent_procutil import metrics
from agent_procutil.metrics import OwnedProcessCpuSampler, ProcessIdentity


class _Missing(Exception):
    pass


class _Denied(Exception):
    pass


class _Process:
    def __init__(self, pid: int, created: float, cpu: float, parent: int = 0) -> None:
        self.pid = pid
        self.created = created
        self.cpu = cpu
        self.parent = parent
        self.descendants: list[_Process] = []
        self.cpu_reads = 0
        self.denied = False

    def create_time(self) -> float:
        return self.created

    def ppid(self) -> int:
        return self.parent

    def cpu_times(self) -> SimpleNamespace:
        if self.denied:
            raise _Denied()
        self.cpu_reads += 1
        return SimpleNamespace(user=self.cpu, system=0.0)

    def children(self, *, recursive: bool) -> list[_Process]:
        assert recursive is False
        return self.descendants


def _backend(processes: dict[int, _Process]) -> SimpleNamespace:
    def process(pid: int) -> _Process:
        if pid not in processes:
            raise _Missing()
        return processes[pid]

    return SimpleNamespace(
        Process=process, AccessDenied=_Denied, NoSuchProcess=_Missing, ZombieProcess=_Missing,
    )


def test_identity_reuse_is_not_a_replacement_cpu_sample(monkeypatch: pytest.MonkeyPatch) -> None:
    replacement = _Process(1, 20, 500)
    monkeypatch.setattr(metrics, "_load_backend", lambda: _backend({1: replacement}))
    result = asyncio.run(OwnedProcessCpuSampler(ProcessIdentity(1, 10)).sample())
    assert result.observed_cpu_seconds is None
    assert replacement.cpu_reads == 0
    assert result.issues[0].reason == "identity_mismatch"


def test_identity_change_during_cpu_read_discards_counter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _Process(1, 10, 500)
    backend = _backend({1: root})

    def reused() -> SimpleNamespace:
        root.created = 20
        return SimpleNamespace(user=500, system=0)

    monkeypatch.setattr(root, "cpu_times", reused)
    monkeypatch.setattr(metrics, "_load_backend", lambda: backend)
    result = asyncio.run(OwnedProcessCpuSampler(ProcessIdentity(1, 10)).sample())
    assert result.observed_cpu_seconds is None
    assert result.issues[0].reason == "identity_mismatch"


def test_descendant_reuse_during_discovery_is_not_adopted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _Process(1, 10, 1)
    captured_child = _Process(2, 11, 0, 1)
    replacement = _Process(2, 20, 500, 99)
    root.descendants = [captured_child]
    monkeypatch.setattr(
        metrics, "_load_backend", lambda: _backend({1: root, 2: replacement}),
    )
    result = asyncio.run(OwnedProcessCpuSampler(ProcessIdentity(1, 10)).sample())
    assert result.observed_cpu_seconds == 1
    assert len(result.identities) == 1
    assert replacement.cpu_reads == 0
    assert result.issues[0].reason == "descendant_identity_unavailable"


def test_denied_descendant_discovery_attempts_remain_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _Process(1, 10, 1)
    attempted = []
    children = [_Process(pid, 10 + pid, 0, 1) for pid in range(2, 20)]

    def denied(pid: int) -> float:
        attempted.append(pid)
        raise _Denied()

    for child in children:
        monkeypatch.setattr(child, "create_time", lambda pid=child.pid: denied(pid))
    root.descendants = children
    monkeypatch.setattr(metrics, "_load_backend", lambda: _backend({1: root}))
    result = asyncio.run(
        OwnedProcessCpuSampler(ProcessIdentity(1, 10), max_identities=2).sample(),
    )
    assert len(attempted) == 2
    assert len(result.identities) == 1
    assert result.truncated
    assert len(result.issues) == 2


def test_totals_reparenting_and_counter_regression(monkeypatch: pytest.MonkeyPatch) -> None:
    root = _Process(1, 10, 1)
    child = _Process(2, 11, 2, 1)
    root.descendants = [child]
    backend = _backend({1: root, 2: child})
    monkeypatch.setattr(metrics, "_load_backend", lambda: backend)
    now = [0.0]
    sampler = OwnedProcessCpuSampler(ProcessIdentity(1, 10), clock=lambda: now[0])

    async def run() -> None:
        first = await sampler.sample()
        assert first.observed_cpu_seconds == 3
        assert await sampler.sample() is first
        assert root.cpu_reads == child.cpu_reads == 1
        now[0] = 1
        root.cpu = 2
        child.cpu = 4
        root.descendants = []
        child.parent = 99
        second = await sampler.sample()
        assert second.observed_cpu_seconds == 6
        assert len(second.identities) == 2
        now[0] = 2
        child.cpu = 1
        regressed = await sampler.sample()
        assert regressed.observed_cpu_seconds == 6
        assert any(issue.reason == "cpu_counter_regressed" for issue in regressed.issues)

    asyncio.run(run())


def test_discovery_cap_and_uncertainty(monkeypatch: pytest.MonkeyPatch) -> None:
    root = _Process(1, 10, 1)
    children = [_Process(pid, 10 + pid, 2, 1) for pid in range(2, 20)]
    root.descendants = children
    processes = {process.pid: process for process in [root, *children]}
    monkeypatch.setattr(metrics, "_load_backend", lambda: _backend(processes))
    result = asyncio.run(
        OwnedProcessCpuSampler(ProcessIdentity(1, 10), max_identities=2).sample(),
    )
    assert len(result.identities) == 2
    assert result.truncated
    assert result.observed_cpu_seconds == 3
    assert result.coverage == "observed_lower_bound"


def test_access_denial_and_disappearance_preserve_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _Process(1, 10, 0)
    root.denied = True
    processes = {1: root}
    monkeypatch.setattr(metrics, "_load_backend", lambda: _backend(processes))
    now = [0.0]
    sampler = OwnedProcessCpuSampler(ProcessIdentity(1, 10), clock=lambda: now[0])

    async def run() -> None:
        denied = await sampler.sample()
        assert denied.observed_cpu_seconds is None
        assert denied.issues[0].reason == "access_denied"
        processes.clear()
        now[0] = 1
        missing = await sampler.sample()
        assert missing.observed_cpu_seconds is None
        assert missing.issues[0].reason == "process_unavailable"

    asyncio.run(run())


def test_missing_optional_backend_is_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing() -> None:
        raise ModuleNotFoundError("optional metrics backend is absent", name="psutil")

    monkeypatch.setattr(metrics, "_load_backend", missing)
    result = asyncio.run(OwnedProcessCpuSampler(ProcessIdentity(1, 10)).sample())
    assert result.observed_cpu_seconds is None
    assert result.issues[0].reason == "metrics_unavailable"


def test_missing_root_identity_never_loads_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden() -> None:
        pytest.fail("unknown root identity must not be sampled")

    monkeypatch.setattr(metrics, "_load_backend", forbidden)
    result = asyncio.run(OwnedProcessCpuSampler(ProcessIdentity(1, None)).sample())
    assert result.observed_cpu_seconds is None
    assert result.issues[0].reason == "root_identity_unavailable"


def test_cancelled_waiter_retains_sampling_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    started = threading.Event()
    release = threading.Event()
    root = _Process(1, 10, 1)
    calls = 0

    def backend() -> SimpleNamespace:
        nonlocal calls
        calls += 1
        started.set()
        assert release.wait(timeout=5)
        return _backend({1: root})

    monkeypatch.setattr(metrics, "_load_backend", backend)
    sampler = OwnedProcessCpuSampler(ProcessIdentity(1, 10))

    async def run() -> None:
        first = asyncio.create_task(sampler.sample())
        try:
            assert await asyncio.to_thread(started.wait, 2)
            holder = sampler.pending_sample_future
            assert holder is not None
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            assert sampler.pending_sample_future is holder
            assert holder.cancel() is False
            joiners = [asyncio.create_task(sampler.sample()) for _ in range(8)]
            release.set()
            results = await asyncio.wait_for(asyncio.gather(*joiners), timeout=2)
            assert all(result.observed_cpu_seconds == 1 for result in results)
            assert calls == 1
        finally:
            release.set()
            if sampler.pending_sample_future is not None:
                await asyncio.wait_for(
                    asyncio.shield(asyncio.wrap_future(sampler.pending_sample_future)), timeout=2,
                )

    asyncio.run(run())


def test_loop_shutdown_does_not_release_native_sample_holder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = threading.Event()
    release = threading.Event()
    calls = 0

    def backend() -> SimpleNamespace:
        nonlocal calls
        calls += 1
        started.set()
        assert release.wait(timeout=5)
        return _backend({1: _Process(1, 10, 1)})

    monkeypatch.setattr(metrics, "_load_backend", backend)
    sampler = OwnedProcessCpuSampler(ProcessIdentity(1, 10))

    async def abandon_loop() -> None:
        waiter = asyncio.create_task(sampler.sample())
        assert await asyncio.to_thread(started.wait, 2)
        assert not waiter.done()

    try:
        asyncio.run(abandon_loop())
        holder = sampler.pending_sample_future
        assert holder is not None
        assert holder.cancel() is False
        release.set()
        result = asyncio.run(sampler.sample())
        assert result.observed_cpu_seconds == 1
        assert calls == 1
    finally:
        release.set()
        if sampler.pending_sample_future is not None:
            sampler.pending_sample_future.result(timeout=2)
