"""Strict local-default containment for finalizing remote-free repositories."""

from __future__ import annotations

import re
from pathlib import Path

from . import git_ops
from .git_repository_environment import repository_identity_scope


def _remote_free(anchor: str) -> bool:
    result = git_ops.git("remote", cwd=anchor, check=False)
    if result.returncode != 0:
        raise ValueError("Cannot inspect Git remotes; local-only finalization is refused.")
    return not result.stdout.strip()


@repository_identity_scope()
def resolve_upstream(repo, *, pr_mode: bool) -> tuple[str, bool]:
    """Select a local default only for a positively remote-free, non-PR repo."""
    if not _remote_free(repo.anchor):
        return f"{repo.remote}/{repo.default_branch}", False
    if pr_mode or repo.pr.enabled or repo.pr.required:
        raise ValueError("PR-mode finalization requires its configured remote; no local fallback.")
    return f"refs/heads/{repo.default_branch}", True


def _commit(ref: str, cwd: str) -> str | None:
    result = git_ops.git(
        "rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}", cwd=cwd, check=False,
    )
    value = result.stdout.strip()
    return value if result.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", value) else None


@repository_identity_scope()
def content_error(repo, worktree_path: str, branch: str, upstream: str) -> str | None:
    """Recheck actual clean checkout containment without moving any reference."""
    try:
        if not _remote_free(repo.anchor):
            return "Git remotes changed during local-only finalization; retry normally."
    except ValueError as exc:
        return str(exc)
    exists = Path(worktree_path).exists()
    cwd = worktree_path if exists else repo.anchor
    if exists:
        status = git_ops.git(
            "status", "--porcelain", "--untracked-files=all", "--ignore-submodules=none",
            cwd=cwd, check=False,
        )
        if status.returncode != 0 or status.stdout.strip():
            return "Local-only finalization requires verified clean content, including untracked files."
    current = "HEAD" if exists else f"refs/heads/{branch}"
    current_commit = _commit(current, cwd)
    default_commit = _commit(upstream, repo.anchor)
    if current_commit is None or default_commit is None:
        return "Cannot verify checkout and declared local default branch commits; finalization refused."
    contained = git_ops.git(
        "merge-base", "--is-ancestor", current_commit, default_commit, cwd=repo.anchor, check=False,
    )
    if contained.returncode != 0:
        return f"Unmerged local work: checkout is not contained in {upstream}; finalization refused."
    return None


@repository_identity_scope()
def cleanup_error(repo, worktree_path: str, branch: str) -> str | None:
    """Reprove surviving local content, allowing an already-removed branch."""
    upstream, local_only = resolve_upstream(repo, pr_mode=False)
    if not local_only:
        return None
    if not Path(worktree_path).exists():
        present = git_ops.git(
            "show-ref", "--verify", "--quiet", f"refs/heads/{branch}",
            cwd=repo.anchor, check=False,
        )
        if present.returncode == 1:
            return None
        if present.returncode != 0:
            return "Cannot inspect local worktree branch; cleanup refused."
    return content_error(repo, worktree_path, branch, upstream)
