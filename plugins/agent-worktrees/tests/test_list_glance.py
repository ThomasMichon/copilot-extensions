"""Tests for `agent-worktrees list --glance` -- the compact situational-awareness
digest (active worktrees, title + disposition summary, ranked by recency, with
no-disposition worktrees named rather than hidden)."""

from __future__ import annotations

import io
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone

from agent_worktrees import __main__ as m
from agent_worktrees import tracking


def _iso(minutes_ago: int) -> str:
    return (
        datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    ).isoformat().replace("+00:00", "Z")


def _rec(wt_id, *, title=None, summary="", note_min=None, follow_up=False,
         status="active"):
    return tracking.WorktreeRecord(
        worktree_id=wt_id,
        branch=f"worktree/{wt_id}",
        worktree_path=f"/tmp/{wt_id}",
        repo="test",
        machine="test",
        platform="wsl",
        started_at="2026-06-01T10:00:00",
        last_resumed_at="2026-06-01T10:00:00",
        resume_count=0,
        title=title,
        status=status,
        completed_at=None,
        summary=summary,
        follow_up=follow_up,
        status_note_at=_iso(note_min) if note_min is not None else None,
    )


def _glance(records) -> str:
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = m._cmd_list_glance(records)
    assert rc == 0
    return buf.getvalue()


def test_glance_lists_disposed_and_names_blanks():
    records = [
        _rec("wt-aaaa", title="Build X", summary="shipping the thing", note_min=5),
        _rec("wt-bbbb", title=None, summary="", note_min=None),  # blank
        _rec("wt-cccc", title="Done Y", summary="done", note_min=None,
             status="finalized"),  # non-active -> excluded
    ]
    out = _glance(records)
    # header counts active only (2), 1 disposed, 1 blank
    assert "Active worktrees on this machine" in out
    assert "2 -- 1 with a recorded disposition, 1 without" in out
    assert "Build X" in out and "shipping the thing" in out
    assert "No recorded disposition (1): wt-bbbb" in out
    # the finalized worktree is not shown
    assert "Done Y" not in out


def test_glance_ranks_by_recency():
    records = [
        _rec("wt-old", title="Old", summary="old work", note_min=600),
        _rec("wt-new", title="New", summary="new work", note_min=2),
        _rec("wt-mid", title="Mid", summary="mid work", note_min=60),
    ]
    out = _glance(records)
    # newest disposition first
    assert out.index("New") < out.index("Mid") < out.index("Old")


def test_glance_marks_follow_up_and_truncates_summary():
    long = "x" * 400
    records = [_rec("wt-f", title="T", summary=long, note_min=1, follow_up=True)]
    out = _glance(records)
    assert "[follow-up]" in out
    assert "..." in out  # summary truncated
    assert "x" * 400 not in out


def test_glance_title_only_no_summary():
    records = [_rec("wt-t", title="Just a title", summary="", note_min=3)]
    out = _glance(records)
    assert "Just a title" in out
    # no " -- " summary separator when there is no summary
    assert "Just a title -- " not in out
