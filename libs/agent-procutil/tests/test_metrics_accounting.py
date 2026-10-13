from __future__ import annotations

import asyncio
import gc
import re
import threading
from collections.abc import Callable

import pytest

from agent_procutil.metrics import CpuObservation, OwnedProcessCpuSampler, ProcessIdentity


def _observation(sampler: OwnedProcessCpuSampler) -> CpuObservation:
    return CpuObservation(100.0, (ProcessIdentity(1, 10.0),), (), False)


def test_native_provenance_and_cache_cost_are_distinct(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wall, cpu = [10.0], [2.0]
    sampler = OwnedProcessCpuSampler(
        ProcessIdentity(1, 10.0), clock=lambda: wall[0], thread_clock=lambda: cpu[0],
    )

    def sample(self: OwnedProcessCpuSampler) -> CpuObservation:
        wall[0] += 0.75
        cpu[0] += 0.125
        return _observation(self)

    monkeypatch.setattr(OwnedProcessCpuSampler, "_sample_sync", sample)

    async def run() -> None:
        first = await sampler.sample()
        assert first.sample is not None
        assert re.fullmatch(r"[a-f0-9]{32}", first.sample.instance)
        assert first.sample.sequence == 1
        assert first.sample.state == "succeeded"
        assert first.sample.started_at == 10.0
        assert first.sample.completed_at == 10.75
        assert first.sample.wall_seconds == 0.75
        assert first.sample.thread_cpu_seconds == 0.125
        assert first.observed_cpu_seconds == 100.0
        assert first.sample.issues == ()
        accounting = sampler.sample_accounting_future
        assert accounting is not None and accounting.result() is first.sample
        assert await sampler.sample() is first
        assert sampler.sample_accounting_future is accounting
        wall[0] += 1
        second = await sampler.sample()
        assert second.sample is not None
        assert second.sample.instance == first.sample.instance
        assert second.sample.sequence == 2
        assert second.sample.thread_cpu_seconds == 0.125
        assert sampler.current_sample is second.sample
        assert sampler.sample_accounting_future is not accounting

    asyncio.run(run())


def test_recreated_sampler_and_sequence_rollover_cannot_reuse_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(OwnedProcessCpuSampler, "_sample_sync", _observation)
    root = ProcessIdentity(1, 10.0)
    first = asyncio.run(OwnedProcessCpuSampler(root).sample()).sample
    second = asyncio.run(OwnedProcessCpuSampler(root).sample()).sample
    assert first is not None and second is not None
    assert first.instance != second.instance
    sampler = OwnedProcessCpuSampler(root)
    old_instance = sampler._instance
    sampler._sequence = (1 << 63) - 1
    rolled = asyncio.run(sampler.sample()).sample
    assert rolled is not None
    assert rolled.instance != old_instance
    assert rolled.sequence == 1


def _denied_clock() -> float:
    raise PermissionError("private clock detail")


@pytest.mark.parametrize(
    ("clock", "reason"),
    [
        (lambda: float("nan"), "invalid"),
        (lambda: float("inf"), "invalid"),
        (lambda: -1.0, "invalid"),
        (lambda: True, "invalid"),
        (lambda: None, "unavailable"),
        (_denied_clock, "failed"),
    ],
)
@pytest.mark.parametrize("broken_clock", ["wall", "thread_cpu"])
def test_instrumentation_uncertainty_preserves_cpu_observation(
    monkeypatch: pytest.MonkeyPatch, clock: Callable[[], float | None], reason: str,
    broken_clock: str,
) -> None:
    monkeypatch.setattr(OwnedProcessCpuSampler, "_sample_sync", _observation)
    sampler = OwnedProcessCpuSampler(
        ProcessIdentity(1, 10.0),
        clock=clock if broken_clock == "wall" else lambda: 10.0,
        thread_clock=clock if broken_clock == "thread_cpu" else lambda: 2.0,
    )
    observation = asyncio.run(sampler.sample())
    assert observation.observed_cpu_seconds == 100.0
    sample = observation.sample
    assert sample is not None and sample.state == "succeeded"
    assert getattr(sample, f"{broken_clock}_seconds") is None
    assert f"{broken_clock}_start_{reason}" in sample.issues
    assert f"{broken_clock}_completion_{reason}" in sample.issues


def test_clock_regression_is_unknown_and_never_reuses_future_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wall, cpu = [10.0], [2.0]

    def sample(self: OwnedProcessCpuSampler) -> CpuObservation:
        wall[0] -= 1
        cpu[0] -= 0.5
        return _observation(self)

    monkeypatch.setattr(OwnedProcessCpuSampler, "_sample_sync", sample)
    sampler = OwnedProcessCpuSampler(
        ProcessIdentity(1, 10.0), clock=lambda: wall[0], thread_clock=lambda: cpu[0],
    )
    first = asyncio.run(sampler.sample())
    assert first.sample is not None
    assert first.sample.wall_seconds is None
    assert first.sample.completed_at is None
    assert first.sample.thread_cpu_seconds is None
    assert set(first.sample.issues) == {"wall_regressed", "thread_cpu_regressed"}
    second = asyncio.run(sampler.sample())
    assert second.sample is not None and second.sample.sequence == 2
    assert second.observed_cpu_seconds == 100.0


def test_only_completion_clock_failure_does_not_erase_native_cpu(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wall_reads = iter([10.0, 10.0, float("nan")])
    cpu_reads = iter([2.0, 2.5])
    monkeypatch.setattr(OwnedProcessCpuSampler, "_sample_sync", _observation)
    result = asyncio.run(
        OwnedProcessCpuSampler(
            ProcessIdentity(1, 10.0),
            clock=lambda: next(wall_reads), thread_clock=lambda: next(cpu_reads),
        ).sample(),
    )
    assert result.sample is not None
    assert result.sample.started_at == 10.0
    assert result.sample.completed_at is None
    assert result.sample.wall_seconds is None
    assert result.sample.thread_cpu_seconds == 0.5
    assert result.sample.issues == ("wall_completion_invalid",)
    assert result.observed_cpu_seconds == 100.0


@pytest.mark.parametrize("fails", [False, True])
def test_cancelled_await_retains_native_accounting_and_exception_semantics(
    monkeypatch: pytest.MonkeyPatch, fails: bool,
) -> None:
    started, release = threading.Event(), threading.Event()
    wall, cpu = [10.0], [2.0]
    failure = RuntimeError("private sample failure")

    def sample(self: OwnedProcessCpuSampler) -> CpuObservation:
        started.set()
        assert release.wait(5)
        wall[0] = 15.0
        cpu[0] = 2.25
        if fails:
            raise failure
        return _observation(self)

    monkeypatch.setattr(OwnedProcessCpuSampler, "_sample_sync", sample)
    sampler = OwnedProcessCpuSampler(
        ProcessIdentity(1, 10.0), clock=lambda: wall[0], thread_clock=lambda: cpu[0],
    )

    async def run() -> None:
        loop = asyncio.get_running_loop()
        unhandled: list[dict] = []
        loop.set_exception_handler(lambda loop, context: unhandled.append(context))
        waiter = asyncio.create_task(sampler.sample())
        try:
            assert await asyncio.to_thread(started.wait, 2)
            holder = sampler.pending_sample_future
            accounting = sampler.sample_accounting_future
            assert holder is not None and accounting is not None
            assert not holder.cancel() and not accounting.cancel()
            pending = sampler.current_sample
            assert pending is not None and pending.state == "pending"
            assert pending.completed_at is pending.wall_seconds is pending.thread_cpu_seconds is None
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
            assert sampler.pending_sample_future is holder
            assert not accounting.done()
            release.set()
            completed = await asyncio.wait_for(asyncio.wrap_future(accounting), timeout=2)
            assert completed.state == ("failed" if fails else "succeeded")
            assert completed.wall_seconds == 5.0
            assert completed.thread_cpu_seconds == 0.25
            assert (completed.instance, completed.sequence) == (pending.instance, pending.sequence)
            assert sampler.current_sample is completed
            if fails:
                with pytest.raises(RuntimeError) as caught:
                    await asyncio.wrap_future(holder)
                assert caught.value is failure
            else:
                assert (await asyncio.wrap_future(holder)).sample is completed
            del waiter
            gc.collect()
            await asyncio.sleep(0)
            assert not unhandled
        finally:
            release.set()
            if sampler.pending_sample_future is not None:
                await asyncio.gather(
                    asyncio.wrap_future(sampler.pending_sample_future), return_exceptions=True,
                )

    asyncio.run(run())


@pytest.mark.parametrize("failure_type", [RuntimeError, OSError])
def test_failed_ordinary_await_and_worker_start_are_not_success_observations(
    monkeypatch: pytest.MonkeyPatch, failure_type: type[Exception],
) -> None:
    failure = failure_type("worker could not start")

    def start(worker: threading.Thread) -> None:
        raise failure

    monkeypatch.setattr(threading.Thread, "start", start)
    sampler = OwnedProcessCpuSampler(ProcessIdentity(1, 10.0))
    with pytest.raises(failure_type) as caught:
        asyncio.run(sampler.sample())
    assert caught.value is failure
    accounting = sampler.sample_accounting_future
    assert accounting is not None
    sample = accounting.result()
    assert sample.state == "failed"
    assert sample.issues == ("worker_start_failed",)
    assert sample.started_at is sample.completed_at is sample.thread_cpu_seconds is None


def test_loop_shutdown_and_repeated_samples_keep_only_current_last_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started, release = threading.Event(), threading.Event()
    now = [10.0]

    def sample(self: OwnedProcessCpuSampler) -> CpuObservation:
        started.set()
        assert release.wait(5)
        return _observation(self)

    monkeypatch.setattr(OwnedProcessCpuSampler, "_sample_sync", sample)
    sampler = OwnedProcessCpuSampler(ProcessIdentity(1, 10.0), clock=lambda: now[0])

    async def abandon() -> None:
        asyncio.create_task(sampler.sample())
        assert await asyncio.to_thread(started.wait, 2)

    try:
        asyncio.run(abandon())
        accounting = sampler.sample_accounting_future
        assert accounting is not None and not accounting.done()
        release.set()
        assert accounting.result(timeout=2).state == "succeeded"
        first = asyncio.run(sampler.sample())
        assert first.sample is accounting.result()
        for sequence in range(2, 52):
            now[0] += 2
            result = asyncio.run(sampler.sample())
            assert result.sample is not None
            assert result.sample.sequence == sequence
            assert sampler.current_sample is result.sample
            assert sampler._last is result
            assert sampler.sample_accounting_future.result() is result.sample
        assert len(sampler._records) == 1
    finally:
        release.set()
        if sampler.pending_sample_future is not None:
            sampler.pending_sample_future.result(timeout=2)


@pytest.mark.parametrize("uncertain_now", [None, 9.0])
@pytest.mark.parametrize("failure_type", [RuntimeError, OSError])
def test_worker_start_failure_never_resurrects_cached_success_after_clock_uncertainty(
    monkeypatch: pytest.MonkeyPatch, uncertain_now: float | None,
    failure_type: type[Exception],
) -> None:
    now: list[float | None] = [10.0]
    monkeypatch.setattr(OwnedProcessCpuSampler, "_sample_sync", _observation)
    sampler = OwnedProcessCpuSampler(ProcessIdentity(1, 10.0), clock=lambda: now[0])
    real_start = threading.Thread.start
    failure = failure_type("worker could not start")

    def fail_start(worker: threading.Thread) -> None:
        raise failure

    async def run() -> None:
        first = await sampler.sample()
        assert first.sample is not None and first.sample.sequence == 1
        now[0] = uncertain_now
        monkeypatch.setattr(threading.Thread, "start", fail_start)
        with pytest.raises(failure_type) as caught:
            await sampler.sample()
        assert caught.value is failure
        failed = sampler.current_sample
        assert failed is not None and failed.state == "failed"
        assert failed.sequence == 2
        assert sampler._last is first
        assert sampler._last_at is None
        now[0] = 10.25
        monkeypatch.setattr(threading.Thread, "start", real_start)
        recovered = await sampler.sample()
        assert recovered is not first
        assert recovered.sample is not None and recovered.sample.sequence == 3
        assert recovered.observed_cpu_seconds == first.observed_cpu_seconds
        assert sampler.current_sample is recovered.sample

    asyncio.run(run())
