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
import sys
import time
import uuid
from pathlib import Path

from . import (
    activity,
    git_ops,
    hooks,
    locks,
    obligations,
    output,
    permissions,
    procs,
    sessions,
    tracking,
)
from .config import Config


def _has_live_session(record) -> bool:
    """Return True if this worktree has a live bound Copilot session.

    Resolved by exact session id (the worktree's session registry) plus its mux
    session -- never by sweeping session-state (invariant:
    ``docs/patterns/session-state-access.md``). An untracked worktree
    (``record is None``) reports not-live; the ``inside_worktree`` gate at the
    call site still guards the in-use case.
    """
    if record is None:
        return False
    if getattr(record, "session_backend_opaque", False):
        return True
    backend = getattr(record, "session_backend", None)
    if backend is not None and backend.state in {"active", "unknown"}:
        return True
    if sessions.worktree_has_live_session(record):
        return True
    wt_id = getattr(record, "worktree_id", None)
    return bool(wt_id and sessions.has_mux_session(wt_id))


class FinalizeLock:
    """Simple file-based lock with timeout and stale detection."""

    def __init__(
        self,
        lock_path: Path,
        timeout: float = 120,
        *,
        stale_after: float | None = None,
    ) -> None:
        self.lock_path = lock_path
        self.timeout = timeout
        self.stale_after = timeout if stale_after is None else stale_after
        self.owner_pid = os.getpid()
        self.owner_start_time = locks.process_start_time(self.owner_pid) or ""
        self.token = (
            f"{self.owner_pid}:{self.owner_start_time}:{uuid.uuid4().hex}"
        )
        self.held = False
        self.guard_path = lock_path.with_name(f"{lock_path.name}.guard")

    def acquire(self) -> None:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        start = time.monotonic()

        while True:
            remaining = self.timeout - (time.monotonic() - start)
            if remaining <= 0:
                raise TimeoutError("Timed out waiting for finalization lock.")
            try:
                with tracking._RecordLock(
                    self.guard_path,
                    timeout=max(0.01, remaining),
                    require_sidecar=True,
                ):
                    if not self.lock_path.exists():
                        fd = os.open(
                            self.lock_path,
                            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                            0o600,
                        )
                        try:
                            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                                handle.write(self.token)
                        except BaseException:
                            try:
                                self.lock_path.unlink()
                            except OSError:
                                pass
                            raise
                        self.held = True
                        return
                    try:
                        observed = self.lock_path.read_text(encoding="utf-8").strip()
                        age = time.time() - self.lock_path.stat().st_mtime
                    except OSError:
                        if time.monotonic() - start > self.timeout:
                            raise TimeoutError(
                                "Timed out waiting for finalization lock."
                            )
                        time.sleep(min(0.1, max(0.01, self.timeout)))
                        continue
                    token_parts = observed.split(":")
                    owner_text = token_parts[0]
                    owner_start_time = (
                        token_parts[1]
                        if len(token_parts) >= 3 and token_parts[1].isdigit()
                        else ""
                    )
                    try:
                        owner_pid = int(owner_text)
                    except ValueError:
                        owner_pid = 0
                    if owner_pid > 0:
                        stale = not locks.pid_alive(owner_pid)
                        if not stale and owner_start_time:
                            current_start_time = locks.process_start_time(owner_pid)
                            stale = bool(
                                current_start_time
                                and current_start_time != owner_start_time
                            )
                        elif not stale and age > self.stale_after:
                            stale = True
                    else:
                        stale = age > self.stale_after
                    if stale:
                        try:
                            output.warn(
                                f"Stale lock detected (age: {int(age)}s) -- breaking."
                            )
                            self.lock_path.unlink()
                            continue
                        except OSError:
                            if time.monotonic() - start > self.timeout:
                                raise TimeoutError(
                                    "Timed out waiting for finalization lock."
                                )
                            time.sleep(min(0.1, max(0.01, self.timeout)))
                            continue
            except TimeoutError:
                pass

            if time.monotonic() - start > self.timeout:
                raise TimeoutError("Timed out waiting for finalization lock.")

            print("Waiting for finalization lock...", file=sys.stderr)
            time.sleep(min(2.0, max(0.01, self.timeout)))

    def release(self) -> None:
        if not self.held:
            return
        try:
            with tracking._RecordLock(
                self.guard_path,
                timeout=min(2.0, max(0.01, self.timeout)),
                require_sidecar=True,
            ):
                try:
                    current = self.lock_path.read_text(encoding="utf-8").strip()
                    if current == self.token:
                        self.lock_path.unlink()
                except OSError:
                    pass
        except (OSError, TimeoutError):
            pass
        finally:
            self.held = False

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
    worktree_path = tracking.resolve_worktree_path(worktree_id, repo.worktree_root)
    upstream = f"{repo.remote}/{repo.default_branch}"
    lock_path = Path(repo.worktree_root) / ".finalize.lock"

    # Load tracking record
    from . import config as cfg
    yaml_path = cfg.tracking_dir() / f"{worktree_id}.yaml"
    record = None
    if yaml_path.exists():
        try:
            record = tracking.load_record(yaml_path)
        except Exception as exc:
            output.err(
                f"Cannot finalize {worktree_id}: its existing claim ledger "
                f"is unreadable ({exc}). Repair the tracking record first; "
                "creator ownership is preserved."
            )
            return False
    branch = _worktree_branch(record, worktree_id)

    # Set title early so it survives even if push fails
    if title and record:
        # Foreground RMW (#4547): persist the title under the blocking record
        # lock. `record` was loaded unlocked above (it drives the whole push
        # flow), so reload fresh inside the lock, apply just the title, and save
        # -- then continue on the fresh snapshot. The window holds no I/O; the
        # heavy fetch/push below runs AFTER the lock is released.
        new_title = title.replace("\n", " ").strip()
        with tracking._RecordLock(yaml_path):
            record = tracking.load_record(yaml_path)
            record.title = new_title
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
            res = git_ops.push(repo.remote, repo.default_branch, cwd=anchor)
            if res:
                pushed = True
                break
            # Surface git's REAL stderr instead of a generic "rejected" (#993):
            # a pre-push hook decline (e.g. the version-consistency gate), an
            # auth 403, or a protected-branch block is invisible otherwise.
            if res.stderr:
                output.err(res.stderr.strip())
            # Only a non-fast-forward race is fixed by fetch+rebase+retry.
            # Everything else recurs identically -- fail fast with the real
            # reason above rather than burning three doomed attempts.
            if not res.retryable:
                output.err(
                    "Push failed and is not a fast-forward race (see the git "
                    "error above) -- not retrying. Resolve the reported cause "
                    "and re-run 'agent-worktrees push-changes'."
                )
                if record:
                    tracking.update_status(record, "orphaned")
                return False
            if attempt < max_retries:
                output.warn("Non-fast-forward -- fetching and retrying...")
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
       returns HEAD there), it is the live checkout, so fast-forward it in place
       (clean, non-ahead, strictly-behind only); under the refspec scheme
       (#1815) the branch sits ahead while the PR is open, so once merged it is
       *diverged* and, when its content is confirmed on upstream, it is realigned
       to the tip in place (the ahead commit is the now-merged squash).

    Best-effort and non-destructive: fast-forward / pointer-realign only, never
    on a dirty tree, never discarding unmerged commits (a realign only happens
    once the branch's content is confirmed on upstream).  All failures are
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
    #    - HEAD *is* worktree/<id> (the #1804 default -- create-pr returns HEAD
    #      here): fast-forward it in place. A plain FF only advances a clean,
    #      non-ahead, strictly-behind branch. Under the refspec scheme (#1815)
    #      the worktree branch legitimately sits AHEAD of master while the PR is
    #      open, so once the PR squash-merges it is ahead+behind (diverged) and
    #      the FF no-ops; when its content is then confirmed on upstream (merged)
    #      we realign to the tip -- the "ahead" commit is the now-merged squash,
    #      so no unmerged work is discarded. Guarded on a clean tree.
    try:
        wt_head = (
            git_ops._get_current_branch_safe(worktree_path)
            if Path(worktree_path).exists()
            else None
        )
        if wt_head == branch:
            ff = git_ops.fast_forward_worktree(
                worktree_path, remote=repo.remote,
                default_branch=repo.default_branch, do_fetch=True,
            )
            if (
                not ff.updated
                and git_ops.is_clean(cwd=worktree_path)
                and _is_content_on_upstream(branch, upstream, cwd=worktree_path)
            ):
                # Prefer a non-destructive rebase: it drops already-merged
                # commits while PRESERVING any commit that diverges from
                # upstream -- e.g. a post-merge revert that nets to the
                # merge-base, the #2854 blind spot the blob check above misses.
                # Only hard-reset when the rebase cannot proceed (the
                # squash-merge phantom-conflict); the content is already
                # confirmed on upstream, so that reset is lossless.
                if not git_ops.rebase(upstream, cwd=worktree_path):
                    up_sha = git_ops.git(
                        "rev-parse", upstream, cwd=worktree_path, check=False
                    ).stdout.strip()
                    if up_sha:
                        git_ops.git(
                            "reset", "--hard", up_sha, "--quiet",
                            cwd=worktree_path, check=False,
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
    """PR-mode push-changes: update the PR head branch, not master.

    Feedback commits ride on ``worktree/{id}`` (create-pr leaves HEAD there at
    the squashed commit, #1804).  Rebase ``worktree/{id}`` onto upstream,
    snapshot the active PR's feature branch to its tip, and force-with-lease
    push that branch.  Mirrors the refspec push-changes; only the publish step
    differs (a named ``feature/`` branch vs a refspec to ``pr/<slug>``).  A
    worktree still checked out on a tracked feature branch (legacy flow) is
    accepted too, and that branch is rebased + pushed as-is.  Never touches
    master or the worktree base branch on the remote.
    """
    repo = config.default_repo
    remote = repo.remote
    upstream = f"{remote}/{repo.default_branch}"
    wt_branch = f"worktree/{worktree_id}"
    feature = record.pr.branch
    worktree_path = tracking.resolve_worktree_path(worktree_id, repo.worktree_root)
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
    on_wt = head == wt_branch
    on_feature = bool(head and any(p.branch == head for p in record.prs))
    if not (on_wt or on_feature):
        output.err(
            f"PR mode: push-changes expects HEAD on '{wt_branch}' (feedback "
            f"commits ride on the worktree branch) or on a tracked feature "
            f"branch, but it is on '{head}'. Checkout '{wt_branch}' first."
        )
        return False

    if on_wt:
        # New model: the active PR's feature branch is (re)snapshotted from
        # worktree/<id>'s tip.
        pushed_pr = record.active_pr() or record.pr
        feature = pushed_pr.branch if (pushed_pr and pushed_pr.branch) else feature
    else:
        # Legacy: HEAD is on a tracked feature branch -- push that one directly.
        feature = head
        pushed_pr = next((p for p in record.prs if p.branch == feature), record.pr)

    if not feature:
        output.err("PR mode: no tracked PR feature branch to update.")
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
        if on_wt:
            print(
                f"[dry-run] Would rebase {wt_branch} onto {upstream}, snapshot "
                f"{feature} to its tip, then push {feature} to {remote} "
                f"(--force-with-lease)."
            )
        else:
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

        if git_ops.ref_exists(upstream, cwd=worktree_path):
            if on_wt:
                # HEAD is on worktree/<id>: rebase it forward, then snapshot the
                # feature branch to the new tip. No checkout dance -- HEAD never
                # leaves the worktree branch.
                if not git_ops.rebase(upstream, cwd=worktree_path):
                    output.err(
                        f"Rebase of {wt_branch} onto {upstream} hit conflicts. "
                        f"Resolve them on '{wt_branch}' and retry push-changes."
                    )
                    return False
                git_ops.git(
                    "branch", "-f", feature, "HEAD", cwd=worktree_path, check=False
                )
            else:
                # Legacy: HEAD on the feature branch. Old two-step rebase chain
                # (base onto master, then feature onto the updated base).
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
            if pushed.stderr:
                output.err(pushed.stderr.strip())
            if pushed_pr is not None and pushed_pr.state in ("", "creating"):
                tracking.save_record(record)
            return False

        head_sha = git_ops.git(
            "rev-parse", feature, cwd=worktree_path, check=False
        ).stdout.strip()
        if pushed_pr is not None:
            pushed_pr.head_sha = head_sha
            if pushed_pr.state in ("", "creating"):
                pushed_pr.state = "open"
        tracking.save_record(record)
        from . import pr_ops
        attribution_error = pr_ops.refresh_source_attribution(
            worktree_id,
            config,
            record,
            pushed_pr,
            head_sha,
        )
        if attribution_error:
            output.warn(
                f"PR head was pushed, but source attribution publication "
                f"failed: {attribution_error}"
            )

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
            if pushed.stderr:
                output.err(pushed.stderr.strip())
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
        from . import pr_ops
        attribution_error = pr_ops.refresh_source_attribution(
            worktree_id,
            config,
            record,
            pushed_pr,
            head_sha,
        )
        if attribution_error:
            output.warn(
                f"PR head was pushed, but source attribution publication "
                f"failed: {attribution_error}"
            )

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


def _resolve_content_ref(
    feature: str,
    worktree_id: str,
    *,
    cwd: str,
) -> str | None:
    """Resolve a local ref whose content represents the worktree's work.

    Prefer the local feature branch (the legacy ``feature/`` snapshot scheme
    creates it locally). When it is absent -- the refspec head scheme (#1815)
    keeps the worktree on ``worktree/<id>`` and only ever pushes ``pr/<slug>``
    to the *remote*, so no local feature branch exists -- fall back to the
    durable worktree branch ``worktree/<id>``, then the current ``HEAD``.
    Returns ``None`` if none resolve.
    """
    for ref in (feature, f"worktree/{worktree_id}", "HEAD"):
        if ref and git_ops.ref_exists(ref, cwd=cwd):
            return ref
    return None


def _pr_is_merged(record: tracking.WorktreeRecord, repo) -> bool:
    """Authoritative, squash-safe check that the tracked PR has merged.

    The durable "did the work land" signal -- **branch-independent** (survives a
    head branch deleted on merge) and **immune to version-file churn** on the
    moving upstream tip (the failure that false-blocks an already-merged
    worktree: its real code matches ``origin/<default>``, but bookkeeping files
    like ``plugin.json`` / ``marketplace.json`` were re-bumped by *later* PRs, so
    blob-equivalence against the live tip sees a spurious diff).

    Fast path: a tracking record whose ``pr.state`` is already ``"merged"`` was
    set from an authoritative observation -- trust it, no network. Otherwise ask
    the provider (``get_pull().merged``). **Fail-CLOSED**: a missing PR number,
    no provider/token, or any provider error returns ``False`` so finalize never
    certifies unmerged work as safe to prune.
    """
    pr = getattr(record, "pr", None)
    if not pr:
        return False
    if getattr(pr, "state", "") == "merged":
        return True
    number = getattr(pr, "number", None)
    slug = getattr(pr, "repo", "") or ""
    if not number or not slug:
        return False
    prcfg = repo.pr
    try:
        from . import providers
        provider = providers.get_provider(prcfg.provider)
        token = providers.account_token_for_slug(slug, prcfg)
        result = provider.get_pull(
            slug, int(number),
            api_base=getattr(prcfg, "api_base", "") or "", token=token,
        )
        return bool(getattr(result, "merged", False))
    except Exception:
        return False


def _pr_finalize_precondition(
    record: tracking.WorktreeRecord,
    repo,
    worktree_path: str,
    anchor: str,
) -> tuple[bool, str | None]:
    """Check whether a PR-mode worktree's work is safely upstream.

    finalize's contract is *safe to prune iff the local work is aligned with
    ``origin/<default>``* -- it does **not** consult the feature/PR branch,
    except in ``detach`` mode. Order:

    1. **Fast path (both modes)** -- content already reachable/patch-equivalent
       on ``origin/<default>`` (git-only, no network).
    2. **Authoritative (both modes)** -- the tracked PR *merged*
       (``_pr_is_merged``); squash-safe and independent of the feature branch or
       version-file churn.
    3. **``detach`` mode only** -- accept "code is upstream in an OPEN PR" (the
       feature branch is on the remote) as an *early* ok, because detached
       finalizes *before* merge. Non-detached (``keep-alive``) never looks at the
       feature branch: after the PR merges, ``sync``/``pr-merge`` realigns
       ``worktree/<id>`` to ``origin/<default>`` (FINAL) and finalize affirms.

    Returns ``(ok, error_message)``.
    """
    remote = repo.remote
    feature = record.pr.branch
    cwd = worktree_path if Path(worktree_path).exists() else anchor
    upstream = f"{remote}/{repo.default_branch}"
    strategy = (getattr(repo.pr, "strategy", "") or "detach").strip().lower()

    # (1) Fast path: content already on origin/<default>. Resolve a durable ref
    #     (feature -> worktree/<id> -> HEAD); the refspec head scheme keeps no
    #     local pr/<slug> branch, so probing ``feature`` alone would miss.
    content_ref = _resolve_content_ref(feature, record.worktree_id, cwd=cwd)
    if (
        content_ref is not None
        and git_ops.ref_exists(upstream, cwd=cwd)
        and _is_content_on_upstream(content_ref, upstream, cwd=cwd)
    ):
        return True, None

    # (2) Authoritative squash-safe signal (both modes): the tracked PR merged.
    if _pr_is_merged(record, repo):
        return True, None

    # (3) DETACHED mode only: "code is upstream in an OPEN PR" (feature branch on
    #     the remote) is an early ok. keep-alive never consults the feature
    #     branch -- it tracks only alignment with origin/<default>.
    if strategy == "detach" and git_ops.remote_branch_exists(remote, feature, cwd=cwd):
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

    # (4) Not upstream. Guide by mode -- never point at a (possibly deleted)
    #     feature branch as the fix.
    if strategy == "detach":
        return False, (
            f"Work for worktree/{record.worktree_id} is not upstream: the "
            f"tracked PR is not merged and no feature branch is on '{remote}'. "
            f"Run 'agent-worktrees create-pr' (or push-changes) to put the work "
            f"in a PR; if the PR already merged, run 'agent-worktrees sync' to "
            f"realign to {upstream}, then finalize."
        )
    return False, (
        f"Work on worktree/{record.worktree_id} is not yet aligned with "
        f"'{upstream}' and the tracked PR is not merged. In '{strategy}' mode "
        f"finalize verifies only alignment with {upstream} (the feature branch "
        f"is not consulted): once the PR merges, run 'agent-worktrees sync' to "
        f"realign, then finalize."
    )


def _settle_parent_obligation(
    record: tracking.WorktreeRecord,
    config: Config,
    worktree_id: str,
) -> None:
    """Settle the claim this worktree's PARENT holds on it, on finalize (Ph3).

    A worktree that finalizes has proven its own work safe, so it flips the
    outbound claim its **parent** carries (the parent's ledger entry whose
    ``ref`` is this worktree's qualified ClaimRef) to ``at-rest`` -- the
    incremental settlement that lets the parent's own finalize gate stop
    treating this child as unsettled without re-deriving its state (the
    recursion collapse).

    Same-machine only: when the parent lives on **this** machine its tracking
    YAML is directly updatable (``~/.{project}/worktrees/{id}.yaml``); a
    cross-machine parent settles later via the lease disposition mirror. Fully
    best-effort and degrade-safe -- any failure (no
    owner_ref, unresolved/foreign parent, no matching claim, I/O error) is a
    silent no-op and never perturbs the finalize.
    """
    try:
        owner = record.owner_claim_ref  # parsed ClaimRef of the parent, or None
        if owner is None or not owner.is_qualified:
            return
        if owner.machine != config.machine:
            return  # cross-machine parent -> lease mirror / sweep handles it
        from . import config as cfg
        parent_path = (
            cfg.project_dir(owner.project) / "worktrees" / f"{owner.worktree_id}.yaml"
        )
        if not parent_path.exists():
            return
        with tracking._RecordLock(parent_path, require_sidecar=True):
            parent = tracking.load_record(parent_path)
            child_ref = tracking.format_claim_ref(
                config.machine, config.repo_name, worktree_id
            )
            settled = tracking.settle_resource_claim(
                parent, child_ref, obligations.AT_REST, save=False,
            )
            if settled is not None:
                tracking.save_record(parent, parent_path)
        if settled is not None:
            output.ok(
                f"Settled parent {owner.worktree_id}'s claim on this worktree "
                f"(-> at-rest)"
            )
    except Exception:  # never let settlement perturb finalize
        return


def _assert_obligations_settled(
    record: tracking.WorktreeRecord | None,
    worktree_id: str,
    *,
    abandon: bool,
    handoff_to: str | None = None,
) -> bool:
    """Obligation gate (resource-obligation-settlement Phase 2).

    A worktree answers for the outbound resources it still owns before it may
    finalize. Reads the **local ledger** (`record.resources`) for **unsettled**
    (``active``) claims -- a cheap, local, no-traversal balance check (a settled
    child has already flipped its claim to ``at-rest``/``released``).

    The legacy ``off``/``warn``/``block`` setting no longer weakens ownership:
    any unsettled resource **refuses** finalize unless ``abandon`` carries an
    affirmative handoff target. The setting may still shape surrounding
    diagnostics, but cannot release creator responsibility.

    ``abandon`` overrides a block only with a non-empty ``handoff_to`` recipient
    or flow: it proceeds and re-homes the unsettled obligations (the downstream
    ``release_all_resources`` marks the claims released and surfaces them for
    the named cleanup/adoption flow). Returns True to proceed, False to block
    the finalize. Degrade-safe: no record / no resources -> proceed.

    Finalize never auto-reclaims a creator's children. A crashed/missed
    settlement is handled explicitly through ``claims sweep`` or an
    operator-directed handoff, preserving affirmative ownership.
    """
    if record is None:
        return True
    try:
        from . import claim_handoffs
        source_ref = tracking.format_claim_ref(
            record.machine, record.repo, record.worktree_id)
        active_handoffs = claim_handoffs.active_bundle_ids_for_source(
            source_ref)
    except Exception as exc:
        output.err(
            f"Cannot verify claim-handoff registry for {worktree_id}; finalize "
            f"is refused fail-closed ({exc}).")
        return False
    if active_handoffs:
        output.err(
            f"Worktree {worktree_id} owns {len(active_handoffs)} nonterminal "
            "claim-handoff bundle(s); finalize is refused until each is "
            "accepted, declined, or cancelled: "
            + ", ".join(active_handoffs)
        )
        return False
    unsettled = [c for c in record.resources if c.is_unsettled]
    if not unsettled:
        return True
    pending = [c for c in unsettled if c.ref.startswith("pending-run:")]
    if pending:
        output.err(
            f"Worktree {worktree_id} has {len(pending)} in-flight resource "
            "creation reservation(s). Pending resources cannot be abandoned or "
            "handed off before their real identity is journaled; wait for the "
            "creating command or repair its pending claim."
        )
        return False
    offered = [c for c in unsettled if c.handoff_bundle]
    if offered:
        output.err(
            f"Worktree {worktree_id} has {len(offered)} claim(s) reserved in "
            "offered handoff bundles. Offered responsibility cannot be settled, "
            "abandoned, or released before consumer acceptance or decline/cancel."
        )
        return False
    if abandon and not (handoff_to or "").strip():
        output.err(
            f"Worktree {worktree_id} cannot abandon {len(unsettled)} unsettled "
            "obligation(s) without an affirmative handoff target. The creating "
            "agent remains responsible: close them now, or after explicit "
            "operator direction pass --abandon --handoff-to <recipient-or-flow>."
        )
        return False

    def _describe() -> None:
        for c in unsettled:
            label = f"  · {c.kind}: {c.ref}"
            if c.note:
                label += f" ({c.note})"
            print(label)

    if not abandon:
        output.err(
            f"Worktree {worktree_id} still owns {len(unsettled)} unsettled "
            f"resource obligation(s) -- finalize is blocked "
            "(creator ownership cannot be disabled by obligation-gate mode):"
        )
        _describe()
        output.err(
            "Close each resource out (a cross-repo worktree: finalize it; a "
            "CodeSpace/container: merge or move its work off-box; a bridge: drive "
            "it to final), then retry -- or pass --abandon to re-home them."
        )
        return False

    output.warn(
        f"Abandoning {len(unsettled)} unsettled resource obligation(s) owned by "
        f"{worktree_id}"
        + f" -- handed to {(handoff_to or '').strip()}:"
    )
    _describe()
    return True


def _rehome_abandoned_obligations(
    record, worktree_id: str, config, *, handoff_to: str,
) -> bool:
    """Durably re-home an ``--abandon`` finalize's still-unsettled obligations.

    Selects the record's unsettled (blocking) claims -- the ones a plain finalize
    would refuse and ``--abandon`` is about to release -- and records each in the
    per-project orphanage (:func:`tracking.rehome_abandoned_obligations`) so the
    orphaned resource it named is not silently dropped. Logs what it re-homed.
    Returns True only when every unsettled claim is durably present with the
    requested handoff target. A write/readback failure returns False so finalize
    preserves the creator worktree and its claims.
    """
    abandoned = [c for c in record.resources if c.is_unsettled]
    if not abandoned:
        return True
    tracking.rehome_abandoned_obligations(
        abandoned, source_worktree=worktree_id, config=config,
        handoff_to=handoff_to)
    persisted = {
        (entry.get("source_worktree"), entry.get("ref"))
        for entry in tracking.load_orphaned_obligations_strict()
        if (entry.get("handoff_to") or "").strip() == handoff_to
    }
    expected = {(worktree_id, claim.ref) for claim in abandoned}
    if not expected.issubset(persisted):
        missing = sorted(ref for owner, ref in expected - persisted)
        output.err(
            "Affirmative handoff could not be durably persisted; finalize is "
            "refused and creator ownership is preserved. Missing: "
            + ", ".join(missing)
        )
        return False
    output.warn(
        f"Re-homed {len(abandoned)} abandoned obligation(s) of {worktree_id} "
        f"to affirmative handoff target {handoff_to!r} via the durable "
        f"orphanage (not dropped) -- list via "
        f"'agent-worktrees claims orphans':")
    for c in abandoned:
        lbl = f"  · {c.kind}: {c.ref}"
        if c.note:
            lbl += f" ({c.note})"
        print(lbl)
    return True


def _worktree_branch(
    record: tracking.WorktreeRecord | None, worktree_id: str
) -> str:
    if record is not None and record.branch:
        return record.branch
    return f"worktree/{worktree_id}"


def validate_and_finalize(
    worktree_id: str,
    config: Config,
    *,
    dry_run: bool = False,
    abandon: bool = False,
    handoff_to: str | None = None,
) -> bool:
    """Validate that worktree content is on upstream, then clean up.

    This is a non-mutating validation step -- it never squashes, rebases,
    or pushes.  If the branch's content is not yet on origin/master, it
    fails with guidance to run push-changes first.

    Args:
        worktree_id: The worktree identifier.
        config: Loaded project configuration.
        dry_run: If True, preview without side effects.
        abandon: If True, proceed past the obligation gate even when the worktree
            still owns **unsettled** outbound resources, re-homing them (marking
            the claims released + surfacing them) rather than being blocked. The
            escape hatch for the resource-obligation-settlement finalize gate.
        handoff_to: Required with ``abandon`` when obligations remain; the
            affirmative recipient or flow recorded on every orphan entry.

    Returns:
        True on success, False if content is not yet on upstream.
    """
    repo = config.default_repo
    anchor = repo.anchor
    worktree_path = tracking.resolve_worktree_path(worktree_id, repo.worktree_root)
    upstream = f"{repo.remote}/{repo.default_branch}"
    lock_path = Path(repo.worktree_root) / ".finalize.lock"

    # Load tracking record
    from . import config as cfg
    yaml_path = cfg.tracking_dir() / f"{worktree_id}.yaml"
    record = None
    if yaml_path.exists():
        try:
            record = tracking.load_record(yaml_path)
        except Exception as exc:
            output.err(
                f"Cannot finalize {worktree_id}: its existing claim ledger is "
                f"unreadable ({exc}). Creator ownership is preserved.")
            return False
    branch = _worktree_branch(record, worktree_id)
    checkout_managed = record is None or record.checkout_managed

    try:
        from . import claim_handoffs
        source_ref = tracking.format_claim_ref(
            config.machine, config.repo_name, worktree_id)
        active_handoffs = claim_handoffs.active_bundle_ids_for_source(source_ref)
    except Exception as exc:
        output.err(
            f"Cannot verify claim-handoff registry for {worktree_id}; finalize "
            f"is refused fail-closed ({exc}).")
        return False
    if active_handoffs:
        output.err(
            f"Worktree {worktree_id} owns nonterminal claim-handoff bundles; "
            "finalize is refused: " + ", ".join(active_handoffs))
        return False

    wt_exists = Path(worktree_path).exists()
    pr_mode = bool(
        repo.pr.enabled and record and record.pr and record.pr.branch
    )

    if dry_run:
        _dry_run_finalize_preview(
            worktree_id, config, worktree_path, branch, upstream,
        )
        return True

    # Seal the worktree's durable identity (title + session registry) from
    # session-state BEFORE any teardown, so a finalized/pruned worktree never
    # reads as "(untitled)" with no session linkage -- even when the
    # best-effort register/deregister-session hooks were bypassed (a dispatched
    # or crashed session, a bare-resume cwd). Gap-filling + silent; mutates the
    # in-memory ``record`` so the later update_status(save) preserves it.
    try:
        tracking.seal_worktree_identity(record)
    except Exception:
        pass

    # Obligation gate (resource-obligation-settlement Phase 2). A worktree
    # answers for the outbound resources it still owns before it may finalize.
    # Runs BEFORE any destructive step so a blocking gate refuses cleanly. Read
    # is cheap + local (the ledger), no traversal. Enforcing (block) by default;
    # AGENT_WORKTREES_OBLIGATION_GATE=warn/off relaxes it, and --abandon overrides.
    if not _assert_obligations_settled(
        record, worktree_id, abandon=abandon, handoff_to=handoff_to,
    ):
        return False

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

    # Freeze the exact ownership set under the same record lock used by
    # `claims add`: reload after the potentially-long fetch/content validation,
    # re-check every claim, persist+verify the handoff, then mark the creator
    # `finalizing`. Claim creation rejects that terminal transition, so no new
    # resource can appear between this check and release_all_resources below.
    try:
        active_handoffs = claim_handoffs.active_bundle_ids_for_source(source_ref)
    except Exception as exc:
        output.err(
            f"Cannot verify claim-handoff registry before cleanup ({exc}); "
            "finalize is refused.")
        return False
    if active_handoffs:
        output.err(
            f"Worktree {worktree_id} acquired/retains nonterminal "
            "claim-handoff bundles before cleanup; finalize is refused: "
            + ", ".join(active_handoffs))
        return False

    if yaml_path.exists():
        try:
            with tracking._RecordLock(yaml_path, require_sidecar=True):
                record = tracking.load_record(yaml_path)
                try:
                    from . import claim_handoffs
                    source_ref = tracking.format_claim_ref(
                        record.machine, record.repo, record.worktree_id)
                    active_handoffs = (
                        claim_handoffs.active_bundle_ids_for_source(source_ref))
                except Exception as exc:
                    output.err(
                        f"Cannot verify claim-handoff registry before cleanup "
                        f"({exc}); finalize is refused.")
                    return False
                if active_handoffs:
                    output.err(
                        f"Worktree {worktree_id} acquired/retains nonterminal "
                        "claim-handoff bundles before cleanup; finalize is "
                        "refused: " + ", ".join(active_handoffs))
                    return False
                unsettled = [c for c in record.resources if c.is_unsettled]
                pending = [
                    c for c in unsettled if c.ref.startswith("pending-run:")
                ]
                if pending:
                    output.err(
                        f"Worktree {worktree_id} acquired {len(pending)} "
                        "in-flight resource creation reservation(s) before "
                        "cleanup. Finalize is refused until each real resource "
                        "identity is journaled."
                    )
                    return False
                offered = [c for c in unsettled if c.handoff_bundle]
                if offered:
                    output.err(
                        f"Worktree {worktree_id} acquired {len(offered)} "
                        "handoff-reserved claim(s) before cleanup. Finalize is "
                        "refused until acceptance or decline/cancel resolves them."
                    )
                    return False
                if unsettled and not abandon:
                    output.err(
                        f"Worktree {worktree_id} acquired {len(unsettled)} "
                        "unsettled obligation(s) before cleanup; finalize is "
                        "refused and creator ownership is preserved."
                    )
                    return False
                if abandon and unsettled and not _rehome_abandoned_obligations(
                    record, worktree_id, config,
                    handoff_to=(handoff_to or "").strip(),
                ):
                    return False
                record.status = "finalizing"
                tracking.save_record(record, yaml_path)
        except Exception as exc:
            output.err(
                f"Cannot freeze {worktree_id}'s claim ledger for finalize "
                f"({exc}); creator ownership is preserved."
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
        if yaml_path.exists():
            with tracking._RecordLock(yaml_path, require_sidecar=True):
                record = tracking.load_record(yaml_path)
                checkout_managed = record.checkout_managed
        inside_worktree = git_ops.is_cwd_inside(worktree_path)
        has_live_session = _has_live_session(record)

        # Reconcile local branch pointers with origin now that the content is
        # verified upstream, so a merged-but-not-yet-cleaned worktree stops
        # rendering as diverged in the picker (#1106).
        _reconcile_merged_pointers(repo, worktree_path, anchor, branch)

        if not checkout_managed or inside_worktree or has_live_session:
            reason = (
                "the checkout is owned by an external session host"
                if not checkout_managed
                else (
                    "this shell is running inside the worktree"
                    if inside_worktree
                    else "a live Copilot session is still using the worktree"
                )
            )
            output.ok(
                f"Finalized: all content from {branch} is on "
                f"{repo.remote}/{repo.default_branch}, so this worktree is "
                f"safe to prune."
            )
            if not checkout_managed:
                output.info(
                    f"Leaving the worktree directory and branch in place because "
                    f"{reason}. Later agent-worktrees cleanup may retire this "
                    f"tracking record, but only the external host may remove the "
                    f"checkout or branch."
                )
            else:
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
                reason=(
                    "external_checkout"
                    if not checkout_managed
                    else ("inside_worktree" if inside_worktree else "live_session")
                ),
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

        if checkout_managed:
            merged = permissions.merge_permissions(anchor, worktree_path)
            if merged:
                for m in merged:
                    print(f"  Merged new permission: {m}")
                print("Permissions merged back to anchor and worktree entry removed.")

            if permissions.remove_trusted_folder(worktree_path):
                print("Removed worktree path from trustedFolders.")

        # Update tracking
        if record:
            # Citadel E1b cascade (#877): a parent that owns outbound worktree
            # resources hands them back on finalize -- release the live claims so
            # the ledger stops asserting the parent holds them, and SURFACE the
            # children so their downstream cleanup isn't silently forgotten. The
            # child records keep their own owner_ref; the claimant-liveness gate
            # now sees this parent as terminal (gone), so the children become
            # orphans governed by their own prune safety.
            released = tracking.release_all_resources(record, save=False)
            tracking.update_status(record, "finalized")
            # Reset the postToolUse disposition-nudge sidecar (#nudge): a
            # finalized worktree's disposition is sealed, so drop its drift
            # counter. Best-effort -- the nudge hook also self-heals on a
            # terminal state.
            try:
                (cfg.install_dir() / "nudge-state" / f"{worktree_id}.json").unlink(
                    missing_ok=True)
            except Exception:
                pass
            if released:
                output.warn(
                    f"Released {len(released)} downstream worktree resource(s) "
                    f"owned by {worktree_id} (finalized) -- review/clean them:"
                )
                for c in released:
                    label = f"  · {c.kind}: {c.ref}"
                    if c.note:
                        label += f" ({c.note})"
                    print(label)

            # Obligation settlement, upward (resource-obligation-settlement Ph3):
            # this worktree finalizing means its OWN work is safe, so settle the
            # claim its PARENT holds on it -- flip the parent-visible claim to
            # at-rest so the parent's finalize gate stops treating this child as
            # unsettled. This is the recursion-collapse: the parent never
            # re-derives the child's state, it trusts this flip. Best-effort +
            # same-machine only (a cross-machine parent settles via the lease
            # disposition mirror / reclaim sweep). Never blocks the finalize.
            _settle_parent_obligation(record, config, worktree_id)

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
    abandon: bool = False,
    handoff_to: str | None = None,
) -> bool:
    """Legacy wrapper -- runs validate_and_finalize only.

    This no longer pushes changes. Use push_changes() + validate_and_finalize()
    for the full two-phase flow.
    """
    return validate_and_finalize(
        worktree_id, config, dry_run=dry_run, abandon=abandon,
        handoff_to=handoff_to,
    )


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
    output.dry_run("Would remove worktree path from trustedFolders")
    output.dry_run("Would update worktree YAML status: finalized")
    print()
    output.ok("Dry run complete -- no changes made")
