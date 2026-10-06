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
    managed project anchors) has no index or work tree, so neither the
    dirty-check (``git status --porcelain``) nor the normal fast-forward
    (``git merge --ff-only``) in :func:`repos.sync_repo` can run there --
    both unconditionally fail with "fatal: this operation must be run in a
    work tree", on every call regardless of actual sync state.

    There is no "dirty" (uncommitted work-tree) concept without a work tree
    to hold it, but the branch ref itself can still be ahead of, or diverged
    from, its upstream -- refs advance independently of any work tree, which
    is exactly what the ancestry check below detects and refuses to clobber.
    This fetches the upstream branch into a private scratch ref -- rather
    than trusting a plain fetch, which would honor whatever fetch refspec
    the repo is configured with (a mirror-style bare clone can carry
    ``+refs/*:refs/*``; git applies a configured refspec IN ADDITION TO an
    explicit one, not instead of it, so even our own explicit refspec alone
    would not stop it from also force-writing straight into
    ``refs/heads/*`` and clobbering the branch before any check here runs --
    the configured refspec is temporarily cleared for the fetch and restored
    after) -- then moves the branch ref forward with an atomic
    compare-and-swap ``update-ref`` once confirmed to be a strict
    fast-forward, mirroring
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
    old_sha = _git("rev-parse", "--verify", f"refs/heads/{target}").stdout.strip()
    if not old_sha:
        return ("error", f"could not resolve '{target}'")

    scratch_ref = f"refs/agent-worktrees/repos-sync-fetch/{target}"

    def _cleanup_scratch() -> None:
        _git("update-ref", "-d", scratch_ref)

    # Git applies a remote's configured fetch refspec(s) IN ADDITION TO an
    # explicit one given on the command line, not instead of it -- so a
    # mirror-style `+refs/*:refs/*` remote would still force-write straight
    # into refs/heads/<target> even though we only asked for scratch_ref.
    # Save and temporarily clear any configured refspec(s) for this fetch so
    # ours is the only one that can land anywhere, then restore them.
    saved_refspecs = _git(
        "config", "--get-all", "remote.origin.fetch",
    ).stdout.splitlines()
    _git("config", "--unset-all", "remote.origin.fetch")
    try:
        # Route the fetch through the same credential-aware auth-config
        # resolution `git_ops.fetch` uses (dotfiles#2069), but with our own
        # explicit refspec into `scratch_ref` only.
        fetched = git_ops.git(
            *git_ops._auth_config_args("origin", cwd=path),
            "fetch", "--quiet", "--no-tags", "origin",
            f"+refs/heads/{target}:{scratch_ref}",
            cwd=path, check=False, timeout=180,
        )
    finally:
        for refspec in saved_refspecs:
            _git("config", "--add", "remote.origin.fetch", refspec)
    if fetched.returncode != 0:
        _cleanup_scratch()
        return ("error", fetched.stderr.strip() or "git fetch failed")

    new_sha = _git("rev-parse", "--verify", scratch_ref).stdout.strip()
    if not new_sha:
        _cleanup_scratch()
        return ("skipped", f"origin/{target} not found after fetch")
    if new_sha == old_sha:
        _cleanup_scratch()
        return ("synced", target)
    ancestry = _git("merge-base", "--is-ancestor", old_sha, new_sha)
    if ancestry.returncode != 0:
        _cleanup_scratch()
        return ("skipped", "not fast-forwardable (diverged)")
    updated = _git("update-ref", f"refs/heads/{target}", new_sha, old_sha)
    _cleanup_scratch()
    if updated.returncode != 0:
        return ("error", updated.stderr.strip() or "git update-ref failed")
    return ("synced", target)
