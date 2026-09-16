"""Tests wiring `prune.assemble_closure_descriptor` into `_worktree_to_dict`
(worktree-finality-and-obligations Phase 4: `list --json --classify` publishes
the canonical closure descriptor, additive alongside the legacy
`cleanup_bucket`/`state` fields)."""

from __future__ import annotations

from agent_worktrees import __main__ as cli
from agent_worktrees import git_ops, tracking


def _rec(**kw):
    base = dict(
        worktree_id="wt-1", branch="worktree/wt-1", worktree_path="/tmp/wt-1",
        repo="owner/repo", machine="m", platform="wsl",
        started_at="2026-06-01T10:00:00", last_resumed_at="2026-06-01T10:00:00",
        resume_count=0, title=None, status="finalized", completed_at=None,
    )
    base.update(kw)
    return tracking.WorktreeRecord(**base)


def test_closure_present_and_final_when_clean():
    rec = _rec()
    info = git_ops.WorktreeStateInfo(
        state=git_ops.WorktreeState.COMPLETED, fetch_requested=True,
    )
    row = cli._worktree_to_dict(rec, state_info=info)
    assert "closure" in row
    assert row["closure"]["closure"] == {"final": True}
    assert row["closure"]["label"] == "FINAL"
    assert row["closure"]["facts"]["upstream_containment"]["confirmed"] is True
    assert row["closure"]["facts"]["open_claims"]["confirmed"] is True


def test_closure_reports_merged_when_held_claim_present():
    rec = _rec()
    rec.resources = [
        tracking.ResourceClaim(kind="codespace", ref="cs-1", state="active")
    ]
    info = git_ops.WorktreeStateInfo(
        state=git_ops.WorktreeState.COMPLETED, fetch_requested=True,
    )
    row = cli._worktree_to_dict(rec, state_info=info)
    assert row["cleanup_bucket"] == "held-claims"
    assert row["closure"]["label"] == "MERGED"
    assert row["closure"]["claims"] == {"held": 1}
    assert {"code": "held-claims", "count": 1} in row["closure"]["blockers"]


def test_closure_absent_without_state_info():
    rec = _rec()
    row = cli._worktree_to_dict(rec)
    assert "closure" not in row


def test_closure_downgrades_to_cached_when_fetch_failed():
    # worktree-finality-and-obligations Phase 5 follow-up: a classification
    # whose REQUESTED fetch failed/timed out must not still report
    # "refreshed" evidence -- that would let a stale/failed --fetch attempt
    # claim FINAL on out-of-date local refs.
    rec = _rec()
    info = git_ops.WorktreeStateInfo(
        state=git_ops.WorktreeState.COMPLETED,
        fetch_requested=True, fetch_failed=True,
    )
    row = cli._worktree_to_dict(rec, state_info=info)
    assert row["closure"]["facts"]["upstream_containment"]["confirmed"] is False
    assert row["closure"]["label"] == "MERGED"
    assert row["closure"]["closure"] == {"final": False}


def test_closure_downgrades_to_cached_when_no_fetch_requested():
    # #discussion_r4009567365: this is the REAL current-callers' scenario --
    # `_classify_one_record` and the Picker/streaming `--classify` paths all
    # call `classify_worktree(..., fetch=False)`, so `fetch_requested`
    # defaults False even though the classification itself is fresh. Without
    # checking `fetch_requested`, an ordinary fetch-free `list --json
    # --classify` could still report "refreshed"/FINAL from stale local refs.
    rec = _rec()
    info = git_ops.WorktreeStateInfo(state=git_ops.WorktreeState.COMPLETED)
    row = cli._worktree_to_dict(rec, state_info=info)
    assert row["closure"]["facts"]["upstream_containment"]["confirmed"] is False
    assert row["closure"]["label"] == "MERGED"
    assert row["closure"]["closure"] == {"final": False}

