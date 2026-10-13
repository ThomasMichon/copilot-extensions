"""Native accounting remains distinct from reader CPU and waiter lifetime."""

from __future__ import annotations

import asyncio
import json
import subprocess
import threading
from collections.abc import Iterator

import pytest
from agent_procutil.metrics import CpuObservation, OwnedProcessCpuSampler, ProcessIdentity

from agent_bridge import reader_command, reader_observation, telemetry

ROOT = ProcessIdentity(123, 456.0)


@pytest.fixture
def sample_events() -> Iterator[list[dict]]:
    events: list[dict] = []
    telemetry.set_telemetry_sink(events.append)
    try:
        yield events
    finally:
        telemetry.clear_telemetry_sink()


def _mock_command(monkeypatch: pytest.MonkeyPatch) -> None:
    def run(
        self: reader_command.OwnedReaderCommand, cmd: list[str], *, timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        self.receipt.set_result(ROOT)
        self.wall_seconds = 0.25
        return subprocess.CompletedProcess(cmd, 0, "[]", "private output")

    monkeypatch.setattr(reader_command.OwnedReaderCommand, "run", run)


@pytest.mark.asyncio
async def test_completed_accounting_export_and_cached_reuse_are_not_new_cost(
    monkeypatch: pytest.MonkeyPatch, sample_events: list[dict],
) -> None:
    _mock_command(monkeypatch)
    monkeypatch.setattr(
        OwnedProcessCpuSampler, "_sample_sync",
        lambda self: CpuObservation(100.0, (ROOT,), (), False),
    )
    observer = reader_observation.ReaderCpuObservation("test")
    assert (await observer.run(["private-command"], timeout=1)).returncode == 0
    completed = [e for e in sample_events if e["event"] == "reader_cpu_sample_completed"]
    reads = [e for e in sample_events if e["event"] == "reader_cpu_observation"]
    assert len(completed) == len(reads) == 1
    sample = completed[0]["sample"]
    assert sample["state"] == "succeeded"
    assert sample["wall_seconds"] >= 0
    assert sample["thread_cpu_seconds"] >= 0
    assert reads[0]["sample"] == sample
    assert reads[0]["observation_sample"] == {
        "instance": sample["instance"], "sequence": sample["sequence"],
    }
    assert reads[0]["observed_cpu_seconds"] == 100.0
    collector = completed[0]["collector"]
    assert reads[0]["collector"] == collector
    assert collector["completed"] == collector["succeeded"] == 1
    assert collector["failed"] == 0
    assert collector["wall_seconds"] == sample["wall_seconds"]
    assert collector["thread_cpu_seconds"] == sample["thread_cpu_seconds"]
    assert collector["wall_known"] == collector["thread_cpu_known"] == 1
    assert collector["wall_unknown"] == collector["thread_cpu_unknown"] == 0
    sampler = observer._sampler
    assert sampler is not None
    observer._next_sample_at = 0
    observer._sample_completed = None
    cached, issue = await observer._sample(sampler, ROOT, None)
    assert issue is None and cached is not None
    assert cached.sample is sampler.current_sample
    assert len([e for e in sample_events if e["event"] == "reader_cpu_sample_completed"]) == 1
    assert observer._collector.snapshot.fields() == collector
    assert "private" not in json.dumps(sample_events)


@pytest.mark.asyncio
@pytest.mark.parametrize("fails", [False, True])
@pytest.mark.parametrize("cancel_observer", [False, True])
async def test_late_completion_exports_final_cost_after_command_or_waiter_exit(
    monkeypatch: pytest.MonkeyPatch, sample_events: list[dict], fails: bool,
    cancel_observer: bool,
) -> None:
    started, release, command_release = threading.Event(), threading.Event(), threading.Event()

    def run(
        self: reader_command.OwnedReaderCommand, cmd: list[str], *, timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        self.receipt.set_result(ROOT)
        if cancel_observer:
            assert command_release.wait(5)
        return subprocess.CompletedProcess(cmd, 0, "[]", "")

    def sample(self: OwnedProcessCpuSampler) -> CpuObservation:
        started.set()
        assert release.wait(5)
        if fails:
            raise RuntimeError("private sample detail")
        return CpuObservation(0.5, (ROOT,), (), False)

    monkeypatch.setattr(reader_command.OwnedReaderCommand, "run", run)
    monkeypatch.setattr(OwnedProcessCpuSampler, "_sample_sync", sample)
    observer = reader_observation.ReaderCpuObservation("private/namespace")
    read = asyncio.create_task(observer.run(["private-command"], timeout=1))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        if cancel_observer:
            read.cancel()
            with pytest.raises(asyncio.CancelledError):
                await read
        else:
            assert (await read).returncode == 0
        holder = observer.pending_sample_future
        assert holder is not None
        pending_event = next(e for e in sample_events if e["event"] == "reader_cpu_observation")
        pending = pending_event["sample"]
        assert pending["state"] == "pending"
        assert pending["completed_at"] is pending["wall_seconds"] is None
        assert pending["thread_cpu_seconds"] is None
        assert not any(e["event"] == "reader_cpu_sample_completed" for e in sample_events)
        pending_collector = pending_event["collector"]
        assert pending_collector["completed"] == 0
        assert pending_collector["wall_seconds"] is None
        assert pending_collector["thread_cpu_seconds"] is None
        release.set()
        await asyncio.gather(asyncio.wrap_future(holder), return_exceptions=True)
        final = [e for e in sample_events if e["event"] == "reader_cpu_sample_completed"]
        assert len(final) == 1
        completed = final[0]["sample"]
        assert completed["state"] == ("failed" if fails else "succeeded")
        assert completed["instance"] == pending["instance"]
        assert completed["sequence"] == pending["sequence"]
        assert completed["completed_at"] >= completed["started_at"]
        assert completed["wall_seconds"] >= (0.1 if not cancel_observer else 0)
        assert completed["thread_cpu_seconds"] >= 0
        assert final[0]["namespace"] == "other"
        collector = final[0]["collector"]
        assert collector["epoch"] == pending_collector["epoch"]
        assert collector["completed"] == 1
        assert collector["failed"] == int(fails)
        assert collector["succeeded"] == int(not fails)
        assert collector["wall_seconds"] == completed["wall_seconds"]
        assert collector["thread_cpu_seconds"] == completed["thread_cpu_seconds"]
        assert "private" not in json.dumps(sample_events)
    finally:
        release.set()
        command_release.set()
        await asyncio.gather(read, return_exceptions=True)
        if observer._sample_holder is not None:
            await asyncio.gather(observer._sample_holder, return_exceptions=True)


@pytest.mark.asyncio
async def test_failed_sample_accounting_does_not_label_prior_cpu_as_new(
    monkeypatch: pytest.MonkeyPatch, sample_events: list[dict],
) -> None:
    _mock_command(monkeypatch)
    monkeypatch.setattr(
        OwnedProcessCpuSampler, "_sample_sync",
        lambda self: CpuObservation(100.0, (ROOT,), (), False),
    )
    observer = reader_observation.ReaderCpuObservation("test")
    await observer.run(["fake"], timeout=1)
    sampler = observer._sampler
    assert sampler is not None
    previous = sampler._last
    assert previous is not None and previous.sample is not None
    old_accounting = previous.sample

    def fail(self: OwnedProcessCpuSampler) -> CpuObservation:
        raise RuntimeError("private failure")

    monkeypatch.setattr(OwnedProcessCpuSampler, "_sample_sync", fail)
    sampler._last_at = None
    observer._next_sample_at = 0
    observer._sample_completed = None
    observation, issue = await observer._sample(sampler, ROOT, previous)
    assert observation is previous
    assert issue is not None and issue.reason == "sampling_failed"
    current = sampler.current_sample
    assert current is not None and current.state == "failed"
    assert current.instance == old_accounting.instance
    assert current.sequence > old_accounting.sequence
    assert observation.sample is old_accounting
    assert len([e for e in sample_events if e["event"] == "reader_cpu_sample_completed"]) == 2
    collector = observer._collector.snapshot
    assert collector.completed == 2 and collector.succeeded == collector.failed == 1
    assert collector.wall_known == collector.thread_cpu_known == 2


def test_loop_shutdown_and_recreation_keep_late_cost_in_original_epoch(
    monkeypatch: pytest.MonkeyPatch, sample_events: list[dict],
) -> None:
    started, release = threading.Event(), threading.Event()

    def run(
        self: reader_command.OwnedReaderCommand, cmd: list[str], *, timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        self.receipt.set_result(ProcessIdentity(123 if cmd[0] == "old" else 124, 456.0))
        return subprocess.CompletedProcess(cmd, 0, "[]", "")

    def sample(self: OwnedProcessCpuSampler) -> CpuObservation:
        if self._root.pid == 123:
            started.set()
            assert release.wait(5)
        return CpuObservation(0.5, (self._root,), (), False)

    monkeypatch.setattr(reader_command.OwnedReaderCommand, "run", run)
    monkeypatch.setattr(OwnedProcessCpuSampler, "_sample_sync", sample)
    original = reader_observation.ReaderCpuObservation("test")
    replacement = reader_observation.ReaderCpuObservation("test")
    holder = None
    try:
        asyncio.run(original.run(["old"], timeout=1))
        assert started.is_set()
        holder = original.pending_sample_future
        assert holder is not None and not holder.cancel()
        before = original._collector.snapshot
        assert before.completed == 0
        asyncio.run(replacement.run(["new"], timeout=1))
        current = replacement._collector.snapshot
        assert current.completed == 1 and current.epoch != before.epoch
        release.set()
        assert holder.result(timeout=2).observed_cpu_seconds == 0.5
        old = original._collector.snapshot
        assert old.completed == 1 and old.epoch == before.epoch
        assert replacement._collector.snapshot is current
        late = sample_events[-1]
        assert late["event"] == "reader_cpu_sample_completed"
        assert late["collector"] == old.fields()
        assert all(event["namespace"] == "test" for event in sample_events)
        assert {event["role"] for event in sample_events} == {"namespace-list"}
        assert {event["plugin"] for event in sample_events} == {"agent-bridge"}
    finally:
        release.set()
        if holder is not None:
            holder.result(timeout=2)
