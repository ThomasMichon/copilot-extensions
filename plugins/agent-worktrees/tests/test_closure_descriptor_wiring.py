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
    info = git_ops.WorktreeStateInfo(state=git_ops.WorktreeState.COMPLETED)
    row = cli._worktree_to_dict(rec, state_info=info)
    assert "closure" in row
    assert row["closure"]["closure"] == {"final": True}
    assert row["closure"]["label"] == "FINAL"
    assert row["closure"]["evidence_mode"] == "refreshed"


def test_closure_reports_merged_when_held_claim_present():
    rec = _rec()
    rec.resources = [
        tracking.ResourceClaim(kind="codespace", ref="cs-1", state="active")
    ]
    info = git_ops.WorktreeStateInfo(state=git_ops.WorktreeState.COMPLETED)
    row = cli._worktree_to_dict(rec, state_info=info)
    assert row["cleanup_bucket"] == "held-claims"
    assert row["closure"]["label"] == "MERGED"
    assert row["closure"]["claims"] == {"held": 1}
    assert {"code": "held-claims", "count": 1} in row["closure"]["blockers"]


def test_closure_absent_without_state_info():
    rec = _rec()
    row = cli._worktree_to_dict(rec)
    assert "closure" not in row
