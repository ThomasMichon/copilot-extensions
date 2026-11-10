"""Strict local-default containment for finalizing remote-free repositories."""

from __future__ import annotations

import re
from pathlib import Path

from . import git_ops


def _remote_free(anchor: str) -> bool:
    result = git_ops.git("remote", cwd=anchor, check=False)
    if result.returncode != 0:
        raise ValueError("Cannot inspect Git remotes; local-only finalization is refused.")
    return not result.stdout.strip()


def resolve_upstream(repo, *, pr_mode: bool) -> tuple[str, bool]:
    """Select a local default only for a positively remote-free, non-PR repo."""
    if not _remote_free(repo.anchor):
        return f"{repo.remote}/{repo.default_branch}", False
    if pr_mode or repo.pr.enabled or repo.pr.required:
        raise ValueError("PR-mode finalization requires its configured remote; no local fallback.")
    return f"refs/heads/{repo.default_branch}", True


def content_error(repo, worktree_path: str, branch: str, upstream: str) -> str | None:
    """Recheck actual clean checkout containment without moving any reference."""
    try:
        if not _remote_free(repo.anchor):
            return "Git remotes changed during local-only finalization; retry normally."
    except ValueError as exc:
        return str(exc)
    exists = Path(worktree_path).exists()
    cwd = worktree_path if exists else repo.anchor
    if exists and not git_ops.is_clean(cwd=cwd):
        return "Local-only finalization requires a clean worktree; preserve and resolve its changes."
    current = "HEAD" if exists else f"refs/heads/{branch}"
    result = git_ops.git(
        "rev-parse", "--revs-only", "--end-of-options",
        f"{current}^{{commit}}", f"{upstream}^{{commit}}", cwd=cwd, check=False,
    )
    commits = result.stdout.splitlines()
    if result.returncode != 0 or len(commits) != 2 or not all(
        re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit) for commit in commits
    ):
        return "Cannot verify checkout and declared local default branch commits; finalization refused."
    contained = git_ops.git(
        "merge-base", "--is-ancestor", *commits, cwd=cwd, check=False,
    )
    if contained.returncode != 0:
        return f"Unmerged local work: checkout is not contained in {upstream}; finalization refused."
    return None
