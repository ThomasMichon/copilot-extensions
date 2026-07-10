"""Tests for the title (overall-summary) backfill in `backfill-sessions`.

The Picker reads a worktree's display title from the tracking record's
``title`` slot.  ``backfill-sessions`` runs two passes: it fills empty
session registries *and* captures a title from the newest session's
``workspace.yaml`` for any record still lacking one -- so worktrees created
before titles were persisted stop showing "(untitled)".  The title pass runs
even when every record already has session data (the common case after an
earlier sessions-only backfill left titles null).
"""

from __future__ import annotations

import argparse
from pathlib import Path

from conftest import make_session_dir

from agent_worktrees import __main__ as m
from agent_worktrees import tracking


def _wire_dirs(monkeypatch, tracking_dir: Path, state_dir: Path) -> None:
    monkeypatch.setattr(m.cfg, "tracking_dir", lambda: tracking_dir)
    monkeypatch.setattr(m.sessions, "_session_state_dir", lambda: state_dir)


def _save(rec: tracking.WorktreeRecord, tracking_dir: Path) -> None:
    tracking.save_record(rec, tracking_dir / f"{rec.worktree_id}.yaml")


def _record(wt_id: str, wt_path: str, *, title=None, sessions=None, status="active"):
    return tracking.WorktreeRecord(
        worktree_id=wt_id,
        branch=f"worktree/{wt_id}",
        worktree_path=wt_path,
        repo="test",
        machine="test",
        platform="wsl",
        started_at="2026-06-01T10:00:00",
        last_resumed_at="2026-06-01T10:00:00",
        resume_count=0,
        title=title,
        status=status,
        completed_at=None,
        sessions=sessions,
    )


def test_title_pass_fills_record_with_existing_sessions(
    monkeypatch, tmp_tracking_dir, tmp_session_state_dir
):
    """A record that already has sessions but a null title (post sessions-only
    backfill) gets its title captured from the newest session."""
    wt_path = "/w/already-sessioned"
    make_session_dir(
        tmp_session_state_dir, "sess-1", wt_path,
        summary="Investigate Agent-Bridge", updated_at="2026-06-01T10:00:00.000Z",
    )
    make_session_dir(
        tmp_session_state_dir, "sess-2", wt_path,
        summary="Fix Agent-Bridge Daemon", updated_at="2026-06-02T10:00:00.000Z",
    )
    rec = _record("wt-a", wt_path, title=None, sessions=[
        tracking.SessionEntry("sess-1", "2026-06-01T10:00:00"),
        tracking.SessionEntry("sess-2", "2026-06-02T10:00:00"),
    ])
    _save(rec, tmp_tracking_dir)
    _wire_dirs(monkeypatch, tmp_tracking_dir, tmp_session_state_dir)

    rc = m.cmd_backfill_sessions(argparse.Namespace())
    assert rc == 0

    after = tracking.load_record(tmp_tracking_dir / "wt-a.yaml")
    # Newest session (sess-2) wins.
    assert after.title == "Fix Agent-Bridge Daemon"


def test_title_pass_falls_back_to_older_session(
    monkeypatch, tmp_tracking_dir, tmp_session_state_dir
):
    """When the newest session's state is gone, an older session still
    supplies the title."""
    wt_path = "/w/partial"
    # Only the older session's state survives.
    make_session_dir(
        tmp_session_state_dir, "old-sess", wt_path,
        summary="Resume PushChannel E2E",
    )
    rec = _record("wt-b", wt_path, title=None, sessions=[
        tracking.SessionEntry("old-sess", "2026-06-01T10:00:00"),
        tracking.SessionEntry("gone-sess", "2026-06-09T10:00:00"),
    ])
    _save(rec, tmp_tracking_dir)
    _wire_dirs(monkeypatch, tmp_tracking_dir, tmp_session_state_dir)

    m.cmd_backfill_sessions(argparse.Namespace())

    after = tracking.load_record(tmp_tracking_dir / "wt-b.yaml")
    assert after.title == "Resume PushChannel E2E"


def test_title_pass_preserves_curated_title(
    monkeypatch, tmp_tracking_dir, tmp_session_state_dir
):
    wt_path = "/w/curated"
    make_session_dir(
        tmp_session_state_dir, "sess-x", wt_path, summary="Live Summary",
    )
    rec = _record("wt-c", wt_path, title="Curated PR Title", sessions=[
        tracking.SessionEntry("sess-x", "2026-06-01T10:00:00"),
    ])
    _save(rec, tmp_tracking_dir)
    _wire_dirs(monkeypatch, tmp_tracking_dir, tmp_session_state_dir)

    m.cmd_backfill_sessions(argparse.Namespace())

    after = tracking.load_record(tmp_tracking_dir / "wt-c.yaml")
    assert after.title == "Curated PR Title"


def test_backfills_sessions_and_title_together(
    monkeypatch, tmp_tracking_dir, tmp_session_state_dir
):
    """An empty-sessions record gets both its registry and its title filled."""
    wt_path = "/w/fresh"
    make_session_dir(
        tmp_session_state_dir, "disc-sess", wt_path,
        summary="Update SPO.Core PR KillSwitch",
    )
    rec = _record("wt-d", wt_path, title=None, sessions=[])
    _save(rec, tmp_tracking_dir)
    _wire_dirs(monkeypatch, tmp_tracking_dir, tmp_session_state_dir)

    m.cmd_backfill_sessions(argparse.Namespace())

    after = tracking.load_record(tmp_tracking_dir / "wt-d.yaml")
    assert [s.session_id for s in (after.sessions or [])] == ["disc-sess"]
    assert after.title == "Update SPO.Core PR KillSwitch"


def test_capture_session_title_returns_bool(
    monkeypatch, tmp_tracking_dir, tmp_session_state_dir
):
    wt_path = "/w/cap"
    make_session_dir(tmp_session_state_dir, "cap-sess", wt_path, summary="Cap Title")
    rec = _record("wt-e", wt_path, title=None, sessions=[])
    _save(rec, tmp_tracking_dir)
    _wire_dirs(monkeypatch, tmp_tracking_dir, tmp_session_state_dir)

    assert m._capture_session_title("wt-e", "cap-sess") is True
    # Second call no-ops: title already set.
    assert m._capture_session_title("wt-e", "cap-sess") is False
    # Missing session-state -> False.
    assert m._capture_session_title("wt-e", "no-such-sess") is False


def test_capture_session_title_skips_detached(
    monkeypatch, tmp_tracking_dir, tmp_session_state_dir
):
    """A detached subconscious session must never supply a worktree title."""
    wt_path = "/w/det"
    sdir = make_session_dir(
        tmp_session_state_dir, "det-sess", wt_path,
        summary="Apply context_board add/prune updates for this session",
    )
    (sdir / ".detached").write_text("")
    rec = _record("wt-f", wt_path, title=None, sessions=[])
    _save(rec, tmp_tracking_dir)
    _wire_dirs(monkeypatch, tmp_tracking_dir, tmp_session_state_dir)

    assert m._capture_session_title("wt-f", "det-sess") is False
    after = tracking.load_record(tmp_tracking_dir / "wt-f.yaml")
    assert not (after.title and after.title != "null")


def test_title_pass_ignores_detached_prefers_real(
    monkeypatch, tmp_tracking_dir, tmp_session_state_dir
):
    """When a worktree's newest session is detached, the title pass skips it
    and uses the real session instead of the 'Apply context_board' prompt."""
    wt_path = "/w/mixed"
    make_session_dir(
        tmp_session_state_dir, "real-sess", wt_path,
        summary="Investigate Agent-Bridge Performance",
        updated_at="2026-06-01T10:00:00.000Z",
    )
    det = make_session_dir(
        tmp_session_state_dir, "det-sess", wt_path,
        summary="Apply context_board add/prune updates for this session",
        updated_at="2026-06-05T10:00:00.000Z",  # newer, but detached
    )
    (det / ".detached").write_text("")
    rec = _record("wt-g", wt_path, title=None, sessions=[
        tracking.SessionEntry("real-sess", "2026-06-01T10:00:00"),
        tracking.SessionEntry("det-sess", "2026-06-05T10:00:00"),
    ])
    _save(rec, tmp_tracking_dir)
    _wire_dirs(monkeypatch, tmp_tracking_dir, tmp_session_state_dir)

    m.cmd_backfill_sessions(argparse.Namespace())

    after = tracking.load_record(tmp_tracking_dir / "wt-g.yaml")
    assert after.title == "Investigate Agent-Bridge Performance"

