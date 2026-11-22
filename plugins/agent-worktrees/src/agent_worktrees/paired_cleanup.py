"""Pair-scoped cleanup admission and retained interrupted-operation evidence."""

from __future__ import annotations

from contextlib import ExitStack
from contextvars import ContextVar
import json
from pathlib import Path
from typing import Any, Callable, cast

from . import config as cfg, finalize_lock, launch_admission, paired_claims
from . import git_ops, procs, sessions, terminal_pair_progress, tracking
from .creation_tail_repair import _REPAIR_ERRORS
from .checkout_data import preservation_reason
from .git_repository_environment import repository_identity_scope

_admitted: ContextVar[frozenset[tuple[str | None, ...]]] = ContextVar(
    "paired_cleanup_admission", default=frozenset(),
)


def _key(record: tracking.WorktreeRecord) -> tuple[str | None, ...]:
    return (
        record.repo, record.worktree_id, record.creation_nonce, record.worktree_path,
        record.branch, record.pair_id, record.pair_role, record.pair_kind, record.pair_ref,
        record.owner_ref,
    )


def admitted(record: tracking.WorktreeRecord) -> bool:
    """Only the lock-held native joint operation can discharge a pair."""
    return _key(record) in _admitted.get()


def authorizes_retired_parent(record: tracking.WorktreeRecord, config: cfg.Config) -> bool:
    """Keep exact joint admission valid after this pass removes the parent."""
    if (
        not admitted(record) or record.repo != config.repo_name
        or record.pair_role != "knowledge" or record.pair_kind != "worktree"
        or not record.owner_ref or record.owner_ref != record.pair_ref
    ):
        return False
    parent = paired_claims.reciprocal_sibling(record)
    return parent is not None and admitted(parent)


def journal_path(record: tracking.WorktreeRecord) -> Path:
    return cfg.project_dir(record.repo) / "worktrees" / f"{record.worktree_id}.paired-cleanup.json"


def require_joint(record: tracking.WorktreeRecord) -> None:
    pair_evidence = any((record.pair_id, record.pair_role, record.pair_ref, record.pair_kind))
    anchor_pair = record.pair_kind == "anchor" and record.pair_role == "harness"
    if pair_evidence and not anchor_pair and not admitted(record):
        raise ValueError(
            "paired checkout removal requires cleanup --worktree-id <harness-id> --paired; "
            "both members and harness inactivity must be admitted together"
        )


def _subject(record: tracking.WorktreeRecord) -> dict[str, Any]:
    primary_identity = cast(
        Callable[[tracking.WorktreeRecord], dict[str, Any]], terminal_pair_progress.primary_identity,
    )
    return {
        **primary_identity(record),
        "creation_nonce": record.creation_nonce,
        "owner_ref": record.owner_ref,
    }


def _inactive(record: tracking.WorktreeRecord) -> None:
    from .execution_spaces import require_cleanup_identity

    receiving = cfg.load_project_config(record.repo, include_control_plane_related_pr=False)
    require_cleanup_identity(record, receiving.default_repo.anchor)
    live = sessions.verify_worktree_active(record, strict=True)
    if live.active or not live.probes_ok or not live.mux_probe_ok:
        raise ValueError(f"{record.worktree_id}: execution is live or unknown")
    if Path(record.worktree_path).exists():
        if reason := procs.checkout_retirement_blocker(record.worktree_path):
            raise ValueError(f"{record.worktree_id}: {reason}")


@repository_identity_scope()
def reap_pair(
    worktree_id: str, *, include_unused: bool = False, include_conversations: bool = False,
) -> dict[str, Any]:
    """Compose the existing two cleanup admissions while holding both fences."""
    from . import cleanup_gc_cli, cleanup_reap

    result: dict[str, Any] = {
        "worktree_id": worktree_id, "ok": False, "removed": False,
        "reason": "", "warnings": [],
    }
    try:
        config = cfg.load_config()
        if not worktree_id or Path(worktree_id).name != worktree_id or ".." in worktree_id:
            raise ValueError("joint cleanup requires an exact harness worktree ID")
        primary_path = cfg.project_dir(config.repo_name) / "worktrees" / f"{worktree_id}.yaml"
        if primary_path.is_symlink():
            raise ValueError("joint cleanup refuses a redirected harness record")
        primary = tracking.load_record(primary_path)
        if primary.pair_role != "harness" or primary.pair_kind != "worktree":
            raise ValueError("joint cleanup must select the harness, not a companion")
        sibling = paired_claims.reciprocal_sibling(primary)
        if sibling is None:
            raise ValueError("joint cleanup requires the exact reciprocal companion record")
        sibling_path = cfg.project_dir(sibling.repo) / "worktrees" / f"{sibling.worktree_id}.yaml"
        if sibling_path.is_symlink():
            raise ValueError("joint cleanup refuses a redirected companion record")
        expected = [_subject(member) for member in (primary, sibling)]
        if not terminal_pair_progress.reciprocal_identity_valid(*expected):
            raise ValueError("joint cleanup pair identity is not reciprocal")
        configs = [
            cfg.load_project_config(member.repo, include_control_plane_related_pr=False)
            for member in (primary, sibling)
        ]
        receipt_path = journal_path(primary)
        receipt = {"version": 1, "members": expected}
        if receipt_path.exists():
            if receipt_path.is_symlink() or json.loads(receipt_path.read_text(encoding="utf-8")) != receipt:
                raise ValueError("interrupted joint cleanup identity changed; retain recovery evidence")
        with ExitStack() as locks:
            for lock in sorted({
                Path(conf.default_repo.worktree_root) / ".finalize.lock" for conf in configs
            }):
                locks.enter_context(finalize_lock.FinalizeLock(lock, timeout=3, stale_after=3600))
            for path in sorted((primary_path, sibling_path)):
                locks.enter_context(launch_admission.guard(path))

            def remove_both(
                current: tracking.WorktreeRecord, current_info: git_ops.WorktreeStateInfo,
            ) -> tuple[int, list[str]]:
                def remove_companion(
                    child: tracking.WorktreeRecord, child_info: git_ops.WorktreeStateInfo,
                ) -> tuple[int, list[str]]:
                    members = (current, child)
                    if [_subject(member) for member in members] != expected:
                        raise ValueError("pair identity changed during joint admission")
                    for member in members:
                        if member.status != "finalized" or not member.checkout_managed:
                            raise ValueError("joint cleanup requires both managed members finalized")
                        _inactive(member)
                        if reason := preservation_reason(member.worktree_path):
                            raise ValueError(f"{member.worktree_id}: {reason}")
                    if receipt_path.exists() and (
                        receipt_path.is_symlink()
                        or json.loads(receipt_path.read_text(encoding="utf-8")) != receipt
                    ):
                        raise ValueError("joint cleanup journal changed during admission")
                    # Preserve the exact operation across one-half removal or
                    # branch/record failures; no filesystem-wide atomicity claim.
                    tracking._atomic_write(receipt_path, json.dumps(receipt, sort_keys=True) + "\n")
                    token = _admitted.set(frozenset(_key(member) for member in members))
                    try:
                        for member, info, conf, path in zip(
                            members, (current_info, child_info), configs, (primary_path, sibling_path),
                        ):
                            # Recheck both execution identities at each physical
                            # boundary, not just the first admission.
                            for subject in members:
                                _inactive(subject)
                            failures, warnings = cleanup_reap._reap_locked(
                                member, info, conf.default_repo, path.parent,
                            )
                            if failures:
                                return failures, warnings
                        for member, path in zip(members, (primary_path, sibling_path)):
                            if not tracking.retire_record(member, path.parent, retain_pair_history=True):
                                return 1, ["joint removal complete; record retirement remains retryable"]
                        receipt_path.unlink()
                        result.update(ok=True, removed=True)
                        return 0, []
                    finally:
                        _admitted.reset(token)

                # Only a retained receipt from an already-admitted operation
                # permits recovery after its own parent checkout disappeared.
                recovery_token = None
                if receipt_path.exists():
                    if (
                        receipt_path.is_symlink()
                        or json.loads(receipt_path.read_text(encoding="utf-8")) != receipt
                    ):
                        raise ValueError("joint cleanup recovery receipt changed")
                    recovery_token = _admitted.set(frozenset(_key(member) for member in (current, sibling)))
                try:
                    child_result = cleanup_gc_cli._revalidate_cleanup_safety(
                        sibling.worktree_id, repo=configs[1].default_repo,
                        tracking_path=sibling_path.parent, include_unused=include_unused,
                        include_conversations=include_conversations, reap=remove_companion,
                    )
                finally:
                    if recovery_token is not None:
                        _admitted.reset(recovery_token)
                if not child_result.cleanable or child_result.failures:
                    return 1, child_result.warnings or [child_result.reason]
                return 0, child_result.warnings

            primary_result = cleanup_gc_cli._revalidate_cleanup_safety(
                primary.worktree_id, repo=configs[0].default_repo,
                tracking_path=primary_path.parent, include_unused=include_unused,
                include_conversations=include_conversations, reap=remove_both,
            )
            if not result["removed"]:
                result["reason"] = "; ".join(primary_result.warnings) or primary_result.reason
                result["warnings"] = primary_result.warnings
    except _REPAIR_ERRORS as exc:
        result["reason"] = str(exc)
    return result
