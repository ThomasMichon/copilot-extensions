"""Git-collaboration primitives -- the invariant-bearing operations that sit
below the high-level push-changes/create-pr/finalize lifecycle and above raw
git.  See the ``git-collaboration`` skill for the command surface and the
forbidden/wrapped/blessed-direct boundary.

So far:

- :func:`sync_forward` -- pull the worktree branch forward onto the updated
  remote default branch (build on top of a just-merged PR).
- :func:`manage_feature_branch` -- create/update, push, or sync a durable shared
  feature branch (host publishes; delegates sync).
- :func:`merge_to_feature` -- rebase the worktree branch onto a shared feature
  branch and ff-merge + push it (the delegate handoff).
"""

from __future__ import annotations

from pathlib import Path

from . import git_ops, output, tracking
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
    worktree_path = tracking.resolve_worktree_path(worktree_id, repo.worktree_root)

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
    except Exception as e:
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


# --- Shared feature branch -------------------------------------------------
#
# A *shared* feature branch (``feature/<name>``) is a durable remote branch that
# several agent-bridge agents build on for one effort.  It is distinct from the
# local-only ``worktree/<id>`` base and from the per-worktree ``feature/<slug>``
# branch that ``create-pr`` derives.  The worktree always stays on its own
# ``worktree/<id>`` branch; the shared feature branch is manipulated as a ref via
# fetch / ``git branch -f`` / push, never checked out into the worktree.
#
# Ownership: every delegate may **ff-push** its slice to the shared feature
# branch (that is the handoff).  Only the **host** opens PRs from it.  No one
# force-pushes it.


def _feature_ref(name: str) -> str:
    """Normalize a shared-branch name to its ``feature/<name>`` ref."""
    name = name.strip().strip("/")
    return name if name.startswith("feature/") else f"feature/{name}"


def _is_ancestor(maybe_ancestor: str, ref: str, *, cwd: str | Path) -> bool:
    """Return True if *maybe_ancestor* is an ancestor of *ref*."""
    r = git_ops.git(
        "merge-base", "--is-ancestor", maybe_ancestor, ref, cwd=cwd, check=False,
    )
    return r.returncode == 0


def manage_feature_branch(
    worktree_id: str,
    config: Config,
    name: str,
    *,
    push: bool = False,
    sync: bool = False,
    dry_run: bool = False,
) -> bool:
    """Create/update, push, or sync a durable shared feature branch.

    Modes (the worktree stays on its ``worktree/<id>`` branch throughout):

    - **default** -- ensure a local ``feature/<name>`` exists. Create it at the
      worktree HEAD if absent; if it already exists, fast-forward it to HEAD only
      when HEAD descends from it (else refuse -- diverged, use ``--sync`` /
      ``merge-to-feature``).
    - ``--push`` -- also push ``feature/<name>`` to the remote (plain, ff-only;
      a non-ff push is refused -- ``--sync`` first).
    - ``--sync`` -- fetch and fast-forward the local ``feature/<name>`` to
      ``<remote>/feature/<name>`` (pull the shared branch forward).
    """
    repo = config.default_repo
    remote = repo.remote
    feature = _feature_ref(name)
    remote_feature = f"{remote}/{feature}"
    worktree_path = tracking.resolve_worktree_path(worktree_id, repo.worktree_root)

    if not Path(worktree_path).exists():
        output.err(f"Worktree path not found: {worktree_path}")
        return False
    branch = git_ops._get_current_branch_safe(worktree_path)
    if branch is None:
        output.err("Worktree is in a detached HEAD state; checkout a branch first.")
        return False
    if branch == feature:
        output.err(
            f"Worktree is on '{feature}' itself; shared-branch commands operate on "
            f"it as a ref from the worktree branch, not while it is checked out."
        )
        return False

    # --- sync: pull the shared branch forward -----------------------------
    if sync:
        if dry_run:
            output.dry_run(f"Would fetch from {remote}")
            output.dry_run(f"Would fast-forward local {feature} to {remote_feature}")
            return True
        print(f"Fetching from {remote}...")
        try:
            git_ops.fetch(remote, cwd=worktree_path)
        except Exception as e:
            output.err(f"Fetch from {remote} failed: {e}")
            return False
        if not git_ops.ref_exists(remote_feature, cwd=worktree_path):
            output.err(
                f"No {remote_feature} to sync from. Has the host pushed the shared "
                f"branch yet (feature-branch {name} --push)?"
            )
            return False
        if git_ops.local_branch_exists(feature, cwd=worktree_path) and not _is_ancestor(
            feature, remote_feature, cwd=worktree_path
        ):
            output.err(
                f"Local {feature} has diverged from {remote_feature}; refusing to "
                f"force it. Resolve by hand."
            )
            return False
        r = git_ops.git(
            "branch", "-f", feature, remote_feature, cwd=worktree_path, check=False,
        )
        if r.returncode != 0:
            output.err(f"Failed to update {feature} to {remote_feature}: {r.stderr.strip()}")
            return False
        print(f"[OK] {feature} synced to {remote_feature}.")
        return True

    # --- default: ensure local feature exists / ff to HEAD ----------------
    exists = git_ops.local_branch_exists(feature, cwd=worktree_path)
    if dry_run:
        if exists:
            output.dry_run(f"Would fast-forward {feature} to worktree HEAD (if descended)")
        else:
            output.dry_run(f"Would create {feature} at worktree HEAD")
        if push:
            output.dry_run(f"Would push {feature} to {remote} (ff-only)")
        return True

    if exists:
        if not _is_ancestor(feature, "HEAD", cwd=worktree_path):
            output.err(
                f"{feature} has commits not in this worktree's HEAD; advancing it "
                f"would drop them. Run 'git feature-branch {name} --sync' or "
                f"'git merge-to-feature {name}' instead."
            )
            return False
        r = git_ops.git("branch", "-f", feature, "HEAD", cwd=worktree_path, check=False)
    else:
        r = git_ops.git("branch", feature, "HEAD", cwd=worktree_path, check=False)
    if r.returncode != 0:
        output.err(f"Failed to set {feature}: {r.stderr.strip()}")
        return False
    print(f"[OK] {feature} {'updated to' if exists else 'created at'} worktree HEAD.")

    if push:
        print(f"Pushing {feature} to {remote}...")
        if not git_ops.push(remote, feature, cwd=worktree_path):
            output.err(
                f"Push of {feature} to {remote} failed (likely non-ff). Run "
                f"'git feature-branch {name} --sync' to pull the shared branch "
                f"forward, then retry."
            )
            return False
        print(f"[OK] {feature} pushed to {remote}.")
    return True


def merge_to_feature(
    worktree_id: str,
    config: Config,
    name: str,
    *,
    push: bool = True,
    dry_run: bool = False,
) -> bool:
    """Rebase the worktree branch onto the shared feature branch and ff-merge.

    The delegate handoff: rebase this worktree's ``worktree/<id>`` branch onto the
    latest ``feature/<name>`` (so the merge is a strict fast-forward), advance
    ``feature/<name>`` to the rebased HEAD (no two-parent merge node), and -- by
    default -- push the shared branch back to the remote so the host can sync
    forward.  ``--no-push`` stops after the local ff (rare).

    A dirty tree or a true rebase conflict stops with a clear message (the rebase
    auto-aborts, leaving the worktree branch untouched).
    """
    repo = config.default_repo
    remote = repo.remote
    feature = _feature_ref(name)
    remote_feature = f"{remote}/{feature}"
    worktree_path = tracking.resolve_worktree_path(worktree_id, repo.worktree_root)

    if not Path(worktree_path).exists():
        output.err(f"Worktree path not found: {worktree_path}")
        return False
    branch = git_ops._get_current_branch_safe(worktree_path)
    if branch is None:
        output.err("Worktree is in a detached HEAD state; checkout a branch first.")
        return False
    if branch == feature:
        output.err(f"Worktree is on '{feature}' itself; nothing to merge.")
        return False
    if not git_ops.is_clean(cwd=worktree_path):
        dirty = git_ops.get_dirty_files(cwd=worktree_path)
        output.err(
            "Worktree has uncommitted changes; commit or stash before merging:\n  "
            + "\n  ".join(dirty[:20])
        )
        return False

    if dry_run:
        output.dry_run(f"Would fetch from {remote}")
        output.dry_run(f"Would rebase {branch} onto {feature} (from {remote_feature})")
        output.dry_run(f"Would fast-forward {feature} to {branch}")
        if push:
            output.dry_run(f"Would push {feature} to {remote}")
        return True

    print(f"Fetching from {remote}...")
    try:
        git_ops.fetch(remote, cwd=worktree_path)
    except Exception as e:
        output.err(f"Fetch from {remote} failed: {e}")
        return False
    if not git_ops.remote_branch_exists(remote, feature, cwd=worktree_path):
        output.err(
            f"Shared branch {feature} is not on {remote}. The host must publish it "
            f"first (feature-branch {name} --push)."
        )
        return False

    # Track the latest remote feature locally (force-update is safe: it is not
    # checked out, and we are about to rebase onto it).
    git_ops.git("branch", "-f", feature, remote_feature, cwd=worktree_path, check=False)

    print(f"Rebasing {branch} onto {feature}...")
    if not git_ops.rebase(feature, cwd=worktree_path):
        output.err(
            f"Rebase of {branch} onto {feature} hit a conflict and was aborted; the "
            f"branch is unchanged. Resolve by hand (git rebase {feature}), then retry."
        )
        return False

    # ff-merge: feature is now an ancestor of HEAD, so advancing it is a pure ff.
    r = git_ops.git("branch", "-f", feature, "HEAD", cwd=worktree_path, check=False)
    if r.returncode != 0:
        output.err(f"Failed to fast-forward {feature}: {r.stderr.strip()}")
        return False
    print(f"[OK] {feature} fast-forwarded to {branch}.")

    if push:
        print(f"Pushing {feature} to {remote}...")
        if not git_ops.push(remote, feature, cwd=worktree_path):
            output.err(
                f"Push of {feature} to {remote} failed (likely a concurrent update). "
                f"Run 'git merge-to-feature {name}' again to rebase onto the latest "
                f"and retry."
            )
            return False
        print(f"[OK] {feature} pushed to {remote}.")
    return True

