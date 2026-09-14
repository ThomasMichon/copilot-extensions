"""Tests for the finalize freeze-rollback safety net (worktree-finality-and-
obligations, Phase 2): a failure after `record.status` is frozen to
`finalizing` must not wedge the record there permanently."""

from __future__ import annotations

from pathlib import Path

from agent_worktrees import finalize, tracking


def _rec(tmp_path: Path, *, status: str) -> Path:
    path = tmp_path / "wt-1.yaml"
    rec = tracking.create_new_record(
        "wt-1", "worktree/wt-1", str(tmp_path / "wt-1"), "owner/repo",
        "m", "wsl", tmp_path,
    )
    rec.status = status
    tracking.save_record(rec, path)
    return path


def test_rollback_restores_prior_status(tmp_path):
    path = _rec(tmp_path, status="finalizing")
    finalize._rollback_finalizing_freeze(path, "pushed", "wt-1")
    assert tracking.load_record(path).status == "pushed"


def test_rollback_noop_without_prior_status(tmp_path):
    path = _rec(tmp_path, status="finalizing")
    finalize._rollback_finalizing_freeze(path, None, "wt-1")
    assert tracking.load_record(path).status == "finalizing"


def test_rollback_noop_when_no_longer_finalizing(tmp_path):
    # A concurrent process already resolved it (e.g. to `finalized`) --
    # the rollback must not clobber that outcome.
    path = _rec(tmp_path, status="finalized")
    finalize._rollback_finalizing_freeze(path, "pushed", "wt-1")
    assert tracking.load_record(path).status == "finalized"


def test_rollback_noop_when_record_missing(tmp_path):
    missing = tmp_path / "does-not-exist.yaml"
    # Must not raise.
    finalize._rollback_finalizing_freeze(missing, "active", "wt-1")
    assert not missing.exists()
