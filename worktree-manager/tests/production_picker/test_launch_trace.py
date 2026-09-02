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


def test_manager_picker_runs_post_refresh_callback(monkeypatch):
    calls = []

    class InlineThread:
        def __init__(self, target, **kwargs):
            calls.append(("thread", kwargs))
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(engine.threading, "Thread", InlineThread)
    screen = engine.PickerScreen(
        object(),
        after_first_refresh=lambda: calls.append(("callback", {})),
    )

    screen._record_first_refresh()

    assert calls[0][1]["name"] == "picker-after-first-refresh"
    assert calls[1][0] == "callback"
