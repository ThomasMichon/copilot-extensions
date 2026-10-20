"""Shared real-Git helper for the PR workflow tests."""

from __future__ import annotations

from pathlib import Path

from agent_worktrees import git_ops


def _git(*args: str, cwd: Path) -> str:
    return git_ops.git(*args, cwd=str(cwd)).stdout.strip()
