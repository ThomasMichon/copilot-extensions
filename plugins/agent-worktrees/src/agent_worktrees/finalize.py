"""Finalization flow -- push-changes and validate-and-finalize with locking.

Two-phase worktree completion:

Phase 1 -- push_changes():
  1. Acquire lock
  2. Fetch from remote
  3. Pre-squash all worktree commits into one
  4. Rebase the single commit onto upstream
  5. Validate core files (config-driven hooks)
  6. Anchor hygiene check (block on dirty, warn on stash)
  7. Update local default branch and fast-forward merge
  8. Push with retry
  9. Update tracking status to "pushed"

Phase 2 -- validate_and_finalize():
  1. Non-mutating check: is the branch content already on origin/master?
     The worktree's commit must be in origin/master's history (or be
     equal to origin/master) for the worktree to be considered safe to
     prune.
  2. If yes: the worktree is finalized. Merge permissions and update
     tracking to "finalized". The worktree's branch and directory are
     removed *only* when nothing is using them -- i.e. no live Copilot
     session and the current shell is not inside the worktree. When a
     session is still live (the common case, since users typically run
     "finalize" from inside their session), the git branch and the
     folder are intentionally left in place for a later cleanup; this is
     normal, not an error.
  3. If no: error with guidance to run push-changes first

"finalize" never deletes a worktree out from under a running session and
never force-removes the directory. Its job is to guarantee the branch's
work is merged to master; directory/branch pruning is a separate,
deferred concern handled by cleanup once the worktree is idle.
"""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

from . import activity, git_ops, hooks, output, permissions, procs, sessions, tracking
from .config import Config


def _has_live_session(worktree_path: str) -> bool:
    """Return True if any Copilot session is currently using this worktree."""
    ctx = sessions.scan_sessions([worktree_path])
    # scan_sessions keys results by the normalized path it was given,
    # so just check if any active sessions were returned.
    return bool(ctx.active_sessions)


class FinalizeLock:
    """Simple file-based lock with timeout and stale detection."""

    def __init__(self, lock_path: Path, timeout: int = 120) -> None:
        self.lock_path = lock_path
        self.timeout = timeout

    def acquire(self) -> None:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        start = time.monotonic()

        while self.lock_path.exists():
            try:
                age = time.time() - self.lock_path.stat().st_mtime
            except OSError:
                break
            if age > self.timeout:
                output.warn(f"Stale lock detected (age: {int(age)}s) -- breaking.")
                self.lock_path.unlink(missing_ok=True)
                break

            print("Waiting for finalization lock...")
            time.sleep(2)

            if time.monotonic() - start > self.timeout:
                raise TimeoutError("Timed out waiting for finalization lock.")

        self.lock_path.write_text(f"{os.getpid()}")

    def release(self) -> None:
        self.lock_path.unlink(missing_ok=True)

    def __enter__(self) -> FinalizeLock:
        self.acquire()
        return self

    def __exit__(self, *_: object) -> None:
        self.release()


def push_changes(
    worktree_id: str,
    config: Config,
    *,
    title: str | None = None,
    dry_run: bool = False,
    allow_unsquashed: bool = False,
) -> bool:
    """Push worktree changes to the remote default branch.

    Squashes all worktree commits, rebases onto upstream, validates,
    merges to local default branch, and pushes.  Does NOT remove the
    worktree or branch -- call validate_and_finalize() after this.

    Args:
        worktree_id: The worktree identifier.
        config: Loaded project configuration.
        title: Optional title to set on the tracking record.
        dry_run: If True, preview without side effects.
        allow_unsquashed: If True, proceed with the individual commits when
            the pre-squash step fails, instead of aborting. Off by default --
            a squash failure must never silently degrade to pushing every
            commit to the shared default branch (see issue #783).

    Returns:
        True on success, False on failure (worktree preserved).
    """
    repo = config.default_repo
    anchor = repo.anchor
    worktree_path = str(Path(repo.worktree_root) / worktree_id)
    branch = f"worktree/{worktree_id}"
    upstream = f"{repo.remote}/{repo.default_branch}"
    lock_path = Path(repo.worktree_root) / ".finalize.lock"

    # Load tracking record
    from . import config as cfg
    yaml_path = cfg.tracking_dir() / f"{worktree_id}.yaml"
    record = None
    if yaml_path.exists():
        try:
            record = tracking.load_record(yaml_path)
        except Exception:
            pass

    # Set title early so it survives even if push fails
    if title and record:
        record.title = title.replace("\n", " ").strip()
        tracking.save_record(record)

    # PR mode: push the feature branch, not master.
    if repo.pr.enabled and record and record.pr and record.pr.branch:
        return _push_changes_pr(worktree_id, config, record, dry_run=dry_run)

    # PRs required: refuse to push directly to the default branch. The only
    # way to land work is the PR path -- run create-pr first.
    if repo.pr.required:
        output.err(
            f"PRs are required for this repo -- 'push-changes' cannot push "
            f"directly to {upstream}.\n"
            f"Open a pull request instead:\n"
            f"  1. agent-worktrees create-pr --title \"...\"\n"
            f"  2. open the PR via the '{repo.pr.provider}' provider "
            f"(see the worktree skill 'PR Workflow')\n"
            f"  3. agent-worktrees set-pr --url <URL> --number <N>\n"
            f"Then re-run push-changes to update the feature branch."
        )
        return False

    # Guard against branch drift
    if Path(worktree_path).exists():
        actual = git_ops._get_current_branch_safe(worktree_path)
        if actual and actual != branch:
            output.err(
                f"Branch drift detected: worktree HEAD is on '{actual}', "
                f"but push-changes expects '{branch}'. "
                f"Switch back to '{branch}' or handle the feature branch "
                f"manually before pushing."
            )
            return False

    if dry_run:
        _dry_run_push_preview(
            worktree_id, config, worktree_path, branch, upstream, lock_path,
        )
        return True

    # Acquire lock
    lock = FinalizeLock(lock_path)
    try:
        lock.acquire()
    except TimeoutError:
        output.err("Timed out waiting for finalization lock.")
        if record:
            tracking.update_status(record, "orphaned")
        return False

    try:
        # 1. Fetch
        print(f"Fetching from {repo.remote}...")
        git_ops.fetch(repo.remote, cwd=anchor)

        # 2. Dirty check
        wt_exists = Path(worktree_path).exists()
        if wt_exists and not git_ops.is_clean(cwd=worktree_path):
            dirty = git_ops.get_dirty_files(cwd=worktree_path)
            detail = "\n".join(f"    {ln}" for ln in dirty)
            output.err(
                "Working tree has uncommitted changes. "
                "Commit or stash them before pushing:\n"
                f"{detail}"
            )
            return False

        # 3. Divergence check
        ahead_commits = git_ops.get_commits_ahead(branch, upstream, cwd=worktree_path)
        behind_r = git_ops.git(
            "rev-list", "--count", f"{branch}..{upstream}",
            cwd=worktree_path, check=False,
        )
        behind_count = int(behind_r.stdout.strip()) if behind_r.returncode == 0 else 0
        ahead_count = len(ahead_commits)

        if ahead_count == 0:
            output.warn(
                f"Branch {branch} has no commits ahead of {upstream} -- "
                f"nothing to push."
            )
            # Still mark as pushed if title was set -- content is on master
            if record:
                tracking.update_status(record, "pushed")
            return True

        if behind_count > 0:
            output.warn(
                f"Branch {branch} has diverged from {upstream}: "
                f"{ahead_count} ahead, {behind_count} behind. "
                f"Will squash and rebase."
            )

        # 4. Pre-squash
        if wt_exists and ahead_count > 1:
            squash_title = title or (record.title if record else None)
            squash_msg = squash_title or f"squash: merge worktree/{worktree_id}"
            print(f"Squashing {ahead_count} commits into one...")
            squashed, squash_reason = git_ops.squash_branch(
                upstream, squash_msg, cwd=worktree_path
            )
            if squashed:
                ahead_count = 1
            elif allow_unsquashed:
                output.warn(
                    "Pre-squash failed -- proceeding with individual commits "
                    "(--allow-unsquashed)."
                )
                if squash_reason:
                    output.warn(f"  Reason: {squash_reason}")
            else:
                # Never silently push unsquashed commits to the shared default
                # branch -- that is irreversible there (issue #783). Abort and
                # leave the worktree with its original commits, unpushed.
                output.err(
                    f"Pre-squash failed for {worktree_id} -- aborting push so "
                    f"the unsquashed commits do not land on "
                    f"{repo.remote}/{repo.default_branch}."
                )
                if squash_reason:
                    output.err(f"  Reason: {squash_reason}")
                output.warn(
                    "Resolve the cause and retry, or pass --allow-unsquashed "
                    "to push the individual commits intentionally."
                )
                # squash_branch already restored the original commits and
                # deleted its backup ref on failure -- do NOT restore again
                # here (refs/pre-squash-backup is repo-global, so a stale
                # backup from a prior run could be wrongly applied).
                if record:
                    tracking.update_status(record, "active")
                return False

        # 5. Rebase
        print(f"Rebasing {branch} onto {upstream}...")
        if not git_ops.rebase(upstream, cwd=worktree_path):
            output.warn("Rebase failed -- aborting and preserving worktree.")
            if git_ops.restore_backup_ref(cwd=worktree_path):
                output.warn("Restored original commits from pre-squash backup.")
            if record:
                tracking.update_status(record, "orphaned")
            return False

        # 6. Validate core files
        from . import validate as val
        plat = cfg.detect_platform()
        hook_cmd = repo.validate_hook.get(plat)

        if hook_cmd:
            print("Running configured validation hook...")
            expanded = [
                c.replace("{work_dir}", worktree_path)
                 .replace("{default_branch}", upstream)
                for c in hook_cmd
            ]
            import subprocess
            result = subprocess.run(
                expanded, capture_output=True, text=True,
            )
            if result.returncode != 0:
                output.warn("Core validation failed. Worktree preserved for fixes.")
                print(result.stdout)
                if record:
                    tracking.update_status(record, "active")
                return False
        elif repo.validate_paths:
            print("Checking for core infrastructure changes...")
            failures = val.validate_files(
                worktree_path,
                default_branch=upstream,
                validate_paths=repo.validate_paths,
            )
            if failures:
                output.warn("Core validation failed. Worktree preserved for fixes.")
                if record:
                    tracking.update_status(record, "active")
                return False
        else:
            validate_script = Path(worktree_path) / "tools" / "worktree" / "validate-core.ps1"
            if validate_script.exists():
                print("Checking for core infrastructure changes (legacy)...")
                import subprocess
                result = subprocess.run(
                    ["pwsh.exe", "-NoProfile", "-File", str(validate_script),
                     "-WorktreePath", worktree_path, "-DefaultBranch", upstream],
                    capture_output=True, text=True,
                )
                if result.returncode != 0:
                    output.warn("Core validation failed. Worktree preserved for fixes.")
                    print(result.stdout)
                    if record:
                        tracking.update_status(record, "active")
                    return False

        # 7. Anchor hygiene
        from . import anchor_hygiene
        anchor_report = anchor_hygiene.check_anchor(anchor)
        if anchor_report.has_dirty_files:
            output.err(
                f"Anchor repo has {len(anchor_report.dirty_files)} uncommitted "
                f"file(s). Commit, stash, or discard them before pushing."
            )
            for f in anchor_report.dirty_files[:5]:
                print(f"       {f}")
            if len(anchor_report.dirty_files) > 5:
                print(f"       ... and {len(anchor_report.dirty_files) - 5} more")
            return False
        if anchor_report.has_stash:
            output.warn(
                f"Anchor repo has {len(anchor_report.stash_entries)} stash "
                f"entr{'y' if len(anchor_report.stash_entries) == 1 else 'ies'} "
                f"-- consider rescuing this work."
            )
            for entry in anchor_report.stash_entries[:3]:
                print(f"       {entry}")

        # 8. Update local default branch and merge
        print(f"Updating local {repo.default_branch}...")
        git_ops.checkout(repo.default_branch, cwd=anchor)
        if not git_ops.merge_ff(f"{repo.remote}/{repo.default_branch}", cwd=anchor):
            output.err(f"Failed to fast-forward local {repo.default_branch}")
            if record:
                tracking.update_status(record, "orphaned")
            return False

        print(f"Merging {branch} into {repo.default_branch}...")
        if not git_ops.merge_ff(branch, cwd=anchor):
            head_sha = git_ops.git("rev-parse", "HEAD", cwd=anchor, check=False).stdout.strip()[:8]
            branch_sha = git_ops.git(
                "rev-parse", branch, cwd=anchor, check=False
            ).stdout.strip()[:8]
            output.err(
                f"Fast-forward merge failed unexpectedly "
                f"(master={head_sha}, {branch}={branch_sha}). "
                f"Worktree preserved for manual resolution."
            )
            if record:
                tracking.update_status(record, "orphaned")
            return False

        # 9. Push with retry
        max_retries = 3
        pushed = False
        for attempt in range(1, max_retries + 1):
            print(f"Pushing to {repo.remote} (attempt {attempt}/{max_retries})...")
            if git_ops.push(repo.remote, repo.default_branch, cwd=anchor):
                pushed = True
                break
            if attempt < max_retries:
                output.warn("Push rejected -- fetching and retrying...")
                git_ops.fetch(repo.remote, cwd=anchor)
                if not git_ops.rebase(upstream, cwd=anchor):
                    output.err("Rebase after push rejection failed")
                    if record:
                        tracking.update_status(record, "orphaned")
                    return False

        if not pushed:
            output.err(f"Push failed after {max_retries} attempts")
            if record:
                tracking.update_status(record, "orphaned")
            return False

        # 10. Update tracking status
        if record:
            tracking.update_status(record, "pushed")

        activity.log_event(
            "changes_pushed",
            worktree_id=worktree_id,
            branch=branch,
        )

        # Clean up pre-squash backup ref
        if wt_exists:
            git_ops.delete_backup_ref(cwd=worktree_path)

        output.ok(
            f"Worktree {worktree_id} pushed to "
            f"{repo.remote}/{repo.default_branch}. "
            f"Run 'agent-worktrees finalize' to clean up."
        )
        return True

    except Exception as e:
        output.err(f"Push failed: {e}")
        output.warn(f"Worktree preserved at {worktree_path} for manual resolution.")
        if Path(worktree_path).exists():
            if git_ops.restore_backup_ref(cwd=worktree_path):
                output.warn("Restored original commits from pre-squash backup.")
        if record:
            tracking.update_status(record, "orphaned")
        return False
    finally:
        lock.release()


def _is_content_on_upstream(
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


def _reconcile_merged_pointers(
    repo,
    worktree_path: str,
    anchor: str,
    branch: str,
) -> None:
    """Align local branch pointers with origin after a finalize (#1106).

    Once a worktree's content is confirmed on ``origin/<default>`` (the PR
    merged, or direct work pushed), this leaves the local refs reconciled so
    the picker stops rendering a merged worktree as ``↑ahead↓behind``:

    1. Fast-forward the anchor's local default branch to ``origin/<default>``.
    2. Realign the worktree base branch (``worktree/<id>``) with the origin
       tip.  When HEAD is elsewhere (e.g. checked out on a feature branch),
       ``worktree/<id>`` is a free pointer moved with ``branch -f`` in the
       anchor.  When HEAD *is* ``worktree/<id>`` (the #1804 default -- create-pr
       now returns HEAD there), it is the live checkout, so fast-forward it in
       place in the worktree instead (clean, non-ahead, strictly-behind only).

    Best-effort and non-destructive: fast-forward / pointer-reset only, never
    on a dirty tree, never discarding unmerged commits.  All failures are
    swallowed -- reconciliation is a tidiness pass, not a correctness gate.
    """
    upstream = f"{repo.remote}/{repo.default_branch}"
    if not git_ops.ref_exists(upstream, cwd=anchor):
        return

    # 1. Fast-forward the anchor's checked-out default branch to origin.
    try:
        if (
            git_ops._get_current_branch_safe(anchor) == repo.default_branch
            and git_ops.is_clean(cwd=anchor)
        ):
            git_ops.merge_ff(upstream, cwd=anchor)
    except Exception:
        pass

    # 2. Realign worktree/<id> with the origin tip when safe:
    #    - HEAD is elsewhere (the free-pointer case, e.g. a feature-branch
    #      checkout): move the pointer with `branch -f` in the anchor.
    #    - HEAD *is* worktree/<id> (the #1804 default -- create-pr now returns
    #      HEAD here): fast-forward it in place in the worktree. This only
    #      advances a clean, non-ahead, strictly-behind branch, so it never
    #      rebases, merges, or discards local commits.
    try:
        wt_head = (
            git_ops._get_current_branch_safe(worktree_path)
            if Path(worktree_path).exists()
            else None
        )
        if wt_head == branch:
            git_ops.fast_forward_worktree(
                worktree_path, remote=repo.remote,
                default_branch=repo.default_branch, do_fetch=True,
            )
        elif _is_content_on_upstream(branch, upstream, cwd=anchor):
            up_sha = git_ops.git(
                "rev-parse", upstream, cwd=anchor, check=False
            ).stdout.strip()
            if up_sha:
                git_ops.git(
                    "branch", "-f", branch, up_sha, cwd=anchor, check=False
                )
    except Exception:
        pass


def _push_changes_pr(
    worktree_id: str,
    config: Config,
    record: tracking.WorktreeRecord,
    *,
    dry_run: bool = False,
) -> bool:
    """PR-mode push-changes: update the feature branch, not master.

    Runs the rebase chain (worktree/{id} onto upstream, feature onto
    worktree/{id}) and force-with-lease pushes the *feature* branch.  Never
    touches master or the worktree base branch on the remote.
    """
    repo = config.default_repo
    remote = repo.remote
    upstream = f"{remote}/{repo.default_branch}"
    wt_branch = f"worktree/{worktree_id}"
    feature = record.pr.branch
    worktree_path = str(Path(repo.worktree_root) / worktree_id)
    lock_path = Path(repo.worktree_root) / ".finalize.lock"

    if not Path(worktree_path).exists():
        output.err(f"Worktree path not found: {worktree_path}")
        return False

    if repo.pr.head_scheme == "refspec":
        return _push_changes_pr_refspec(
            worktree_id, config, record, wt_branch, worktree_path, lock_path,
            dry_run=dry_run,
        )

    head = git_ops._get_current_branch_safe(worktree_path)
    # Accept ANY tracked PR branch as the feature to push (a worktree may carry
    # parallel PRs): prefer the branch matching the current HEAD over the
    # active PR's branch.
    if head and any(p.branch == head for p in record.prs):
        feature = head
    if head != feature:
        output.err(
            f"PR mode: push-changes expects HEAD on a tracked feature branch "
            f"(active: '{feature}'), but it is on '{head}'. Checkout it first "
            f"(`git checkout {feature}`) to push feedback commits directly to "
            f"the PR branch, or re-run create-pr from '{head}' to re-squash new "
            f"work onto it."
        )
        return False

    # The PRRecord this push updates -- the one matching the branch being pushed.
    pushed_pr = next((p for p in record.prs if p.branch == feature), record.pr)

    if not git_ops.is_clean(cwd=worktree_path):
        dirty = git_ops.get_dirty_files(cwd=worktree_path)
        detail = "\n".join(f"    {ln}" for ln in dirty)
        output.err(
            "Working tree has uncommitted changes. Commit them before "
            f"push-changes:\n{detail}"
        )
        return False

    if dry_run:
        print(
            f"[dry-run] Would rebase {wt_branch} onto {upstream}, rebase "
            f"{feature} onto {wt_branch}, then push {feature} to {remote} "
            f"(--force-with-lease)."
        )
        return True

    lock = FinalizeLock(lock_path)
    try:
        lock.acquire()
    except TimeoutError:
        output.err("Timed out waiting for finalization lock.")
        return False

    try:
        print(f"Fetching from {remote}...")
        git_ops.fetch(remote, cwd=worktree_path)

        # Rebase chain: base onto master, then feature onto the updated base.
        if git_ops.ref_exists(upstream, cwd=worktree_path):
            git_ops.checkout(wt_branch, cwd=worktree_path)
            if not git_ops.rebase(upstream, cwd=worktree_path):
                output.err(
                    f"Rebase of {wt_branch} onto {upstream} hit conflicts. "
                    f"Resolve them and retry push-changes."
                )
                git_ops.checkout(feature, cwd=worktree_path)
                return False
            git_ops.checkout(feature, cwd=worktree_path)
            if not git_ops.rebase(wt_branch, cwd=worktree_path):
                output.err(
                    f"Rebase of {feature} onto {wt_branch} hit conflicts. "
                    f"Resolve them and retry push-changes."
                )
                return False

        with hooks.allow_pr_push():
            pushed = git_ops.push(remote, feature, cwd=worktree_path, force_with_lease=True)
        if not pushed:
            output.err(f"Failed to push {feature} to {remote}.")
            if pushed_pr is not None and pushed_pr.state in ("", "creating"):
                tracking.save_record(record)
            return False

        head_sha = git_ops.git(
            "rev-parse", "HEAD", cwd=worktree_path, check=False
        ).stdout.strip()
        if pushed_pr is not None:
            pushed_pr.head_sha = head_sha
            if pushed_pr.state in ("", "creating"):
                pushed_pr.state = "open"
        tracking.save_record(record)

        activity.log_event(
            "pr_changes_pushed", worktree_id=worktree_id, branch=feature,
        )
        output.ok(
            f"Pushed {feature} to {remote} (--force-with-lease). "
            f"The open PR is updated."
        )
        return True
    finally:
        lock.release()


def _push_changes_pr_refspec(
    worktree_id: str,
    config: Config,
    record: tracking.WorktreeRecord,
    wt_branch: str,
    worktree_path: str,
    lock_path: Path,
    *,
    dry_run: bool = False,
) -> bool:
    """Refspec-mode push-changes (#1815): update the PR head ref directly.

    The work lives on ``worktree/<id>`` (the only local branch); the PR head is
    a remote-only ref.  Rebase ``worktree/<id>`` onto upstream -- so it picks up
    the default branch and any feedback commits ride on top -- then push it to
    the PR head ref via a refspec.  No checkout dance; HEAD never leaves
    ``worktree/<id>``.  Never touches master or the base branch on the remote.
    """
    repo = config.default_repo
    remote = repo.remote
    upstream = f"{remote}/{repo.default_branch}"

    head = git_ops._get_current_branch_safe(worktree_path)
    if head != wt_branch:
        output.err(
            f"PR mode (refspec): push-changes updates the PR head from "
            f"'{wt_branch}', but HEAD is on '{head}'. Checkout '{wt_branch}' "
            f"first."
        )
        return False

    # The PR head ref to update is the active (live) PR's branch.
    pushed_pr = record.active_pr() or record.pr
    feature = pushed_pr.branch if (pushed_pr and pushed_pr.branch) else ""
    if not feature:
        output.err("PR mode (refspec): no tracked PR head ref to update.")
        return False

    if not git_ops.is_clean(cwd=worktree_path):
        dirty = git_ops.get_dirty_files(cwd=worktree_path)
        detail = "\n".join(f"    {ln}" for ln in dirty)
        output.err(
            "Working tree has uncommitted changes. Commit them before "
            f"push-changes:\n{detail}"
        )
        return False

    if dry_run:
        print(
            f"[dry-run] Would rebase {wt_branch} onto {upstream}, then push "
            f"{wt_branch}:refs/heads/{feature} to {remote} (--force-with-lease)."
        )
        return True

    lock = FinalizeLock(lock_path)
    try:
        lock.acquire()
    except TimeoutError:
        output.err("Timed out waiting for finalization lock.")
        return False

    try:
        print(f"Fetching from {remote}...")
        git_ops.fetch(remote, cwd=worktree_path)

        # Rebase the worktree branch forward onto the default branch; feedback
        # commits ride on top. HEAD stays on wt_branch throughout.
        if git_ops.ref_exists(upstream, cwd=worktree_path):
            if not git_ops.rebase(upstream, cwd=worktree_path):
                output.err(
                    f"Rebase of {wt_branch} onto {upstream} hit conflicts. "
                    f"Resolve them on '{wt_branch}' and retry push-changes."
                )
                return False

        with hooks.allow_pr_push():
            pushed = git_ops.push(
                remote, f"{wt_branch}:refs/heads/{feature}",
                cwd=worktree_path, force_with_lease=True,
            )
        if not pushed:
            output.err(f"Failed to push {wt_branch} to {remote}/{feature}.")
            if pushed_pr is not None and pushed_pr.state in ("", "creating"):
                tracking.save_record(record)
            return False

        head_sha = git_ops.git(
            "rev-parse", "HEAD", cwd=worktree_path, check=False
        ).stdout.strip()
        if pushed_pr is not None:
            pushed_pr.head_sha = head_sha
            if pushed_pr.state in ("", "creating"):
                pushed_pr.state = "open"
        tracking.save_record(record)

        activity.log_event(
            "pr_changes_pushed", worktree_id=worktree_id, branch=feature,
        )
        output.ok(
            f"Pushed {wt_branch} to {remote}/{feature} (--force-with-lease). "
            f"The open PR is updated."
        )
        return True
    finally:
        lock.release()


def _pr_finalize_precondition(
    record: tracking.WorktreeRecord,
    repo,
    worktree_path: str,
    anchor: str,
) -> tuple[bool, str | None]:
    """Check whether a PR-mode worktree's work is safely upstream.

    Work is safe when the feature branch exists on the remote and the local
    feature branch has no commits that have not been pushed.  Returns
    ``(ok, error_message)``.
    """
    remote = repo.remote
    feature = record.pr.branch
    cwd = worktree_path if Path(worktree_path).exists() else anchor
    upstream = f"{remote}/{repo.default_branch}"

    # #1045: If the feature branch's content is already on origin/<default>
    # (the PR was merged), the work is safely upstream -- regardless of a
    # stale origin/<feature> ref (which lags at the pre-merge head after a
    # squash-merge) or whether the remote feature branch was deleted on merge.
    # Treat as safe so finalize does not false-block a merged PR and send the
    # user to push-changes (which would re-push an already-merged branch).
    if git_ops.ref_exists(upstream, cwd=cwd) and _is_content_on_upstream(
        feature, upstream, cwd=cwd
    ):
        return True, None

    if not git_ops.remote_branch_exists(remote, feature, cwd=cwd):
        return False, (
            f"Feature branch '{feature}' is not on '{remote}'. Run "
            f"'agent-worktrees create-pr' (or push-changes) to push your work "
            f"upstream before finalizing."
        )

    local = git_ops.git("rev-parse", feature, cwd=cwd, check=False)
    remote_ref = git_ops.git("rev-parse", f"{remote}/{feature}", cwd=cwd, check=False)
    if local.returncode == 0 and remote_ref.returncode == 0:
        ahead = git_ops.git(
            "rev-list", "--count", f"{remote}/{feature}..{feature}",
            cwd=cwd, check=False,
        )
        if ahead.returncode == 0 and ahead.stdout.strip() not in ("", "0"):
            return False, (
                f"Feature branch '{feature}' has unpushed commits. Run "
                f"'agent-worktrees push-changes' to update the PR branch, "
                f"then finalize."
            )
    return True, None


def validate_and_finalize(
    worktree_id: str,
    config: Config,
    *,
    dry_run: bool = False,
) -> bool:
    """Validate that worktree content is on upstream, then clean up.

    This is a non-mutating validation step -- it never squashes, rebases,
    or pushes.  If the branch's content is not yet on origin/master, it
    fails with guidance to run push-changes first.

    Args:
        worktree_id: The worktree identifier.
        config: Loaded project configuration.
        dry_run: If True, preview without side effects.

    Returns:
        True on success, False if content is not yet on upstream.
    """
    repo = config.default_repo
    anchor = repo.anchor
    worktree_path = str(Path(repo.worktree_root) / worktree_id)
    branch = f"worktree/{worktree_id}"
    upstream = f"{repo.remote}/{repo.default_branch}"
    lock_path = Path(repo.worktree_root) / ".finalize.lock"

    # Load tracking record
    from . import config as cfg
    yaml_path = cfg.tracking_dir() / f"{worktree_id}.yaml"
    record = None
    if yaml_path.exists():
        try:
            record = tracking.load_record(yaml_path)
        except Exception:
            pass

    wt_exists = Path(worktree_path).exists()
    pr_mode = bool(
        repo.pr.enabled and record and record.pr and record.pr.branch
    )

    if dry_run:
        _dry_run_finalize_preview(
            worktree_id, config, worktree_path, branch, upstream,
        )
        return True

    # Fetch to get current upstream state
    print(f"Fetching from {repo.remote}...")
    git_ops.fetch(repo.remote, cwd=anchor)

    if pr_mode:
        # PR mode: finalize is decoupled from merge. Work is safe to prune as
        # soon as the feature branch is pushed -- the PR may still be open.
        ok, err = _pr_finalize_precondition(record, repo, worktree_path, anchor)
        if not ok:
            output.err(err or "PR finalize precondition not met.")
            return False
        print(
            f"Verified: feature branch '{record.pr.branch}' is safely on "
            f"{repo.remote}. Finalizing this worktree (the PR may still be open)."
        )
    elif wt_exists:
        # Check if the worktree is unused (0 commits, clean tree)
        ahead_commits = git_ops.get_commits_ahead(branch, upstream, cwd=worktree_path)
        is_clean = git_ops.is_clean(cwd=worktree_path)
        if len(ahead_commits) == 0 and is_clean:
            print("No commits and clean tree -- finalizing unused worktree.")
            # Fall through to cleanup
        elif not _is_content_on_upstream(branch, upstream, cwd=worktree_path):
            if repo.pr.required:
                output.err(
                    f"Unmerged work detected on {branch}, and PRs are required "
                    f"for this repo -- it cannot be finalized direct-to-master.\n"
                    f"Land it through a pull request:\n"
                    f"  1. agent-worktrees create-pr --title \"...\"\n"
                    f"  2. open the PR via the '{repo.pr.provider}' provider, "
                    f"then 'agent-worktrees set-pr --url <URL> --number <N>'\n"
                    f"Once the feature branch is pushed, finalize succeeds "
                    f"(the PR may still be open)."
                )
            else:
                output.err(
                    f"Unmerged work detected on {branch}. "
                    f"Run 'agent-worktrees push-changes' to push your changes "
                    f"to {repo.remote}/{repo.default_branch} first, "
                    f"then retry 'agent-worktrees finalize'."
                )
            return False
        else:
            print(f"Verified: all content from {branch} is on {upstream}.")
    else:
        # Worktree directory gone -- check if branch content is on upstream
        # from the anchor repo
        branch_exists = git_ops.git(
            "rev-parse", "--verify", branch, cwd=anchor, check=False,
        ).returncode == 0
        if branch_exists and not _is_content_on_upstream(branch, upstream, cwd=anchor):
            output.err(
                f"Unmerged work detected on {branch}. "
                f"Cannot finalize -- content is not on "
                f"{repo.remote}/{repo.default_branch}."
            )
            return False

    # Acquire lock for cleanup
    lock = FinalizeLock(lock_path)
    try:
        lock.acquire()
    except TimeoutError:
        output.err("Timed out waiting for finalization lock.")
        return False

    try:
        # Cleanup -- remove worktree and branch
        inside_worktree = git_ops.is_cwd_inside(worktree_path)
        has_live_session = _has_live_session(worktree_path)

        # Reconcile local branch pointers with origin now that the content is
        # verified upstream, so a merged-but-not-yet-cleaned worktree stops
        # rendering as diverged in the picker (#1106).
        _reconcile_merged_pointers(repo, worktree_path, anchor, branch)

        if inside_worktree or has_live_session:
            reason = (
                "this shell is running inside the worktree" if inside_worktree
                else "a live Copilot session is still using the worktree"
            )
            output.ok(
                f"Finalized: all content from {branch} is on "
                f"{repo.remote}/{repo.default_branch}, so this worktree is "
                f"safe to prune."
            )
            output.info(
                f"Leaving the worktree directory and branch in place because "
                f"{reason}. Finalize never deletes the git branch or the "
                f"folder of an active worktree -- that's expected, not a "
                f"failure. They'll be removed by 'agent-worktrees cleanup' "
                f"once the session ends (this is the normal outcome when you "
                f"finalize from inside the session)."
            )
            activity.log_event(
                "finalize_skipped_removal",
                worktree_id=worktree_id,
                branch=branch,
                reason="inside_worktree" if inside_worktree else "live_session",
            )
        else:
            print("Removing worktree...")
            # Tear down the mux session and terminate any process still rooted
            # in the worktree before removing it, so directory locks don't leave
            # an empty shell behind (issue dotfiles#139).
            sessions.kill_tmux_session(worktree_id)
            try:
                killed = procs.terminate_processes_under(worktree_path)
            except Exception:
                killed = []
            if killed:
                names = ", ".join(
                    f"{k['name'] or '?'}({k['pid']})" for k in killed if k["killed"])
                if names:
                    output.info(f"Terminated lingering process(es): {names}")

            if not git_ops.remove_worktree(anchor, worktree_path):
                output.warn("Could not remove worktree via git -- forcing directory removal.")

            print(f"Removing branch {branch}...")
            if not git_ops.delete_branch(branch, cwd=anchor):
                output.warn(f"Could not delete branch {branch} (may already be gone).")

            if pr_mode and record.prs:
                # Remove every tracked PR's local feature branch (serial +
                # parallel); the remote branches are left intact as PR backing.
                seen: set[str] = set()
                for pr in record.prs:
                    if not pr.branch or pr.branch in seen:
                        continue
                    seen.add(pr.branch)
                    print(f"Removing local feature branch {pr.branch}...")
                    git_ops.delete_branch(pr.branch, cwd=anchor, force=True)
                    output.info(
                        f"Remote feature branch '{pr.branch}' left intact on "
                        f"{repo.remote} -- it backs the PR and is the recovery source."
                    )

            wt_dir = Path(worktree_path)
            if wt_dir.exists():
                for attempt in range(4):
                    shutil.rmtree(wt_dir, ignore_errors=True)
                    if not wt_dir.exists():
                        break
                    time.sleep(0.25 * (attempt + 1))
                if wt_dir.exists():
                    output.warn(f"Directory still present after cleanup: {wt_dir}")

            git_ops.prune_worktrees(cwd=anchor)

        # Merge permissions
        merged = permissions.merge_permissions(anchor, worktree_path)
        if merged:
            for m in merged:
                print(f"  Merged new permission: {m}")
            print("Permissions merged back to anchor and worktree entry removed.")

        if permissions.remove_trusted_folder(worktree_path):
            print("Removed worktree path from trusted_folders.")

        # Update tracking
        if record:
            tracking.update_status(record, "finalized")

        activity.log_event(
            "worktree_finalized",
            worktree_id=worktree_id,
            branch=branch,
            removed=not (inside_worktree or has_live_session),
        )

        output.ok(f"Worktree {worktree_id} finalized.")
        return True

    except Exception as e:
        output.err(f"Finalization cleanup failed: {e}")
        return False
    finally:
        lock.release()


# Keep finalize() as a backward-compatible wrapper that runs both phases.
def finalize(
    worktree_id: str,
    config: Config,
    *,
    dry_run: bool = False,
) -> bool:
    """Legacy wrapper -- runs validate_and_finalize only.

    This no longer pushes changes. Use push_changes() + validate_and_finalize()
    for the full two-phase flow.
    """
    return validate_and_finalize(worktree_id, config, dry_run=dry_run)


def _dry_run_push_preview(
    worktree_id: str,
    config: Config,
    worktree_path: str,
    branch: str,
    upstream: str,
    lock_path: Path,
) -> None:
    """Show what push-changes would do without side effects."""
    repo = config.default_repo

    print()
    print(f"Push-changes plan for worktree {worktree_id}:")
    output.dry_run(f"Would acquire lock: {lock_path}")

    try:
        commits = git_ops.get_commits_ahead(branch, upstream, cwd=worktree_path)
        if commits:
            output.dry_run(f"Worktree has {len(commits)} commit(s) to push:")
            for c in commits[:5]:
                print(f"       {c}")
            if len(commits) > 5:
                print(f"       ... and {len(commits) - 5} more")
            if len(commits) > 1:
                output.dry_run(f"Would squash {len(commits)} commits into one before rebase")
        else:
            output.dry_run(f"Worktree has no commits ahead of {upstream}")
    except Exception:
        output.dry_run("Could not inspect commits (worktree may be gone)")

    output.dry_run(f"Would fetch from {repo.remote}")
    output.dry_run(f"Would squash and rebase onto {upstream}")
    output.dry_run("Would check anchor repo for uncommitted work (blocks if dirty)")
    output.dry_run(f"Would fast-forward merge into local {repo.default_branch}")
    output.dry_run(f"Would push {repo.default_branch} to {repo.remote}")
    output.dry_run("Would update tracking status to 'pushed'")
    output.dry_run("Would release lock")
    print()
    output.ok("Dry run complete -- no changes made")


def _dry_run_finalize_preview(
    worktree_id: str,
    config: Config,
    worktree_path: str,
    branch: str,
    upstream: str,
) -> None:
    """Show what finalize would do without side effects."""
    repo = config.default_repo

    print()
    print(f"Finalization plan for worktree {worktree_id}:")
    output.dry_run(f"Would fetch from {repo.remote}")
    output.dry_run(f"Would validate that {branch} content is on {upstream}")
    output.dry_run(
        f"Would remove worktree directory and branch ONLY if idle "
        f"(no live session / not inside it): {worktree_path}"
    )
    output.dry_run("Would merge worktree permissions back to anchor")
    output.dry_run("Would remove worktree path from trusted_folders")
    output.dry_run("Would update worktree YAML status: finalized")
    print()
    output.ok("Dry run complete -- no changes made")



