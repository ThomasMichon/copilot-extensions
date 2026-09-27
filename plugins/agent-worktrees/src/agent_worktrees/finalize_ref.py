"""Resolve which ref finalize's content-on-upstream check should validate.

Split out of ``finalize.py`` (module-size guard) -- see ``resolve_finalize_ref``.
"""

from __future__ import annotations

from . import git_ops, output


def is_content_on_upstream(
    branch: str,
    upstream: str,
    cwd: str,
) -> bool:
    """Non-mutating check: is the branch's content already on upstream?

    Uses multiple strategies in order of reliability:
    1. Ancestor check (branch is ancestor of upstream)
    2. git cherry (patch-id comparison)
    3. Blob comparison of changed files
    """
    # Strategy 1: branch is an ancestor of upstream (already merged)
    r = git_ops.git(
        "merge-base", "--is-ancestor", branch, upstream,
        cwd=cwd, check=False,
    )
    if r.returncode == 0:
        return True

    # Strategy 2: git cherry -- all patches accounted for on upstream
    cherry_r = git_ops.git(
        "cherry", upstream, branch,
        cwd=cwd, check=False,
    )
    if cherry_r.returncode == 0 and cherry_r.stdout.strip():
        unmerged = [ln for ln in cherry_r.stdout.splitlines() if ln.startswith("+")]
        if not unmerged:
            return True

    # Strategy 3: compare file blobs between branch and upstream
    merge_base_r = git_ops.git(
        "merge-base", branch, upstream,
        cwd=cwd, check=False,
    )
    if merge_base_r.returncode != 0:
        return False

    diff_r = git_ops.git(
        "diff", "--name-only", merge_base_r.stdout.strip(), branch,
        cwd=cwd, check=False,
    )
    changed_files = [f for f in diff_r.stdout.splitlines() if f.strip()]
    if not changed_files:
        return True

    for file in changed_files:
        b_blob = git_ops.git(
            "rev-parse", f"{branch}:{file}", cwd=cwd, check=False
        )
        m_blob = git_ops.git(
            "rev-parse", f"{upstream}:{file}", cwd=cwd, check=False
        )
        if b_blob.stdout.strip() != m_blob.stdout.strip():
            return False

    return True


def resolve_finalize_ref(
    tracked_branch: str, worktree_path: str,
) -> tuple[str, str | None, bool]:
    """Resolve which ref the "is this worktree's content on upstream?"
    check should actually validate (#7723).

    ``tracked_branch`` (``_worktree_branch``'s result) is the name recorded
    when the worktree was CREATED. It can go stale: the checkout may later
    move to a differently-named branch (e.g. a ``-journal`` suffix variant)
    or end up detached, without ever updating the tracking record. Checking
    the stale tracked name against the content that's *actually* about to be
    discarded is wrong in both directions -- it can false-block finalize on
    content that's actually already safe, or silently pass while the real
    checkout holds something different.

    Returns ``(effective_ref, current_ref, diverged)``:
      - ``effective_ref``: what finalize should actually validate against
        upstream -- the worktree's real checked-out branch, or ``"HEAD"``
        when detached.
      - ``current_ref``: the worktree's actual checked-out branch name, or
        ``None`` when detached.
      - ``diverged``: True when the checkout is not sitting on the
        originally tracked branch name -- either a differently-named
        branch, or detached HEAD (``current_ref is None``). Both are a
        signal worth surfacing, independent of whether the tracked
        branch's own content turns out to still matter.
    """
    current_ref = git_ops.current_branch(worktree_path)
    effective_ref = current_ref or "HEAD"
    diverged = current_ref != tracked_branch
    return effective_ref, current_ref, diverged


def warn_if_tracked_branch_diverged(
    worktree_id: str,
    tracked_branch: str,
    current_ref: str | None,
    upstream: str,
    worktree_path: str,
) -> bool:
    """Surface (non-blocking) orphaned work on a stale tracked branch (#7723).

    Called only when the checkout has diverged from ``tracked_branch``. The
    tracked name is still meaningful as a second, independent signal: if it
    names a ref that itself still holds content never folded into the
    actual checkout, that's real orphaned work worth a human's attention --
    flag it, but never block finalize on it (the worktree being deleted is
    the *actual checkout*, not the stale tracked name).

    Returns True when the tracked branch was flagged as possibly orphaned
    (still exists, content not confirmed on upstream) -- callers should
    preserve that ref through cleanup rather than deleting it alongside the
    checkout, so the flagged content stays reachable for a rescue.
    """
    tracked_exists = git_ops.git(
        "rev-parse", "--verify", tracked_branch, cwd=worktree_path, check=False,
    ).returncode == 0
    if not tracked_exists:
        return False
    if is_content_on_upstream(tracked_branch, upstream, cwd=worktree_path):
        return False
    checkout_desc = current_ref if current_ref is not None else "a detached HEAD"
    output.warn(
        f"Worktree {worktree_id}'s checked-out branch has diverged from its "
        f"originally tracked branch ('{tracked_branch}') -- it is now on "
        f"{checkout_desc} -- and the tracked branch still has content not on "
        f"{upstream}. This worktree's tracked branch may hold orphaned work "
        f"-- inspect 'git log {upstream}..{tracked_branch}' before it "
        f"becomes unreachable, e.g. via 'agent-worktrees claims orphans' or "
        f"a manual rescue branch/PR. Proceeding to validate the actual "
        f"checkout ({checkout_desc}) instead. The tracked branch ref is "
        f"preserved (not deleted) through cleanup."
    )
    return True


