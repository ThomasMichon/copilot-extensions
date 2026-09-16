from __future__ import annotations

import json

from agent_worktrees.launch_trace import append_launch_event


def test_append_launch_event_writes_checkpoint(tmp_path, monkeypatch):
    path = tmp_path / "picker-launches.jsonl"
    monkeypatch.setenv("AGENT_WORKTREES_LAUNCH_TRACE", str(path))
    monkeypatch.setenv("AGENT_WORKTREES_LAUNCH_ID", "demo-456")

    append_launch_event("textual_app_start", live=True)

    event = json.loads(path.read_text())
    assert event["event"] == "textual_app_start"
    assert event["launch_id"] == "demo-456"
    assert event["live"] is True


def test_append_launch_event_disabled_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENT_WORKTREES_LAUNCH_TRACE", raising=False)

    # Best-effort and fail-silent: no trace path configured means no-op, not
    # an exception -- this is called unconditionally from the launch/resolve
    # path regardless of whether anyone wants the trace.
    append_launch_event("resolve_handler_start")
