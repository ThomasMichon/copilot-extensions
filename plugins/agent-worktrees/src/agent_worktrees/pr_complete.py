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

``pr-complete`` **rebases the branch forward first** -- a non-destructive replay
that drops commits already applied upstream (by patch-id) while **preserving any
genuinely-new commit**, including one authored *after* the merge whose content
happens to coincide with upstream (the aperture-labs #2854 regression, where a
blanket hard reset silently discarded such a commit and left ``create-pr``
reporting "nothing ahead").  Only when that rebase **phantom-conflicts** -- the
squash-merge case where several branch commits were folded into one upstream
commit, so a per-commit replay conflicts even though the net content is
identical -- does it fall back to **fast-forwarding past the squash** by
hard-resetting to the upstream tip.  That fallback is gated on the branch's work
being confirmed already on upstream (tree/blob equivalence), so the reset is
content-lossless -- the #2147 fix, which succeeds where a strict fast-forward
refuses on ``ahead > 0``.  A pre-reconcile backup ref is written for
recoverability whichever path runs.

This is distinct from ``finalize`` (worktree-lifecycle cleanup): ``pr-complete``
lands the worktree *forward* after a merge; a keep-alive worktree runs it and
keeps working.
"""

from __future__ import annotations

from pathlib import Path

from . import git_ops, tracking
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
    worktree_path = tracking.resolve_worktree_path(worktree_id, repo.worktree_root)
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

    # Local commits present. Is every change the branch introduced already on
    # upstream (a squash-merge folded it)?  Used only to decide the *fallback*
    # below -- the primary move is a non-destructive rebase.
    fully_merged = _branch_fully_merged(merge_base, branch, upstream, cwd=worktree_path)

    if dry_run:
        if fully_merged:
            return {**base, "success": True, "action": "reset-past-squash",
                    "dropped": ahead,
                    "message": (f"Would reconcile {branch} onto {upstream} "
                                f"(rebase; hard-reset past the squash only if the "
                                f"replay phantom-conflicts), dropping up to "
                                f"{ahead} already-merged commit(s).")}
        return {**base, "success": True, "action": "rebased",
                "message": (f"Would rebase {branch} onto {upstream}, preserving "
                            "new commit(s) not yet upstream.")}

    # Back up the pre-reconcile tip so any dropped commit stays recoverable,
    # whichever path is taken below.
    pre = git_ops.git("rev-parse", branch, cwd=worktree_path, check=False).stdout.strip()
    if pre:
        git_ops.git("update-ref", BACKUP_REF, pre, cwd=worktree_path, check=False)

    # PRIMARY: rebase forward. This is non-destructive -- it drops commits that
    # are already applied upstream (by patch-id) while PRESERVING any commit that
    # is genuinely new, *including one authored after the merge whose content
    # happens to coincide with upstream*.  A blanket ``reset --hard upstream``
    # (the old ``fully_merged`` fast-path) silently discarded such a commit and
    # left create-pr reporting "nothing ahead" -- aperture-labs #2854.  Only
    # fall back to the hard reset when the rebase cannot proceed.
    if git_ops.rebase(upstream, cwd=worktree_path):
        kept = git_ops._rev_count(f"{upstream}..{branch}", cwd=worktree_path)
        if kept == 0:
            # Every local commit was already merged; the rebase dropped them
            # cleanly (no hard reset needed).  Report as the squash-reconcile.
            return {**base, "success": True, "action": "reset-past-squash",
                    "dropped": ahead, "backup_ref": BACKUP_REF,
                    "head": _short_head(worktree_path),
                    "message": (f"{branch} reconciled onto {upstream}: all "
                                f"{ahead} local commit(s) were already merged; "
                                f"HEAD now {_short_head(worktree_path)} == "
                                f"{upstream}. (pre-complete state saved at "
                                f"{BACKUP_REF})")}
        return {**base, "success": True, "action": "rebased", "kept": kept,
                "backup_ref": BACKUP_REF,
                "head": _short_head(worktree_path),
                "message": (f"{branch} rebased onto {upstream}, preserving "
                            f"{kept} new commit(s); HEAD now "
                            f"{_short_head(worktree_path)}.")}

    # The rebase hit a conflict and was aborted (branch unchanged).  When the
    # branch's work is confirmed already on upstream, the conflict is the
    # squash-merge phantom-conflict (#2147): several branch commits were folded
    # into one upstream commit, so a per-commit replay conflicts even though the
    # net content is identical.  Fast-forward PAST the squash by hard-resetting
    # to upstream -- content-lossless, because ``fully_merged`` guarantees every
    # branch change is already present upstream.  Otherwise it is a genuine
    # conflict the operator must resolve.
    if not fully_merged:
        return {**base, "action": "error",
                "error": (f"Rebase of {branch} onto {upstream} hit a conflict and "
                          "was aborted; the branch is unchanged. Resolve by hand "
                          f"(git rebase {upstream}), then retry.")}
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


def _short_head(cwd: str) -> str:
    return git_ops.git("rev-parse", "--short", "HEAD", cwd=cwd, check=False).stdout.strip()


__all__ = ["complete_worktree"]
