from __future__ import annotations

import json

from worktree_manager.production_picker.picker_tui import engine


def test_manager_picker_records_first_refresh(tmp_path, monkeypatch):
    path = tmp_path / "picker-launches.jsonl"
    monkeypatch.setenv("AGENT_WORKTREES_LAUNCH_TRACE", str(path))
    monkeypatch.setenv("AGENT_WORKTREES_LAUNCH_ID", "manager-123")
    monkeypatch.setenv("AGENT_WORKTREES_BINSTUB_STARTED", "start-value")

    screen = engine.PickerScreen(object())
    screen._record_first_refresh()
    screen._frame_health.close(wait=True)

    events = [json.loads(line) for line in path.read_text().splitlines()]
    assert [event["event"] for event in events] == [
        "textual_first_refresh",
        "picker_stop",
    ]
    assert events[0]["launch_id"] == "manager-123"
