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
        tracking.stamp_mux_live("aaaa", True, sync=True)
    back = tracking.load_record(p)
    assert back.mux_live is True and back.mux_live_at


def test_stamp_mux_live_async_writes_via_queue(tmp_path):
    """Async (default): the stamp is enqueued and lands after a queue flush --
    the render/UI thread is not blocked on the YAML write (dotfiles#948)."""
    p = tmp_path / "aaaa.yaml"
    tracking.save_record(_rec(), p)
    with patch("agent_worktrees.config.tracking_dir", return_value=tmp_path):
        tracking.stamp_mux_live("aaaa", True)  # async by default
        tracking.flush_stamp_writes()
    back = tracking.load_record(p)
    assert back.mux_live is True and back.mux_live_at


def test_stamp_mux_live_noop_when_record_absent(tmp_path):
    with patch("agent_worktrees.config.tracking_dir", return_value=tmp_path):
        tracking.stamp_mux_live("ghost", True, sync=True)  # must not raise
    assert not (tmp_path / "ghost.yaml").exists()


# ── stamp_mux_live: freshness-refresh + throttle (#4057 write-points slice) ──

def test_stamp_no_refresh_keeps_noop_on_unchanged(tmp_path):
    """Default (refresh=False): an unchanged value never rewrites, so a
    steadily-live worktree's stamp is NOT renewed -- the legacy behaviour that
    made the hint age out. mux_live_at stays put."""
    old = (datetime.now() - timedelta(seconds=300)).isoformat()
    p = tmp_path / "aaaa.yaml"
    tracking.save_record(_rec(mux_live=True, mux_live_at=old), p)
    with patch("agent_worktrees.config.tracking_dir", return_value=tmp_path):
        tracking.stamp_mux_live("aaaa", True, sync=True)  # unchanged, no refresh
    assert tracking.load_record(p).mux_live_at == old


def test_stamp_refresh_renews_aged_timestamp(tmp_path):
    """refresh=True renews mux_live_at for an unchanged value once the stamp has
    aged past the throttle -- so an authoritative re-observation of a still-live
    worktree keeps the populate hint fresh."""
    old = (datetime.now() - timedelta(seconds=300)).isoformat()
    p = tmp_path / "aaaa.yaml"
    tracking.save_record(_rec(mux_live=True, mux_live_at=old), p)
    with patch("agent_worktrees.config.tracking_dir", return_value=tmp_path):
        tracking.stamp_mux_live("aaaa", True, refresh=True, throttle_secs=60,
                                sync=True)
    back = tracking.load_record(p)
    assert back.mux_live is True and back.mux_live_at != old


def test_stamp_refresh_throttled_within_window(tmp_path):
    """refresh=True does NOT rewrite when the stamp is still within the throttle
    window -- bounding YAML churn for a hot re-observation."""
    recent = (datetime.now() - timedelta(seconds=5)).isoformat()
    p = tmp_path / "aaaa.yaml"
    tracking.save_record(_rec(mux_live=True, mux_live_at=recent), p)
    with patch("agent_worktrees.config.tracking_dir", return_value=tmp_path):
        tracking.stamp_mux_live("aaaa", True, refresh=True, throttle_secs=60,
                                sync=True)
    assert tracking.load_record(p).mux_live_at == recent


def test_stamp_value_change_always_writes(tmp_path):
    """A liveness CHANGE (True->False) always persists, regardless of refresh or
    how recent the prior stamp was -- the transition must be recorded."""
    recent = (datetime.now() - timedelta(seconds=5)).isoformat()
    p = tmp_path / "aaaa.yaml"
    tracking.save_record(_rec(mux_live=True, mux_live_at=recent), p)
    with patch("agent_worktrees.config.tracking_dir", return_value=tmp_path):
        tracking.stamp_mux_live("aaaa", False, sync=True)
    back = tracking.load_record(p)
    assert back.mux_live is False and back.mux_live_at != recent


# ── Confirmed-teardown write-point: the idle-gated reaper clears the hint ──

def test_reaper_clears_hint_on_successful_kill(tmp_path):
    """#4057: a successful, idle-gated mux reap is a CONFIRMED teardown, so the
    reaper stamps mux_live=False. This is the shared sweep run at both lifecycle
    boundaries (picker-launch + session-end), so it also covers post-exit."""
    rec = _rec("aaaa", path=str(tmp_path / "wt"))
    rec.status = "finalized"          # a reap-eligible orphan
    calls = []
    with patch("agent_worktrees.sessions._list_mux_sessions",
               return_value={"wt-aaaa": 0}), \
         patch("agent_worktrees.sessions._mux_session_activity",
               return_value={"wt-aaaa": 0.0}), \
         patch("agent_worktrees.tracking.list_records", return_value=[rec]), \
         patch("agent_worktrees.config.tracking_dir", return_value=tmp_path), \
         patch("agent_worktrees.sessions.kill_tmux_session", return_value=True), \
         patch("agent_worktrees.tracking.stamp_mux_live",
               side_effect=lambda wt, live, **kw: calls.append((wt, live))):
        res = cli.reap_orphan_mux_sessions(now=1e9)
    assert "aaaa" in res["reaped"]
    assert ("aaaa", False) in calls


def test_reaper_does_not_clear_hint_when_kill_fails(tmp_path):
    """A failed kill is NOT a confirmed teardown -- no False stamp (the mux may
    still be live)."""
    rec = _rec("aaaa", path=str(tmp_path / "wt"))
    rec.status = "finalized"
    calls = []
    with patch("agent_worktrees.sessions._list_mux_sessions",
               return_value={"wt-aaaa": 0}), \
         patch("agent_worktrees.sessions._mux_session_activity",
               return_value={"wt-aaaa": 0.0}), \
         patch("agent_worktrees.tracking.list_records", return_value=[rec]), \
         patch("agent_worktrees.config.tracking_dir", return_value=tmp_path), \
         patch("agent_worktrees.sessions.kill_tmux_session", return_value=False), \
         patch("agent_worktrees.tracking.stamp_mux_live",
               side_effect=lambda wt, live, **kw: calls.append((wt, live))):
        cli.reap_orphan_mux_sessions(now=1e9)
    assert calls == []


# ── Stop clears the cached hint ──

def test_restart_copilot_clears_hint_on_graceful_stop():
    calls = []
    with patch("agent_worktrees.sessions.has_mux_session", return_value=True), \
         patch("agent_worktrees.sessions.graceful_quit_mux_session",
               return_value=True), \
         patch("agent_worktrees.tracking.stamp_mux_live",
               side_effect=lambda wt, live, **kw: calls.append((wt, live))):
        res = sessions.restart_worktree_copilot("aaaa")
    assert res["method"] == "graceful"
    assert ("aaaa", False) in calls


def test_restart_copilot_no_session_stamps_false():
    calls = []
    with patch("agent_worktrees.sessions.has_mux_session", return_value=False), \
         patch("agent_worktrees.tracking.stamp_mux_live",
               side_effect=lambda wt, live, **kw: calls.append((wt, live))):
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


# ── reconcile_bound_live also reconciles the cached mux_live hint (dotfiles#1205) ──
#
# mux_live is otherwise only stamped at discrete lifecycle events, so a wt-<id>
# mux that appears AFTER its last event-time stamp (a psmux startup-restore that
# lands after resume) persists a stale ``false``. The off-hot-path sweep now
# recomputes mux presence for the same record set, mirroring bound liveness.

from types import SimpleNamespace  # noqa: E402

from agent_worktrees.picker_tui import data_local  # noqa: E402


def _reconcile_mux(records, *, mux_present_ids, bound_ids=(), tmp_path):
    """Run reconcile_bound_live with a stubbed bound scan + stubbed mux batch."""
    for rec in records:
        tracking.save_record(rec, tmp_path / f"{rec.worktree_id}.yaml")
    loaded = [tracking.load_record(tmp_path / f"{r.worktree_id}.yaml")
              for r in records]
    mux_map = {wt: SimpleNamespace(exists=True, clients=1)
               for wt in mux_present_ids}
    with patch("agent_worktrees.config.tracking_dir", return_value=tmp_path), \
         patch("agent_worktrees.config.detect_platform", return_value="wsl"), \
         patch("agent_worktrees.tracking.list_records", return_value=loaded), \
         patch("agent_worktrees.reclaim.resolve_bound_copilots",
               return_value=[{"worktree_id": w} for w in bound_ids]), \
         patch("agent_worktrees.sessions.mux_status_many", return_value=mux_map):
        return data_local.reconcile_bound_live()


def test_reconcile_stamps_mux_live_true_when_mux_appears_after_stamp(tmp_path):
    """dotfiles#1205 core case: a worktree stamped mux_live=False at resume time
    whose wt-<id> mux was (re)created AFTER that stamp is re-observed live by the
    sweep, flipping the stale false-negative to True."""
    (tmp_path / "wt-a").mkdir()
    old = (datetime.now() - timedelta(seconds=300)).isoformat()
    rec = _rec("aaaa", path=str(tmp_path / "wt-a"),
               mux_live=False, mux_live_at=old)
    changed = _reconcile_mux([rec], mux_present_ids={"aaaa"}, tmp_path=tmp_path)
    assert changed >= 1
    assert tracking.load_record(tmp_path / "aaaa.yaml").mux_live is True


def test_reconcile_clears_mux_live_true_to_false_when_mux_gone(tmp_path):
    """A worktree whose cached mux_live=True but whose mux has since torn down is
    stamped False (a real ACTIVE-visibility transition)."""
    (tmp_path / "wt-a").mkdir()
    rec = _rec("aaaa", path=str(tmp_path / "wt-a"),
               mux_live=True, mux_live_at=datetime.now().isoformat())
    changed = _reconcile_mux([rec], mux_present_ids=set(), tmp_path=tmp_path)
    assert changed >= 1
    assert tracking.load_record(tmp_path / "aaaa.yaml").mux_live is False


def test_reconcile_leaves_never_muxed_untouched(tmp_path):
    """mux_live None + no mux -> no write, so the fleet's idle YAMLs are not
    rewritten (mirrors the bound Unknown-never-persisted rule)."""
    (tmp_path / "wt-a").mkdir()
    rec = _rec("aaaa", path=str(tmp_path / "wt-a"))  # mux_live None
    changed = _reconcile_mux([rec], mux_present_ids=set(), tmp_path=tmp_path)
    assert changed == 0
    text = (tmp_path / "aaaa.yaml").read_text(encoding="utf-8")
    assert "mux_live" not in text


def test_reconcile_mux_present_already_true_renews_but_no_transition(tmp_path):
    """A steadily-muxed worktree already True is not a transition (the mux arm
    adds nothing to ``changed``), but its freshness is renewed once aged past the
    throttle so the populate hint never expires while genuinely live."""
    (tmp_path / "wt-a").mkdir()
    old = (datetime.now() - timedelta(seconds=300)).isoformat()
    rec = _rec("aaaa", path=str(tmp_path / "wt-a"),
               mux_live=True, mux_live_at=old)
    changed = _reconcile_mux([rec], mux_present_ids={"aaaa"}, tmp_path=tmp_path)
    assert changed == 0
    assert tracking.load_record(tmp_path / "aaaa.yaml").mux_live_at != old
