"""Tests for the classifier's tracking-status override (#1447).

A finalized/complete worktree must read COMPLETED regardless of the raw git
state -- a squash-merged worktree branch reads "N ahead" until reconciled, and
that un-reconciled squash artifact must not present as WIP in the picker.
"""

from __future__ import annotations

import agent_worktrees.__main__ as m
from agent_worktrees import git_ops
from agent_worktrees import tracking


def _rec(status: str):
    return tracking.WorktreeRecord(
        worktree_id="wt1",
        branch="worktree/wt1",
        worktree_path="/tmp/wt1",
        repo="owner/repo",
        machine="m",
        platform="wsl",
        started_at="2026-06-01T10:00:00",
        last_resumed_at="2026-06-01T10:00:00",
        resume_count=0,
        title=None,
        status=status,
        completed_at=None,
        sessions=[],
        prs=[],
        kind="session",
    )


def _info(state, *, ahead=0, behind=0):
    return git_ops.WorktreeStateInfo(state=state, ahead=ahead, behind=behind)


class TestApplyTrackingOverride:
    def test_finalized_squash_merged_wip_reads_completed(self):
        # The #1447 case: finalized, but the branch still carries pre-squash
        # commits so raw git classifies it ACTIVE/WIP with ahead>0.
        info = _info(git_ops.WorktreeState.ACTIVE, ahead=3, behind=7)
        out = m._apply_tracking_override(_rec("finalized"), info)
        assert out.state == git_ops.WorktreeState.COMPLETED

    def test_finalized_zero_commit_still_completed(self):
        # The original zero-commit case stays covered.
        info = _info(git_ops.WorktreeState.UNUSED)
        out = m._apply_tracking_override(_rec("finalized"), info)
        assert out.state == git_ops.WorktreeState.COMPLETED

    def test_complete_and_completed_statuses_honored(self):
        for status in ("complete", "completed"):
            info = _info(git_ops.WorktreeState.ACTIVE, ahead=1)
            out = m._apply_tracking_override(_rec(status), info)
            assert out.state == git_ops.WorktreeState.COMPLETED, status

    def test_gone_worktree_never_masked(self):
        # A missing checkout is real regardless of a finalized status.
        info = _info(git_ops.WorktreeState.GONE)
        out = m._apply_tracking_override(_rec("finalized"), info)
        assert out.state == git_ops.WorktreeState.GONE

    def test_active_status_is_untouched(self):
        info = _info(git_ops.WorktreeState.ACTIVE, ahead=2, behind=1)
        out = m._apply_tracking_override(_rec("active"), info)
        assert out.state == git_ops.WorktreeState.ACTIVE
        assert out.ahead == 2 and out.behind == 1
