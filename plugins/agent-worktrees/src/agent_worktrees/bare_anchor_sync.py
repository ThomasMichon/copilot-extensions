"""Fast-forward a bare agent-worktrees anchor's branch ref directly.

Split out of ``repos.py`` to respect its shrink-only module-size baseline
(``tools/module-size-baseline.json``) -- see :func:`repos.sync_repo`, which
delegates to :func:`sync_bare_anchor` whenever the registered repo is
detected to be bare.
"""

from __future__ import annotations

import os
import shlex
import uuid

from . import bare_anchor_files, git_ops


def _display_command(argv: list[str]) -> str:
    """A copy-paste command for the operator's shell: PowerShell literal
    quoting on Windows, POSIX quoting elsewhere, so ``$``, backticks and
    quotes in a path or ref are never reinterpreted."""
    if os.name == "nt":
        return " ".join([argv[0], *("'" + a.replace("'", "''") + "'" for a in argv[1:])])
    return shlex.join(argv)


def is_bare(path: str | os.PathLike[str]) -> bool:
    """Whether ``path`` is a bare repository (no work tree of its own)."""
    probe = git_ops.git(
        "rev-parse", "--is-bare-repository", cwd=path, check=False, isolated_repository=True,
    )
    return probe.returncode == 0 and probe.stdout.strip() == "true"


def sync_bare_anchor(
    path: str, declared_branch: str | None, *, remote: str = "origin",
) -> tuple[str, str]:
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

    An anchor that still carries an index and checked-out files (a converted
    ordinary clone) has those files advanced with the ref
    (:mod:`bare_anchor_files`): a stale index, local edits, or an untracked or
    ignored path in the way refuse BEFORE the ref moves. The detail always
    ends with ``; files=<status>`` once the ref is known to be current.
    """

    # Inherited GIT_DIR/GIT_WORK_TREE/GIT_INDEX_FILE must never redirect the
    # ref move or the fetch to another repository: always act on `path`.
    def _git(*args: str):
        return git_ops.git(*args, cwd=path, check=False, isolated_repository=True)

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
            "(bare anchor); switch it explicitly with: "
            + _display_command(
                ["git", "-C", str(path), "symbolic-ref", "HEAD", f"refs/heads/{target}"]
            ),
        )
    old_sha = _git("rev-parse", "--verify", f"refs/heads/{target}").stdout.strip()
    if not old_sha:
        return ("error", f"could not resolve '{target}'")

    scratch_ref = f"refs/agent-worktrees/repos-sync-fetch/{target}.{uuid.uuid4().hex}"

    def _cleanup_scratch() -> None:
        _git("update-ref", "-d", scratch_ref)

    # Git applies a remote's configured fetch refspec(s) IN ADDITION TO an
    # explicit one given on the command line, not instead of it -- so a
    # mirror-style `+refs/*:refs/*` remote would still force-write straight
    # into refs/heads/<target> even though we only asked for scratch_ref.
    # Save and temporarily clear any configured refspec(s) for this fetch so
    # ours is the only one that can land anywhere, then restore them.
    saved_refspecs = _git(
        "config", "--get-all", f"remote.{remote}.fetch",
    ).stdout.splitlines()
    _git("config", "--unset-all", f"remote.{remote}.fetch")
    try:
        # Route the fetch through the same credential-aware auth-config
        # resolution `git_ops.fetch` uses (dotfiles#2069), but with our own
        # explicit refspec into `scratch_ref` only.
        fetched = git_ops.git(
            *git_ops._auth_config_args(remote, cwd=path),
            "fetch", "--quiet", "--no-tags", remote,
            f"+refs/heads/{target}:{scratch_ref}",
            cwd=path, check=False, timeout=180, isolated_repository=True,
        )
    finally:
        for refspec in saved_refspecs:
            _git("config", "--add", f"remote.{remote}.fetch", refspec)
    if fetched.returncode != 0:
        _cleanup_scratch()
        return ("error", fetched.stderr.strip() or "git fetch failed")

    new_sha = _git("rev-parse", "--verify", scratch_ref).stdout.strip()
    if not new_sha:
        _cleanup_scratch()
        return ("skipped", f"{remote}/{target} not found after fetch")
    if new_sha == old_sha:
        _cleanup_scratch()
        return ("synced", f"{target}; files={bare_anchor_files.current_status(path, old_sha)}")
    ancestry = _git("merge-base", "--is-ancestor", old_sha, new_sha)
    if ancestry.returncode != 0:
        _cleanup_scratch()
        return ("skipped", "not fast-forwardable (diverged)")
    refusal = bare_anchor_files.precheck(path, old_sha, new_sha)
    if refusal:
        _cleanup_scratch()
        return (
            "skipped",
            f"{target} not fast-forwarded: anchor files cannot advance safely; files={refusal}",
        )
    updated = _git("update-ref", f"refs/heads/{target}", new_sha, old_sha)
    _cleanup_scratch()
    if updated.returncode != 0:
        return ("error", updated.stderr.strip() or "git update-ref failed")
    files = bare_anchor_files.refresh(path, old_sha, new_sha)
    if files.startswith(bare_anchor_files.REFRESH_FAILED):
        # The ref already moved; the CLI reports the stale files as a warning.
        return (
            "synced",
            f"{target} ref advanced from {old_sha[:12]} but anchor files were not refreshed; "
            f"files={files}",
        )
    return ("synced", f"{target}; files={files}")
