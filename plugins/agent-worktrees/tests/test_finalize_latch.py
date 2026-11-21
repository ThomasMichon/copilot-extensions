"""Finalize is a latch: continued conversation never reopens a finalized record."""

from __future__ import annotations

import types
from pathlib import Path

import pytest

import agent_worktrees.__main__ as m
from agent_worktrees import git_ops, tracking, tracking_claims


def _finalized(path: str = "/tmp/wt1") -> tracking.WorktreeRecord:
    return tracking.WorktreeRecord(
        worktree_id="wt1", branch="worktree/wt1", worktree_path=path, repo="owner/repo",
        machine="m", platform="wsl", started_at="2026-06-01T10:00:00",
        last_resumed_at="2026-06-01T10:00:00", resume_count=0, title=None,
        status="finalized", completed_at=None, sessions=[], prs=[], kind="session",
    )


def _info(state: git_ops.WorktreeState, *, ahead: int = 0) -> git_ops.WorktreeStateInfo:
    return git_ops.WorktreeStateInfo(state=state, ahead=ahead, behind=0, dirty=0)


def _latched(monkeypatch: pytest.MonkeyPatch, head: str) -> tracking.WorktreeRecord:
    rec = _finalized()
    rec.finalized_checkout = {"head": "a" * 40, "branch": "worktree/wt1"}
    monkeypatch.setattr(
        git_ops, "git", lambda *a, **k: types.SimpleNamespace(stdout=head + "\n"))
    return rec


def test_session_self_claim_does_not_reopen_finalized_owner(tmp_path: Path) -> None:
    rec = _finalized(str(tmp_path / "wt-A"))
    tracking_claims.add_resource_claim(
        rec, tracking_claims.ResourceClaim(kind="session", ref="host/repo/wt-A#sess-1"),
        save=False,
    )
    assert rec.status == "finalized"


def test_finalized_head_moved_past_latch_is_not_masked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rec = _latched(monkeypatch, "b" * 40)
    out = m._apply_tracking_override(rec, _info(git_ops.WorktreeState.WIP, ahead=2))
    assert out.state == git_ops.WorktreeState.WIP


def test_finalized_head_at_latch_or_landed_stays_completed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rec = _latched(monkeypatch, "a" * 40)
    out = m._apply_tracking_override(rec, _info(git_ops.WorktreeState.WIP, ahead=2))
    assert out.state == git_ops.WorktreeState.COMPLETED
    rec = _latched(monkeypatch, "b" * 40)  # fast-forwarded to landed content
    out = m._apply_tracking_override(rec, _info(git_ops.WorktreeState.UNUSED))
    assert out.state == git_ops.WorktreeState.COMPLETED


def test_legacy_finalized_without_latch_stays_completed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(git_ops, "git", lambda *a, **k: pytest.fail("no probe"))
    out = m._apply_tracking_override(
        _finalized(), _info(git_ops.WorktreeState.WIP, ahead=2))
    assert out.state == git_ops.WorktreeState.COMPLETED
