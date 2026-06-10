"""Git subprocess helper and worktree state classification.

All git operations go through :func:`git` which provides consistent
error handling, explicit cwd, and machine-readable output.
"""

from __future__ import annotations

import os
import platform
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

# --- Path helpers -----------------------------------------------------------

def _normalize_wt_path(p: str) -> str:
    """Normalize a worktree path for comparison (case-insensitive on Windows)."""
    p = str(Path(p).resolve()).rstrip("/\\")
    if platform.system() == "Windows":
        return p.lower()
    return p


def is_cwd_inside(worktree_path: str) -> bool:
    """Return True if the current working directory is inside *worktree_path*."""
    cwd = _normalize_wt_path(os.getcwd())
    wt = _normalize_wt_path(worktree_path)
    return cwd == wt or cwd.startswith(wt + os.sep)


def resolve_to_anchor(repo_path: Path) -> Path:
    """Resolve a git worktree path back to its anchor (main checkout).

    If *repo_path* is already the anchor (has a ``.git`` directory), it is
    returned unchanged.  If it is a worktree (has a ``.git`` file),
    ``git rev-parse --git-common-dir`` is used to find the shared ``.git``
    directory whose parent is the anchor repo root.
    """
    git_path = repo_path / ".git"
    if git_path.is_dir():
        return repo_path  # already the anchor
    if git_path.is_file():
        try:
            r = subprocess.run(
                ["git", "-C", str(repo_path), "rev-parse", "--git-common-dir"],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0:
                common = Path(r.stdout.strip())
                if not common.is_absolute():
                    common = (repo_path / common).resolve()
                # common is the shared .git dir; its parent is the anchor
                anchor = common.parent
                if (anchor / ".git").is_dir():
                    return anchor
        except Exception:
            pass
    return repo_path


class GitError(Exception):
    """A git command failed."""

    def __init__(self, cmd: list[str], returncode: int, stderr: str) -> None:
        self.cmd = cmd
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(f"git {' '.join(cmd[1:])} failed (rc={returncode}): {stderr}")


def git(
    *args: str,
    cwd: str | Path | None = None,
    check: bool = True,
    capture: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run a git command with consistent error handling.

    Args:
        *args: Git subcommand and arguments (without 'git' prefix).
        cwd: Working directory for the command.
        check: If True, raise GitError on non-zero exit.
        capture: If True, capture stdout and stderr.

    Returns:
        CompletedProcess with stdout/stderr as strings.
    """
    cmd = ["git", *args]
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    result = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=capture,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    if check and result.returncode != 0:
        raise GitError(cmd, result.returncode, result.stderr.strip())
    return result


class WorktreeState(str, Enum):
    """Git-level worktree state classification."""

    ACTIVE = "active"
    WIP = "wip"
    DIRTY = "dirty"
    UNUSED = "unused"
    COMPLETED = "completed"
    GONE = "gone"
    ORPHAN = "orphan"
    UNKNOWN = "unknown"


@dataclass
class WorktreeStateInfo:
    """Result of classifying a worktree's git state."""

    state: WorktreeState
    ahead: int = 0
    behind: int = 0
    dirty: int = 0
    title: str = ""
    current_branch: str | None = None
    """The worktree's actual HEAD branch (None if detached or unreadable)."""
    branch_drift: bool = False
    """True when the worktree's HEAD is on a different branch than tracked."""


def _get_current_branch_safe(cwd: str | Path) -> str | None:
    """Return the worktree's current branch, or None if detached/unreadable."""
    result = git("rev-parse", "--abbrev-ref", "HEAD", cwd=cwd, check=False)
    if result.returncode != 0:
        return None
    name = result.stdout.strip()
    # "HEAD" means detached -- not a named branch
    return None if name == "HEAD" else name


def classify_worktree(
    worktree_path: str,
    branch: str,
    *,
    fetch: bool = False,
    remote: str = "origin",
    default_branch: str = "master",
    active_paths: set[str] | None = None,
) -> WorktreeStateInfo:
    """Classify a worktree's git state.

    Args:
        worktree_path: Filesystem path to the worktree.
        branch: The worktree's branch name (e.g., worktree/id).
        fetch: If True, fetch before classification (slower but more accurate).
        remote: Remote name.
        default_branch: Default branch name.
        active_paths: Set of normalized worktree paths with live Copilot
            sessions.  When provided, any worktree whose path is in this
            set is classified as ACTIVE regardless of git state -- it must
            never appear as COMPLETED or UNUSED.

    Returns:
        WorktreeStateInfo with the classification result.
    """
    path = Path(worktree_path)
    if not path.exists():
        return WorktreeStateInfo(state=WorktreeState.GONE)

    # A directory without a .git entry (file or dir) is a zombie -- the
    # worktree was partially removed or never fully created.
    if not (path / ".git").exists():
        return WorktreeStateInfo(state=WorktreeState.GONE)

    # Detect branch drift: if the worktree's HEAD is on a different branch
    # than what the tracking record says, use the actual HEAD for
    # classification so unmerged work on feature branches isn't hidden.
    actual_branch = _get_current_branch_safe(path)
    drift = bool(
        actual_branch
        and actual_branch != branch
    )
    effective_branch = actual_branch if drift else branch

    # If a live Copilot session owns this worktree, it is ACTIVE -- period.
    # active_paths stores paths stripped of trailing separators (but NOT
    # lowercased).  Use case-insensitive lookup on Windows to match.
    if active_paths is not None:
        norm = worktree_path.rstrip("/\\")
        _casefold = platform.system() == "Windows"
        if any(
            norm == ap if not _casefold else norm.lower() == ap.lower()
            for ap in active_paths
        ):
            return WorktreeStateInfo(
                state=WorktreeState.ACTIVE,
                current_branch=actual_branch,
                branch_drift=drift,
            )

    upstream = f"{remote}/{default_branch}"

    if fetch:
        git("fetch", remote, default_branch, "--quiet", cwd=path, check=False)

    # Dirty check
    result = git("status", "--porcelain", cwd=path, check=False)
    dirty_lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
    dirty_count = len(dirty_lines)

    # Merge base -- use effective_branch (actual HEAD when drifted)
    mb = git("merge-base", upstream, effective_branch, cwd=path, check=False)
    if mb.returncode != 0:
        return WorktreeStateInfo(
            state=WorktreeState.ORPHAN, dirty=dirty_count,
            current_branch=actual_branch, branch_drift=drift,
        )
    merge_base = mb.stdout.strip()

    # Ahead / behind
    ahead_r = git(
        "rev-list", "--count", f"{merge_base}..{effective_branch}",
        cwd=path, check=False,
    )
    behind_r = git(
        "rev-list", "--count", f"{effective_branch}..{upstream}",
        cwd=path, check=False,
    )
    ahead = int(ahead_r.stdout.strip()) if ahead_r.returncode == 0 else 0
    behind = int(behind_r.stdout.strip()) if behind_r.returncode == 0 else 0

    # Last commit subject as fallback title
    title = ""
    if ahead > 0:
        title_r = git(
            "--no-pager", "log", "-1", "--format=%s", effective_branch,
            cwd=path, check=False,
        )
        if title_r.returncode == 0:
            title = title_r.stdout.strip()
            if len(title) > 60:
                title = title[:57] + "..."

    _drift_fields = dict(current_branch=actual_branch, branch_drift=drift)

    if dirty_count > 0:
        return WorktreeStateInfo(
            state=WorktreeState.DIRTY,
            ahead=ahead, behind=behind, dirty=dirty_count, title=title,
            **_drift_fields,
        )

    if ahead == 0:
        # Check reflog for past commits (squash-merged back)
        reflog = git(
            "--no-pager", "reflog", "show", effective_branch, "--format=%gs",
            cwd=path, check=False,
        )
        has_commits = any(
            ln.startswith("commit")
            for ln in (reflog.stdout or "").splitlines()
        )
        state = WorktreeState.COMPLETED if has_commits else WorktreeState.UNUSED
        return WorktreeStateInfo(
            state=state, ahead=0, behind=behind, title="",
            **_drift_fields,
        )

    # Branch has commits -- check if changes already in master (squash-merged).
    #
    # Use `git cherry` for patch-id comparison: it detects equivalent
    # patches even when commit SHAs differ (squash-merge) and even when
    # upstream later modified the same files.  Lines starting with "-"
    # are already on upstream; "+" means unmerged.
    cherry_r = git(
        "cherry", upstream, effective_branch,
        cwd=path, check=False,
    )
    if cherry_r.returncode == 0 and cherry_r.stdout.strip():
        unmerged = [ln for ln in cherry_r.stdout.splitlines()
                    if ln.startswith("+")]
        if not unmerged:
            return WorktreeStateInfo(
                state=WorktreeState.COMPLETED,
                ahead=ahead, behind=behind, title=title,
                **_drift_fields,
            )

    # Fallback: direct blob comparison for cases git-cherry can't match
    # (e.g. content arrived on upstream via a different patch shape).
    diff_r = git(
        "diff", "--name-only", merge_base, effective_branch,
        cwd=path, check=False,
    )
    changed_files = [f for f in diff_r.stdout.splitlines() if f.strip()]

    if not changed_files:
        return WorktreeStateInfo(
            state=WorktreeState.UNUSED,
            ahead=ahead, behind=behind, title=title,
            **_drift_fields,
        )

    # Compare file blobs between branch and master
    for file in changed_files:
        b_blob = git(
            "rev-parse", f"{effective_branch}:{file}", cwd=path, check=False
        )
        m_blob = git(
            "rev-parse", f"{upstream}:{file}", cwd=path, check=False
        )
        if b_blob.stdout.strip() != m_blob.stdout.strip():
            return WorktreeStateInfo(
                state=WorktreeState.WIP,
                ahead=ahead, behind=behind, title=title,
                **_drift_fields,
            )

    return WorktreeStateInfo(
        state=WorktreeState.COMPLETED,
        ahead=ahead, behind=behind, title=title,
        **_drift_fields,
    )


# --- High-level git operations for finalization ---


def fetch(remote: str, *, cwd: str | Path) -> None:
    """Fetch from a remote."""
    git("fetch", remote, "--quiet", cwd=cwd)


def rebase(onto: str, *, cwd: str | Path) -> bool:
    """Rebase the current branch onto a ref. Returns True on success."""
    result = git("rebase", onto, cwd=cwd, check=False)
    if result.returncode != 0:
        git("rebase", "--abort", cwd=cwd, check=False)
        return False
    return True


def checkout(branch: str, *, cwd: str | Path) -> None:
    """Checkout a branch."""
    git("checkout", branch, "--quiet", cwd=cwd)


def merge_ff(branch: str, *, cwd: str | Path) -> bool:
    """Fast-forward merge. Returns True on success."""
    result = git("merge", branch, "--ff-only", "--quiet", cwd=cwd, check=False)
    return result.returncode == 0


def merge_squash(branch: str, worktree_id: str, *, cwd: str | Path) -> bool:
    """Squash merge with auto-commit. Returns True on success."""
    result = git("merge", branch, "--squash", "--quiet", cwd=cwd, check=False)
    if result.returncode != 0:
        return False
    commit_r = git(
        "commit", "--no-edit", "-m", f"squash: merge worktree/{worktree_id}",
        cwd=cwd, check=False,
    )
    return commit_r.returncode == 0


def push(remote: str, branch: str, *, cwd: str | Path) -> bool:
    """Push a branch to remote. Returns True on success."""
    result = git("push", remote, branch, "--quiet", cwd=cwd, check=False)
    return result.returncode == 0


def ref_exists(ref: str, *, cwd: str | Path) -> bool:
    """Return True if a git ref/commit resolves in the repo."""
    result = git(
        "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}",
        cwd=cwd, check=False,
    )
    return result.returncode == 0


def resolve_start_point(
    remote: str, default_branch: str, *, cwd: str | Path
) -> str:
    """Pick the best start point for a new worktree branch.

    Prefers ``<remote>/<default_branch>`` (normal case), then a local
    ``<default_branch>``, then ``HEAD`` -- so a repo with no remote (or no
    fetched default branch) still works instead of failing with
    ``fatal: invalid reference: <remote>/<default_branch>``.
    """
    upstream = f"{remote}/{default_branch}"
    if ref_exists(upstream, cwd=cwd):
        return upstream
    if ref_exists(default_branch, cwd=cwd):
        return default_branch
    return "HEAD"


def create_worktree(
    anchor: str | Path,
    worktree_path: str,
    branch: str,
    start_point: str,
) -> None:
    """Create a new git worktree on a new branch."""
    git(
        "worktree", "add", worktree_path, "-b", branch, start_point, "--quiet",
        cwd=anchor,
    )


def remove_worktree(anchor: str | Path, worktree_path: str) -> bool:
    """Remove a worktree. Returns True on success."""
    result = git(
        "worktree", "remove", worktree_path, "--force",
        cwd=anchor, check=False,
    )
    return result.returncode == 0


def delete_branch(name: str, *, cwd: str | Path, force: bool = False) -> bool:
    """Delete a local branch. Returns True on success."""
    flag = "-D" if force else "-d"
    result = git("branch", flag, name, cwd=cwd, check=False)
    return result.returncode == 0


def get_dirty_files(cwd: str | Path) -> list[str]:
    """Return list of uncommitted changes (porcelain format)."""
    result = git("status", "--porcelain", cwd=cwd, check=False)
    return [ln for ln in result.stdout.splitlines() if ln.strip()]


def get_commits_ahead(
    branch: str, upstream: str, *, cwd: str | Path
) -> list[str]:
    """Return one-line commit log of branch commits not in upstream."""
    result = git(
        "log", "--oneline", f"{upstream}..{branch}",
        cwd=cwd, check=False,
    )
    return [ln for ln in result.stdout.splitlines() if ln.strip()]


def is_clean(*, cwd: str | Path) -> bool:
    """Return True if the working tree has no uncommitted changes."""
    result = git("status", "--porcelain", cwd=cwd, check=False)
    return result.returncode == 0 and not result.stdout.strip()


def squash_branch(upstream: str, message: str, *, cwd: str | Path) -> bool:
    """Squash all commits ahead of *upstream* into one on the current branch.

    Uses soft reset to merge-base, then re-commits.  A backup ref is
    created before the reset and restored on failure.

    Returns True on success (including the no-op case of 0-1 commits).
    """
    mb = git("merge-base", upstream, "HEAD", cwd=cwd, check=False)
    if mb.returncode != 0:
        return False
    merge_base = mb.stdout.strip()

    count_r = git("rev-list", "--count", f"{merge_base}..HEAD", cwd=cwd, check=False)
    if count_r.returncode != 0:
        return False
    count = int(count_r.stdout.strip())
    if count <= 1:
        return True  # nothing to squash

    # Save backup ref for rollback
    orig_head = git("rev-parse", "HEAD", cwd=cwd, check=False).stdout.strip()
    git("update-ref", "refs/pre-squash-backup", orig_head, cwd=cwd, check=False)

    reset_r = git("reset", "--soft", merge_base, cwd=cwd, check=False)
    if reset_r.returncode != 0:
        git("reset", "--hard", orig_head, cwd=cwd, check=False)
        git("update-ref", "-d", "refs/pre-squash-backup", cwd=cwd, check=False)
        return False

    commit_r = git("commit", "-m", message, cwd=cwd, check=False)
    if commit_r.returncode != 0:
        git("reset", "--hard", orig_head, cwd=cwd, check=False)
        git("update-ref", "-d", "refs/pre-squash-backup", cwd=cwd, check=False)
        return False

    return True


def delete_backup_ref(*, cwd: str | Path) -> None:
    """Remove the pre-squash backup ref after successful finalization."""
    git("update-ref", "-d", "refs/pre-squash-backup", cwd=cwd, check=False)


def restore_backup_ref(*, cwd: str | Path) -> bool:
    """Restore the worktree branch from the pre-squash backup ref.

    Returns True if restoration succeeded, False if no backup exists.
    """
    ref = git("rev-parse", "refs/pre-squash-backup", cwd=cwd, check=False)
    if ref.returncode != 0:
        return False
    git("reset", "--hard", ref.stdout.strip(), cwd=cwd, check=False)
    git("update-ref", "-d", "refs/pre-squash-backup", cwd=cwd, check=False)
    return True


def get_current_branch(cwd: str | Path) -> str:
    """Return the current branch name."""
    result = git("rev-parse", "--abbrev-ref", "HEAD", cwd=cwd)
    return result.stdout.strip()


def prune_worktrees(*, cwd: str | Path) -> None:
    """Prune stale worktree entries."""
    git("worktree", "prune", cwd=cwd, check=False)


def is_branch_merged(
    branch: str,
    target: str,
    *,
    cwd: str | Path,
) -> bool:
    """Check if all commits on *branch* are reachable from *target*.

    Compares tree content (blob-level), not commit identity, so this
    returns True for squash-merged branches too.  Falls back to
    commit-ancestry check when tree comparison isn't possible.
    """
    # Fast path: branch ref doesn't exist locally
    ref_check = git("rev-parse", "--verify", branch, cwd=cwd, check=False)
    if ref_check.returncode != 0:
        return True  # branch already gone -- nothing to protect

    # Check commit ancestry first
    result = git(
        "merge-base", "--is-ancestor", branch, target,
        cwd=cwd, check=False,
    )
    if result.returncode == 0:
        return True

    # Patch-id comparison via `git cherry` -- detects equivalent patches
    # even when commit SHAs differ (squash-merge) and even when the
    # target later modified the same files.
    cherry_r = git("cherry", target, branch, cwd=cwd, check=False)
    if cherry_r.returncode == 0 and cherry_r.stdout.strip():
        unmerged = [ln for ln in cherry_r.stdout.splitlines()
                    if ln.startswith("+")]
        if not unmerged:
            return True

    # Fallback: direct blob comparison for cases git-cherry can't match.
    diff_r = git("diff", "--name-only", branch, target, cwd=cwd, check=False)
    if diff_r.returncode != 0:
        return False  # can't determine -- assume not merged
    changed = [f for f in diff_r.stdout.splitlines() if f.strip()]
    if not changed:
        return True  # identical trees

    # Compare blobs for files the branch changed vs their state on target
    mb = git("merge-base", target, branch, cwd=cwd, check=False)
    if mb.returncode != 0:
        return False
    merge_base = mb.stdout.strip()

    branch_diff = git(
        "diff", "--name-only", merge_base, branch, cwd=cwd, check=False,
    )
    branch_files = [f for f in branch_diff.stdout.splitlines() if f.strip()]
    for file in branch_files:
        b_blob = git("rev-parse", f"{branch}:{file}", cwd=cwd, check=False)
        t_blob = git("rev-parse", f"{target}:{file}", cwd=cwd, check=False)
        if b_blob.stdout.strip() != t_blob.stdout.strip():
            return False

    return True
