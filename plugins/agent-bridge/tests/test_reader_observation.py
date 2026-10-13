"""Owned receipts, bounded measurement and content-free reader telemetry."""

from __future__ import annotations

import asyncio
import gc
import json
import subprocess
import sys
import threading
import time
from collections.abc import Coroutine
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from agent_procutil.metrics import CpuObservation, OwnedProcessCpuSampler, ProcessIdentity

from agent_bridge import reader_command, reader_observation, telemetry
from agent_bridge.agent_registry_cache import AgentRosterCache
from agent_bridge.agent_registry_namespace import CliNamespaceResolver
from agent_bridge.agent_registry_resolver import AgentResolver


@pytest.fixture
def events() -> list[dict]:
    received: list[dict] = []
    telemetry.set_telemetry_sink(
        lambda event: received.append(event)
        if event.get("event") == "reader_cpu_observation" else None,
    )
    try:
        yield received
    finally:
        telemetry.clear_telemetry_sink()


def _mock_command(monkeypatch: pytest.MonkeyPatch, root: ProcessIdentity) -> None:
    def run(
        self: reader_command.OwnedReaderCommand,
        cmd: list[str],
        *,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        self.receipt.set_result(root)
        self.wall_seconds = 0.25
        return subprocess.CompletedProcess(cmd, 0, '[{"name":"valid"}]', "private stderr")

    monkeypatch.setattr(reader_command.OwnedReaderCommand, "run", run)


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["metrics_unavailable", "access_denied"])
async def test_uncertain_measurement_preserves_success(
    monkeypatch: pytest.MonkeyPatch,
    events: list[dict],
    reason: str,
) -> None:
    import psutil
    from agent_procutil import metrics

    root = ProcessIdentity(123, 456.0)
    _mock_command(monkeypatch, root)
    if reason == "metrics_unavailable":

        def backend() -> None:
            raise ModuleNotFoundError("missing backend", name="psutil")

        monkeypatch.setattr(metrics, "_load_backend", backend)
    else:
        monkeypatch.setattr(
            metrics,
            "_load_backend",
            lambda: SimpleNamespace(
                AccessDenied=psutil.AccessDenied,
                NoSuchProcess=psutil.NoSuchProcess,
                ZombieProcess=psutil.ZombieProcess,
                Process=Mock(side_effect=psutil.AccessDenied(123)),
            ),
        )
    provider = CliNamespaceResolver("test", "unused", command=["private-command"])
    assert [row.name for row in await provider.list()] == ["valid"]
    assert events[0]["observed_cpu_seconds"] is None
    assert events[0]["issues"] == [{"pid": 123, "reason": reason}]
    assert events[0]["wall_seconds"] == 0.25
    assert "private" not in json.dumps(events)


@pytest.mark.asyncio
async def test_sampler_failure_preserves_success_and_hides_exception(
    monkeypatch: pytest.MonkeyPatch,
    events: list[dict],
) -> None:
    _mock_command(monkeypatch, ProcessIdentity(123, 456.0))

    def fail(self: OwnedProcessCpuSampler) -> CpuObservation:
        raise RuntimeError("private-path credential output")

    monkeypatch.setattr(OwnedProcessCpuSampler, "_sample_sync", fail)
    provider = CliNamespaceResolver("private/path", "unused", command=["private-command"])
    assert [row.name for row in await provider.list()] == ["valid"]
    assert events[0]["namespace"] == "other"
    assert events[0]["issues"] == [{"pid": 123, "reason": "sampling_failed"}]
    assert "private" not in json.dumps(events)


@pytest.mark.asyncio
async def test_provider_sampling_rate_is_bounded_across_fast_reads(
    monkeypatch: pytest.MonkeyPatch,
    events: list[dict],
) -> None:
    monkeypatch.setenv("AGENT_BRIDGE_NAMESPACE_LIST_TTL", "0")
    _mock_command(monkeypatch, ProcessIdentity(123, None))
    calls = 0
    original = OwnedProcessCpuSampler._sample_sync

    def sample(self: OwnedProcessCpuSampler) -> CpuObservation:
        nonlocal calls
        calls += 1
        return original(self)

    monkeypatch.setattr(OwnedProcessCpuSampler, "_sample_sync", sample)
    monkeypatch.setattr(reader_observation, "time", SimpleNamespace(monotonic=lambda: 100.0))
    provider = CliNamespaceResolver("test", "unused", command=["fake"])
    await provider.list()
    for _ in range(3):
        await provider.list()
    assert events[0]["issues"][0]["reason"] == "root_identity_unavailable"
    assert all(event["issues"][0]["reason"] == "sampling_rate_limited" for event in events[1:])
    assert len(events) == 4
    assert calls == 1


@pytest.mark.asyncio
async def test_sampler_setup_failure_preserves_enumeration(
    monkeypatch: pytest.MonkeyPatch,
    events: list[dict],
) -> None:
    _mock_command(monkeypatch, ProcessIdentity(123, 456.0))
    monkeypatch.setattr(
        reader_observation,
        "OwnedProcessCpuSampler",
        Mock(side_effect=RuntimeError("private setup")),
    )
    provider = CliNamespaceResolver("test", "unused", command=["fake"])
    assert [row.name for row in await provider.list()] == ["valid"]
    assert events[0]["issues"] == [{"pid": 123, "reason": "sampling_failed"}]
    assert "private" not in json.dumps(events)


@pytest.mark.asyncio
async def test_sample_completing_after_join_budget_is_exported_at_command_exit(
    monkeypatch: pytest.MonkeyPatch,
    events: list[dict],
) -> None:
    root = ProcessIdentity(123, 456.0)

    def run(
        self: reader_command.OwnedReaderCommand,
        cmd: list[str],
        *,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        self.receipt.set_result(root)
        time.sleep(0.25)
        return subprocess.CompletedProcess(cmd, 0, "[]", "")

    def sample(self: OwnedProcessCpuSampler) -> CpuObservation:
        time.sleep(0.15)
        return CpuObservation(0.5, (root,), (), False)

    monkeypatch.setattr(reader_command.OwnedReaderCommand, "run", run)
    monkeypatch.setattr(OwnedProcessCpuSampler, "_sample_sync", sample)
    provider = CliNamespaceResolver("test", "unused", command=["fake"])
    assert await provider.list() == []
    assert events[0]["observed_cpu_seconds"] == 0.5
    assert events[0]["issues"] == []


@pytest.mark.asyncio
async def test_distinct_asyncio_timeout_remains_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class LegacyAsyncioTimeout(Exception):
        pass

    async def timeout(awaitable: object, *, timeout: float) -> None:
        raise LegacyAsyncioTimeout()

    monkeypatch.setattr(reader_observation.asyncio, "TimeoutError", LegacyAsyncioTimeout)
    monkeypatch.setattr(reader_observation.asyncio, "wait_for", timeout)
    root = ProcessIdentity(123, None)
    observer = reader_observation.ReaderCpuObservation("test")
    sampler = OwnedProcessCpuSampler(root)
    observation, issue = await observer._sample(sampler, root, None)
    assert observation is None
    assert issue is not None and issue.reason == "sample_pending"
    assert observer._sample_holder is not None
    await observer._sample_holder


@pytest.mark.asyncio
async def test_late_native_completion_sets_next_provider_sample_deadline(
    monkeypatch: pytest.MonkeyPatch,
    events: list[dict],
) -> None:
    monkeypatch.setenv("AGENT_BRIDGE_NAMESPACE_LIST_TTL", "0")
    now = [100.0]
    monkeypatch.setattr(
        reader_observation,
        "time",
        SimpleNamespace(monotonic=lambda: now[0]),
    )
    root = ProcessIdentity(123, 456.0)
    _mock_command(monkeypatch, root)
    release = threading.Event()
    calls = 0

    def sample(self: OwnedProcessCpuSampler) -> CpuObservation:
        nonlocal calls
        calls += 1
        assert release.wait(10)
        return CpuObservation(0.5, (root,), (), False)

    monkeypatch.setattr(OwnedProcessCpuSampler, "_sample_sync", sample)
    provider = CliNamespaceResolver("test", "unused", command=["fake"])
    try:
        await provider.list()
        holder = provider.pending_cpu_sample_future
        assert holder is not None
        now[0] = 150.0
        release.set()
        await asyncio.wrap_future(holder)
        async with asyncio.timeout(2):
            while not provider._cpu_observation._sample_completed.done():
                await asyncio.sleep(0)
        now[0] = 150.5
        await provider.list()
        assert calls == 1
        assert events[-1]["issues"] == [{"pid": 123, "reason": "sampling_rate_limited"}]
        now[0] = 151.0
        await provider.list()
        assert calls == 2
        assert events[-1]["observed_cpu_seconds"] == 0.5
    finally:
        release.set()
        if provider.pending_cpu_sample_future is not None:
            await asyncio.wrap_future(provider.pending_cpu_sample_future)


@pytest.mark.asyncio
async def test_detached_native_and_sample_failures_are_consumed(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    command_release, sample_release = threading.Event(), threading.Event()
    sample_started = threading.Event()
    root = ProcessIdentity(123, 456.0)

    def run(
        self: reader_command.OwnedReaderCommand,
        cmd: list[str],
        *,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        self.receipt.set_result(root)
        assert command_release.wait(10)
        raise subprocess.TimeoutExpired(cmd, timeout)

    def sample(self: OwnedProcessCpuSampler) -> CpuObservation:
        sample_started.set()
        assert sample_release.wait(10)
        raise RuntimeError("late sample error")

    monkeypatch.setattr(reader_command.OwnedReaderCommand, "run", run)
    monkeypatch.setattr(OwnedProcessCpuSampler, "_sample_sync", sample)
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()
    unhandled: list[dict] = []
    loop.set_exception_handler(lambda loop, context: unhandled.append(context))
    observer = reader_observation.ReaderCpuObservation("test")
    read = asyncio.create_task(observer.run(["fake"], timeout=3))
    try:
        async with asyncio.timeout(2):
            while not sample_started.is_set():
                await asyncio.sleep(0.001)
        read.cancel()
        with pytest.raises(asyncio.CancelledError):
            await read
        command_release.set()
        sample_release.set()
        async with asyncio.timeout(2):
            while not (
                "command task failed (TimeoutExpired)" in caplog.text
                and "sample task failed (RuntimeError)" in caplog.text
            ):
                await asyncio.sleep(0.001)
        del read
        observer._sample_holder = None
        gc.collect()
        await asyncio.sleep(0)
        assert not unhandled
    finally:
        command_release.set()
        sample_release.set()
        loop.set_exception_handler(previous_handler)


@pytest.mark.asyncio
async def test_cancelled_command_task_does_not_invent_native_wall_duration(
    monkeypatch: pytest.MonkeyPatch,
    events: list[dict],
) -> None:
    started, release, finished = threading.Event(), threading.Event(), threading.Event()

    def run(
        self: reader_command.OwnedReaderCommand,
        cmd: list[str],
        *,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        began = time.monotonic()
        try:
            self.receipt.set_result(ProcessIdentity(123, None))
            started.set()
            assert release.wait(10)
            return subprocess.CompletedProcess(cmd, 0, "[]", "")
        finally:
            self.wall_seconds = time.monotonic() - began
            finished.set()

    real_create_task = asyncio.create_task
    tasks: list[asyncio.Task] = []

    def create_task(coro: Coroutine) -> asyncio.Task:
        task = real_create_task(coro)
        tasks.append(task)
        return task

    monkeypatch.setattr(reader_command.OwnedReaderCommand, "run", run)
    monkeypatch.setattr(reader_observation.asyncio, "create_task", create_task)
    observer = reader_observation.ReaderCpuObservation("test")
    read = real_create_task(observer.run(["fake"], timeout=3))
    try:
        async with asyncio.timeout(2):
            while not started.is_set():
                await asyncio.sleep(0.001)
        tasks[0].cancel()
        with pytest.raises(asyncio.CancelledError):
            await read
        assert not finished.is_set()
        assert events[0]["wall_seconds"] is None
        assert "observer_cancelled" in {item["reason"] for item in events[0]["issues"]}
    finally:
        release.set()
        async with asyncio.timeout(2):
            while not finished.is_set():
                await asyncio.sleep(0.001)


@pytest.mark.asyncio
@pytest.mark.parametrize("replace_provider", [False, True])
async def test_stuck_sample_retained_after_command_and_provider_replacement(
    monkeypatch: pytest.MonkeyPatch,
    events: list[dict],
    replace_provider: bool,
) -> None:
    monkeypatch.setenv("AGENT_BRIDGE_NAMESPACE_LIST_TTL", "0")
    _mock_command(monkeypatch, ProcessIdentity(123, 456.0))
    started, release = threading.Event(), threading.Event()
    samples = 0

    def sample(self: OwnedProcessCpuSampler) -> CpuObservation:
        nonlocal samples
        samples += 1
        started.set()
        assert release.wait(10)
        return CpuObservation(0.1, (ProcessIdentity(123, 456.0),), (), False)

    monkeypatch.setattr(OwnedProcessCpuSampler, "_sample_sync", sample)
    provider = CliNamespaceResolver("test", "unused", command=["private-command"])
    all_events: list[dict] = []
    telemetry.set_telemetry_sink(all_events.append)
    epoch = provider._cpu_observation._collector.snapshot.epoch
    registry = AgentResolver({}, {})
    registry.register_namespace_resolver(provider)
    cache = AgentRosterCache(registry, refresh_interval=60)
    cache._CANCELLATION_GRACE = 0.02
    try:
        await cache._refresh_namespace("test")
        assert started.is_set()
        holder = provider.pending_cpu_sample_future
        assert holder is not None
        assert holder in cache._owned_provider_samples
        for _ in range(3):
            assert [row.name for row in await provider.list()] == ["valid"]
            assert provider.pending_cpu_sample_future is holder
        assert samples == 1
        assert all_events[-1]["issues"] == [{"pid": 123, "reason": "previous_sample_pending"}]
        assert all_events[-1]["collector"]["completed"] == 0
        replacement = None
        if replace_provider:
            registry.unregister_namespace_resolver("test")
            replacement = CliNamespaceResolver("test", "missing-provider")
            registry.register_namespace_resolver(replacement)
            cache._reconcile_namespace_set()
        await asyncio.wait_for(cache.stop(), timeout=1)
        assert not holder.done()
        assert not holder.cancel()
        assert holder in cache._owned_provider_samples
        release.set()
        await asyncio.wait_for(asyncio.wrap_future(holder), timeout=2)
        await asyncio.sleep(0)
        assert holder not in cache._owned_provider_samples
        completion = next(
            event for event in all_events if event["event"] == "reader_cpu_sample_completed"
        )
        assert completion["collector"]["epoch"] == epoch
        assert completion["collector"]["completed"] == 1
        if replacement is not None:
            snapshot = replacement._cpu_observation._collector.snapshot
            assert snapshot.epoch != epoch and snapshot.completed == 0
    finally:
        release.set()
        await cache.stop()
        if provider.pending_cpu_sample_future is not None:
            await asyncio.wait_for(
                asyncio.wrap_future(provider.pending_cpu_sample_future), timeout=2
            )


@pytest.mark.asyncio
async def test_cache_captures_sample_started_after_outer_scan_cancelled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command_release = threading.Event()
    sample_release = threading.Event()
    root = ProcessIdentity(123, 456.0)

    def run(
        self: reader_command.OwnedReaderCommand,
        cmd: list[str],
        *,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        assert command_release.wait(10)
        self.receipt.set_result(root)
        return subprocess.CompletedProcess(cmd, 0, "[]", "")

    def sample(self: OwnedProcessCpuSampler) -> CpuObservation:
        assert sample_release.wait(10)
        return CpuObservation(None, (root,), (), False)

    monkeypatch.setattr(reader_command.OwnedReaderCommand, "run", run)
    monkeypatch.setattr(OwnedProcessCpuSampler, "_sample_sync", sample)
    provider = CliNamespaceResolver("test", "unused", command=["fake-command"])
    registry = AgentResolver({}, {})
    registry.register_namespace_resolver(provider)
    cache = AgentRosterCache(registry)
    cache._CANCELLATION_GRACE = 0.01
    scan = cache._refresh_namespace("test")
    try:
        async with asyncio.timeout(2):
            while provider.pending_list_task is None:
                await asyncio.sleep(0.001)
        native = provider.pending_list_task
        scan.cancel()
        await asyncio.gather(scan, return_exceptions=True)
        assert native in cache._owned_provider_reads
        assert provider.pending_cpu_sample_future is None
        command_release.set()
        await asyncio.wait_for(asyncio.shield(native), timeout=2)
        holder = provider.pending_cpu_sample_future
        assert holder is not None
        assert holder in cache._owned_provider_samples
        await cache.stop()
        assert holder in cache._owned_provider_samples
        assert not holder.done()
    finally:
        command_release.set()
        sample_release.set()
        await cache.stop()
        if provider.pending_cpu_sample_future is not None:
            await asyncio.wait_for(
                asyncio.wrap_future(provider.pending_cpu_sample_future), timeout=2
            )


def test_owned_receipt_precedes_communicate_and_reap(monkeypatch: pytest.MonkeyPatch) -> None:
    owned = reader_command.OwnedReaderCommand()
    assert not owned.receipt.cancel()
    assert owned.wall_seconds is None
    process = Mock(pid=123, returncode=0)
    process.__enter__ = Mock(return_value=process)
    process.__exit__ = Mock(return_value=False)
    root = ProcessIdentity(123, 456.0)

    def identity(child: subprocess.Popen[str]) -> ProcessIdentity:
        assert child is process
        process.poll.assert_not_called()
        process.wait.assert_not_called()
        process.communicate.assert_not_called()
        return root

    def communicate(*, timeout: float) -> tuple[str, str]:
        assert owned.receipt.result() == root
        assert 0 < timeout <= 3.5
        return "stdout", "stderr"

    monkeypatch.setattr(reader_command, "capture_identity", identity)
    monkeypatch.setattr(reader_command.subprocess, "Popen", Mock(return_value=process))
    process.communicate.side_effect = communicate
    result = owned.run(["provider", "namespace-list"], timeout=3.5)
    assert (result.returncode, result.stdout, result.stderr) == (0, "stdout", "stderr")
    assert owned.wall_seconds is not None
    process.kill.assert_not_called()


@pytest.mark.asyncio
async def test_cancelled_observer_retains_raw_sample_and_marks_partial_wall(
    monkeypatch: pytest.MonkeyPatch,
    events: list[dict],
) -> None:
    command_release, sample_release = threading.Event(), threading.Event()
    sample_started, command_done = threading.Event(), threading.Event()
    root = ProcessIdentity(123, 456.0)

    def run(
        self: reader_command.OwnedReaderCommand,
        cmd: list[str],
        *,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        self.receipt.set_result(root)
        assert command_release.wait(10)
        command_done.set()
        return subprocess.CompletedProcess(cmd, 0, "[]", "")

    def sample(self: OwnedProcessCpuSampler) -> CpuObservation:
        sample_started.set()
        assert sample_release.wait(10)
        return CpuObservation(0.1, (root,), (), False)

    monkeypatch.setattr(reader_command.OwnedReaderCommand, "run", run)
    monkeypatch.setattr(OwnedProcessCpuSampler, "_sample_sync", sample)
    observer = reader_observation.ReaderCpuObservation("test")
    read = asyncio.create_task(observer.run(["fake"], timeout=3))
    try:
        async with asyncio.timeout(2):
            while not sample_started.is_set():
                await asyncio.sleep(0.001)
        holder = observer.pending_sample_future
        assert holder is not None
        read.cancel()
        with pytest.raises(asyncio.CancelledError):
            await read
        assert observer.pending_sample_future is holder
        assert not holder.cancel()
        assert not command_done.is_set()
        assert events[0]["wall_seconds"] is None
        assert "observer_cancelled" in {item["reason"] for item in events[0]["issues"]}
    finally:
        command_release.set()
        sample_release.set()
        await asyncio.gather(read, return_exceptions=True)
        if observer.pending_sample_future is not None:
            await asyncio.wait_for(asyncio.wrap_future(observer.pending_sample_future), timeout=2)
        async with asyncio.timeout(2):
            while not command_done.is_set():
                await asyncio.sleep(0.001)


def test_missing_receipt_never_adopts_delayed_pid(monkeypatch: pytest.MonkeyPatch) -> None:
    process = Mock(pid=123)
    if sys.platform == "win32":
        monkeypatch.setattr(
            reader_command, "_windows_created_at", Mock(side_effect=OSError("denied"))
        )
    else:
        import psutil

        monkeypatch.setattr(psutil, "Process", Mock(side_effect=psutil.AccessDenied(123)))
    assert reader_command.capture_identity(process) == ProcessIdentity(123, None)


@pytest.mark.asyncio
async def test_native_cpu_receipt_and_telemetry(
    monkeypatch: pytest.MonkeyPatch,
    events: list[dict],
    tmp_path: Path,
) -> None:
    import psutil

    monkeypatch.setenv("AGENT_BRIDGE_NAMESPACE_LIST_TTL", "0")
    thread_reads: list[tuple[int, float]] = []

    def thread_clock() -> float:
        value = time.thread_time()
        thread_reads.append((threading.get_ident(), value))
        return value

    def sampler(root: ProcessIdentity) -> OwnedProcessCpuSampler:
        return OwnedProcessCpuSampler(root, thread_clock=thread_clock)

    monkeypatch.setattr(reader_observation, "OwnedProcessCpuSampler", sampler)
    source = str(Path(psutil.__file__).parent.parent)
    receipt = tmp_path / "receipt.json"
    script = (
        "import sys;sys.path.insert(0,sys.argv[1]);"
        "import json,os,pathlib,psutil,time;"
        "pathlib.Path(sys.argv[2]).write_text(json.dumps("
        "{'pid':os.getpid(),'created_at':psutil.Process().create_time()}));"
        "end=time.monotonic()+3.3;"
        "\nwhile time.monotonic()<end: sum(i*i for i in range(1000))"
        '\nprint(\'[{"name":"native"}]\')'
    )
    provider = CliNamespaceResolver(
        "test",
        "unused",
        command=[sys.executable, "-c", script, source, str(receipt)],
    )
    captured: list[tuple[ProcessIdentity, float]] = []
    original = reader_command.capture_identity

    def capture(process: subprocess.Popen[str]) -> ProcessIdentity:
        identity = original(process)
        captured.append((identity, psutil.Process(process.pid).create_time()))
        return identity

    monkeypatch.setattr(reader_command, "capture_identity", capture)
    rows = await provider.list(timeout=8)
    assert [row.name for row in rows] == ["native"]
    native_identity = json.loads(receipt.read_text())
    event = events[-1]
    root, backend_created_at = captured[0]
    assert root.created_at == backend_created_at
    assert event["pid"] == root.pid
    assert event["created_at"] == backend_created_at
    assert native_identity["pid"] in provider._cpu_observation._sampler._records
    assert event["observed_cpu_seconds"] is not None
    assert event["observed_cpu_seconds"] > 0.1
    assert event["coverage"] == "observed_lower_bound"
    assert event["wall_seconds"] >= 3.3
    sample = event["sample"]
    assert sample["state"] == "succeeded"
    assert sample["completed_at"] >= sample["started_at"]
    assert sample["wall_seconds"] >= 0
    assert sample["thread_cpu_seconds"] >= 0
    start, end = thread_reads[2 * (sample["sequence"] - 1):2 * sample["sequence"]]
    assert start[0] == end[0] != threading.get_ident()
    assert sample["thread_cpu_seconds"] == end[1] - start[1]
    assert sample["issues"] == []
    collector = event["collector"]
    assert collector["completed"] == collector["succeeded"] == sample["sequence"]
    assert collector["failed"] == 0
    assert collector["wall_known"] == collector["thread_cpu_known"] == sample["sequence"]
    assert collector["wall_unknown"] == collector["thread_cpu_unknown"] == 0
    assert collector["thread_cpu_seconds"] == sum(
        thread_reads[index + 1][1] - thread_reads[index][1]
        for index in range(0, len(thread_reads), 2)
    )
    assert collector["wall_seconds"] >= sample["wall_seconds"]
    assert collector["issues"] == ()
    assert event["observation_sample"] == {
        "instance": sample["instance"], "sequence": sample["sequence"],
    }
    assert not event["truncated"]
    assert "identity_mismatch" not in {item["reason"] for item in event["issues"]}
    assert not psutil.pid_exists(event["pid"])
    assert str(tmp_path) not in json.dumps(events)


@pytest.mark.asyncio
async def test_untrusted_root_and_exited_fast_reader_never_invent_zero(
    monkeypatch: pytest.MonkeyPatch,
    events: list[dict],
) -> None:
    _mock_command(monkeypatch, ProcessIdentity(123, None))
    provider = CliNamespaceResolver("test", "unused", command=["fake"])
    assert [row.name for row in await provider.list()] == ["valid"]
    assert events[0]["observed_cpu_seconds"] is None
    assert events[0]["issues"] == [{"pid": 123, "reason": "root_identity_unavailable"}]


@pytest.mark.asyncio
async def test_sink_failure_does_not_change_command_result(
    monkeypatch: pytest.MonkeyPatch,
    events: list[dict],
) -> None:
    _mock_command(monkeypatch, ProcessIdentity(123, None))
    telemetry.set_telemetry_sink(Mock(side_effect=RuntimeError("sink failure")))
    result = await reader_observation.ReaderCpuObservation("test").run(["fake"], timeout=1)
    assert result.returncode == 0


def test_owned_timeout_cleanup_matches_subprocess_run(monkeypatch: pytest.MonkeyPatch) -> None:
    owned = reader_command.OwnedReaderCommand()
    process = Mock(pid=123)
    process.__enter__ = Mock(return_value=process)
    process.__exit__ = Mock(return_value=False)
    error = subprocess.TimeoutExpired(["provider"], 0.5, output=b"partial")
    process.communicate.side_effect = [error, ("stdout", "stderr")]
    monkeypatch.setattr(reader_command.subprocess, "Popen", Mock(return_value=process))
    monkeypatch.setattr(
        reader_command, "capture_identity", lambda child: ProcessIdentity(123, None)
    )
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        owned.run(["provider"], timeout=0.5)
    assert caught.value is error
    process.kill.assert_called_once()
    if sys.platform == "win32":
        assert error.stdout == "stdout"
        assert error.stderr == "stderr"
    else:
        process.wait.assert_called_once()
