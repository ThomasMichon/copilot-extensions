from __future__ import annotations

import json

from agent_worktrees.picker_tui.frame_health import FrameHealthReporter


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
    assert [event["event"] for event in events] == ["start", "gap", "stop"]
    assert events[1]["gap_ms"] == 700.0
    assert events[1]["frame"] == 2
