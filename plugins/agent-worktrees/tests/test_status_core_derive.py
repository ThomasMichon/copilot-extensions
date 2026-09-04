"""worktree-status-core: derive-layer rendering of the agent-asserted
disposition overlay (follow-up glyph + summary, follow-up cleanup bucket).

Imports only ``picker_tui.derive`` (no textual dependency), so it runs even
without the optional TUI dep installed.
"""

from __future__ import annotations

from agent_worktrees.picker_tui import derive


def _raw(**kw):
    base = dict(id="anomalous-potato-win-20260715-0000-abcd", machine="anomalous-potato",
                title="Feeder cam", status="finalized", state="completed")
    base.update(kw)
    return base


class TestDispositionGlyph:
    def test_flagged_gets_glyph_and_summary(self):
        n = derive.norm(_raw(follow_up=True, summary="Phases C/D left; PR open"),
                        "anomalous-potato", "win")
        assert n["title"].startswith("\u271a ")          # ✚ prefix
        assert "Phases C/D left; PR open" in n["title"]   # summary appended
        assert n["follow_up"] is True
        assert n["summary"] == "Phases C/D left; PR open"

    def test_unflagged_has_no_glyph(self):
        n = derive.norm(_raw(title="Done"), "anomalous-potato", "win")
        assert not n["title"].startswith("\u271a")
        assert n["follow_up"] is False

    def test_summary_without_title_uses_summary(self):
        n = derive.norm(_raw(title="", follow_up=True, summary="just this"),
                        "anomalous-potato", "win")
        assert n["title"] == "\u271a just this"

    def test_state_stays_pure_for_bucketing(self):
        # The glyph never leaks into ``state`` (bucket()/prune key off it).
        n = derive.norm(_raw(follow_up=True, summary="x"), "anomalous-potato", "win")
        assert n["state"] == "FINAL"


class TestPairMarker:
    """citadel #957: a paired -harness/-knowledge row is scannable + carries the
    pair linkage on the normalized record."""

    def test_paired_gets_link_glyph_and_fields(self):
        n = derive.norm(
            _raw(title="Harness work", pair_id="20260806-ab",
                 pair_role="harness", pair_kind="worktree"),
            "anomalous-potato", "win",
        )
        assert n["title"].startswith("\u26ad ")   # ⚭ pair marker
        assert n["is_paired"] is True
        assert n["pair_id"] == "20260806-ab"
        assert n["pair_role"] == "harness"
        assert n["pair_kind"] == "worktree"

    def test_unpaired_has_no_marker_or_fields(self):
        n = derive.norm(_raw(title="Solo"), "anomalous-potato", "win")
        assert not n["title"].startswith("\u26ad")
        assert n["is_paired"] is False
        assert n["pair_id"] is None
        assert n["pair_role"] is None
        assert n["pair_kind"] is None

    def test_pair_marker_inner_of_orphan_outer_of_follow_up(self):
        # Marker order: ⚠ (orphan, outermost) · ⚭ (pair) · ✚ (follow-up).
        n = derive.norm(
            _raw(title="T", pair_id="p", pair_role="knowledge",
                 follow_up=True, summary="s", session_bare_orphan=True),
            "m", "e",
        )
        assert n["title"].startswith("\u26a0 \u26ad \u271a ")

    def test_pair_marker_does_not_leak_into_state(self):
        n = derive.norm(
            _raw(pair_id="p", pair_role="harness"), "anomalous-potato", "win"
        )
        assert n["state"] == "FINAL"


class TestAnnotatePairs:
    """citadel #957: the aggregated dual-status data layer over the row set."""

    def _row(self, **kw):
        return derive.norm(_raw(**kw), "m", "e")

    def test_sibling_summary_both_directions(self):
        harness = self._row(id="m-win-0000-hhhh", title="H", status="active",
                            state="active", pair_id="p1", pair_role="harness")
        knowledge = self._row(id="m-win-0000-kkkk", title="K", status="finalized",
                              state="completed", pair_id="p1", pair_role="knowledge")
        derive.annotate_pairs([harness, knowledge])
        assert harness["pair_sibling"]["role"] == "knowledge"
        assert knowledge["pair_sibling"]["role"] == "harness"

    def test_unpaired_untouched(self):
        solo = self._row(id="m-win-0000-ssss", title="S")
        derive.annotate_pairs([solo])
        assert "pair_sibling" not in solo
        assert "pair_attention" not in solo

    def test_sibling_absent_is_none(self):
        harness = self._row(id="m-win-0000-hhhh", pair_id="p1", pair_role="harness")
        derive.annotate_pairs([harness])  # sibling not in set
        assert harness["pair_sibling"] is None

    def test_attention_aggregates_from_sibling(self):
        # A clean finalized harness whose KNOWLEDGE sibling is dirty -> the pair
        # wants attention on the harness row too.
        harness = self._row(id="m-win-0000-hhhh", status="finalized",
                            state="completed", pair_id="p1", pair_role="harness")
        knowledge = self._row(id="m-win-0000-kkkk", state="dirty",
                              pair_id="p1", pair_role="knowledge")
        derive.annotate_pairs([harness, knowledge])
        assert harness["pair_attention"] is True
        assert knowledge["pair_attention"] is True

    def test_attention_from_follow_up(self):
        h = self._row(id="m-win-0000-hhhh", follow_up=True, summary="x",
                     pair_id="p1", pair_role="harness")
        k = self._row(id="m-win-0000-kkkk", status="finalized", state="completed",
                     pair_id="p1", pair_role="knowledge")
        derive.annotate_pairs([h, k])
        assert h["pair_attention"] is True and k["pair_attention"] is True

    def test_no_attention_when_both_clean(self):
        h = self._row(id="m-win-0000-hhhh", status="finalized", state="completed",
                     pair_id="p1", pair_role="harness")
        k = self._row(id="m-win-0000-kkkk", status="finalized", state="completed",
                     pair_id="p1", pair_role="knowledge")
        derive.annotate_pairs([h, k])
        assert h["pair_attention"] is False and k["pair_attention"] is False

    def test_for_machine_annotates(self):
        h = self._row(id="m-win-0000-hhhh", state="completed", status="finalized",
                     pair_id="p1", pair_role="harness")
        k = self._row(id="m-win-0000-kkkk", state="wip",
                     pair_id="p1", pair_role="knowledge")
        derive.for_machine([h, k], "m", "e")
        assert h["pair_sibling"]["role"] == "knowledge"
        assert h["pair_attention"] is True   # sibling is WIP


class TestFastPassActive:
    """The classification-absent (fast Phase-1 populate) heuristic must mark a
    live worktree ACTIVE from the CHEAP mux/lock signals, so the picker's Active
    section paints at once instead of waiting seconds for the per-worktree git
    classify -- and it must agree with the git pass to avoid flicker.

    All fixtures omit ``state`` (classification absent). ``status='active'`` is
    the tracking status; the live signals are ``mux_session`` / ``mux_attached``
    (from the batched mux read) and ``session_lock_live`` (from the lock scan)."""

    def _raw_active(self, **kw):
        base = dict(id="anomalous-potato-win-20260803-0000-abcd",
                    machine="anomalous-potato", title="Live", status="active")
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

    def test_ahp_live_marks_active_without_mux(self):
        n = derive.norm(self._raw_active(session_ahp_live=True), "m", "e")
        assert n["state"] == "ACTIVE"
        assert n["sessionless"] is False

    def test_bridge_live_marks_active_without_git(self):
        # #4272 bridge-lock: no mux, no lock, no bound hint -- only a live
        # bridge.lock (a bridge-owned bare session) marks the worktree ACTIVE.
        n = derive.norm(self._raw_active(session_bridge_live=True), "m", "e")
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
                        "anomalous-potato", "win")
        assert n["cleanup_bucket"] == "follow-up"

    def test_fallback_flagged_finalized_is_follow_up(self):
        # No authoritative cleanup_bucket (old remote): a flagged finalized
        # worktree downgrades from clean -> follow-up.
        assert derive._bucket_from_raw(
            {"id": "x", "status": "finalized", "follow_up": True}) == "follow-up"

    def test_fallback_unflagged_finalized_is_clean(self):
        assert derive._bucket_from_raw(
            {"id": "x", "status": "finalized"}) == "clean"
