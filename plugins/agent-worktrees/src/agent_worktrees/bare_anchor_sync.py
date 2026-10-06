"""Fast-forward a bare agent-worktrees anchor's branch ref directly.

Split out of ``repos.py`` to respect its shrink-only module-size baseline
(``tools/module-size-baseline.json``) -- see :func:`repos.sync_repo`, which
delegates to :func:`sync_bare_anchor` whenever the registered repo is
detected to be bare.
"""

from __future__ import annotations

from . import git_ops


def sync_bare_anchor(path: str, declared_branch: str | None) -> tuple[str, str]:
    """Fast-forward a bare worktree-class anchor's branch ref directly.

    A bare anchor (``core.bare`` with every real edit happening in linked
    worktrees -- agent-worktrees' own worktree-class layout, e.g. its
    managed project anchors) has no index or work tree. Both the dirty-check
    (``git status --porcelain``) and the normal fast-forward (``git merge
    --ff-only``) in :func:`repos.sync_repo` unconditionally fail there with
    "fatal: this operation must be run in a work tree" -- every single call,
    synced or not -- which is indistinguishable from a real divergence to a
    caller that only checks the exit code. Previously that failure was
    reported as ``"not fast-forwardable (diverged)"`` even when the anchor
    was already fully in sync.

    There is no "dirty" (uncommitted work-tree) concept without a work tree
    to hold it, but the branch ref itself can still be ahead of, or diverged
    from, its upstream -- refs advance independently of any work tree, which
    is exactly what the ancestry check below detects and refuses to clobber.
    This fetches ``origin`` (through the same credential-aware
    ``git_ops.fetch`` the non-bare path uses) and moves the branch ref
    forward with an atomic compare-and-swap ``update-ref`` once confirmed to
    be a strict fast-forward -- mirroring
    ``agent_machines.self_update._fast_forward_bare_repo``.
    """

    def _git(*args: str):
        return git_ops.git(*args, cwd=path, check=False)

    current = _git("branch", "--show-current").stdout.strip()
    if not current:
        return ("skipped", "detached HEAD")
    target = declared_branch or current
    if target != current:
        # A bare repo has no index/work tree for `git checkout` to act on;
        # moving HEAD to a different declared branch is a structural change
        # best left to an explicit operator action, not an unattended sync.
        return (
            "skipped",
            f"HEAD is on '{current}', not declared default '{target}' "
            "(bare anchor: switch it explicitly, e.g. `git symbolic-ref "
            f"HEAD refs/heads/{target}`)",
        )
    old_sha = _git("rev-parse", "--verify", target).stdout.strip()
    if not old_sha:
        return ("error", f"could not resolve '{target}'")
    git_ops.fetch("origin", cwd=path, timeout=180)
    new_sha = _git("rev-parse", "--verify", f"origin/{target}").stdout.strip()
    if not new_sha:
        return ("skipped", f"origin/{target} not found after fetch")
    if new_sha == old_sha:
        return ("synced", target)
    ancestry = _git("merge-base", "--is-ancestor", old_sha, new_sha)
    if ancestry.returncode != 0:
        return ("skipped", "not fast-forwardable (diverged)")
    updated = _git("update-ref", f"refs/heads/{target}", new_sha, old_sha)
    if updated.returncode != 0:
        return ("error", updated.stderr.strip() or "git update-ref failed")
    return ("synced", target)
