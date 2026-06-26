"""Git-collaboration primitives -- the invariant-bearing operations that sit
below the high-level push-changes/create-pr/finalize lifecycle and above raw
git.  See the ``git-collaboration`` skill for the command surface and the
forbidden/wrapped/blessed-direct boundary.

So far:

- :func:`sync_forward` -- pull the worktree branch forward onto the updated
  remote default branch (build on top of a just-merged PR).

``feature-branch`` and ``merge-to-feature`` (shared-branch coordination) land in
follow-up changes.
"""

from __future__ import annotations

from pathlib import Path

from . import git_ops, output
from .config import Config


def sync_forward(worktree_id: str, config: Config, *, dry_run: bool = False) -> bool:
    """Rebase the worktree branch forward onto the updated remote default branch.

    Fetches the remote and rebases the *current* worktree branch onto
    ``<remote>/<default>``.  Commits that were **squash-merged** upstream are
    skipped by git as already-applied (so a just-merged PR's commits drop away),
    while genuinely-new local commits are preserved on top.  This is the
    "pull forward / build on top of the merged PR" primitive.

    Mid-flight only: it never finalizes, prunes, merges to the default branch, or
    pushes.  A dirty tree, a detached HEAD, a missing worktree/upstream, or a true
    rebase conflict stops it with a clear message rather than guessing -- on a
    conflict the rebase is auto-aborted so the branch is left untouched.

    Returns True on success (including already-up-to-date), False on any blocker.
    """
    repo = config.default_repo
    remote = repo.remote
    upstream = f"{remote}/{repo.default_branch}"
    worktree_path = str(Path(repo.worktree_root) / worktree_id)

    if not Path(worktree_path).exists():
        output.err(f"Worktree path not found: {worktree_path}")
        return False

    branch = git_ops._get_current_branch_safe(worktree_path)
    if branch is None:
        output.err("Worktree is in a detached HEAD state; checkout a branch first.")
        return False

    if not git_ops.is_clean(cwd=worktree_path):
        dirty = git_ops.get_dirty_files(cwd=worktree_path)
        listing = "\n  ".join(dirty[:20])
        output.err(
            "Worktree has uncommitted changes; commit or stash before syncing:\n  "
            + listing
        )
        return False

    if dry_run:
        output.dry_run(f"Would fetch from {remote}")
        output.dry_run(
            f"Would rebase {branch} onto {upstream} "
            "(squash-merged commits drop as already-applied)"
        )
        return True

    print(f"Fetching from {remote}...")
    try:
        git_ops.fetch(remote, cwd=worktree_path)
    except Exception as e:  # noqa: BLE001 -- surface any fetch failure to the agent
        output.err(f"Fetch from {remote} failed: {e}")
        return False

    if not git_ops.ref_exists(upstream, cwd=worktree_path):
        output.err(f"Upstream {upstream} not found after fetch.")
        return False

    behind = git_ops.git(
        "rev-list", "--count", f"{branch}..{upstream}",
        cwd=worktree_path, check=False,
    ).stdout.strip()

    print(f"Rebasing {branch} onto {upstream}...")
    if not git_ops.rebase(upstream, cwd=worktree_path):
        output.err(
            f"Rebase of {branch} onto {upstream} hit a conflict and was aborted; "
            f"the branch is unchanged. Resolve by hand in the worktree "
            f"(git rebase {upstream}), then retry."
        )
        return False

    head = git_ops.git(
        "rev-parse", "--short", "HEAD", cwd=worktree_path, check=False,
    ).stdout.strip()
    suffix = f" (was {behind} behind)" if behind and behind != "0" else ""
    print(f"[OK] {branch} synced onto {upstream}{suffix}; HEAD now {head}.")
    return True
