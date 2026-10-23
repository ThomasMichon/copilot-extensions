"""Source observation admission and reuse of the existing frame writer."""

from __future__ import annotations

import json
import io
import threading

import pytest

from worktree_manager.performance_observation import SourceObservation
from worktree_manager.performance_report import child_result, source_report, trace_readback
from worktree_manager.production_picker.picker_tui import frame_health


@pytest.fixture(autouse=True)
def clear_source_scope(monkeypatch):
    monkeypatch.setattr(frame_health, "_source_reporter", None)
    monkeypatch.setattr(frame_health, "_last_source_reporter", None)


def admitted(**kwargs):
    return SourceObservation({
        "source_revision": "a" * 40, "selected_version": "1.0",
        "serving_version": "1.0", "payload_version": None,
    }, **kwargs)


def test_disabled_reporter_has_no_clock_file_thread_or_import_work(monkeypatch):
    monkeypatch.delenv("AGENT_WORKTREES_LAUNCH_TRACE", raising=False)
    monkeypatch.delenv("AGENT_WORKTREES_PICKER_FRAME_HEALTH", raising=False)

    def forbidden(*args, **kwargs):
        raise AssertionError("disabled reporter performed work")

    monkeypatch.setattr(frame_health, "_timestamp", forbidden)
    monkeypatch.setattr(frame_health.threading, "Thread", forbidden)
    assert frame_health.FrameHealthReporter.from_env() is None
    frame_health.append_launch_event("source_entry", private=object())


def test_admission_is_copied_once_and_read_only(tmp_path):
    identity = {"source_revision": "a" * 40}
    observation = SourceObservation(identity)
    identity["source_revision"] = "b" * 40
    assert observation.identity["source_revision"] == "a" * 40
    with pytest.raises(TypeError):
        observation.identity["source_revision"] = "c" * 40
    with pytest.raises(AttributeError):
        observation.sampled = False
    with pytest.raises(AttributeError):
        observation.endpoint = "http://127.0.0.1:1/v1/traces"
    reporter = frame_health.FrameHealthReporter(tmp_path / "events", observation=observation)
    with pytest.raises(AttributeError):
        reporter.observation = None


def test_source_scope_reuses_one_queue_and_writer_and_removes_private_fields(tmp_path):
    observation = admitted()
    reporter = frame_health.FrameHealthReporter(tmp_path / "events.jsonl", observation=observation)
    with frame_health.source_observation_scope(reporter):
        frame_health.append_launch_event("source_entry", duration_ns=0, debug="private")
        assert frame_health.FrameHealthReporter.from_env() is reporter
        assert not reporter.path.exists()
        assert reporter._thread is None
        reporter.start()
        thread = reporter._thread
        reporter.start()
        assert reporter._thread is thread
        reporter.observe("input_ack", duration_ns=1, argv="private")
    events = [json.loads(line) for line in reporter.path.read_text().splitlines()]
    assert [value["event"] for value in events] == [
        "source_entry", "textual_first_refresh", "input_ack", "picker_stop",
    ]
    assert "private" not in reporter.path.read_text()
    assert {value["span"]["trace_id"] for value in events} == {observation.trace_id}
    assert len({value["span"]["span_id"] for value in events}) == 4
    state = reporter.observation_summary()
    assert state["counts"]["queued"] == state["counts"]["written"] == 4
    assert not state["writer_alive"] and not state["queued_remaining"]
    assert state["identity_missing"] == ["payload_version"]
    frame_health.append_launch_event("textual_app_start")
    assert observation.counts()["after_close"] == 1
    assert frame_health.FrameHealthReporter.from_env() is None
    with pytest.raises(RuntimeError, match="single-use"):
        with frame_health.source_observation_scope(reporter):
            pass


def test_queue_wait_uses_enqueue_not_backdated_observation(tmp_path, monkeypatch):
    from worktree_manager import performance_observation

    observation = SourceObservation({}, origin_ns=0, wall_ns=0)
    reporter = frame_health.FrameHealthReporter(tmp_path / "events", observation=observation)
    clock = iter((100, 200, 300))
    monkeypatch.setattr(performance_observation.time, "perf_counter_ns", lambda: next(clock))
    reporter.observe("source_entry", observed_ns=50, duration_ns=0)
    record = reporter._queue.get_nowait()
    observation.deliver(io.StringIO(), [record])
    assert record["observed_ns"] == 50
    assert record["enqueued_ns"] == 200
    assert record["span"]["queue_wait_ns"] == 100
    assert record["span"]["start_time_ns"] == 50


def test_sampled_out_trace_creates_no_writer_or_file(tmp_path, monkeypatch):
    observation = admitted(sampled=False)
    reporter = frame_health.FrameHealthReporter(tmp_path / "events.jsonl", observation=observation)

    def forbidden(*args, **kwargs):
        raise AssertionError("sampled-out trace started a writer")

    monkeypatch.setattr(frame_health.threading, "Thread", forbidden)
    with frame_health.source_observation_scope(reporter):
        reporter.observe("source_entry")
        reporter.start()
    assert not reporter.path.exists()
    state = reporter.observation_summary()
    assert state["counts"]["sampled_out"] == 3
    assert state["first_refresh_observed"] and not state["writer_started"]


def test_slow_export_does_not_hold_ui_and_cannot_create_a_second_writer(tmp_path, monkeypatch):
    from process_telemetry import otlp

    entered, release = threading.Event(), threading.Event()
    writer_threads = []

    class Slow:
        def __init__(self, endpoint):
            writer_threads.append(threading.get_ident())

        def export_batch(self, spans):
            entered.set()
            assert release.wait(5)

        def close(self):
            pass

    monkeypatch.setattr(otlp, "LocalOtlpSink", Slow)
    observation = admitted(endpoint="http://127.0.0.1:1/v1/traces")
    reporter = frame_health.FrameHealthReporter(tmp_path / "events.jsonl", observation=observation)
    try:
        with frame_health.source_observation_scope(reporter):
            reporter.start()
            assert entered.wait(2)
            for _ in range(256):
                reporter.observe("input_ack", duration_ns=0)
            assert observation.counts()["queue_full"] > 0
            from types import SimpleNamespace
            from worktree_manager.production_picker.picker_tui.engine_loading import PickerScreenLoadingMixin
            screen = SimpleNamespace(
                _bg_cancel=threading.Event(), _provider_loader_lock=threading.Lock(),
                _provider_loader=None, _frame_health=reporter, _pivot_runtimes={},
            )
            original_join = reporter._thread.join
            def forbidden_join(*args, **kwargs):
                raise AssertionError("source UI unmount joined the exporter")
            reporter._thread.join = forbidden_join
            try:
                PickerScreenLoadingMixin.on_unmount(screen)
            finally:
                reporter._thread.join = original_join
            assert reporter._thread.is_alive()
            other = frame_health.FrameHealthReporter(tmp_path / "other", observation=admitted())
            with pytest.raises(RuntimeError):
                with frame_health.source_observation_scope(other):
                    pass
            assert writer_threads == [reporter._thread.ident]
            assert writer_threads[0] != threading.get_ident()
            release.set()
    finally:
        release.set()
        reporter.close()
        reporter.drain()
    assert not reporter.observation_summary()["writer_alive"]


def test_source_write_failure_is_visible_and_not_restarted(tmp_path):
    parent = tmp_path / "not-a-directory"
    parent.write_text("synthetic")
    observation = admitted()
    reporter = frame_health.FrameHealthReporter(parent / "events", observation=observation)
    with frame_health.source_observation_scope(reporter):
        reporter.start()
        reporter.close(wait=True)
        reporter.start()
    assert observation.counts()["writer_failed"] == 1
    assert reporter.observation_summary()["queued_remaining"] > 0


def test_admission_finishes_before_close_and_owner_summary(tmp_path, monkeypatch):
    observation = admitted()
    reporter = frame_health.FrameHealthReporter(tmp_path / "events", observation=observation)
    entered, release = threading.Event(), threading.Event()
    closing, finished = threading.Event(), threading.Event()
    captured = []
    original = observation.capture
    def capture(event):
        if event["event"] == "input_ack":
            entered.set()
            assert release.wait(3)
        return original(event)
    monkeypatch.setattr(observation, "capture", capture)
    reporter.start()
    producer = threading.Thread(target=lambda: reporter.observe("input_ack", duration_ns=0))
    def close():
        closing.set()
        reporter.close(wait=True)
        assert reporter.drain()
        captured.append(reporter.observation_summary())
        finished.set()
    closer = threading.Thread(target=close)
    producer.start()
    try:
        assert entered.wait(2)
        closer.start()
        assert closing.wait(2)
        assert not finished.wait(.05)
    finally:
        release.set()
        producer.join(3)
        if closer.ident is not None:
            closer.join(4)
        reporter.close()
        reporter.drain()
    assert not producer.is_alive() and not closer.is_alive()
    assert finished.is_set()
    state = captured[0]
    assert state["counts"]["queued"] == state["counts"]["written"] == 3
    assert state["queued_remaining"] == state["abandoned"] == 0
    assert state["counts"]["after_close"] == 0


def test_summary_snapshots_writer_reference_once(tmp_path, monkeypatch):
    from types import SimpleNamespace

    reporter = frame_health.FrameHealthReporter(tmp_path / "events", observation=admitted())
    reads = []
    writer = SimpleNamespace(is_alive=lambda: False)
    def current_thread(self):
        reads.append(True)
        return writer if len(reads) == 1 else None
    monkeypatch.setattr(frame_health.FrameHealthReporter, "_thread", property(current_thread), raising=False)
    assert reporter.observation_summary()["writer_alive"] is False
    assert len(reads) == 1


def test_terminal_counts_are_read_after_writer_liveness(tmp_path):
    observation = admitted()
    reporter = frame_health.FrameHealthReporter(tmp_path / "events", observation=observation)
    observation.increment("queued", 3)
    observation.increment("written", 2)
    reporter._source_closed = True
    class Completing:
        def is_alive(self):
            observation.increment("written")
            reporter._thread = None
            return False
    reporter._thread = Completing()
    state = reporter.observation_summary()
    assert state["counts"]["queued"] == state["counts"]["written"] == 3
    assert state["abandoned"] == 0


@pytest.mark.parametrize("initialization_failure", [False, True])
def test_export_failure_preserves_local_observations(tmp_path, monkeypatch, initialization_failure):
    from process_telemetry import otlp

    class Unavailable:
        def __init__(self, endpoint):
            if initialization_failure:
                raise ImportError("synthetic unavailable SDK")

        def export_batch(self, spans):
            raise OSError("synthetic receiver outage")

        def close(self):
            pass

    monkeypatch.setattr(otlp, "LocalOtlpSink", Unavailable)
    observation = admitted(endpoint="http://127.0.0.1:1/v1/traces")
    reporter = frame_health.FrameHealthReporter(tmp_path / "events", observation=observation)
    with frame_health.source_observation_scope(reporter):
        reporter.start()
    counts = observation.counts()
    assert counts["queued"] == counts["written"] == counts["export_failed"] == 2
    assert counts["sdk_unavailable"] == int(initialization_failure)
    assert len(reporter.path.read_text().splitlines()) == 2
    assert not reporter.observation_summary()["writer_alive"]


def test_report_preserves_failure_drop_and_tiny_sample_limits():
    good = {
        "mode": "disabled", "outcome": "ok",
        "points_ns": {"source_entry": 0, "first_refresh": 10, "input_ack": 2, "fixture_rows": 11},
    }
    failed = {"mode": "enabled", "outcome": "error", "points_ns": {},
              "telemetry": {"counts": {"queue_full": 3}}}
    report = source_report([good, failed], expected_per_mode=2)
    assert report["modes"]["disabled"]["completed"] == 1
    assert report["modes"]["enabled"]["failed"] == 1
    assert report["modes"]["enabled"]["drops"] == 3
    assert report["modes"]["enabled"]["telemetry_incomplete"] == 1
    assert report["modes"]["disabled"]["points"]["first_refresh"]["p99_ns"] is None
    assert report["overhead"]["first_refresh"]["difference_of_medians_ns"] is None
    assert report["qualification"] == "not-qualified-for-headed-or-installed-budgets"
    assert "authoritative_rows" in report["unavailable"]


@pytest.mark.parametrize("text", ["", "MANAGER_OBSERVATION invalid", "MANAGER_OBSERVATION []"])
def test_missing_or_malformed_child_result_stays_a_failed_sample(text):
    value = child_result(text, mode="enabled", returncode=0)
    assert value["outcome"] == "error"
    report = source_report([value], expected_per_mode=1)
    assert report["modes"]["enabled"]["completed"] == 1
    assert report["modes"]["enabled"]["failed"] == 1


def test_nonzero_process_exit_cannot_be_overridden_by_a_child_report():
    value = child_result(
        'MANAGER_OBSERVATION {"mode":"enabled","outcome":"ok","points_ns":{}}',
        mode="enabled", returncode=3,
    )
    assert value["outcome"] == "error" and value["exit_code"] == 3


@pytest.mark.parametrize("duration", [True, -1, "slow", {}, 1.5, float("nan")])
def test_malformed_child_durations_are_failed_samples(duration):
    value = child_result(
        "MANAGER_OBSERVATION " + json.dumps({
            "mode": "disabled", "outcome": "ok", "points_ns": {"first_refresh": duration},
        }),
        mode="disabled", returncode=0,
    )
    report = source_report([value], expected_per_mode=1)
    assert report["modes"]["disabled"]["completed"] == report["modes"]["disabled"]["failed"] == 1
    assert value["error_code"] == "invalid_child_report"


def test_overhead_distinguishes_paired_deltas_from_difference_of_medians():
    runs = []
    for mode, values in (("disabled", [1, 10, 20]), ("enabled", [5, 6, 30])):
        for index, value in enumerate(values):
            runs.append({
                "mode": mode, "sample_index": index, "outcome": "ok",
                "points_ns": {"source_entry": 0, "first_refresh": value,
                              "input_ack": value, "fixture_rows": value},
            })
    overhead = source_report(runs, expected_per_mode=3)["overhead"]["first_refresh"]
    assert overhead["difference_of_medians_ns"] == -4
    assert overhead["paired_median_delta_ns"] == 4
    assert [pair["delta_ns"] for pair in overhead["paired_deltas"]] == [4, -4, 10]


def test_even_condition_medians_are_arithmetic_not_nearest_rank():
    runs = []
    for mode, values in (("disabled", [1, 10]), ("enabled", [3, 4])):
        for index, value in enumerate(values):
            runs.append({
                "mode": mode, "sample_index": index, "outcome": "ok",
                "points_ns": {"source_entry": 0, "first_refresh": value,
                              "input_ack": value, "fixture_rows": value},
            })
    report = source_report(runs, expected_per_mode=2)
    overhead = report["overhead"]["first_refresh"]
    assert overhead["difference_of_medians_ns"] == -2
    assert overhead["ratio_of_medians"] == pytest.approx(3.5 / 5.5)
    assert report["modes"]["disabled"]["points"]["first_refresh"]["p50_ns"] == 1


def test_readback_returns_only_owned_identity_metadata():
    record = {"resourceSpans": [{"scopeSpans": [{"spans": [
        {"traceId": "owned", "spanId": "one", "private": "do-not-return"},
        {"traceId": "other", "spanId": "two"},
    ]}]}]}
    report = trace_readback([json.dumps(record)], {("owned", "one"), ("owned", "missing")})
    assert report["matched"] == 1 and report["expected"] == 2
    assert not report["complete"]
    assert report["missing"] == [{"trace_id": "owned", "span_id": "missing"}]
    assert "do-not-return" not in json.dumps(report)


def test_readback_requires_expected_labels_and_reports_duplicates():
    record = {"resourceSpans": [{"scopeSpans": [{"spans": [{
        "traceId": "owned", "spanId": "one",
        "attributes": [{"key": "ce.stage", "value": {"stringValue": "first_refresh"}}],
    }]}]}]}
    lines = [json.dumps(record)] * 2
    mismatch = trace_readback(lines, {("owned", "one")},
                              expected_labels={("owned", "one"): {"ce.stage": "input_ack"}})
    assert mismatch["identity_matches"] == 1 and mismatch["label_mismatches"] == 1
    assert mismatch["duplicates"] == 1 and not mismatch["complete"]
    matched = trace_readback(lines, {("owned", "one")},
                             expected_labels={("owned", "one"): {"ce.stage": "first_refresh"}})
    assert matched["matched"] == 1 and matched["complete"]


@pytest.mark.parametrize("reverse", [False, True])
def test_one_bad_duplicate_cannot_be_hidden_by_a_good_match(reverse):
    def line(stage):
        return json.dumps({"resourceSpans": [{"scopeSpans": [{"spans": [{
            "traceId": "owned", "spanId": "one",
            "attributes": [{"key": "ce.stage", "value": {"stringValue": stage}}],
        }]}]}]})
    lines = [line("first_refresh"), line("input_ack")]
    report = trace_readback(
        list(reversed(lines)) if reverse else lines, {("owned", "one")},
        expected_labels={("owned", "one"): {"ce.stage": "first_refresh"}},
    )
    assert report["matched"] == report["label_mismatches"] == 1
    assert report["duplicates"] == 1
    assert not report["complete"]


def test_manager_uses_canonical_vendored_dependency():
    import tomllib
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    project = tomllib.loads((root / "pyproject.toml").read_text())
    assert "agent-process-telemetry" in project["project"]["dependencies"]
    assert project["tool"]["uv"]["sources"]["agent-process-telemetry"] == {
        "path": "../libs/process-telemetry", "editable": True,
    }
