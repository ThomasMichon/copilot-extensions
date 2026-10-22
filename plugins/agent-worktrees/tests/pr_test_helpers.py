"""Shared real-Git helper for the PR workflow tests."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from agent_worktrees import git_ops


def _git(*args: str, cwd: Path) -> str:
    return git_ops.git(*args, cwd=str(cwd)).stdout.strip()


def _stub_remote_target(monkeypatch, remote, head_repo, head_identity=None):
    """Label a real local transport as the scenario's fictitious provider repository."""
    from agent_worktrees import pr_publish

    original = pr_publish._remote_snapshot

    def snapshot(name, *, cwd):
        fetch, push, target = original(name, cwd=cwd)
        if (remote is None or name == remote) and target.head_repo:
            target = replace(
                target, head_repo=head_repo,
                head_identity=target.head_identity if head_identity is None else head_identity,
            )
        return fetch, push, target

    monkeypatch.setattr(pr_publish, "_remote_snapshot", snapshot)
