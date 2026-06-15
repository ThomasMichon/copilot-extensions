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

from . import activity, git_ops, output, permissions, sessions, tracking
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
            if not git_ops.squash_branch(upstream, squash_msg, cwd=worktree_path):
                output.warn("Pre-squash failed -- proceeding with individual commits.")
            else:
                ahead_count = 1

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

    if dry_run:
        _dry_run_finalize_preview(
            worktree_id, config, worktree_path, branch, upstream,
        )
        return True

    # Fetch to get current upstream state
    print(f"Fetching from {repo.remote}...")
    git_ops.fetch(repo.remote, cwd=anchor)

    # Check if the worktree is unused (0 commits, clean tree)
    if wt_exists:
        ahead_commits = git_ops.get_commits_ahead(branch, upstream, cwd=worktree_path)
        is_clean = git_ops.is_clean(cwd=worktree_path)
        if len(ahead_commits) == 0 and is_clean:
            print("No commits and clean tree -- finalizing unused worktree.")
            # Fall through to cleanup
        elif not _is_content_on_upstream(branch, upstream, cwd=worktree_path):
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
            if not git_ops.remove_worktree(anchor, worktree_path):
                output.warn("Could not remove worktree via git -- forcing directory removal.")

            print(f"Removing branch {branch}...")
            if not git_ops.delete_branch(branch, cwd=anchor):
                output.warn(f"Could not delete branch {branch} (may already be gone).")

            wt_dir = Path(worktree_path)
            if wt_dir.exists():
                shutil.rmtree(wt_dir, ignore_errors=True)
                if wt_dir.exists():
                    output.warn(f"Directory still present after cleanup: {wt_dir}")

            git_ops.prune_worktrees(cwd=anchor)
            sessions.kill_tmux_session(worktree_id)

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



