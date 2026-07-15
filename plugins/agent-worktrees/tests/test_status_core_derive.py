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
