"""Physical checkout removal and guarded tracking-record retirement."""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from . import activity, config as cfg, disposition_history, git_ops, handoff_trace
from . import permissions, procs, sessions, tracking
from .retirement_safety import retirement_blocker, same_pair


def reap_worktree(
    rec: tracking.WorktreeRecord,
    info: git_ops.WorktreeStateInfo,
    repo: cfg.RepoConfig,
    tracking_path: Path,
) -> tuple[int, list[str]]:
    """Reload and fence responsibility under the caller's finalization lock."""
    path = tracking_path / f"{rec.worktree_id}.yaml"
    try:
        with tracking._RecordLock(path, require_sidecar=True):
            latest = tracking.load_record(path)
            if (
                latest.worktree_path != rec.worktree_path
                or latest.branch != rec.branch
                or latest.checkout_managed != rec.checkout_managed
                or not same_pair(rec, latest)
            ):
                return 1, [f"Cannot retire {rec.worktree_id}: checkout identity changed; retry cleanup"]
            return _reap_locked(latest, info, repo, tracking_path)
    except (OSError, ValueError) as exc:
        return 1, [f"Cannot retire {rec.worktree_id}: {exc}"]


def _reap_locked(
    rec: tracking.WorktreeRecord,
    info: git_ops.WorktreeStateInfo,
    repo: cfg.RepoConfig,
    tracking_path: Path,
) -> tuple[int, list[str]]:
    """Keep the responsibility fence through removal and record retirement."""
    warnings: list[str] = []
    failures = 0
    blocker = retirement_blocker(rec)
    if blocker is not None:
        return 1, [f"Cannot retire {rec.worktree_id}: {blocker.reason}"]

    if not rec.checkout_managed:
        try:
            retired = tracking.retire_record(rec, tracking_path)
        except ValueError as exc:
            return 1, [str(exc)]
        if not retired:
            warnings.append(
                f"Tracking record for {rec.worktree_id} changed or is busy "
                "-- retirement deferred to a later pass."
            )
            return 0, warnings
        disposition_history.remove(rec.worktree_id)
        handoff_trace.remove_trace(cfg.active_project(), rec.worktree_id)
        activity.log_event(
            "external_worktree_tracking_retired",
            worktree_id=rec.worktree_id,
            path=rec.worktree_path,
        )
        return 0, warnings

    if rec.worktree_path and Path(rec.worktree_path).exists():
        # Windows cwd handles must be released before directory removal.
        sessions.kill_tmux_session(rec.worktree_id)
        try:
            killed = procs.terminate_processes_under(rec.worktree_path)
        except Exception:
            killed = []
        if killed:
            names = ", ".join(f"{k['name'] or '?'}({k['pid']})" for k in killed if k["killed"])
            if names:
                warnings.append(f"Terminated lingering process(es): {names}")
            activity.log_event(
                "worktree_procs_terminated",
                worktree_id=rec.worktree_id,
                count=sum(1 for k in killed if k["killed"]),
            )

        if not git_ops.remove_worktree(repo.anchor, rec.worktree_path):
            warnings.append("Could not remove worktree via git -- forcing directory removal.")
        wt_dir = Path(rec.worktree_path)
        if wt_dir.exists():
            for attempt in range(4):
                shutil.rmtree(wt_dir, ignore_errors=True)
                if not wt_dir.exists():
                    break
                time.sleep(0.25 * (attempt + 1))
            if wt_dir.exists():
                warnings.append(f"Directory still present: {wt_dir}")
                failures += 1

    if rec.branch:
        if not git_ops.delete_branch(rec.branch, cwd=repo.anchor, force=True):
            warnings.append(f"Could not delete branch {rec.branch}")
            failures += 1

    if rec.worktree_path:
        permissions.merge_permissions(repo.anchor, rec.worktree_path)
        permissions.remove_trusted_folder(rec.worktree_path)

    # Preserve the record on deferral or a newly observed responsibility hold.
    try:
        retired = tracking.retire_record(rec, tracking_path)
    except ValueError as exc:
        return failures + 1, [*warnings, str(exc)]
    if retired:
        disposition_history.remove(rec.worktree_id)
        handoff_trace.remove_trace(cfg.active_project(), rec.worktree_id)
    else:
        warnings.append(
            f"Tracking record for {rec.worktree_id} changed or is busy "
            "-- worktree/branch removed; record will retire on a later pass."
        )

    activity.log_event(
        "worktree_reaped",
        worktree_id=rec.worktree_id,
        branch=rec.branch,
        state=info.state.value,
    )
    return failures, warnings
