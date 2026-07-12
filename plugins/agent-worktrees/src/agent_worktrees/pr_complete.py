"""``pr-complete`` -- reconcile the worktree after its PR squash-merges.

The post-merge git step the ``pr-*`` family owns.  After the review gate
**squash-merges** a PR, the worktree branch (``worktree/<id>``) still sits on the
pre-merge squashed commit -- *ahead* of the old upstream but with work that is
already present on the *new* upstream (folded into one squash commit).  Two
existing moves both misbehave here:

- a strict **fast-forward** refuses (the branch is ``ahead > 0`` -- the pain in
  aperture-labs #2147, where ``finalize`` balks after an external squash-merge);
- a plain **rebase** *replays* the local commit onto the new upstream, which can
  hit a phantom conflict when the squash-merge folded/re-ordered the change.

``pr-complete`` instead **detects** that the branch's work is already on upstream
(patch-id equivalence via ``git cherry``) and **fast-forwards past the
squash-merge commit** -- hard-resetting the branch to the upstream tip so the now
redundant local commits simply drop, with **no replay and no conflict**.  When
the branch instead carries genuinely-new commits (a behind-but-unmerged PR, or
extra work committed after the PR), it falls back to a **rebase** so that new
work is preserved on top.  A pre-reset backup ref is written for recoverability.

This is distinct from ``finalize`` (worktree-lifecycle cleanup): ``pr-complete``
lands the worktree *forward* after a merge; a keep-alive worktree runs it and
keeps working.
"""

from __future__ import annotations

from pathlib import Path

from . import git_ops
from .config import Config

BACKUP_REF = "refs/pre-complete-backup"


def _branch_fully_merged(
    merge_base: str, branch: str, upstream: str, *, cwd: str
) -> bool | None:
    """True when every change the branch introduced is already on ``upstream``.

    **Tree/blob equivalence**, not per-commit patch-id -- this is what correctly
    detects a *squash-merge*, where several branch commits were folded into one
    upstream commit (so ``git cherry``'s per-commit patch-id finds no match and
    wrongly reports the commits as unmerged).  For each path the branch touched
    (``merge_base..branch``), the branch's resulting blob must match upstream's
    (an added/modified file), or the path must be absent on both (a deletion).

    Returns ``None`` if the comparison could not be run (caller treats unknown as
    "not safe to reset").  Renames/copies are compared by their destination path.
    """
    r = git_ops.git("diff", "--name-status", merge_base, branch, cwd=cwd, check=False)
    if r.returncode != 0:
        return None
    for line in r.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status = parts[0]
        path = parts[-1]  # destination path (handles rename/copy)
        deleted = status.startswith("D")
        b = git_ops.git("rev-parse", "--verify", "-q", f"{branch}:{path}",
                        cwd=cwd, check=False)
        u = git_ops.git("rev-parse", "--verify", "-q", f"{upstream}:{path}",
                        cwd=cwd, check=False)
        u_has = u.returncode == 0
        if deleted:
            # Branch removed the path; upstream must also lack it to be "merged".
            if u_has:
                return False
        else:
            # Branch added/modified the path; upstream must carry the same blob.
            if not u_has or b.stdout.strip() != u.stdout.strip():
                return False
    return True


def complete_worktree(
    worktree_id: str, config: Config, *, dry_run: bool = False
) -> dict:
    """Reconcile ``worktree_id`` onto the updated default branch after a merge.

    Returns a result dict ``{success, action, ...}`` where ``action`` is one of:

    - ``up-to-date``     -- already on the upstream tip; nothing to do.
    - ``fast-forwarded`` -- branch had no local commits; ff'd to upstream.
    - ``reset-past-squash`` -- the branch's work was squash-merged; hard-reset to
      upstream, dropping the redundant local commits (no replay).
    - ``rebased``        -- the branch carried genuinely-new commits; rebased onto
      upstream (already-merged commits drop, new ones preserved).
    - ``error``          -- a blocker (dirty tree, detached HEAD, conflict, …);
      ``error`` field explains, the branch is left untouched.
    """
    repo = config.default_repo
    remote = repo.remote
    upstream = f"{remote}/{repo.default_branch}"
    worktree_path = str(Path(repo.worktree_root) / worktree_id)
    base: dict = {"success": False, "worktree_id": worktree_id, "upstream": upstream}

    if not Path(worktree_path).exists():
        return {**base, "action": "error",
                "error": f"Worktree path not found: {worktree_path}"}

    branch = git_ops._get_current_branch_safe(worktree_path)
    if branch is None:
        return {**base, "action": "error",
                "error": "Worktree is in a detached HEAD state; checkout a branch first."}
    base["branch"] = branch

    if not git_ops.is_clean(cwd=worktree_path):
        dirty = git_ops.get_dirty_files(cwd=worktree_path)
        return {**base, "action": "error",
                "error": ("Worktree has uncommitted changes; commit or stash "
                          "before completing:\n  " + "\n  ".join(dirty[:20]))}

    # Fetch so ahead/behind + patch-id detection are against the freshest tip.
    if git_ops.has_remote(remote, cwd=worktree_path):
        try:
            git_ops.fetch(remote, cwd=worktree_path)
        except Exception as e:
            return {**base, "action": "error", "error": f"Fetch from {remote} failed: {e}"}

    if not git_ops.ref_exists(upstream, cwd=worktree_path):
        return {**base, "action": "error", "error": f"Upstream {upstream} not found after fetch."}

    mb = git_ops.git("merge-base", upstream, branch, cwd=worktree_path, check=False)
    if mb.returncode != 0:
        return {**base, "action": "error",
                "error": f"No merge-base between {branch} and {upstream} (unrelated histories)."}
    merge_base = mb.stdout.strip()
    ahead = git_ops._rev_count(f"{merge_base}..{branch}", cwd=worktree_path)
    behind = git_ops._rev_count(f"{branch}..{upstream}", cwd=worktree_path)
    base.update(ahead=ahead, behind=behind)

    # No local commits: a plain fast-forward (or already current).
    if ahead == 0:
        if behind == 0:
            return {**base, "success": True, "action": "up-to-date",
                    "message": f"{branch} already at {upstream}."}
        if dry_run:
            return {**base, "success": True, "action": "fast-forwarded",
                    "message": f"Would fast-forward {branch} to {upstream} ({behind} behind)."}
        if not git_ops.merge_ff(upstream, cwd=worktree_path):
            return {**base, "action": "error",
                    "error": f"Fast-forward of {branch} to {upstream} failed."}
        return {**base, "success": True, "action": "fast-forwarded",
                "head": _short_head(worktree_path),
                "message": f"{branch} fast-forwarded to {upstream} (was {behind} behind)."}

    # Local commits present -- is every change already on upstream (squash-merged)?
    fully_merged = _branch_fully_merged(merge_base, branch, upstream, cwd=worktree_path)

    if fully_merged:
        # The squash-merge already carries every local patch. Fast-forward PAST
        # the squash commit by hard-resetting to upstream -- drop the now
        # redundant local commits, no replay, no phantom conflict. This is the
        # #2147 fix: it succeeds where a strict ff refuses on ahead>0.
        if dry_run:
            return {**base, "success": True, "action": "reset-past-squash",
                    "dropped": ahead,
                    "message": (f"Would reset {branch} to {upstream}, dropping "
                                f"{ahead} squash-merged commit(s).")}
        pre = git_ops.git("rev-parse", branch, cwd=worktree_path, check=False).stdout.strip()
        if pre:
            git_ops.git("update-ref", BACKUP_REF, pre, cwd=worktree_path, check=False)
        reset = git_ops.git("reset", "--hard", upstream, cwd=worktree_path, check=False)
        if reset.returncode != 0:
            return {**base, "action": "error",
                    "error": f"Reset of {branch} to {upstream} failed: {reset.stderr.strip()}"}
        return {**base, "success": True, "action": "reset-past-squash",
                "dropped": ahead, "backup_ref": BACKUP_REF,
                "head": _short_head(worktree_path),
                "message": (f"{branch} fast-forwarded past the squash-merge: "
                            f"dropped {ahead} redundant local commit(s); HEAD now "
                            f"{_short_head(worktree_path)} == {upstream}. "
                            f"(pre-complete state saved at {BACKUP_REF})")}

    # Genuinely-new local commits (behind-but-unmerged, or extra work after the
    # PR). Rebase forward: already-merged commits drop as already-applied; the
    # new commits are preserved on top.
    if dry_run:
        return {**base, "success": True, "action": "rebased",
                "message": (f"Would rebase {branch} onto {upstream}, preserving "
                            "new commit(s) not yet upstream.")}
    if not git_ops.rebase(upstream, cwd=worktree_path):
        return {**base, "action": "error",
                "error": (f"Rebase of {branch} onto {upstream} hit a conflict and "
                          "was aborted; the branch is unchanged. Resolve by hand "
                          f"(git rebase {upstream}), then retry.")}
    kept = git_ops._rev_count(f"{upstream}..{branch}", cwd=worktree_path)
    return {**base, "success": True, "action": "rebased", "kept": kept,
            "head": _short_head(worktree_path),
            "message": (f"{branch} rebased onto {upstream}"
                        + (f", preserving {kept} new commit(s)" if kept else "")
                        + f"; HEAD now {_short_head(worktree_path)}.")}


def _short_head(cwd: str) -> str:
    return git_ops.git("rev-parse", "--short", "HEAD", cwd=cwd, check=False).stdout.strip()


__all__ = ["complete_worktree"]
