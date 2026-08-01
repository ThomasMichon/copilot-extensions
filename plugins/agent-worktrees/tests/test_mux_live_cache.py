"""Tests for the #4057 cached mux-liveness hint (Slice 3b).

Covers the ground-layer write-back (``tracking.stamp_mux_live`` + record
round-trip), the Stop-path clear (``sessions.restart_worktree_copilot``), and the
populate consume (``__main__._build_active_paths`` preferring a FRESH cached hint
over the slow per-worktree probe when the live mux batch is unavailable).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import patch

from agent_worktrees import __main__ as cli
from agent_worktrees import sessions, tracking


def _rec(wt_id="aaaa", *, path="/tmp/wt", mux_live=None, mux_live_at=None):
    return tracking.WorktreeRecord(
        worktree_id=wt_id,
        branch=f"worktree/{wt_id}",
        worktree_path=path,
        repo="owner/repo",
        machine="m",
        platform="wsl",
        started_at="2026-06-01T10:00:00",
        last_resumed_at="2026-06-01T10:00:00",
        resume_count=0,
        title=None,
        status="active",
        completed_at=None,
        sessions=[],
        prs=[],
        kind="session",
        mux_live=mux_live,
        mux_live_at=mux_live_at,
    )


# ── Round-trip: mux_live / mux_live_at survive save+load, absent by default ──

def test_mux_live_absent_by_default_roundtrips_clean(tmp_path):
    rec = _rec()
    p = tmp_path / "aaaa.yaml"
    tracking.save_record(rec, p)
    assert "mux_live" not in p.read_text(encoding="utf-8")
    back = tracking.load_record(p)
    assert back.mux_live is None and back.mux_live_at is None


def test_mux_live_stamp_roundtrips(tmp_path):
    rec = _rec(mux_live=True, mux_live_at="2026-07-31T20:00:00")
    p = tmp_path / "aaaa.yaml"
    tracking.save_record(rec, p)
    text = p.read_text(encoding="utf-8")
    assert "mux_live: true" in text and "mux_live_at: 2026-07-31T20:00:00" in text
    back = tracking.load_record(p)
    assert back.mux_live is True and back.mux_live_at == "2026-07-31T20:00:00"


def test_mux_live_false_roundtrips(tmp_path):
    rec = _rec(mux_live=False, mux_live_at="2026-07-31T20:00:00")
    p = tmp_path / "aaaa.yaml"
    tracking.save_record(rec, p)
    assert "mux_live: false" in p.read_text(encoding="utf-8")
    assert tracking.load_record(p).mux_live is False


# ── stamp_mux_live: writes, no-ops on absent record, skips unchanged ──

def test_stamp_mux_live_writes(tmp_path):
    p = tmp_path / "aaaa.yaml"
    tracking.save_record(_rec(), p)
    with patch("agent_worktrees.config.tracking_dir", return_value=tmp_path):
        tracking.stamp_mux_live("aaaa", True)
    back = tracking.load_record(p)
    assert back.mux_live is True and back.mux_live_at


def test_stamp_mux_live_noop_when_record_absent(tmp_path):
    with patch("agent_worktrees.config.tracking_dir", return_value=tmp_path):
        tracking.stamp_mux_live("ghost", True)  # must not raise
    assert not (tmp_path / "ghost.yaml").exists()


# ── Stop clears the cached hint ──

def test_restart_copilot_clears_hint_on_graceful_stop():
    calls = []
    with patch("agent_worktrees.sessions.has_mux_session", return_value=True), \
         patch("agent_worktrees.sessions.graceful_quit_mux_session",
               return_value=True), \
         patch("agent_worktrees.tracking.stamp_mux_live",
               side_effect=lambda wt, live: calls.append((wt, live))):
        res = sessions.restart_worktree_copilot("aaaa")
    assert res["method"] == "graceful"
    assert ("aaaa", False) in calls


def test_restart_copilot_no_session_stamps_false():
    calls = []
    with patch("agent_worktrees.sessions.has_mux_session", return_value=False), \
         patch("agent_worktrees.tracking.stamp_mux_live",
               side_effect=lambda wt, live: calls.append((wt, live))):
        res = sessions.restart_worktree_copilot("aaaa")
    assert res["method"] == "none"
    assert ("aaaa", False) in calls


# ── Populate consume: fresh hint preferred, stale falls back to probe ──

def _fresh():
    return datetime.now().isoformat()


def _stale():
    return (datetime.now() - timedelta(seconds=cli._MUX_LIVE_HINT_TTL_SECS + 60)).isoformat()


def _empty_ctx():
    import types
    return types.SimpleNamespace(active_sessions={})


def test_fresh_true_hint_marks_active_without_probe():
    """Batch unavailable + a FRESH mux_live=True stamp -> active with NO
    per-worktree has_mux_session probe."""
    rec = _rec("aaaa", path="/tmp/a", mux_live=True, mux_live_at=_fresh())
    with patch("agent_worktrees.sessions._list_mux_sessions", return_value=None), \
         patch("agent_worktrees.sessions.has_mux_session") as m_has:
        active = cli._build_active_paths([rec], session_ctx=_empty_ctx())
    assert active == {"/tmp/a"}
    m_has.assert_not_called()


def test_fresh_false_hint_is_inactive_without_probe():
    rec = _rec("aaaa", path="/tmp/a", mux_live=False, mux_live_at=_fresh())
    with patch("agent_worktrees.sessions._list_mux_sessions", return_value=None), \
         patch("agent_worktrees.sessions.has_mux_session") as m_has:
        active = cli._build_active_paths([rec], session_ctx=_empty_ctx())
    assert active == set()
    m_has.assert_not_called()


def test_stale_hint_falls_back_to_probe():
    rec = _rec("aaaa", path="/tmp/a", mux_live=True, mux_live_at=_stale())
    with patch("agent_worktrees.sessions._list_mux_sessions", return_value=None), \
         patch("agent_worktrees.sessions.has_mux_session", return_value=True) as m_has:
        active = cli._build_active_paths([rec], session_ctx=_empty_ctx())
    assert active == {"/tmp/a"}
    m_has.assert_called_once()


def test_no_hint_falls_back_to_probe():
    rec = _rec("aaaa", path="/tmp/a")  # never stamped
    with patch("agent_worktrees.sessions._list_mux_sessions", return_value=None), \
         patch("agent_worktrees.sessions.has_mux_session", return_value=False) as m_has:
        active = cli._build_active_paths([rec], session_ctx=_empty_ctx())
    assert active == set()
    m_has.assert_called_once()


def test_batch_available_ignores_hint():
    """When the live batch IS available it stays authoritative -- a stale/false
    hint never overrides a present live session, and vice-versa."""
    rec = _rec("aaaa", path="/tmp/a", mux_live=False, mux_live_at=_fresh())
    with patch("agent_worktrees.sessions._list_mux_sessions",
               return_value={"wt-aaaa": 1}), \
         patch("agent_worktrees.sessions.has_mux_session") as m_has:
        active = cli._build_active_paths([rec], session_ctx=_empty_ctx())
    assert active == {"/tmp/a"}  # live batch wins over the false hint
    m_has.assert_not_called()
