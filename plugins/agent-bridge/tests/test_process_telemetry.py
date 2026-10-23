"""Bounded shared seam exercised through its real Bridge consumer."""

from __future__ import annotations

import json
import threading

import pytest

from agent_bridge import telemetry
from process_telemetry import SinkSlot
from process_telemetry.buffer import BufferedSink, _Counter, snapshot
from process_telemetry.measurement import TraceContext, begin
from process_telemetry.report import summarize


def test_disabled_and_sampled_out_measurements_do_no_work(monkeypatch):
    from process_telemetry import measurement

    def forbidden(*args):
        raise AssertionError("disabled path performed work")

    monkeypatch.setattr(measurement.time, "perf_counter_ns", forbidden)
    monkeypatch.setattr(measurement.time, "time_ns", forbidden)
    monkeypatch.setattr(measurement.time, "process_time_ns", forbidden)
    monkeypatch.setattr(measurement.uuid, "uuid4", forbidden)
    slot = SinkSlot()
    assert begin(slot, "invalid/content", "unknown") is None
    slot.set(forbidden)
    assert begin(slot, "invalid/content", "unknown", sampled=False) is None


def test_measurement_context_and_private_fields(monkeypatch):
    from process_telemetry import measurement

    times = iter((100, 140))
    cpus = iter((10, 13))
    monkeypatch.setattr(measurement.time, "perf_counter_ns", lambda: next(times))
    monkeypatch.setattr(measurement.time, "process_time_ns", lambda: next(cpus))
    monkeypatch.setattr(measurement.time, "time_ns", lambda: 999)
    records = []
    slot = SinkSlot()
    slot.set(records.append)
    parent = TraceContext("a" * 32, "b" * 16)
    span = begin(slot, "agent-bridge", "spawn", parent=parent,
                 identity={"runtime_version": "1.2.3"})
    assert span.context.trace_id == parent.trace_id
    assert TraceContext.parse(span.context.traceparent()) == span.context
    span.finish(spawn_count=1)
    span.finish()
    assert len(records) == 1
    assert records[0]["duration_ns"] == 40
    assert records[0]["cpu_ns"] == 3
    assert records[0]["parent_span_id"] == parent.span_id
    assert "child_cpu_ns" not in records[0]
    assert "argv" not in records[0]


@pytest.mark.parametrize("value", [
    "", "00-" + "0" * 32 + "-" + "b" * 16 + "-01",
    "00-" + "a" * 32 + "-" + "0" * 16 + "-01",
    "00-" + "a" * 32 + "-" + "b" * 16 + "-ff",
])
def test_invalid_trace_context(value):
    with pytest.raises(ValueError):
        TraceContext.parse(value)


def test_private_or_unbounded_measurement_attributes_refused():
    slot = SinkSlot()
    slot.set(lambda _: None)
    with pytest.raises(ValueError):
        begin(slot, "bridge", "spawn", identity={"path": "private"})
    span = begin(slot, "bridge", "spawn")
    for fields in ({"argv": "private"}, {"spawn_count": True}, {"cpu_ns": -1}):
        with pytest.raises(ValueError):
            span.finish(**fields)
    assert not span.finished
    span.finish("cancelled")


def test_snapshot_is_bounded_independent_and_never_calls_repr():
    class Private:
        def __repr__(self):
            raise AssertionError("must not stringify private object")

    original = {"nested": {"items": ["ok", 1]}}
    copied = snapshot(original)
    original["nested"]["items"].append(2)
    assert copied == {"nested": {"items": ["ok", 1]}}
    for value in ({"x": Private()}, {"x": "a" * 513}, {"x": float("nan")},
                  {"x": [0] * 33}, {"x": 2**63}, {"x": {"y": {"z": {"w": 1}}}}):
        with pytest.raises(ValueError):
            snapshot(value)
    with pytest.raises(ValueError):
        snapshot({"x": "a" * 100}, max_bytes=128)


@pytest.mark.parametrize("key", ["capacity", "max_bytes", "batch_size"])
@pytest.mark.parametrize("value", [1.5, True, "128", None, float("nan")])
def test_noninteger_queue_bounds_rejected_before_start(monkeypatch, key, value):
    def forbidden_start(*args):
        raise AssertionError("invalid construction started a writer")

    monkeypatch.setattr(BufferedSink, "start", forbidden_start)
    slot = SinkSlot()
    with pytest.raises(ValueError, match="integers"):
        slot.set_buffered(lambda _: None, **{key: value})
    assert not slot.enabled()


def test_real_consumer_blocked_sink_drops_and_close_does_not_wait():
    entered, release = threading.Event(), threading.Event()
    seen = []

    def slow(event):
        entered.set()
        assert release.wait(5)
        seen.append(event)

    writer = telemetry.set_buffered_telemetry_sink(slow, capacity=1, batch_size=1)
    try:
        event = telemetry.session_lifecycle_event("session_state_changed", "synthetic",
                                                  {"status": "idle", "message": "private"})
        # A producer is allowed to drop while the drain briefly owns the queue.
        for _ in range(20):
            telemetry.emit(event)
            if writer.stats()["accepted"]:
                break
            entered.wait(.01)
        assert entered.wait(2)
        telemetry.emit(event)
        telemetry.emit(event)
        assert writer.stats()["full"] == 1
        telemetry.clear_telemetry_sink()
        assert writer.join(0) is False
        with pytest.raises(RuntimeError, match="has not stopped"):
            telemetry.set_buffered_telemetry_sink(lambda _: None)
        writer(event)
        assert writer.stats()["inactive"] == 1
    finally:
        telemetry.clear_telemetry_sink()
        writer.close()
        release.set()
        assert writer.join(5)
    assert writer.stats()["accepted"] == 2
    assert writer.stats()["delivered"] == 1
    assert writer.stats()["abandoned"] == 1
    assert len(seen) == 1 and "message" not in seen[0]


def test_replacing_owned_buffer_closes_it_without_waiting():
    writer = telemetry.set_buffered_telemetry_sink(lambda _: None)
    seen = []
    telemetry.set_telemetry_sink(seen.append)
    try:
        writer({"kind": "after-replacement"})
        telemetry.emit({"kind": "replacement"})
        assert writer.stats()["inactive"] == 1
        assert seen == [{"kind": "replacement"}]
    finally:
        telemetry.clear_telemetry_sink()
        assert writer.join(5)


def test_concurrent_registration_preserves_single_owned_writer(monkeypatch):
    slot = SinkSlot()
    constructing, release = threading.Event(), threading.Event()
    original = BufferedSink.start
    writers, failures = [], []

    def paused_start(writer):
        constructing.set()
        assert release.wait(3)
        return original(writer)

    def register():
        try:
            writers.append(slot.set_buffered(lambda _: None))
        except RuntimeError as exc:
            failures.append(str(exc))

    monkeypatch.setattr(BufferedSink, "start", paused_start)
    first = threading.Thread(target=register)
    second = threading.Thread(target=register)
    first.start()
    try:
        assert constructing.wait(2)
        second.start()
    finally:
        release.set()
        first.join(3)
        if second.ident is not None:
            second.join(3)
        slot.set(None)
        for writer in writers:
            assert writer.join(3)
    assert not first.is_alive() and not second.is_alive()
    assert len(writers) == 1
    assert failures == ["previous telemetry writer has not stopped"]
    assert slot.sink is None
    writers[0]({"kind": "after-clear"})
    assert writers[0].stats()["inactive"] == 1


def test_real_buffered_spool_persists_content_free_event(tmp_path):
    path = tmp_path / "diagnostic.jsonl"
    written = threading.Event()
    spool = telemetry.make_spool_sink(path)

    def persist(event):
        spool(event)
        written.set()

    writer = telemetry.set_buffered_telemetry_sink(persist)
    try:
        event = telemetry.session_lifecycle_event(
            "session_state_changed", "synthetic", {"status": "idle", "message": "private"},
        )
        for _ in range(20):
            telemetry.emit(event)
            if writer.stats()["accepted"]:
                break
            written.wait(.01)
        assert written.wait(2)
        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        assert len(records) == 1
        assert records[0]["to"] == "idle"
        assert "private" not in path.read_text(encoding="utf-8")
    finally:
        telemetry.clear_telemetry_sink()
        assert writer.join(5)


def test_throwing_sink_isolated_and_writer_lifecycle_owned():
    attempted = threading.Event()

    def throwing(event):
        attempted.set()
        raise OSError("synthetic")

    writer = BufferedSink(throwing)
    writer({"kind": "before-start"})
    assert writer.stats()["inactive"] == 1
    assert writer.start() is writer
    thread = writer._thread
    assert writer.start()._thread is thread
    try:
        for _ in range(20):
            writer({"kind": "event"})
            if writer.stats()["accepted"]:
                break
            attempted.wait(.01)
        assert attempted.wait(2)
    finally:
        writer.close()
        assert writer.join(5)
    assert writer.stats()["sink_failed"] == 1
    with pytest.raises(RuntimeError):
        writer.start()


def test_contended_and_invalid_producers_return_without_wait():
    writer = BufferedSink(lambda _: None).start()
    try:
        writer({"private": object()})
        with writer._lock:
            writer({"kind": "event"})
        assert writer.stats()["invalid"] == 1
        assert writer.stats()["contended"] == 1
    finally:
        writer.close()
        assert writer.join(5)


def test_concurrent_counter_readers_cannot_manufacture_events():
    counter = _Counter()
    entered, release, overtook = threading.Event(), threading.Event(), threading.Event()
    events = counter._events
    values = []

    class PausingIterator:
        def __next__(self):
            value = next(events)
            if value == 0:
                entered.set()
                assert release.wait(3)
            else:
                overtook.set()
            return value

    counter._events = PausingIterator()
    first = threading.Thread(target=lambda: values.append(counter.read()))
    second = threading.Thread(target=lambda: values.append(counter.read()))
    first.start()
    try:
        assert entered.wait(2)
        second.start()
        assert not overtook.wait(.1)
    finally:
        release.set()
        first.join(3)
        if second.ident is not None:
            second.join(3)
    assert not first.is_alive() and not second.is_alive()
    assert values == [0, 0]
    assert counter.read() == 0
    next(counter)
    assert counter.read() == 1


def test_summary_retains_missing_lossy_and_failed_samples():
    samples = [
        {"outcome": "ok", "duration_ns": 10, "dropped": 0},
        {"outcome": "unavailable", "duration_ns": None, "dropped": 0},
        {"outcome": "ok", "duration_ns": 5, "dropped": 1},
        {"outcome": "error", "duration_ns": 30, "dropped": 0},
    ]
    report = summarize(samples, expected=5, budget_ns=20)
    assert report["samples"] == samples
    assert report["successful"] == 1
    assert report["observed"] == 4
    assert report["max_ns"] == 10
    assert report["complete"] is False
    assert report["budget_met"] is None
    assert report["tail_qualified"] is False


def test_summary_quantiles_and_explicit_ceiling():
    samples = [{"outcome": "ok", "duration_ns": x, "dropped": 0} for x in range(1, 101)]
    report = summarize(samples, expected=100, budget_ns=99)
    assert [report[key] for key in ("p50_ns", "p95_ns", "p99_ns", "max_ns")] == [50, 95, 99, 100]
    assert report["tail_qualified"] is True
    assert report["budget_met"] is False
    with pytest.raises(ValueError):
        summarize(samples, expected=99)
    with pytest.raises(ValueError):
        summarize([{"outcome": "ok", "duration_ns": True, "dropped": 0}], expected=1)
