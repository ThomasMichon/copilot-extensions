"""Tests for the worktree activity log."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agent_worktrees import activity


@pytest.fixture
def patch_install_dir(monkeypatch, tmp_path: Path) -> Path:
    """Redirect the activity log into a tmp install dir."""
    monkeypatch.setattr(
        "agent_worktrees.config.install_dir", lambda: tmp_path / ".agent-worktrees"
    )
    return tmp_path / ".agent-worktrees"


def test_log_event_writes_jsonl(patch_install_dir: Path):
    activity.log_event(
        "worktree_created", worktree_id="wt-1", branch="worktree/wt-1"
    )
    events = activity.read_events()
    assert len(events) == 1
    rec = events[0]
    assert rec["event"] == "worktree_created"
    assert rec["worktree_id"] == "wt-1"
    assert rec["branch"] == "worktree/wt-1"
    # session_id present (None) but no spurious extras
    assert rec["session_id"] is None
    assert "ts" in rec and "pid" in rec and "host" in rec


def test_log_event_drops_none_fields(patch_install_dir: Path):
    activity.log_event("session_started", worktree_id="wt-1", reason=None)
    rec = activity.read_events()[0]
    assert "reason" not in rec


def test_read_events_filters(patch_install_dir: Path):
    activity.log_event("worktree_created", worktree_id="wt-1")
    activity.log_event("session_started", worktree_id="wt-1", session_id="s1")
    activity.log_event("worktree_created", worktree_id="wt-2")

    assert len(activity.read_events(worktree_id="wt-1")) == 2
    assert len(activity.read_events(event="worktree_created")) == 2
    assert len(activity.read_events(worktree_id="wt-2", event="worktree_created")) == 1


def test_read_events_limit_returns_most_recent(patch_install_dir: Path):
    for i in range(5):
        activity.log_event("session_started", worktree_id=f"wt-{i}")
    recent = activity.read_events(limit=2)
    assert len(recent) == 2
    assert recent[0]["worktree_id"] == "wt-3"
    assert recent[1]["worktree_id"] == "wt-4"


def test_read_events_missing_file(patch_install_dir: Path):
    assert activity.read_events() == []


def test_parse_since_durations():
    now = datetime.now(timezone.utc)
    got = activity.parse_since("2d")
    assert got is not None
    assert abs((now - got) - timedelta(days=2)) < timedelta(seconds=5)
    assert activity.parse_since("30m") is not None
    assert activity.parse_since("1w") is not None
    assert activity.parse_since("garbage") is None
    assert activity.parse_since("") is None


def test_parse_since_iso():
    got = activity.parse_since("2026-06-09")
    assert got is not None
    assert got.year == 2026 and got.month == 6 and got.day == 9


def test_since_filter_excludes_old(patch_install_dir: Path, monkeypatch):
    # Write one old and one new event by controlling the timestamp.
    log = activity.log_path()
    log.parent.mkdir(parents=True, exist_ok=True)
    old_ts = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    new_ts = datetime.now(timezone.utc).isoformat()
    log.write_text(
        f'{{"ts": "{old_ts}", "event": "x", "worktree_id": "wt-old"}}\n'
        f'{{"ts": "{new_ts}", "event": "x", "worktree_id": "wt-new"}}\n'
    )
    since = activity.parse_since("2d")
    got = activity.read_events(since=since)
    assert [r["worktree_id"] for r in got] == ["wt-new"]


def test_prune_drops_old_lines(patch_install_dir: Path):
    log = activity.log_path()
    log.parent.mkdir(parents=True, exist_ok=True)
    old_ts = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    new_ts = datetime.now(timezone.utc).isoformat()
    log.write_text(
        f'{{"ts": "{old_ts}", "event": "x", "worktree_id": "old"}}\n'
        f'{{"ts": "{new_ts}", "event": "x", "worktree_id": "new"}}\n'
    )
    kept = activity._prune(log, retention_days=7)
    assert kept == 1
    remaining = activity.read_events()
    assert len(remaining) == 1
    assert remaining[0]["worktree_id"] == "new"


def test_render_events_empty():
    assert activity.render_events([]) == "No activity recorded."


def test_render_events_aligns(patch_install_dir: Path):
    activity.log_event("worktree_created", worktree_id="wt-1", branch="b")
    activity.log_event(
        "session_started", worktree_id="wt-1", session_id="abcdef123456"
    )
    out = activity.render_events(activity.read_events())
    lines = out.splitlines()
    assert len(lines) == 2
    assert "worktree_created" in lines[0]
    assert "branch=b" in lines[0]
    # session id truncated to 8 chars
    assert "abcdef12" in lines[1]


def test_cmd_activity_log_appends(patch_install_dir: Path):
    class Args:
        event = "mux_attached"
        worktree_id = "wt-1"
        session_id = None
        source = "launcher"
        field = ("mux=join", "ignored_without_eq")

    rc = activity.cmd_activity_log(Args())
    assert rc == 0
    rec = activity.read_events()[0]
    assert rec["event"] == "mux_attached"
    assert rec["mux"] == "join"
    assert rec["source"] == "launcher"


def test_cmd_activity_invalid_since(patch_install_dir: Path, capsys):
    class Args:
        since = "nonsense"
        worktree_id = None
        event = None
        lines = None
        json = False

    rc = activity.cmd_activity(Args())
    assert rc == 1
