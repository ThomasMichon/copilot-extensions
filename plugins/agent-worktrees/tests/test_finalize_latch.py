"""Finalize is a latch: continued conversation never reopens a finalized record."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import NoReturn

import pytest

import agent_worktrees.__main__ as m
from agent_worktrees import git_ops, tracking, tracking_claims, worktree_git_facts


def _finalized(path: str = "/tmp/wt1") -> tracking.WorktreeRecord:
    return tracking.WorktreeRecord(
        worktree_id="wt1", branch="worktree/wt1", worktree_path=path, repo="owner/repo",
        machine="m", platform="wsl", started_at="2026-06-01T10:00:00",
        last_resumed_at="2026-06-01T10:00:00", resume_count=0, title=None,
        status="finalized", completed_at=None, sessions=[], prs=[], kind="session",
    )


def _info(state: git_ops.WorktreeState, *, ahead: int = 0) -> git_ops.WorktreeStateInfo:
    return git_ops.WorktreeStateInfo(state=state, ahead=ahead, behind=0, dirty=0)


def _latched(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, head: str,
) -> tracking.WorktreeRecord:
    rec = _finalized(str(tmp_path))
    rec.finalized_checkout = {"head": "a" * 40, "branch": "worktree/wt1"}
    (tmp_path / ".git").mkdir(exist_ok=True)
    (tmp_path / ".git" / "HEAD").write_text(head + "\n", encoding="utf-8")

    def _no_child(*args: object, **kwargs: object) -> NoReturn:
        pytest.fail("finalize latch probe must not start a Git child")

    monkeypatch.setattr(git_ops, "git", _no_child)
    monkeypatch.setattr(subprocess, "Popen", _no_child)
    return rec


def test_session_self_claim_does_not_reopen_finalized_owner(tmp_path: Path) -> None:
    rec = _finalized(str(tmp_path / "wt-A"))
    tracking_claims.add_resource_claim(
        rec, tracking_claims.ResourceClaim(kind="session", ref="host/repo/wt-A#sess-1"),
        save=False,
    )
    assert rec.status == "finalized"


def test_finalized_head_moved_past_latch_is_not_masked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    rec = _latched(monkeypatch, tmp_path, "b" * 40)
    out = m._apply_tracking_override(rec, _info(git_ops.WorktreeState.WIP, ahead=2))
    assert out.state == git_ops.WorktreeState.WIP


def test_finalized_head_at_latch_or_landed_stays_completed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    rec = _latched(monkeypatch, tmp_path, "a" * 40)
    out = m._apply_tracking_override(rec, _info(git_ops.WorktreeState.WIP, ahead=2))
    assert out.state == git_ops.WorktreeState.COMPLETED
    rec = _latched(monkeypatch, tmp_path, "b" * 40)  # fast-forwarded to landed content
    out = m._apply_tracking_override(rec, _info(git_ops.WorktreeState.UNUSED))
    assert out.state == git_ops.WorktreeState.COMPLETED


def test_legacy_finalized_without_latch_stays_completed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(git_ops, "git", lambda *a, **k: pytest.fail("no probe"))
    out = m._apply_tracking_override(
        _finalized(), _info(git_ops.WorktreeState.WIP, ahead=2))
    assert out.state == git_ops.WorktreeState.COMPLETED


@pytest.mark.parametrize("head", ["", "not-a-sha", None])
def test_unreadable_or_empty_head_fails_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, head: str | None,
) -> None:
    rec = _latched(monkeypatch, tmp_path, "")
    if head is None:
        (tmp_path / ".git" / "HEAD").unlink()
    else:
        (tmp_path / ".git" / "HEAD").write_text(head, encoding="utf-8")
    out = m._apply_tracking_override(rec, _info(git_ops.WorktreeState.WIP, ahead=2))
    assert out.state == git_ops.WorktreeState.WIP


def test_exhausted_observation_budget_starts_no_git_child(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    rec = _latched(monkeypatch, tmp_path, "a" * 40)
    monkeypatch.setattr(git_ops, "_windows_observation", lambda: True)
    monkeypatch.setattr(git_ops, "_observation_deadline", lambda *a: 0.0)
    out = worktree_git_facts.apply_tracking_override(
        rec, _info(git_ops.WorktreeState.WIP, ahead=2))
    assert out.state == git_ops.WorktreeState.COMPLETED


def test_linked_worktree_head_resolves_through_common_packed_refs(tmp_path: Path) -> None:
    common = tmp_path / "anchor.git"
    private = common / "worktrees" / "wt1"
    private.mkdir(parents=True)
    (private / "HEAD").write_text("ref: refs/heads/worktree/wt1\n", encoding="utf-8")
    (private / "commondir").write_text("../..\n", encoding="utf-8")
    (common / "packed-refs").write_text(
        "# pack-refs\n" + "c" * 40 + " refs/heads/worktree/wt1\n", encoding="utf-8")
    checkout = tmp_path / "wt1"
    checkout.mkdir()
    (checkout / ".git").write_text(f"gitdir: {private}\n", encoding="utf-8")
    assert worktree_git_facts._read_head(str(checkout)) == "c" * 40
    loose = common / "refs" / "heads" / "worktree"
    loose.mkdir(parents=True)
    (loose / "wt1").write_text("d" * 40 + "\n", encoding="utf-8")
    assert worktree_git_facts._read_head(str(checkout)) == "d" * 40
    (loose / "wt1").write_text("", encoding="utf-8")
    assert worktree_git_facts._read_head(str(checkout)) is None
