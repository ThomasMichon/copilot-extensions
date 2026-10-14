"""Bounded producer-local aggregation, including native callback concurrency."""

from __future__ import annotations

import gc
import math
import threading
import weakref
from dataclasses import FrozenInstanceError
from uuid import uuid4

import pytest
from agent_procutil.metrics import SampleAccounting

from agent_bridge import reader_collector
from agent_bridge.reader_collector import CollectorLedger


def test_epochs_and_unknown_empty_snapshot() -> None:
    first, replacement = CollectorLedger(), CollectorLedger()
    snapshot = first.snapshot
    assert len(snapshot.epoch) == 32
    assert snapshot.epoch != replacement.snapshot.epoch
    assert snapshot.completed == 0
    assert snapshot.wall_seconds is snapshot.thread_cpu_seconds is None
    assert snapshot.wall_known == snapshot.wall_unknown == 0
    assert snapshot.thread_cpu_known == snapshot.thread_cpu_unknown == 0
    with pytest.raises(FrozenInstanceError):
        snapshot.completed = 1
    fields = snapshot.fields()
    fields["completed"] = 99
    assert first.snapshot.completed == 0


def test_completed_attempts_mixed_costs_and_old_cached_reuse() -> None:
    ledger = CollectorLedger()
    source = ledger.source()
    instance = uuid4().hex
    pending = SampleAccounting(instance, 1, "pending")
    assert source.complete(pending) is None
    assert ledger.snapshot.completed == 0
    first = SampleAccounting(instance, 1, "succeeded", wall_seconds=0.25)
    old = source.complete(first)
    assert old is not None and old.thread_cpu_seconds is None
    assert source.complete(first) is None
    failed = SampleAccounting(instance, 2, "failed", thread_cpu_seconds=0.5)
    assert source.complete(failed) is not None
    zero = SampleAccounting(instance, 3, "succeeded", wall_seconds=0, thread_cpu_seconds=0)
    assert source.complete(zero) is not None
    assert source.complete(first) is None
    assert source.complete(SampleAccounting(instance, 4, "failed")) is not None
    snapshot = ledger.snapshot
    assert snapshot.completed == 4
    assert snapshot.succeeded == snapshot.failed == 2
    assert snapshot.wall_seconds == 0.25
    assert snapshot.thread_cpu_seconds == 0.5
    assert snapshot.wall_known == snapshot.wall_unknown == 2
    assert snapshot.thread_cpu_known == snapshot.thread_cpu_unknown == 2
    assert snapshot.issues == ()
    assert old.completed == 1


def test_streaming_float_costs_match_compensated_sum_without_losing_contributions() -> None:
    ledger = CollectorLedger()
    source = ledger.source()
    instance = uuid4().hex
    costs = (0.1, 0.2, 0.3)
    for sequence, cost in enumerate(costs, 1):
        source.complete(SampleAccounting(
            instance, sequence, "succeeded", thread_cpu_seconds=cost,
        ))
    actual = ledger.snapshot.thread_cpu_seconds
    expected = math.fsum(costs)
    assert actual != expected
    assert actual == pytest.approx(expected, rel=1e-12, abs=1e-12)
    assert actual != pytest.approx(math.fsum(costs[:-1]), rel=1e-12, abs=1e-12)
    assert ledger.snapshot.completed == ledger.snapshot.thread_cpu_known == len(costs)
    assert ledger.snapshot.thread_cpu_unknown == 0
    assert ledger.snapshot.issues == ()


def test_zero_is_known_and_invalid_costs_are_unknown(caplog: pytest.LogCaptureFixture) -> None:
    ledger = CollectorLedger()
    source = ledger.source()
    instance = uuid4().hex
    source.complete(SampleAccounting(instance, 1, "succeeded", wall_seconds=0))
    assert ledger.snapshot.wall_seconds == 0
    assert ledger.snapshot.thread_cpu_seconds is None
    for sequence, invalid in enumerate((float("inf"), float("nan"), -1.0, True), 2):
        source.complete(SampleAccounting(
            instance, sequence, "succeeded",
            wall_seconds=invalid, thread_cpu_seconds=invalid,
        ))
    snapshot = ledger.snapshot
    assert snapshot.wall_seconds == 0
    assert snapshot.wall_known == 1 and snapshot.wall_unknown == 4
    assert snapshot.thread_cpu_seconds is None
    assert snapshot.thread_cpu_unknown == 5
    assert snapshot.issues == ("thread_cpu_invalid", "wall_invalid")
    assert "contribution is invalid" in caplog.text


def test_gap_and_changed_identity_do_not_claim_continuity() -> None:
    ledger = CollectorLedger()
    source = ledger.source()
    instance = uuid4().hex
    source.complete(SampleAccounting(instance, 2, "failed"))
    source.complete(SampleAccounting(instance, 4, "succeeded"))
    assert ledger.snapshot.completed == 2
    assert ledger.snapshot.issues == ("sample_continuity_lost",)
    assert source.complete(SampleAccounting(uuid4().hex, 1, "succeeded")) is None
    assert ledger.snapshot.completed == 2


def test_sequence_rotation_retains_old_cursor_and_epoch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(reader_collector, "_MAX_SEQUENCE", 2)
    ledger = CollectorLedger()
    source = ledger.source()
    epoch = ledger.snapshot.epoch
    first, second = uuid4().hex, uuid4().hex
    old = SampleAccounting(first, 2, "succeeded", wall_seconds=1)
    source.complete(SampleAccounting(first, 1, "succeeded", wall_seconds=1))
    source.complete(old)
    source.complete(SampleAccounting(second, 1, "succeeded", wall_seconds=1))
    assert source.complete(old) is None
    assert ledger.snapshot.completed == 3
    assert ledger.snapshot.wall_seconds == 3
    assert ledger.snapshot.epoch == epoch
    assert ledger.snapshot.issues == ()


def test_overflow_is_explicit_and_never_wraps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(reader_collector, "_MAX_COUNT", 2)
    ledger = CollectorLedger()
    source = ledger.source()
    instance = uuid4().hex
    for sequence in range(1, 6):
        source.complete(SampleAccounting(
            instance, sequence, "succeeded",
            wall_seconds=1e308, thread_cpu_seconds=1,
        ))
    snapshot = ledger.snapshot
    assert snapshot.completed == snapshot.succeeded == 2
    assert snapshot.failed == 0
    assert snapshot.wall_seconds == 1e308
    assert snapshot.wall_known == snapshot.wall_unknown == 1
    assert snapshot.thread_cpu_seconds == 2
    assert snapshot.thread_cpu_known == 2 and snapshot.thread_cpu_unknown == 0
    assert snapshot.issues == ("counter_overflow", "wall_overflow")


def test_invalid_provenance_is_visible_without_invented_attempts() -> None:
    ledger = CollectorLedger()
    source = ledger.source()
    assert source.complete(SampleAccounting("invalid", 1, "succeeded")) is None
    assert ledger.snapshot.completed == 0
    assert ledger.snapshot.issues == ("sample_provenance_invalid",)


def test_sources_have_no_ledger_history_and_late_duplicate_stays_deduplicated() -> None:
    ledger = CollectorLedger()
    first = ledger.source()
    sample = SampleAccounting(uuid4().hex, 1, "succeeded", wall_seconds=1)
    first.complete(sample)
    retired: list[weakref.ReferenceType] = []
    for _ in range(1000):
        source = ledger.source()
        source.complete(SampleAccounting(uuid4().hex, 1, "succeeded", wall_seconds=1))
        retired.append(weakref.ref(source))
    del source
    gc.collect()
    assert all(reference() is None for reference in retired)
    assert first.complete(sample) is None
    assert ledger.snapshot.completed == 1001
    assert ledger.snapshot.wall_seconds == 1001
    assert ledger.snapshot.issues == ()
    assert set(vars(ledger)) == {"_lock", "_snapshot"}
    assert set(vars(first)) == {"_ledger", "_last", "_previous"}


def test_concurrent_completions_and_publication_are_coherent() -> None:
    ledger = CollectorLedger()
    first, second = ledger.source(), ledger.source()
    instances = uuid4().hex, uuid4().hex
    barrier = threading.Barrier(3)
    done = [threading.Event(), threading.Event()]

    def complete(index: int) -> None:
        barrier.wait(timeout=5)
        try:
            source = (first, second)[index]
            for sequence in range(1, 501):
                sample = SampleAccounting(
                    instances[index], sequence, "succeeded" if index == 0 else "failed",
                    wall_seconds=0.5, thread_cpu_seconds=0.25,
                )
                source.complete(sample)
                source.complete(sample)
        finally:
            done[index].set()

    threads = [threading.Thread(target=complete, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    try:
        barrier.wait(timeout=5)
        while not all(event.is_set() for event in done):
            snapshot = ledger.snapshot
            assert snapshot.completed == snapshot.succeeded + snapshot.failed
            assert snapshot.wall_known == snapshot.thread_cpu_known == snapshot.completed
            assert snapshot.wall_unknown == snapshot.thread_cpu_unknown == 0
            assert snapshot.wall_seconds == (
                snapshot.completed * 0.5 if snapshot.completed else None
            )
            assert snapshot.thread_cpu_seconds == (
                snapshot.completed * 0.25 if snapshot.completed else None
            )
    finally:
        for thread in threads:
            thread.join(timeout=5)
    assert not any(thread.is_alive() for thread in threads)
    assert ledger.snapshot.completed == 1000
    assert ledger.snapshot.succeeded == ledger.snapshot.failed == 500
    assert ledger.snapshot.issues == ()
