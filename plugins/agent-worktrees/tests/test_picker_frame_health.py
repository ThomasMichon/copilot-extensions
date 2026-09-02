from __future__ import annotations

import json
import queue
import threading

import pytest

from agent_worktrees.picker_tui.frame_health import (
    FrameHealthReporter,
    append_launch_event,
)


def test_frame_health_reports_only_threshold_gaps(tmp_path, monkeypatch):
    path = tmp_path / "frame-health.jsonl"
    times = iter([10.0, 10.1, 10.8])
    monkeypatch.setattr(
        "agent_worktrees.picker_tui.frame_health.time.monotonic",
        lambda: next(times),
    )
    reporter = FrameHealthReporter(path, threshold_seconds=0.5)

    reporter.start()
    reporter.tick(frame=1, debug="loading", busy="Loading")
    reporter.tick(frame=2, debug="loading", busy="Loading")
    reporter.close(wait=True)

    events = [json.loads(line) for line in path.read_text().splitlines()]
    assert [event["event"] for event in events] == [
        "textual_first_refresh",
        "gap",
        "picker_stop",
    ]
    assert events[1]["gap_ms"] == 700.0
    assert events[1]["frame"] == 2


def test_frame_health_full_queue_still_stops(tmp_path):
    reporter = FrameHealthReporter(tmp_path / "unused.jsonl")
    reporter._thread = threading.Thread(target=reporter._stop.wait, daemon=True)
    reporter._thread.start()
    for index in range(reporter._queue.maxsize):
        reporter._queue.put_nowait({"index": index})

    reporter.close(wait=True)

    assert reporter._stop.is_set()
    assert reporter._thread is None
    with pytest.raises(queue.Full):
        reporter._queue.put_nowait({"overflow": True})


def test_launch_trace_records_first_refresh_without_gap_reporting(
    tmp_path, monkeypatch
):
    path = tmp_path / "picker-launches.jsonl"
    monkeypatch.setenv("AGENT_WORKTREES_LAUNCH_TRACE", str(path))
    monkeypatch.setenv("AGENT_WORKTREES_LAUNCH_ID", "demo-123")
    monkeypatch.setenv("AGENT_WORKTREES_BINSTUB_STARTED", "start-value")
    reporter = FrameHealthReporter.from_env()

    assert reporter is not None
    reporter.start()
    reporter.tick(frame=1, debug="loading", busy="Loading")
    reporter.close(wait=True)

    events = [json.loads(line) for line in path.read_text().splitlines()]
    assert [event["event"] for event in events] == [
        "textual_first_refresh",
        "picker_stop",
    ]
    assert events[0]["launch_id"] == "demo-123"
    assert events[0]["binstub_started"] == "start-value"


@pytest.mark.parametrize("value", ["0", "false", "no", "off"])
def test_false_launch_trace_values_disable_reporter(monkeypatch, value):
    monkeypatch.delenv("AGENT_WORKTREES_PICKER_FRAME_HEALTH", raising=False)
    monkeypatch.setenv("AGENT_WORKTREES_LAUNCH_TRACE", value)

    assert FrameHealthReporter.from_env() is None


@pytest.mark.parametrize("value", ["1", "true", "yes", "on"])
def test_truthy_launch_trace_values_use_standard_history(monkeypatch, value):
    monkeypatch.delenv("AGENT_WORKTREES_PICKER_FRAME_HEALTH", raising=False)
    monkeypatch.setenv("AGENT_WORKTREES_LAUNCH_TRACE", value)

    reporter = FrameHealthReporter.from_env()

    assert reporter is not None
    assert reporter.path.name == "picker-launches.jsonl"


def test_launch_trace_only_tick_does_not_read_clock(tmp_path, monkeypatch):
    reporter = FrameHealthReporter(
        tmp_path / "picker-launches.jsonl",
        report_gaps=False,
    )
    monkeypatch.setattr(
        "agent_worktrees.picker_tui.frame_health.time.monotonic",
        lambda: (_ for _ in ()).throw(AssertionError("clock read")),
    )

    reporter.tick(frame=1, debug="loading", busy=None)


def test_append_launch_event_writes_checkpoint(tmp_path, monkeypatch):
    path = tmp_path / "picker-launches.jsonl"
    monkeypatch.setenv("AGENT_WORKTREES_LAUNCH_TRACE", str(path))
    monkeypatch.setenv("AGENT_WORKTREES_LAUNCH_ID", "demo-456")

    append_launch_event("textual_app_start", live=True)

    event = json.loads(path.read_text())
    assert event["event"] == "textual_app_start"
    assert event["launch_id"] == "demo-456"
    assert event["live"] is True
