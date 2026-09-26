"""Integration test for worktree-finality-and-obligations Phase 6: exercise a
full finalized -> resumed -> held claim -> settled/released -> final cycle
across the modules the effort actually touched (tracking_claims + prune),
proving Phase 1's held-claims reopen fix, Phase 4's closure descriptor, and
`cleanup_disposition`'s claim gate compose correctly end-to-end -- not just
unit-tested in isolation."""

from __future__ import annotations

from agent_worktrees import git_ops, prune, tracking
from agent_worktrees.tracking import ResourceClaim
from agent_worktrees.tracking_claims import (
    add_resource_claim,
    release_resource_claim,
    settle_resource_claim,
)

S = git_ops.WorktreeState


def _rec() -> tracking.WorktreeRecord:
    return tracking.WorktreeRecord(
        worktree_id="wt-cycle",
        branch="worktree/wt-cycle",
        worktree_path="/tmp/wt-cycle",
        repo="owner/repo",
        machine="m",
        platform="wsl",
        started_at="2026-06-01T10:00:00",
        last_resumed_at="2026-06-01T10:00:00",
        resume_count=0,
        title=None,
        status="finalized",
        completed_at="2026-06-01T12:00:00",
        sessions=[],
        prs=[],
    )


def _info() -> git_ops.WorktreeStateInfo:
    # Git content never changes across this cycle -- the worktree's on-disk
    # state stays git-COMPLETED throughout; only the tracking-record status
    # and its held claims move. Mirrors a real reopen: an operator adds a new
    # obligation to an already-landed worktree without touching its content.
    return git_ops.WorktreeStateInfo(state=S.COMPLETED)


def _descriptor(rec: tracking.WorktreeRecord, info: git_ops.WorktreeStateInfo):
    held = sum(1 for c in rec.resources if c.is_live)
    open_follow_ups = tracking.effective_open_follow_up_count(rec)
    disp = prune.cleanup_disposition(rec, info, claimant_alive=lambda _ref: True)
    descriptor = prune.assemble_closure_descriptor(
        rec, info, disp,
        held_claims=held, open_follow_ups=open_follow_ups,
        evidence_mode="refreshed", evidence_complete=True,
    )
    return disp, descriptor


def test_finalized_resumed_held_claim_settled_released_final_cycle():
    rec = _rec()
    info = _info()

    # 1. Finalized, no obligations -> FINAL, safe to prune.
    disp, descriptor = _descriptor(rec, info)
    assert disp.cleanable is True
    assert descriptor.label == "FINAL"
    assert descriptor.final is True
    assert descriptor.action_disposition == "safe"

    # 2. Resume: a new obligation reopens the (finalize-not-terminal) owner.
    reopened = add_resource_claim(
        rec, ResourceClaim(kind="codespace", ref="cs-1", state="active"), save=False,
    )
    assert reopened.ref == "cs-1"
    assert rec.status == "active"  # Phase 1's held-claims reopen fix.

    # 3. Held (active): blocked, MERGED with a C1 marker -- never FINAL, even
    # though the underlying git content is unchanged (still COMPLETED).
    disp, descriptor = _descriptor(rec, info)
    assert disp.cleanable is False
    assert disp.bucket == "held-claims"
    assert descriptor.label == "MERGED"
    assert descriptor.final is False
    assert descriptor.action_disposition == "blocked"
    assert descriptor.compact == "MERGED C1"

    # 4. Settle to at-rest: STILL held (Phase 4: active|at-rest both count),
    # so still blocked -- only an explicit release (or `claims
    # reconcile-at-rest --apply`) actually frees it.
    settled = settle_resource_claim(rec, "cs-1", save=False)
    assert settled.state == "at-rest"
    disp, descriptor = _descriptor(rec, info)
    assert disp.cleanable is False
    assert disp.bucket == "held-claims"
    assert descriptor.compact == "MERGED C1"

    # 5. Release: no longer held -> back to FINAL, safe to prune again, with
    # no residual C1 marker -- the exact composition Phase 6 asks to prove.
    released = release_resource_claim(rec, "cs-1", save=False)
    assert released.state == "released"
    disp, descriptor = _descriptor(rec, info)
    assert disp.cleanable is True
    assert disp.bucket == "clean"
    assert descriptor.label == "FINAL"
    assert descriptor.final is True
    assert descriptor.action_disposition == "safe"
    assert descriptor.compact == "FINAL"
