"""Tests for cleanup/GC's closure-descriptor blocker enrichment (worktree-
finality-and-obligations Phase 4): a worktree blocked by more than one
concurrently-true condition (e.g. both a held claim AND an open follow-up)
must have every blocker named, not just whichever one `cleanup_disposition`
happened to check first and short-circuit on. This is reporting-only -- the
cleanable/not decision itself stays exactly `disp.cleanable` (see
`_closure_blockers`'s own docstring for why a full switch to the descriptor's
`action_disposition` as the actual gate was deferred)."""

from __future__ import annotations

from _cleanup_revalidation_helpers import CleanupHarness, S, info, make_record

from agent_worktrees import cleanup_gc_cli as cgc
from agent_worktrees import prune, tracking


def _disp(bucket, reason="reason text", cleanable=False):
    return prune.CleanupDisposition(cleanable, bucket, reason)


def test_enrich_is_a_noop_with_zero_or_one_blocker():
    assert cgc._enrich_reason_with_blockers("x", []) == "x"
    assert cgc._enrich_reason_with_blockers("x", [{"code": "dirty", "count": 1}]) == "x"


def test_enrich_appends_every_additional_blocker():
    reason = cgc._enrich_reason_with_blockers(
        "1 held resource claim(s) pending",
        [
            {"code": "held-claims", "count": 1},
            {"code": "open-follow-ups", "count": 2},
        ],
    )
    assert reason == (
        "1 held resource claim(s) pending \u00b7 blockers: "
        "held-claims\u00d71, open-follow-ups\u00d72"
    )


def test_closure_blockers_surfaces_held_claims_and_follow_ups_even_though_disp_short_circuits():
    """`cleanup_disposition` returns as soon as it hits held-claims (checked
    before follow-ups), so `disp.reason`/`disp.bucket` alone only ever name
    ONE of the two. The closure descriptor's own `blockers` list is not
    short-circuited -- it must report both."""
    rec = tracking.WorktreeRecord(
        worktree_id="wt1", branch="worktree/wt1", worktree_path="/tmp/wt1",
        repo="owner/repo", machine="m", platform="wsl",
        started_at="1970-01-01T00:00:00+00:00",
        last_resumed_at="1970-01-01T00:00:00+00:00", resume_count=0,
        title=None, status="finalized", completed_at=None, sessions=[], prs=[],
        kind="session",
        resources=[tracking.ResourceClaim(kind="codespace", ref="cs-1", state="active")],
        follow_ups=[tracking.FollowUpRecord(id="fu-1", summary="deploy it", state="open")],
    )
    wt_info = info(S.COMPLETED)
    disp = _disp("held-claims", "reason", cleanable=False)
    blockers = cgc._closure_blockers(rec, wt_info, disp, turn_count=0)
    codes = {b["code"]: b["count"] for b in blockers}
    assert codes.get("held-claims") == 1
    assert codes.get("open-follow-ups") == 1


def test_cmd_cleanup_reports_every_blocker_when_a_worktree_has_both(capsys, monkeypatch, tmp_path):
    harness = CleanupHarness(monkeypatch, tmp_path)
    rec = make_record(tmp_path, status="finalized")
    rec.resources.append(tracking.ResourceClaim(kind="codespace", ref="cs-1", state="active"))
    rec.follow_ups.append(tracking.FollowUpRecord(id="fu-1", summary="deploy it", state="open"))
    harness.seed(rec)
    harness.set_classifier(info(S.UNUSED), info(S.UNUSED))
    assert harness.run_batch() == 0
    captured = capsys.readouterr()
    out = captured.out + captured.err
    # The short-circuited single-reason text (held-claims, checked first) is
    # still present, but the follow-up blocker the plain reason would have
    # hidden is now named too.
    assert "held resource claim" in out
    assert "open-follow-ups" in out
    assert harness.reaped == []
