"""Unchanged ordinary POSIX classification while its deadline work stays open."""

from __future__ import annotations

from pathlib import Path
import subprocess


def classify(go, worktree_path, branch, *, fetch, remote, default_branch, active_paths):
    path = Path(worktree_path)
    if not path.exists() or not (path / ".git").exists():
        return go.WorktreeStateInfo(state=go.WorktreeState.GONE)
    actual = go._get_current_branch_safe(path)
    drift = bool(actual and actual != branch)
    norm = worktree_path.rstrip("/\\")
    if active_paths is not None and norm in active_paths:
        return go.WorktreeStateInfo(
            state=go.WorktreeState.ACTIVE, current_branch=actual, branch_drift=drift,
        )
    try:
        return go._classify_git_state(
            path, actual if drift else branch, f"{remote}/{default_branch}",
            fetch=fetch, remote=remote, default_branch=default_branch,
            actual_branch=actual, drift=drift,
        )
    except subprocess.TimeoutExpired:
        go.log.warning("classify_worktree: git timed out for %s -- reporting UNKNOWN", path)
        return go.WorktreeStateInfo(
            state=go.WorktreeState.UNKNOWN, current_branch=actual, branch_drift=drift,
            fetch_requested=fetch, fetch_failed=fetch,
        )
