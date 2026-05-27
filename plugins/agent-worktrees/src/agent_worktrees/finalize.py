"""Finalization flow — squash, rebase, merge, push, cleanup with locking.

Orchestrates the full worktree finalization lifecycle:
1. Acquire file-based lock
2. Fetch from remote
3. Pre-squash all worktree commits into one
4. Rebase the single commit onto upstream
5. Fast-forward merge into default branch
6. Push with retry
7. Remove worktree and branch
8. Merge permissions back to anchor
9. Update tracking YAML → finalized
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
from pathlib import Path

from . import git_ops, output, permissions, sessions, tracking
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
                output.warn(f"Stale lock detected (age: {int(age)}s) — breaking.")
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


def finalize(
    worktree_id: str,
    config: Config,
    *,
    dry_run: bool = False,
) -> bool:
    """Run the full finalization flow for a worktree.

    Args:
        worktree_id: The worktree identifier.
        config: Loaded project configuration.
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

    # Guard against branch drift — if the worktree's HEAD is on a
    # different branch (e.g. a feature branch), refuse to finalize.
    # Squashing/rebasing/deleting an unexpected branch is dangerous.
    if Path(worktree_path).exists():
        actual = git_ops._get_current_branch_safe(worktree_path)
        if actual and actual != branch:
            output.err(
                f"Branch drift detected: worktree HEAD is on '{actual}', "
                f"but finalization expects '{branch}'. "
                f"Switch back to '{branch}' or handle the feature branch "
                f"manually before finalizing."
            )
            return False

    if dry_run:
        _dry_run_preview(
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

        # 1b. Dirty check — refuse finalization with uncommitted changes
        wt_exists = Path(worktree_path).exists()
        if wt_exists and not git_ops.is_clean(cwd=worktree_path):
            output.err(
                "Working tree has uncommitted changes. "
                "Commit or stash them before finalizing."
            )
            return False

        # 1c. Divergence check — inform before squash/rebase
        ahead_commits = git_ops.get_commits_ahead(branch, upstream, cwd=worktree_path)
        behind_r = git_ops.git(
            "rev-list", "--count", f"{branch}..{upstream}",
            cwd=worktree_path, check=False,
        )
        behind_count = int(behind_r.stdout.strip()) if behind_r.returncode == 0 else 0
        ahead_count = len(ahead_commits)

        if ahead_count > 0 and behind_count > 0:
            output.warn(
                f"Branch {branch} has diverged from {upstream}: "
                f"{ahead_count} ahead, {behind_count} behind. "
                f"Will squash and rebase."
            )
        elif ahead_count == 0:
            output.warn(
                f"Branch {branch} has no commits ahead of {upstream} — "
                f"nothing to merge."
            )

        # 2. Pre-squash — collapse all worktree commits into one
        if wt_exists and ahead_count > 1:
            title = record.title if record else None
            squash_msg = title or f"squash: merge worktree/{worktree_id}"
            print(f"Squashing {ahead_count} commits into one...")
            if not git_ops.squash_branch(upstream, squash_msg, cwd=worktree_path):
                output.warn("Pre-squash failed — proceeding with individual commits.")
            else:
                ahead_count = 1

        # 3. Rebase
        print(f"Rebasing {branch} onto {upstream}...")
        if not git_ops.rebase(upstream, cwd=worktree_path):
            output.warn("Rebase failed — aborting and preserving worktree.")
            # Restore original commits if we squashed
            if git_ops.restore_backup_ref(cwd=worktree_path):
                output.warn("Restored original commits from pre-squash backup.")
            if record:
                tracking.update_status(record, "orphaned")
            return False

        # 4. Validate core files
        #    Uses config-driven validate_hook if set, otherwise falls back
        #    to the built-in Python validator with config-driven paths.
        from . import validate as val
        plat = cfg.detect_platform()
        hook_cmd = repo.validate_hook.get(plat)

        if hook_cmd:
            # Config provides an external validation command
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
            # Config provides paths — use built-in Python validator
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
            # No validation configured — check for legacy script
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

        # 5. Update local default branch and merge
        print(f"Updating local {repo.default_branch}...")
        git_ops.checkout(repo.default_branch, cwd=anchor)
        if not git_ops.merge_ff(f"{repo.remote}/{repo.default_branch}", cwd=anchor):
            output.err(f"Failed to fast-forward local {repo.default_branch}")
            if record:
                tracking.update_status(record, "orphaned")
            return False

        print(f"Merging {branch} into {repo.default_branch}...")
        if not git_ops.merge_ff(branch, cwd=anchor):
            # After pre-squash + rebase this should not happen.
            head_sha = git_ops.git("rev-parse", "HEAD", cwd=anchor, check=False).stdout.strip()[:8]
            branch_sha = git_ops.git("rev-parse", branch, cwd=anchor, check=False).stdout.strip()[:8]
            output.err(
                f"Fast-forward merge failed unexpectedly "
                f"(master={head_sha}, {branch}={branch_sha}). "
                f"Worktree preserved for manual resolution."
            )
            if record:
                tracking.update_status(record, "orphaned")
            return False

        # 6. Push with retry
        max_retries = 3
        pushed = False
        for attempt in range(1, max_retries + 1):
            print(f"Pushing to {repo.remote} (attempt {attempt}/{max_retries})...")
            if git_ops.push(repo.remote, repo.default_branch, cwd=anchor):
                pushed = True
                break
            if attempt < max_retries:
                output.warn("Push rejected — fetching and retrying...")
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

        # 7. Cleanup — remove worktree and branch (only if not actively in use)
        #
        # Safety rule: if we are running *inside* the target worktree or a
        # live Copilot session owns it, we must not delete the directory or
        # branch.  Everything else (push, permissions, tracking) still
        # proceeds — the worktree becomes inert content already on master.
        inside_worktree = git_ops.is_cwd_inside(worktree_path)
        has_live_session = _has_live_session(worktree_path)

        if inside_worktree or has_live_session:
            reason = "running inside this worktree" if inside_worktree else "live Copilot session detected"
            output.warn(
                f"Skipping worktree/branch removal ({reason}). "
                f"Content is already on {repo.remote}/{repo.default_branch}. "
                f"Run cleanup after the session ends to remove the directory and branch."
            )
        else:
            print("Removing worktree...")
            if not git_ops.remove_worktree(anchor, worktree_path):
                output.warn("Could not remove worktree via git — forcing directory removal.")

            print(f"Removing branch {branch}...")
            if not git_ops.delete_branch(branch, cwd=anchor):
                output.warn(f"Could not delete branch {branch} (may already be gone).")

            # Remove directory if still present
            wt_dir = Path(worktree_path)
            if wt_dir.exists():
                shutil.rmtree(wt_dir, ignore_errors=True)
                if wt_dir.exists():
                    output.warn(f"Directory still present after cleanup: {wt_dir}")

            # Prune stale worktree entries
            git_ops.prune_worktrees(cwd=anchor)

            # Kill any associated tmux session
            sessions.kill_tmux_session(worktree_id)

        # 8. Merge permissions
        merged = permissions.merge_permissions(anchor, worktree_path)
        if merged:
            for m in merged:
                print(f"  Merged new permission: {m}")
            print("Permissions merged back to anchor and worktree entry removed.")

        # Remove worktree from trusted_folders
        if permissions.remove_trusted_folder(worktree_path):
            print("Removed worktree path from trusted_folders.")

        # 9. Update tracking
        if record:
            tracking.update_status(record, "finalized")

        # Clean up pre-squash backup ref
        if wt_exists:
            git_ops.delete_backup_ref(cwd=worktree_path)

        output.ok(f"Worktree {worktree_id} finalized and pushed to {repo.remote}.")
        return True

    except Exception as e:
        output.err(f"Finalization failed: {e}")
        output.warn(f"Worktree preserved at {worktree_path} for manual resolution.")
        # Restore original commits if we squashed
        if Path(worktree_path).exists():
            if git_ops.restore_backup_ref(cwd=worktree_path):
                output.warn("Restored original commits from pre-squash backup.")
        if record:
            tracking.update_status(record, "orphaned")
        return False
    finally:
        lock.release()


def _dry_run_preview(
    worktree_id: str,
    config: Config,
    worktree_path: str,
    branch: str,
    upstream: str,
    lock_path: Path,
) -> None:
    """Show what finalization would do without side effects."""
    repo = config.default_repo

    print()
    print(f"Finalization plan for worktree {worktree_id}:")
    output.dry_run(f"Would acquire lock: {lock_path}")

    # Show commits on branch
    try:
        commits = git_ops.get_commits_ahead(branch, upstream, cwd=worktree_path)
        if commits:
            output.dry_run(f"Worktree has {len(commits)} commit(s) to merge:")
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
    output.dry_run(f"Would fast-forward merge into local {repo.default_branch}")
    output.dry_run(f"Would push {repo.default_branch} to {repo.remote}")
    output.dry_run(f"Would remove worktree: {worktree_path}")
    output.dry_run(f"Would delete branch: {branch}")
    output.dry_run("Would merge worktree permissions back to anchor")
    output.dry_run("Would remove worktree path from trusted_folders")
    output.dry_run("Would update worktree YAML status: finalized")
    output.dry_run("Would release lock")
    print()
    output.ok("Dry run complete — no changes made")



