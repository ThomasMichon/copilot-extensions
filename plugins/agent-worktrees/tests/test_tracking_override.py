"""Tests for the classifier's tracking-status override (#1447).

A finalized/complete worktree must read COMPLETED regardless of the raw git
state -- a squash-merged worktree branch reads "N ahead" until reconciled, and
that un-reconciled squash artifact must not present as WIP in the picker.
"""

from __future__ import annotations

import dataclasses
import types

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


def _info(state, *, ahead=0, behind=0, dirty=0):
    return git_ops.WorktreeStateInfo(state=state, ahead=ahead, behind=behind, dirty=dirty)


class TestApplyTrackingOverride:
    def test_finalized_squash_merged_wip_reads_completed(self):
        # The #1447 case: finalized, but the branch still carries pre-squash
        # commits so raw git classifies it WIP with ahead>0. (Only a live
        # session yields ACTIVE -- see test_finalized_active_session_not_masked.)
        info = _info(git_ops.WorktreeState.WIP, ahead=3, behind=7)
        out = m._apply_tracking_override(_rec("finalized"), info)
        assert out.state == git_ops.WorktreeState.COMPLETED

    def test_finalized_zero_commit_still_completed(self):
        # The original zero-commit case stays covered.
        info = _info(git_ops.WorktreeState.UNUSED)
        out = m._apply_tracking_override(_rec("finalized"), info)
        assert out.state == git_ops.WorktreeState.COMPLETED

    def test_complete_and_completed_statuses_honored(self):
        for status in ("complete", "completed"):
            info = _info(git_ops.WorktreeState.WIP, ahead=1)
            out = m._apply_tracking_override(_rec(status), info)
            assert out.state == git_ops.WorktreeState.COMPLETED, status

    def test_finalized_active_session_not_masked(self):
        # The bb68/ca29 status-tracking bug: a finalized worktree the operator
        # has re-opened (a live mux/lock session -> classify returns ACTIVE via
        # active_paths) must NOT be masked to COMPLETED. Liveness wins over the
        # durable finalize status, so the row stays actionable (Open/Stop or
        # Reclaim) instead of being stranded FINAL.
        for status in ("finalized", "complete", "completed"):
            info = _info(git_ops.WorktreeState.ACTIVE, ahead=2, behind=1)
            out = m._apply_tracking_override(_rec(status), info)
            assert out.state == git_ops.WorktreeState.ACTIVE, status
            assert out.ahead == 2 and out.behind == 1, status

    def test_gone_worktree_never_masked(self):
        # A missing checkout is real regardless of a finalized status.
        info = _info(git_ops.WorktreeState.GONE)
        out = m._apply_tracking_override(_rec("finalized"), info)
        assert out.state == git_ops.WorktreeState.GONE

    def test_finalized_dirty_worktree_never_masked(self):
        # A worktree finalized earlier and then modified afterward carries
        # real, unlanded uncommitted changes the stale finalized/complete/
        # completed status knows nothing about. Unlike the zero-commit and
        # squash-merged cases above (already-landed work misread by the raw
        # classifier), DIRTY here is a correct read of genuinely new content
        # -- masking it to COMPLETED would let plain `cleanup --clean` delete
        # it with no `--force` at all.
        for status in ("finalized", "complete", "completed"):
            info = _info(git_ops.WorktreeState.DIRTY, dirty=1)
            out = m._apply_tracking_override(_rec(status), info)
            assert out.state == git_ops.WorktreeState.DIRTY, status

    def test_finalized_orphan_with_dirty_never_masked(self):
        # git_ops._classify_git_state can report ORPHAN (no merge base) while
        # still carrying a nonzero dirty count. The override must key off
        # info.dirty, not just state == DIRTY, or an orphaned-but-modified
        # finalized worktree would still get masked to COMPLETED.
        for status in ("finalized", "complete", "completed"):
            info = _info(git_ops.WorktreeState.ORPHAN, dirty=2)
            out = m._apply_tracking_override(_rec(status), info)
            assert out.state == git_ops.WorktreeState.ORPHAN, status

    def test_finalized_cached_dirty_state_with_zero_count_never_masked(self):
        # Some callers (e.g. _classify_from_cache) reconstruct a
        # WorktreeStateInfo from a cached git_state string without
        # repopulating `dirty` -- so state == DIRTY, dirty == 0 is a real,
        # reachable combination, not just a theoretical one. The override
        # must catch this via the explicit state == DIRTY fallback, not only
        # the dirty > 0 count.
        for status in ("finalized", "complete", "completed"):
            info = _info(git_ops.WorktreeState.DIRTY, dirty=0)
            out = m._apply_tracking_override(_rec(status), info)
            assert out.state == git_ops.WorktreeState.DIRTY, status

    def test_active_status_is_untouched(self):
        info = _info(git_ops.WorktreeState.ACTIVE, ahead=2, behind=1)
        out = m._apply_tracking_override(_rec("active"), info)
        assert out.state == git_ops.WorktreeState.ACTIVE
        assert out.ahead == 2 and out.behind == 1


class TestRevalidateBeforeReap:
    """cmd_cleanup builds `to_clean` from a pre-lock snapshot; _revalidate_
    before_reap re-checks each worktree fresh right before it's actually
    deleted, closing the gap where a worktree could become dirty (or gain a
    live session) between that scan and the reap.
    """

    def _repo(self):
        return types.SimpleNamespace(remote="origin", default_branch="master")

    def test_still_clean_worktree_is_reaped_with_fresh_info(self, tmp_path, monkeypatch):
        wt = tmp_path / "wt1"
        wt.mkdir()
        rec = _rec("finalized")
        rec = dataclasses.replace(rec, worktree_path=str(wt))
        fresh = _info(git_ops.WorktreeState.UNUSED)
        monkeypatch.setattr(git_ops, "classify_worktree", lambda *a, **k: fresh)
        out, reason = m._revalidate_before_reap(rec, _info(git_ops.WorktreeState.COMPLETED),
                                                repo=self._repo(), active_paths=set())
        assert out is not None
        assert reason is None
        assert out.state == git_ops.WorktreeState.COMPLETED  # override applied

    def test_became_dirty_since_scan_is_not_reaped(self, tmp_path, monkeypatch):
        wt = tmp_path / "wt1"
        wt.mkdir()
        rec = _rec("finalized")
        rec = dataclasses.replace(rec, worktree_path=str(wt))
        fresh = _info(git_ops.WorktreeState.DIRTY, dirty=1)
        monkeypatch.setattr(git_ops, "classify_worktree", lambda *a, **k: fresh)
        out, reason = m._revalidate_before_reap(rec, _info(git_ops.WorktreeState.COMPLETED),
                                                repo=self._repo(), active_paths=set())
        assert out is None
        assert reason == "worktree became dirty since the initial scan"

    def test_became_active_since_scan_is_not_reaped(self, tmp_path, monkeypatch):
        wt = tmp_path / "wt1"
        wt.mkdir()
        rec = _rec("finalized")
        rec = dataclasses.replace(rec, worktree_path=str(wt))
        fresh = _info(git_ops.WorktreeState.ACTIVE)
        monkeypatch.setattr(git_ops, "classify_worktree", lambda *a, **k: fresh)
        out, reason = m._revalidate_before_reap(rec, _info(git_ops.WorktreeState.COMPLETED),
                                                repo=self._repo(), active_paths=set())
        assert out is None
        assert reason == "worktree became active since the initial scan"

    def test_missing_path_skips_revalidation(self, tmp_path):
        rec = _rec("finalized")
        rec = dataclasses.replace(rec, worktree_path=str(tmp_path / "gone"))
        original = _info(git_ops.WorktreeState.COMPLETED)
        out, reason = m._revalidate_before_reap(rec, original,
                                                repo=self._repo(), active_paths=set())
        assert out is original
        assert reason is None
