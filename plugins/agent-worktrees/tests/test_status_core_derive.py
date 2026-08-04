"""worktree-status-core: derive-layer rendering of the agent-asserted
disposition overlay (follow-up glyph + summary, follow-up cleanup bucket).

Imports only ``picker_tui.derive`` (no textual dependency), so it runs even
without the optional TUI dep installed.
"""

from __future__ import annotations

from agent_worktrees.picker_tui import derive


def _raw(**kw):
    base = dict(id="lambda-core-win-20260715-0000-abcd", machine="lambda-core",
                title="Feeder cam", status="finalized", state="completed")
    base.update(kw)
    return base


class TestDispositionGlyph:
    def test_flagged_gets_glyph_and_summary(self):
        n = derive.norm(_raw(follow_up=True, summary="Phases C/D left; PR open"),
                        "lambda-core", "win")
        assert n["title"].startswith("\u271a ")          # ✚ prefix
        assert "Phases C/D left; PR open" in n["title"]   # summary appended
        assert n["follow_up"] is True
        assert n["summary"] == "Phases C/D left; PR open"

    def test_unflagged_has_no_glyph(self):
        n = derive.norm(_raw(title="Done"), "lambda-core", "win")
        assert not n["title"].startswith("\u271a")
        assert n["follow_up"] is False

    def test_summary_without_title_uses_summary(self):
        n = derive.norm(_raw(title="", follow_up=True, summary="just this"),
                        "lambda-core", "win")
        assert n["title"] == "\u271a just this"

    def test_state_stays_pure_for_bucketing(self):
        # The glyph never leaks into ``state`` (bucket()/prune key off it).
        n = derive.norm(_raw(follow_up=True, summary="x"), "lambda-core", "win")
        assert n["state"] == "FINAL"


class TestFastPassActive:
    """The classification-absent (fast Phase-1 populate) heuristic must mark a
    live worktree ACTIVE from the CHEAP mux/lock signals, so the picker's Active
    section paints at once instead of waiting seconds for the per-worktree git
    classify -- and it must agree with the git pass to avoid flicker.

    All fixtures omit ``state`` (classification absent). ``status='active'`` is
    the tracking status; the live signals are ``mux_session`` / ``mux_attached``
    (from the batched mux read) and ``session_lock_live`` (from the lock scan)."""

    def _raw_active(self, **kw):
        base = dict(id="lambda-core-win-20260803-0000-abcd",
                    machine="lambda-core", title="Live", status="active")
        base.update(kw)
        return base

    def test_mux_session_marks_active_without_git(self):
        n = derive.norm(self._raw_active(mux_session=True), "m", "e")
        assert n["state"] == "ACTIVE"

    def test_mux_attached_marks_active_without_git(self):
        n = derive.norm(self._raw_active(mux_attached=True), "m", "e")
        assert n["state"] == "ACTIVE"

    def test_lock_live_marks_active_without_git(self):
        n = derive.norm(self._raw_active(session_lock_live=True), "m", "e")
        assert n["state"] == "ACTIVE"

    def test_bound_live_marks_active_without_git(self):
        # #1416 bare-resume: no mux, no lock -- only the cached bound-Copilot hint
        # (a bare-resumed session, cwd=home) marks the worktree ACTIVE.
        n = derive.norm(self._raw_active(session_bound_live=True), "m", "e")
        assert n["state"] == "ACTIVE"

    def test_live_session_beats_merged_pr(self):
        # A just-merged PR with the session still running must render ACTIVE, not
        # FINAL -- the git pass returns ACTIVE (active_paths precedence), so the
        # fast pass must too, or the row flickers FINAL -> ACTIVE after ~5 s.
        n = derive.norm(
            self._raw_active(mux_session=True, pr={"state": "merged"}), "m", "e")
        assert n["state"] == "ACTIVE"

    def test_live_session_beats_finalized_status(self):
        n = derive.norm(
            self._raw_active(status="finalized", mux_session=True), "m", "e")
        assert n["state"] == "ACTIVE"

    def test_no_live_signal_falls_back_to_wip_unused(self):
        # No mux, no lock: unchanged legacy behaviour (turns -> WIP, else UNUSED).
        assert derive.norm(
            self._raw_active(turn_count=3), "m", "e")["state"] == "WIP"
        assert derive.norm(
            self._raw_active(turn_count=0), "m", "e")["state"] == "UNUSED"

    def test_classified_state_still_wins_when_present(self):
        # When git classification IS present, its ``state`` is authoritative and
        # the fast-pass heuristic does not run (no regression to Phase 2).
        n = derive.norm(
            self._raw_active(state="dirty", mux_session=False), "m", "e")
        assert n["state"] == derive._STATE_LABEL.get("dirty", "DIRTY")


class TestFollowUpBucket:
    def test_bucket_dispo_review(self):
        assert derive.BUCKET_DISPO["follow-up"] == "REVIEW"
        assert "follow-up" in derive.BUCKET_REASON

    def test_authoritative_bucket_passthrough(self):
        n = derive.norm(_raw(follow_up=True, cleanup_bucket="follow-up"),
                        "lambda-core", "win")
        assert n["cleanup_bucket"] == "follow-up"

    def test_fallback_flagged_finalized_is_follow_up(self):
        # No authoritative cleanup_bucket (old remote): a flagged finalized
        # worktree downgrades from clean -> follow-up.
        assert derive._bucket_from_raw(
            {"id": "x", "status": "finalized", "follow_up": True}) == "follow-up"

    def test_fallback_unflagged_finalized_is_clean(self):
        assert derive._bucket_from_raw(
            {"id": "x", "status": "finalized"}) == "clean"
