"""Worktree status-core write behavior."""

from __future__ import annotations

import argparse

import pytest

from agent_worktrees import __main__ as main
from agent_worktrees import tracking


@pytest.fixture
def status_env(tmp_path, tmp_tracking_dir, monkeypatch):
    record = tracking.WorktreeRecord(
        worktree_id="wt-status",
        branch="worktree/wt-status",
        worktree_path=str(tmp_path),
        repo="example",
        machine="machine",
        platform="wsl",
        started_at="2026-08-28T00:00:00",
        last_resumed_at="2026-08-28T00:00:00",
        resume_count=0,
        title=None,
        status="finalized",
        completed_at="2026-08-28T01:00:00",
        sessions=[],
    )
    path = tmp_tracking_dir / f"{record.worktree_id}.yaml"
    tracking.save_record(record, path)
    monkeypatch.setattr(main.cfg, "load_config", lambda: object())
    monkeypatch.setattr(main.cfg, "tracking_dir", lambda: tmp_tracking_dir)
    monkeypatch.setattr(
        main,
        "_infer_worktree_id",
        lambda _worktree_id, _config=None: record.worktree_id,
    )
    monkeypatch.setattr(main, "_resolve_worktree_id", lambda worktree_id: worktree_id)
    return path


def test_follow_up_reactivates_finalized_worktree(status_env):
    args = argparse.Namespace(worktree_id=None)

    assert main._cmd_status_write(
        args,
        summary="Begin the next change",
        follow_up=True,
    ) == 0

    record = tracking.load_record(status_env)
    assert record.status == "active"
    assert record.completed_at is None
    assert record.follow_up is True
    assert record.summary == "Begin the next change"


def test_summary_only_preserves_finalized_status(status_env):
    args = argparse.Namespace(worktree_id=None)

    assert main._cmd_status_write(
        args,
        summary="Keep the existing disposition",
        follow_up=None,
    ) == 0

    record = tracking.load_record(status_env)
    assert record.status == "finalized"
    assert record.completed_at == "2026-08-28T01:00:00"
