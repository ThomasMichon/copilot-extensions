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


class TestRevalidateCleanupSafety:
    """`_revalidate_cleanup_safety` is the single canonical, under-lock safety
    recheck shared by all three reapers (cleanup-toctou-revalidation effort,
    replacing the narrower `_revalidate_before_reap`). It reloads the record
    fresh from disk, re-classifies git state, and -- non-forced only -- runs
    the complete `cleanup_disposition`.
    """

    def _repo(self):
        return types.SimpleNamespace(
            anchor="/anchor", remote="origin", default_branch="master",
            worktree_root="/wt-root")

    def _save(self, tracking_dir, rec):
        tracking.save_record(rec, tracking_dir / f"{rec.worktree_id}.yaml")
        return rec

    def _no_sessions(self, monkeypatch):
        """Every worktree reports no live/hosted session and zero turns."""
        monkeypatch.setattr(
            m, "_build_active_paths", lambda records, ctx=None: set())
        monkeypatch.setattr(
            m.sessions, "scan_sessions_fast",
            lambda records: m.sessions.SessionContext())
        monkeypatch.setattr(m, "_hosted_session_blocks_cleanup", lambda rec: False)

    def test_still_clean_worktree_is_reaped_with_fresh_info(
            self, tmp_path, monkeypatch):
        self._no_sessions(monkeypatch)
        wt = tmp_path / "wt1"
        wt.mkdir()
        self._save(tmp_path, dataclasses.replace(
            _rec("finalized"), worktree_id="wt1", worktree_path=str(wt)))
        fresh = _info(git_ops.WorktreeState.UNUSED)
        monkeypatch.setattr(git_ops, "classify_worktree", lambda *a, **k: fresh)
        reaped = []
        result = m._revalidate_cleanup_safety(
            "wt1", repo=self._repo(), tracking_path=tmp_path,
            reap=lambda r, i: (reaped.append(r.worktree_id), (0, []))[1])
        assert result.cleanable is True
        assert result.info.state == git_ops.WorktreeState.COMPLETED  # override
        assert reaped == ["wt1"]

    def test_became_dirty_since_scan_is_not_reaped(self, tmp_path, monkeypatch):
        self._no_sessions(monkeypatch)
        wt = tmp_path / "wt1"
        wt.mkdir()
        self._save(tmp_path, dataclasses.replace(
            _rec("finalized"), worktree_id="wt1", worktree_path=str(wt)))
        fresh = _info(git_ops.WorktreeState.DIRTY, dirty=1)
        monkeypatch.setattr(git_ops, "classify_worktree", lambda *a, **k: fresh)
        reaped = []
        result = m._revalidate_cleanup_safety(
            "wt1", repo=self._repo(), tracking_path=tmp_path,
            reap=lambda r, i: (reaped.append(r.worktree_id), (0, []))[1])
        assert result.cleanable is False and result.bucket == "dirty"
        assert reaped == []

    def test_became_active_since_scan_is_not_reaped(self, tmp_path, monkeypatch):
        wt = tmp_path / "wt1"
        wt.mkdir()
        (wt / ".git").mkdir()
        self._save(tmp_path, dataclasses.replace(
            _rec("finalized"), worktree_id="wt1", worktree_path=str(wt)))
        monkeypatch.setattr(
            m, "_build_active_paths",
            lambda records, ctx=None: {m._normalize_path(str(wt))})
        monkeypatch.setattr(
            m.sessions, "scan_sessions_fast",
            lambda records: m.sessions.SessionContext())
        monkeypatch.setattr(m, "_hosted_session_blocks_cleanup", lambda rec: False)
        result = m._revalidate_cleanup_safety(
            "wt1", repo=self._repo(), tracking_path=tmp_path)
        assert result.cleanable is False and result.bucket == "active"
        assert result.reason == "worktree became active since the initial scan"

    def test_wip_since_scan_is_not_reaped(self, tmp_path, monkeypatch):
        # cleanup_disposition's WIP check now runs before the finalized/
        # COMPLETED shortcut (mirroring #2635's `dirty` ordering fix). Uses a
        # non-"finalized" status here because `_apply_tracking_override`
        # separately masks WIP -> COMPLETED for a `finalized`-status record
        # (a real, distinct residual gap -- see
        # `test_finalized_status_currently_masks_new_wip_gap` below and
        # copilot-extensions#2649).
        self._no_sessions(monkeypatch)
        wt = tmp_path / "wt1"
        wt.mkdir()
        self._save(tmp_path, dataclasses.replace(
            _rec("active"), worktree_id="wt1", worktree_path=str(wt)))
        fresh = _info(git_ops.WorktreeState.WIP)
        monkeypatch.setattr(git_ops, "classify_worktree", lambda *a, **k: fresh)
        result = m._revalidate_cleanup_safety(
            "wt1", repo=self._repo(), tracking_path=tmp_path)
        assert result.cleanable is False and result.bucket == "wip"

    def test_conversation_only_since_scan_is_not_reaped(
            self, tmp_path, monkeypatch):
        wt = tmp_path / "wt1"
        wt.mkdir()
        self._save(tmp_path, dataclasses.replace(
            _rec("active"), worktree_id="wt1", worktree_path=str(wt)))
        monkeypatch.setattr(
            m, "_build_active_paths", lambda records, ctx=None: set())
        monkeypatch.setattr(
            m.sessions, "scan_sessions_fast",
            lambda records: m.sessions.SessionContext(
                turn_count={m._normalize_path(str(wt)): 3}))
        monkeypatch.setattr(m, "_hosted_session_blocks_cleanup", lambda rec: False)
        fresh = _info(git_ops.WorktreeState.UNUSED)
        monkeypatch.setattr(git_ops, "classify_worktree", lambda *a, **k: fresh)
        result = m._revalidate_cleanup_safety(
            "wt1", repo=self._repo(), tracking_path=tmp_path,
            include_conversations=False)
        assert result.cleanable is False and result.bucket == "conversation"

    def test_finalized_status_currently_masks_new_wip_gap(
            self, tmp_path, monkeypatch):
        # KNOWN RESIDUAL GAP (found while implementing this test class, not
        # yet fixed -- tracked as copilot-extensions#2649, cleanup-toctou-
        # revalidation effort): `_apply_tracking_override` masks ANY non-
        # dirty/GONE/ACTIVE git state to COMPLETED for a `finalized`-status
        # record (it exists to correct a squash-merge artifact that reads
        # WIP even though the content already landed) -- but it can't
        # distinguish that from GENUINELY new commits made after finalize,
        # so a finalized record that gains real WIP content today still
        # reads COMPLETED (cleanable) by the time `cleanup_disposition` ever
        # sees it, regardless of that function's own check ordering. This
        # test pins today's actual (unsafe) behavior so a future fix has a
        # red test to flip, rather than silently regressing further.
        self._no_sessions(monkeypatch)
        wt = tmp_path / "wt1"
        wt.mkdir()
        self._save(tmp_path, dataclasses.replace(
            _rec("finalized"), worktree_id="wt1", worktree_path=str(wt)))
        fresh = _info(git_ops.WorktreeState.WIP)
        monkeypatch.setattr(git_ops, "classify_worktree", lambda *a, **k: fresh)
        result = m._revalidate_cleanup_safety(
            "wt1", repo=self._repo(), tracking_path=tmp_path)
        assert result.cleanable is True and result.bucket == "clean"

    def test_missing_path_branch_merged_is_reaped(self, tmp_path, monkeypatch):
        self._no_sessions(monkeypatch)
        self._save(tmp_path, dataclasses.replace(
            _rec("active"), worktree_id="wt1",
            worktree_path=str(tmp_path / "gone")))
        monkeypatch.setattr(git_ops, "is_branch_merged", lambda *a, **k: True)
        reaped = []
        result = m._revalidate_cleanup_safety(
            "wt1", repo=self._repo(), tracking_path=tmp_path,
            reap=lambda r, i: (reaped.append(r.worktree_id), (0, []))[1])
        assert result.cleanable is True and result.bucket == "clean"
        assert reaped == ["wt1"]

    def test_missing_path_branch_unmerged_is_not_reaped(
            self, tmp_path, monkeypatch):
        self._no_sessions(monkeypatch)
        self._save(tmp_path, dataclasses.replace(
            _rec("active"), worktree_id="wt1",
            worktree_path=str(tmp_path / "gone")))
        monkeypatch.setattr(git_ops, "is_branch_merged", lambda *a, **k: False)
        result = m._revalidate_cleanup_safety(
            "wt1", repo=self._repo(), tracking_path=tmp_path)
        assert result.cleanable is False and result.bucket == "unmerged"

    def test_worktree_not_found_is_not_cleanable(self, tmp_path):
        result = m._revalidate_cleanup_safety(
            "no-such-id", repo=self._repo(), tracking_path=tmp_path)
        assert result.cleanable is False
        assert "not found" in result.reason

    def test_forced_dirty_worktree_is_still_reaped(self, tmp_path, monkeypatch):
        # --force bypasses the disposition entirely (dirty/WIP/claims/etc.).
        self._no_sessions(monkeypatch)
        wt = tmp_path / "wt1"
        wt.mkdir()
        self._save(tmp_path, dataclasses.replace(
            _rec("active"), worktree_id="wt1", worktree_path=str(wt)))
        fresh = _info(git_ops.WorktreeState.DIRTY, dirty=3)
        monkeypatch.setattr(git_ops, "classify_worktree", lambda *a, **k: fresh)
        reaped = []
        result = m._revalidate_cleanup_safety(
            "wt1", repo=self._repo(), tracking_path=tmp_path, force=True,
            reap=lambda r, i: (reaped.append(r.worktree_id), (0, []))[1])
        assert result.cleanable is True and result.bucket == "forced"
        assert reaped == ["wt1"]

    def test_forced_active_session_still_rejected(self, tmp_path, monkeypatch):
        # The one check --force never bypasses: a live/hosted session.
        wt = tmp_path / "wt1"
        wt.mkdir()
        (wt / ".git").mkdir()
        self._save(tmp_path, dataclasses.replace(
            _rec("active"), worktree_id="wt1", worktree_path=str(wt)))
        monkeypatch.setattr(
            m, "_build_active_paths",
            lambda records, ctx=None: {m._normalize_path(str(wt))})
        monkeypatch.setattr(
            m.sessions, "scan_sessions_fast",
            lambda records: m.sessions.SessionContext())
        monkeypatch.setattr(m, "_hosted_session_blocks_cleanup", lambda rec: False)
        result = m._revalidate_cleanup_safety(
            "wt1", repo=self._repo(), tracking_path=tmp_path, force=True)
        assert result.cleanable is False and result.bucket == "active"

    def test_forced_attach_after_scan_rejected_not_stale_snapshot(
            self, tmp_path, monkeypatch):
        # --force must refresh liveness fresh under the lock, not trust a
        # stale pre-lock active_paths snapshot -- simulated here by the
        # active_paths builder reporting active on THIS (post-scan) call.
        wt = tmp_path / "wt1"
        wt.mkdir()
        (wt / ".git").mkdir()
        self._save(tmp_path, dataclasses.replace(
            _rec("active"), worktree_id="wt1", worktree_path=str(wt)))
        monkeypatch.setattr(
            m, "_build_active_paths",
            lambda records, ctx=None: {m._normalize_path(str(wt))})
        monkeypatch.setattr(
            m.sessions, "scan_sessions_fast",
            lambda records: m.sessions.SessionContext())
        monkeypatch.setattr(m, "_hosted_session_blocks_cleanup", lambda rec: False)
        reaped = []
        result = m._revalidate_cleanup_safety(
            "wt1", repo=self._repo(), tracking_path=tmp_path, force=True,
            reap=lambda r, i: (reaped.append(r.worktree_id), (0, []))[1])
        assert result.cleanable is False and result.bucket == "active"
        assert reaped == []

    def test_hosted_session_blocks_cleanup_even_non_forced(
            self, tmp_path, monkeypatch):
        wt = tmp_path / "wt1"
        wt.mkdir()
        self._save(tmp_path, dataclasses.replace(
            _rec("finalized"), worktree_id="wt1", worktree_path=str(wt)))
        monkeypatch.setattr(
            m, "_build_active_paths", lambda records, ctx=None: set())
        monkeypatch.setattr(
            m.sessions, "scan_sessions_fast",
            lambda records: m.sessions.SessionContext())
        monkeypatch.setattr(m, "_hosted_session_blocks_cleanup", lambda rec: True)
        result = m._revalidate_cleanup_safety(
            "wt1", repo=self._repo(), tracking_path=tmp_path)
        assert result.cleanable is False and result.bucket == "active"
        assert "hosted" in result.reason

    def test_lock_contention_fails_closed(self, tmp_path, monkeypatch):
        wt = tmp_path / "wt1"
        wt.mkdir()
        self._save(tmp_path, dataclasses.replace(
            _rec("finalized"), worktree_id="wt1", worktree_path=str(wt)))

        class _AlwaysContended:
            def __init__(self, *a, **k):
                pass

            def __enter__(self):
                raise TimeoutError("contended")

            def __exit__(self, *exc):
                return False

        monkeypatch.setattr(tracking, "_RecordLock", _AlwaysContended)
        result = m._revalidate_cleanup_safety(
            "wt1", repo=self._repo(), tracking_path=tmp_path)
        assert result.cleanable is False and result.bucket == "locked"

    def test_reap_callback_receives_fresh_record_and_info_not_stale(
            self, tmp_path, monkeypatch):
        # The delete must act on the revalidated record/info, not whatever a
        # caller happened to pass around pre-lock.
        self._no_sessions(monkeypatch)
        wt = tmp_path / "wt1"
        wt.mkdir()
        self._save(tmp_path, dataclasses.replace(
            _rec("finalized"), worktree_id="wt1", worktree_path=str(wt),
            branch="worktree/wt1"))
        fresh = _info(git_ops.WorktreeState.UNUSED)
        monkeypatch.setattr(git_ops, "classify_worktree", lambda *a, **k: fresh)
        seen = {}

        def _reap(rec, info):
            seen["rec"] = rec
            seen["info"] = info
            return (0, [])

        result = m._revalidate_cleanup_safety(
            "wt1", repo=self._repo(), tracking_path=tmp_path, reap=_reap)
        assert result.cleanable is True
        assert seen["info"].state == git_ops.WorktreeState.COMPLETED
        assert seen["rec"].worktree_id == "wt1"
