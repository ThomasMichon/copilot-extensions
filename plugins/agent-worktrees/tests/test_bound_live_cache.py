"""Tests for the #4057/#1416 cached bound-Copilot liveness signal.

The bare-resume counterpart of ``test_mux_live_cache``: a bare-resumed Copilot
(cwd=home) is invisible to the registered-session lock scan AND the mux batch, so
its worktree wrongly renders non-ACTIVE (#1416). A SEPARATE cached ``bound_live``
signal -- reconciled OFF the hot path from the authoritative machine-wide
``reclaim.resolve_bound_copilots`` scan -- surfaces it in the Active section.

Covers the ground-layer write-back (``tracking.stamp_bound_live`` + record
round-trip, byte-identical when absent), the off-hot-path reconciler
(``data_local.reconcile_bound_live``: minimal-churn tri-state), and the populate
consume (``__main__._build_active_paths`` unioning a FRESH cached hint additively;
``_worktree_to_dict`` surfacing it for the fast pass).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import patch

from agent_worktrees import __main__ as cli
from agent_worktrees import tracking
from agent_worktrees.picker_tui import data_local


def _rec(wt_id="aaaa", *, path="/tmp/wt", bound_live=None, bound_live_at=None):
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
        bound_live=bound_live,
        bound_live_at=bound_live_at,
    )


# ── Round-trip: bound_live / bound_live_at survive save+load, absent by default ──

def test_bound_live_absent_by_default_roundtrips_clean(tmp_path):
    rec = _rec()
    p = tmp_path / "aaaa.yaml"
    tracking.save_record(rec, p)
    text = p.read_text(encoding="utf-8")
    assert "bound_live" not in text  # byte-identical: no new key for legacy YAML
    back = tracking.load_record(p)
    assert back.bound_live is None and back.bound_live_at is None


def test_bound_live_stamp_roundtrips(tmp_path):
    rec = _rec(bound_live=True, bound_live_at="2026-08-03T20:00:00")
    p = tmp_path / "aaaa.yaml"
    tracking.save_record(rec, p)
    text = p.read_text(encoding="utf-8")
    assert "bound_live: true" in text
    assert "bound_live_at: 2026-08-03T20:00:00" in text
    back = tracking.load_record(p)
    assert back.bound_live is True and back.bound_live_at == "2026-08-03T20:00:00"


def test_bound_live_false_roundtrips(tmp_path):
    rec = _rec(bound_live=False, bound_live_at="2026-08-03T20:00:00")
    p = tmp_path / "aaaa.yaml"
    tracking.save_record(rec, p)
    assert "bound_live: false" in p.read_text(encoding="utf-8")
    assert tracking.load_record(p).bound_live is False


def test_bound_live_independent_of_mux_live(tmp_path):
    """The two cached signals are orthogonal: a bare (un-muxed) bound session is
    bound_live=True with NO mux_live -- folding it into mux_live would corrupt
    Open/Resume/Stop gating."""
    rec = _rec(bound_live=True, bound_live_at="2026-08-03T20:00:00")
    p = tmp_path / "aaaa.yaml"
    tracking.save_record(rec, p)
    back = tracking.load_record(p)
    assert back.bound_live is True
    assert back.mux_live is None  # untouched


# ── stamp_bound_live: writes, no-ops on absent record, refresh/throttle/change ──

def test_stamp_bound_live_writes(tmp_path):
    p = tmp_path / "aaaa.yaml"
    tracking.save_record(_rec(), p)
    with patch("agent_worktrees.config.tracking_dir", return_value=tmp_path):
        tracking.stamp_bound_live("aaaa", True)
    back = tracking.load_record(p)
    assert back.bound_live is True and back.bound_live_at


def test_stamp_bound_live_noop_when_record_absent(tmp_path):
    with patch("agent_worktrees.config.tracking_dir", return_value=tmp_path):
        tracking.stamp_bound_live("ghost", True)  # must not raise
    assert not (tmp_path / "ghost.yaml").exists()


def test_stamp_bound_live_does_not_touch_mux_live(tmp_path):
    """A mux_live stamp already present round-trips untouched when bound_live is
    stamped -- the shared RMW helper never crosses the two fields."""
    p = tmp_path / "aaaa.yaml"
    rec = _rec()
    rec.mux_live = True
    rec.mux_live_at = "2026-08-03T19:00:00"
    tracking.save_record(rec, p)
    with patch("agent_worktrees.config.tracking_dir", return_value=tmp_path):
        tracking.stamp_bound_live("aaaa", True)
    back = tracking.load_record(p)
    assert back.bound_live is True
    assert back.mux_live is True and back.mux_live_at == "2026-08-03T19:00:00"


def test_stamp_bound_refresh_renews_aged_timestamp(tmp_path):
    old = (datetime.now() - timedelta(seconds=300)).isoformat()
    p = tmp_path / "aaaa.yaml"
    tracking.save_record(_rec(bound_live=True, bound_live_at=old), p)
    with patch("agent_worktrees.config.tracking_dir", return_value=tmp_path):
        tracking.stamp_bound_live("aaaa", True, refresh=True, throttle_secs=60)
    assert tracking.load_record(p).bound_live_at != old


def test_stamp_bound_refresh_throttled_within_window(tmp_path):
    recent = (datetime.now() - timedelta(seconds=5)).isoformat()
    p = tmp_path / "aaaa.yaml"
    tracking.save_record(_rec(bound_live=True, bound_live_at=recent), p)
    with patch("agent_worktrees.config.tracking_dir", return_value=tmp_path):
        tracking.stamp_bound_live("aaaa", True, refresh=True, throttle_secs=60)
    assert tracking.load_record(p).bound_live_at == recent


def test_stamp_bound_value_change_always_writes(tmp_path):
    recent = (datetime.now() - timedelta(seconds=5)).isoformat()
    p = tmp_path / "aaaa.yaml"
    tracking.save_record(_rec(bound_live=True, bound_live_at=recent), p)
    with patch("agent_worktrees.config.tracking_dir", return_value=tmp_path):
        tracking.stamp_bound_live("aaaa", False)
    back = tracking.load_record(p)
    assert back.bound_live is False and back.bound_live_at != recent


# ── reconcile_bound_live: minimal-churn tri-state off-hot-path reconciler ──

def _reconcile_with(records, bound_ids, tmp_path):
    """Run reconcile_bound_live with a stubbed scan + real YAML store."""
    for rec in records:
        tracking.save_record(rec, tmp_path / f"{rec.worktree_id}.yaml")
    loaded = [tracking.load_record(tmp_path / f"{r.worktree_id}.yaml")
              for r in records]
    with patch("agent_worktrees.config.tracking_dir", return_value=tmp_path), \
         patch("agent_worktrees.config.detect_platform", return_value="wsl"), \
         patch("agent_worktrees.tracking.list_records", return_value=loaded), \
         patch("agent_worktrees.reclaim.resolve_bound_copilots",
               return_value=[{"worktree_id": w} for w in bound_ids]):
        changed = data_local.reconcile_bound_live()
    return changed


def test_reconcile_stamps_live_worktree_true(tmp_path):
    (tmp_path / "wt-a").mkdir()
    rec = _rec("aaaa", path=str(tmp_path / "wt-a"))
    changed = _reconcile_with([rec], {"aaaa"}, tmp_path)
    assert changed == 1
    assert tracking.load_record(tmp_path / "aaaa.yaml").bound_live is True


def test_reconcile_leaves_never_bound_untouched(tmp_path):
    """A never-bound idle worktree (bound_live None) is NOT stamped False -- no
    fleet-wide YAML churn; Unknown is never persisted."""
    (tmp_path / "wt-a").mkdir()
    rec = _rec("aaaa", path=str(tmp_path / "wt-a"))  # bound_live None
    changed = _reconcile_with([rec], set(), tmp_path)
    assert changed == 0
    back = tracking.load_record(tmp_path / "aaaa.yaml")
    assert back.bound_live is None  # left as Unknown, no write
    assert "bound_live" not in (tmp_path / "aaaa.yaml").read_text(encoding="utf-8")


def test_reconcile_clears_true_to_false_on_transition(tmp_path):
    """A worktree that WAS bound_live=True but is no longer bound is stamped
    False (the session-ended transition), and counts as changed."""
    (tmp_path / "wt-a").mkdir()
    rec = _rec("aaaa", path=str(tmp_path / "wt-a"),
               bound_live=True, bound_live_at=datetime.now().isoformat())
    changed = _reconcile_with([rec], set(), tmp_path)
    assert changed == 1
    assert tracking.load_record(tmp_path / "aaaa.yaml").bound_live is False


def test_reconcile_no_change_when_already_true(tmp_path):
    """A steadily-live worktree already stamped True is not a transition (changed
    stays 0, so no needless re-render), though its freshness is renewed."""
    (tmp_path / "wt-a").mkdir()
    old = (datetime.now() - timedelta(seconds=300)).isoformat()
    rec = _rec("aaaa", path=str(tmp_path / "wt-a"),
               bound_live=True, bound_live_at=old)
    changed = _reconcile_with([rec], {"aaaa"}, tmp_path)
    assert changed == 0
    # refresh=True renews the aged stamp so the hint never expires while live.
    assert tracking.load_record(tmp_path / "aaaa.yaml").bound_live_at != old


def test_reconcile_stale_true_live_counts_as_changed(tmp_path):
    """A still-live worktree whose True hint had aged PAST the populate TTL is
    consumer-invisible (populate ignores it), so renewing it back to fresh must
    count as changed -- otherwise the picker leaves it non-ACTIVE until the poll
    (or forever if polling is off)."""
    (tmp_path / "wt-a").mkdir()
    secs = cli._BOUND_LIVE_HINT_TTL_SECS + 120
    stale = (datetime.now() - timedelta(seconds=secs)).isoformat()
    rec = _rec("aaaa", path=str(tmp_path / "wt-a"),
               bound_live=True, bound_live_at=stale)
    changed = _reconcile_with([rec], {"aaaa"}, tmp_path)
    assert changed == 1
    back = tracking.load_record(tmp_path / "aaaa.yaml")
    assert back.bound_live is True and back.bound_live_at != stale


def test_reconcile_unresolved_binding_suppresses_negative(tmp_path):
    """A live bound Copilot the scan could not attribute to a worktree
    (worktree_id None) is a transient attribution miss, not proof of death: it
    must NOT flap a previously-True worktree to False this pass (the positive
    expires via TTL if truly gone)."""
    (tmp_path / "wt-a").mkdir()
    rec = _rec("aaaa", path=str(tmp_path / "wt-a"),
               bound_live=True, bound_live_at=datetime.now().isoformat())
    tracking.save_record(rec, tmp_path / "aaaa.yaml")
    loaded = [tracking.load_record(tmp_path / "aaaa.yaml")]
    with patch("agent_worktrees.config.tracking_dir", return_value=tmp_path), \
         patch("agent_worktrees.config.detect_platform", return_value="wsl"), \
         patch("agent_worktrees.tracking.list_records", return_value=loaded), \
         patch("agent_worktrees.reclaim.resolve_bound_copilots",
               return_value=[{"worktree_id": None}]):  # unresolved live binding
        changed = data_local.reconcile_bound_live()
    assert changed == 0
    # NOT cleared -- stays True, to expire via the freshness TTL if genuinely gone.
    assert tracking.load_record(tmp_path / "aaaa.yaml").bound_live is True


def test_reconcile_scan_failure_persists_nothing(tmp_path):
    """When the authoritative scan raises (Unknown), nothing is stamped."""
    (tmp_path / "wt-a").mkdir()
    rec = _rec("aaaa", path=str(tmp_path / "wt-a"))
    tracking.save_record(rec, tmp_path / "aaaa.yaml")
    loaded = [tracking.load_record(tmp_path / "aaaa.yaml")]
    with patch("agent_worktrees.config.tracking_dir", return_value=tmp_path), \
         patch("agent_worktrees.config.detect_platform", return_value="wsl"), \
         patch("agent_worktrees.tracking.list_records", return_value=loaded), \
         patch("agent_worktrees.reclaim.resolve_bound_copilots",
               side_effect=RuntimeError("scan blew up")):
        changed = data_local.reconcile_bound_live()
    assert changed == 0
    assert tracking.load_record(tmp_path / "aaaa.yaml").bound_live is None


# ── Populate consume: fresh bound hint unions ADDITIVELY into active paths ──

def _fresh():
    return datetime.now().isoformat()


def _stale():
    secs = cli._BOUND_LIVE_HINT_TTL_SECS + 60
    return (datetime.now() - timedelta(seconds=secs)).isoformat()


def _empty_ctx():
    import types
    return types.SimpleNamespace(active_sessions={})


def test_fresh_bound_hint_marks_active_when_no_mux(tmp_path):
    """Bare-resume core case: no mux batch, no mux hint, no lock -- but a FRESH
    bound_live=True hint surfaces the worktree ACTIVE."""
    rec = _rec("aaaa", path="/tmp/a", bound_live=True, bound_live_at=_fresh())
    with patch("agent_worktrees.sessions._list_mux_sessions", return_value=None), \
         patch("agent_worktrees.sessions.has_mux_session", return_value=False):
        active = cli._build_active_paths([rec], session_ctx=_empty_ctx())
    assert active == {"/tmp/a"}


def test_fresh_bound_hint_is_additive_even_with_mux_batch(tmp_path):
    """The bound hint is unioned REGARDLESS of the mux batch (not a
    batch-unavailable fallback) -- it catches what the mux/lock scans structurally
    miss, so it must apply on the batch-present path too."""
    rec = _rec("aaaa", path="/tmp/a", bound_live=True, bound_live_at=_fresh())
    with patch("agent_worktrees.sessions._list_mux_sessions", return_value={}), \
         patch("agent_worktrees.sessions.has_mux_session") as m_has:
        active = cli._build_active_paths([rec], session_ctx=_empty_ctx())
    assert active == {"/tmp/a"}
    m_has.assert_not_called()  # batch present -> no per-worktree mux probe


def test_stale_bound_hint_does_not_mark_active(tmp_path):
    rec = _rec("aaaa", path="/tmp/a", bound_live=True, bound_live_at=_stale())
    with patch("agent_worktrees.sessions._list_mux_sessions", return_value={}), \
         patch("agent_worktrees.sessions.has_mux_session", return_value=False):
        active = cli._build_active_paths([rec], session_ctx=_empty_ctx())
    assert active == set()


def test_false_bound_hint_does_not_mark_active(tmp_path):
    rec = _rec("aaaa", path="/tmp/a", bound_live=False, bound_live_at=_fresh())
    with patch("agent_worktrees.sessions._list_mux_sessions", return_value={}), \
         patch("agent_worktrees.sessions.has_mux_session", return_value=False):
        active = cli._build_active_paths([rec], session_ctx=_empty_ctx())
    assert active == set()


def test_no_bound_hint_is_noop(tmp_path):
    rec = _rec("aaaa", path="/tmp/a")  # never reconciled
    with patch("agent_worktrees.sessions._list_mux_sessions", return_value={}), \
         patch("agent_worktrees.sessions.has_mux_session", return_value=False):
        active = cli._build_active_paths([rec], session_ctx=_empty_ctx())
    assert active == set()


# ── _worktree_to_dict surfaces the fresh hint for the fast (Phase-1) pass ──

def test_worktree_to_dict_emits_session_bound_live_when_fresh(tmp_path):
    rec = _rec("aaaa", path=str(tmp_path), bound_live=True, bound_live_at=_fresh())
    d = cli._worktree_to_dict(rec)
    assert d.get("session_bound_live") is True


def test_worktree_to_dict_omits_session_bound_live_when_stale(tmp_path):
    rec = _rec("aaaa", path=str(tmp_path), bound_live=True, bound_live_at=_stale())
    d = cli._worktree_to_dict(rec)
    assert "session_bound_live" not in d
