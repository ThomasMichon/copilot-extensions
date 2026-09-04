"""Safely prime a disposable worker worktree for managed garbage collection.

This module deliberately does not remove worktrees. It concludes an exact
recorded session when the worker is already gone, reconciles a checkout only
when it has no valuable local work, and converts the tracking record into a
managed final state. The existing managed sweep remains the deletion authority
and re-checks liveness plus its idle grace before removing anything.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from . import finalize, git_ops, sessions, tracking

DISPOSABLE_CLI_POLICY = "disposable-cli"
DISPATCH_ATTEMPT_POLICY = "dispatch-attempt"

def _normalized(path: str | Path) -> str:
    return os.path.normcase(os.path.normpath(str(path)))


def _session_is_live(record: tracking.WorktreeRecord) -> str | None:
    if sessions.has_mux_session(record.worktree_id):
        return "live-mux"
    context = sessions.scan_sessions_fast([record])
    worktree_path = _normalized(record.worktree_path)
    if any(_normalized(path) == worktree_path for path in context.active_sessions):
        return "live-session"
    return None


def _dirty_entries(worktree_path: Path) -> list[tuple[str, str]]:
    result = git_ops.git(
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
        "--no-renames",
        cwd=worktree_path,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            result.stderr.strip() or "could not inspect the worker checkout"
        )
    entries: list[tuple[str, str]] = []
    for raw in result.stdout.split("\0"):
        if not raw:
            continue
        if len(raw) < 4:
            raise RuntimeError("could not parse worker checkout status")
        entries.append((raw[:2], raw[3:]))
    return entries


def _save_session_conclusion(
    record: tracking.WorktreeRecord,
    record_path: Path,
    session_id: str | None,
) -> tuple[dict[str, Any], bool]:
    if not session_id:
        return {"action": "skipped", "reason": "session-unavailable"}, False
    entry = record.session_entry(session_id)
    if entry is None:
        return {
            "action": "skipped",
            "reason": "session-not-tracked",
            "session": session_id,
        }, False
    already = entry.state == "concluded"
    tracking.conclude_session(
        record,
        session_id,
        state="concluded",
        save=False,
    )
    tracking.save_record(record, record_path)
    return {
        "action": "already-concluded" if already else "concluded",
        "session": session_id,
    }, not already


def _preservation_reason(
    record: tracking.WorktreeRecord, *, policy: str
) -> str | None:
    if policy == DISPOSABLE_CLI_POLICY and record.resolved_interface != "cli":
        return "not-cli-worktree"
    if record.pending_handoffs:
        return "pending-handoff"
    if record.follow_up or record.active_effort is not None:
        return "follow-up"
    if record.live_resources or record.owner_ref or record.is_paired:
        return "outstanding-obligations"
    if any(pr.state in {"creating", "open"} for pr in record.prs):
        return "open-pull-request"
    return None


def _identity_reason(
    record: tracking.WorktreeRecord,
    record_path: Path,
) -> str | None:
    if Path(record.worktree_path).name != record.worktree_id:
        return "worktree-path-mismatch"
    if record.branch != f"worktree/{record.worktree_id}":
        return "worktree-branch-mismatch"
    path_key = _normalized(record.worktree_path)
    for peer in tracking.list_records(record_path.parent):
        if peer.worktree_id == record.worktree_id:
            continue
        if _normalized(peer.worktree_path) == path_key:
            return "worktree-path-conflict"
        if peer.branch and peer.branch == record.branch:
            return "worktree-branch-conflict"
    return None


def conclude_disposable_worktree(
    record_path: Path,
    repo: Any,
    *,
    session_id: str | None,
    owner: str,
    policy: str,
    reservation_key: str | None = None,
) -> dict[str, Any]:
    """Prime one exact disposable worktree for the existing managed GC.

    Safe skips return ``action="skipped"`` with a stable reason. Operational
    failures raise so a higher layer can distinguish a failed conclusion from a
    deliberate preservation decision.
    """
    if policy not in {DISPOSABLE_CLI_POLICY, DISPATCH_ATTEMPT_POLICY}:
        raise ValueError(f"unsupported terminal conclusion policy: {policy!r}")

    record_path = Path(record_path)
    record = tracking.load_record(record_path)
    if record.worktree_id != record_path.stem:
        raise RuntimeError(
            "tracking record identity does not match its filename"
        )
    result: dict[str, Any] = {
        "worktree_id": record.worktree_id,
        "policy": policy,
        "session": {"action": "skipped", "reason": "not-attempted"},
        "managed_gc_eligible": False,
    }

    lock = finalize.FinalizeLock(
        Path(repo.worktree_root) / ".finalize.lock",
        timeout=3,
        stale_after=3600,
    )
    try:
        lock.acquire()
    except TimeoutError as exc:
        raise RuntimeError("timed out waiting for the worktree lifecycle lock") from exc

    try:
        with tracking._RecordLock(
            record_path,
            timeout=3,
            require_sidecar=True,
        ):
            record = tracking.load_record(record_path)
            if record.worktree_id != record_path.stem:
                raise RuntimeError(
                    "tracking record identity changed during conclusion"
                )
            if reason := _identity_reason(record, record_path):
                result.update(action="skipped", reason=reason)
                return result
            if policy == DISPATCH_ATTEMPT_POLICY:
                allocation = record.dispatch_attempt
                if allocation is None:
                    result.update(
                        action="skipped",
                        reason="dispatch-provenance-missing",
                    )
                    return result
                if not reservation_key:
                    result.update(
                        action="skipped",
                        reason="reservation-identity-missing",
                    )
                    return result
                if allocation.reservation_key != reservation_key:
                    result.update(
                        action="skipped",
                        reason="reservation-mismatch",
                    )
                    return result
                if allocation.creator_machine.casefold() != record.machine.casefold():
                    result.update(
                        action="skipped",
                        reason="creator-machine-mismatch",
                    )
                    return result
                if allocation.driver != owner:
                    result.update(
                        action="skipped",
                        reason="driver-owner-mismatch",
                    )
                    return result
            live_reason = _session_is_live(record)
            if live_reason:
                result.update(action="skipped", reason=live_reason)
                result["session"] = {
                    "action": "preserved",
                    "reason": live_reason,
                    **({"session": session_id} if session_id else {}),
                }
                return result

            if record.pending_handoffs:
                result.update(action="skipped", reason="pending-handoff")
                result["session"] = {
                    "action": "preserved",
                    "reason": "pending-handoff",
                    **({"session": session_id} if session_id else {}),
                }
                return result

            head_session = record.resolved_head_session
            if head_session and not session_id:
                result.update(action="skipped", reason="session-unavailable")
                result["session"] = {
                    "action": "preserved",
                    "reason": "session-unavailable",
                    "head_session": head_session,
                }
                return result
            if head_session and session_id != head_session:
                result.update(action="skipped", reason="session-mismatch")
                result["session"] = {
                    "action": "preserved",
                    "reason": "session-mismatch",
                    "session": session_id,
                    "head_session": head_session,
                }
                return result

            if reason := _preservation_reason(record, policy=policy):
                result.update(action="skipped", reason=reason)
                return result

            lifecycle_revision = record.lifecycle_revision
            expected_path = record.worktree_path
            expected_branch = record.branch

        worktree_path = Path(expected_path)
        if worktree_path.exists():
            if not (worktree_path / ".git").exists():
                result.update(action="skipped", reason="invalid-worktree")
                return result
            current_branch = git_ops.current_branch(worktree_path)
            if current_branch != expected_branch:
                result.update(action="skipped", reason="branch-drift")
                return result

            upstream = f"{repo.remote}/{repo.default_branch}"
            verified = git_ops.git(
                "rev-parse",
                "--verify",
                f"{upstream}^{{commit}}",
                cwd=worktree_path,
                check=False,
            )
            if verified.returncode != 0:
                result.update(action="skipped", reason="upstream-unavailable")
                return result
            ahead = git_ops.git(
                "rev-list",
                "--count",
                f"{upstream}..HEAD",
                cwd=worktree_path,
                check=False,
            )
            if ahead.returncode != 0:
                raise RuntimeError(
                    ahead.stderr.strip() or "could not inspect local commits"
                )
            try:
                ahead_count = int(ahead.stdout.strip())
            except ValueError as exc:
                raise RuntimeError("could not parse local commit count") from exc
            if ahead_count:
                result.update(
                    action="skipped",
                    reason="local-commits",
                    local_commits=ahead_count,
                )
                return result

            dirty = _dirty_entries(worktree_path)
            if dirty:
                result.update(
                    action="skipped",
                    reason="dirty-work",
                    dirty_paths=sorted(relative for _status, relative in dirty),
                )
                return result

        elif expected_branch:
            branch = git_ops.git(
                "rev-parse",
                "--verify",
                f"refs/heads/{expected_branch}^{{commit}}",
                cwd=repo.anchor,
                check=False,
            )
            if branch.returncode == 0:
                upstream = f"{repo.remote}/{repo.default_branch}"
                verified = git_ops.git(
                    "rev-parse",
                    "--verify",
                    f"{upstream}^{{commit}}",
                    cwd=repo.anchor,
                    check=False,
                )
                if verified.returncode != 0:
                    result.update(
                        action="skipped",
                        reason="upstream-unavailable",
                    )
                    return result
                ahead = git_ops.git(
                    "rev-list",
                    "--count",
                    f"{upstream}..{expected_branch}",
                    cwd=repo.anchor,
                    check=False,
                )
                if ahead.returncode != 0:
                    raise RuntimeError(
                        ahead.stderr.strip()
                        or "could not inspect the missing checkout's branch"
                    )
                try:
                    ahead_count = int(ahead.stdout.strip())
                except ValueError as exc:
                    raise RuntimeError(
                        "could not parse the missing checkout's commit count"
                    ) from exc
                if ahead_count:
                    result.update(
                        action="skipped",
                        reason="local-commits",
                        local_commits=ahead_count,
                    )
                    return result

        with tracking._RecordLock(
            record_path,
            timeout=3,
            require_sidecar=True,
        ):
            record = tracking.load_record(record_path)
            if record.worktree_id != record_path.stem:
                raise RuntimeError(
                    "tracking record identity changed during conclusion"
                )
            if reason := _identity_reason(record, record_path):
                result.update(action="skipped", reason=reason)
                return result
            if live_reason := _session_is_live(record):
                result.update(action="skipped", reason=live_reason)
                return result
            if (
                record.lifecycle_revision != lifecycle_revision
                or record.worktree_path != expected_path
                or record.branch != expected_branch
            ):
                result.update(action="skipped", reason="lifecycle-changed")
                return result
            if reason := _preservation_reason(record, policy=policy):
                result.update(action="skipped", reason=reason)
                return result
            session_result, _session_changed = _save_session_conclusion(
                record,
                record_path,
                session_id,
            )
            result["session"] = session_result
            if record.resolved_head_session is not None:
                result.update(action="skipped", reason="active-lifecycle-head")
                return result
            already_primed = (
                record.kind in tracking.MANAGED_KINDS
                and record.status in {"complete", "finalized"}
                and record.owner == owner
                and record.resolved_origin == "delegate"
                and (
                    policy == DISPATCH_ATTEMPT_POLICY
                    or record.resolved_interface == "cli"
                )
            )
            record.kind = "bridge"
            record.owner = owner
            if policy == DISPOSABLE_CLI_POLICY:
                record.interface = "cli"
            record.origin = "delegate"
            tracking.update_status(record, "complete", save=False)
            tracking.save_record(record, record_path)

            result.update(
                action="already-primed" if already_primed else "primed",
                reason="managed-gc-candidate",
                reconciled=False,
                managed_gc_eligible=True,
            )
            return result
    finally:
        lock.release()


__all__ = [
    "DISPATCH_ATTEMPT_POLICY",
    "DISPOSABLE_CLI_POLICY",
    "conclude_disposable_worktree",
]
