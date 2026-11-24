"""Pair-scoped cleanup admission and retained interrupted-operation evidence."""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
import json
import os
import re
import secrets
from pathlib import Path
from collections.abc import Callable, Iterable, Iterator
from types import MappingProxyType
from typing import Any

from . import config as cfg, finalize_lock, launch_admission, paired_claims
from . import checkout_durability, git_ops, procs, sessions, terminal_pair_progress, tracking
from .creation_tail_repair import _REPAIR_ERRORS
from .checkout_data import preservation_reason
from .git_repository_environment import repository_identity_scope
from .paired_cleanup_state import (
    ReceiptProof as _ReceiptProof, absent as _absent, family as _family,
    key as _key, physical as _physical, retained as _retained,
    subject as _subject, read_receipt as _read,
    journal_path as journal_path, completed_path as completed_path,
    completed_history as completed_history,
)

_admitted: ContextVar[frozenset[tuple[str | None, ...]]] = ContextVar(
    "paired_cleanup_admission", default=frozenset(),
)


def admitted(record: tracking.WorktreeRecord) -> bool:
    """Only the lock-held native joint operation can discharge a pair."""
    return _key(record) in _admitted.get()


def _branch_head(record: tracking.WorktreeRecord, config: cfg.Config) -> str | None:
    observed = git_ops.git(
        "rev-parse", "--verify", "--quiet", f"refs/heads/{record.branch}^{{commit}}",
        cwd=config.default_repo.anchor, check=False,
    )
    value = observed.stdout.strip()
    if observed.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", value):
        return value
    if observed.returncode == 1 and not value:
        return None
    raise ValueError(f"{record.worktree_id}: branch identity observation is unavailable")


def _compatible(current: dict[str, Any], retained: dict[str, Any]) -> bool:
    if (
        {name: value for name, value in current.items() if name != "branch_heads"}
        != {name: value for name, value in retained.items() if name != "branch_heads"}
    ):
        return False
    old_heads = retained.get("branch_heads")
    if not isinstance(old_heads, list) or len(old_heads) != len(current["branch_heads"]):
        return False
    for now, old, member in zip(current["branch_heads"], old_heads, current["members"]):
        if not isinstance(old, dict) or set(old) != {"repo", "worktree_id", "head"}:
            return False
        old_head = old["head"]
        if old_head is not None and (
            not isinstance(old_head, str) or re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", old_head) is None
        ):
            return False
        if {**now, "head": old_head} != old:
            return False
        if now["head"] != old_head and not (now["head"] is None and _absent(member["worktree_path"])):
            return False
    return True


def _capture(
    members: tuple[tracking.WorktreeRecord, tracking.WorktreeRecord], configs: list[cfg.Config],
) -> tuple[dict[str, Any], _ReceiptProof]:
    if len(configs) != 2 or any(member.repo != config.repo_name for member, config in zip(members, configs)):
        raise ValueError("joint cleanup receiving configuration changed")
    if any(not isinstance(member.creation_nonce, str) or not member.creation_nonce for member in members):
        raise ValueError("joint cleanup requires both native allocation incarnations")
    families = {member.repo: _family(config) for member, config in zip(members, configs)}
    paths = {(member.repo, member.worktree_id): _physical(member.worktree_path) for member in members}
    receipt = {
        "version": 2, "members": [_subject(member) for member in members],
        "families": [{"repo": repo, "identity": list(value)} for repo, value in sorted(families.items())],
        "paths": [{"repo": repo, "worktree_id": identity, "path": value}
                  for (repo, identity), value in sorted(paths.items())],
        "branch_heads": [{"repo": member.repo, "worktree_id": member.worktree_id,
                          "head": _branch_head(member, config)} for member, config in zip(members, configs)],
    }
    return receipt, _ReceiptProof(
        frozenset(_key(member) for member in members),
        MappingProxyType(families), MappingProxyType(paths),
    )


def _write(path: Path, receipt: dict[str, Any]) -> None:
    encoded = json.dumps(receipt, sort_keys=True) + "\n"
    if len(encoded.encode("utf-8")) > 64 * 1024:
        raise ValueError("joint cleanup receipt exceeds its retained format limit")
    staging = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    created = False
    try:
        descriptor = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        created = True
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        checkout_durability.replace(staging, path, existing=True)
        checkout_durability.directory(path.parent)
    finally:
        if created:
            staging.unlink(missing_ok=True)


def _prepare_members(
    members: tuple[tracking.WorktreeRecord, tracking.WorktreeRecord],
    paths: tuple[Path, Path], receipt_path: Path,
) -> tuple[tracking.WorktreeRecord, tracking.WorktreeRecord]:
    from . import root_chain, worktree_lifecycle

    with ExitStack() as preparation:
        for path in sorted(paths):
            preparation.enter_context(worktree_lifecycle.guard(path))
            preparation.enter_context(tracking._RecordLock(path, require_sidecar=True))
        current = tuple(tracking.load_record(path) for path in paths)
        if [_subject(member) for member in current] != [_subject(member) for member in members]:
            raise ValueError("pair identity changed before joint admission")
        if any(_absent(member.worktree_path) for member in current) and not receipt_path.exists():
            raise ValueError("missing pair checkout requires an existing validated joint-cleanup receipt")
        if any(not member.creation_nonce for member in current):
            if receipt_path.exists():
                raise ValueError("retained receipt cannot be rebound to new pair incarnations")
            for member in current:
                if member.status != "finalized":
                    raise ValueError("joint cleanup requires both members finalized before native migration")
                _inactive(member)
            for member in current:
                if not root_chain._ensure_creation_nonce(member, member.repo):
                    raise ValueError("native pair incarnation migration did not complete")
            flush: Callable[[Iterable[Path]], None] = checkout_durability.files
            flush(paths)
    return current[0], current[1]


@contextmanager
def _retained_scope(
    members: tuple[tracking.WorktreeRecord, tracking.WorktreeRecord],
    configs: list[cfg.Config], path: Path,
) -> Iterator[None]:
    if not path.exists():
        yield
        return
    expected, proof = _capture(members, configs)
    if not _compatible(expected, _read(path)):
        raise ValueError("joint cleanup retained identity or Git family changed")
    token = _retained.set(proof)
    try:
        yield
    finally:
        _retained.reset(token)


def require_joint(record: tracking.WorktreeRecord) -> None:
    pair_evidence = any((record.pair_id, record.pair_role, record.pair_ref, record.pair_kind))
    anchor_pair = record.pair_kind == "anchor" and record.pair_role == "harness"
    if pair_evidence and not anchor_pair and not admitted(record):
        raise ValueError(
            "paired checkout removal requires cleanup --worktree-id <harness-id> --paired; "
            "both members and harness inactivity must be admitted together"
        )


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
        receipt_path = journal_path(primary)
        sibling = paired_claims.reciprocal_sibling(primary)
        if sibling is None and receipt_path.exists():
            ref = primary.pair_claim_ref
            if ref is not None and ref.is_qualified and ref.project is not None:
                retained_path = cfg.project_dir(ref.project) / "worktrees" / f"{ref.worktree_id}.yaml"
                if retained_path.is_symlink():
                    raise ValueError("joint cleanup refuses a redirected retained companion")
                sibling = tracking.load_record(retained_path)
        if sibling is None:
            raise ValueError("joint cleanup requires the exact reciprocal companion record")
        sibling_path = cfg.project_dir(sibling.repo) / "worktrees" / f"{sibling.worktree_id}.yaml"
        if sibling_path.is_symlink():
            raise ValueError("joint cleanup refuses a redirected companion record")
        configs = [
            cfg.load_project_config(member.repo, include_control_plane_related_pr=False)
            for member in (primary, sibling)
        ]
        with ExitStack() as locks:
            for lock in sorted({
                Path(conf.default_repo.worktree_root) / ".finalize.lock" for conf in configs
            }):
                locks.enter_context(finalize_lock.FinalizeLock(lock, timeout=3, stale_after=3600))
            for path in sorted((primary_path, sibling_path)):
                locks.enter_context(launch_admission.guard(path))
            primary, sibling = _prepare_members(
                (primary, sibling), (primary_path, sibling_path), receipt_path,
            )
            expected = [_subject(member) for member in (primary, sibling)]
            if not terminal_pair_progress.reciprocal_identity_valid(*expected):
                raise ValueError("joint cleanup pair identity is not reciprocal")
            if completed_history(primary, sibling):
                from .retirement_safety import retirement_blocker

                for member in (primary, sibling):
                    blocker = retirement_blocker(member)
                    if blocker is not None:
                        result["reason"] = blocker.reason
                        return result
                    if tracking.effective_open_follow_up_count(member):
                        result["reason"] = f"{member.worktree_id}: open follow-ups remain"
                        return result
                result.update(ok=True, already_retired=True, reason="pair already retired; completed history retained")
                return result
            receipt, _ = _capture((primary, sibling), configs)
            if receipt_path.exists():
                retained_receipt = _read(receipt_path)
                if not _compatible(receipt, retained_receipt):
                    raise ValueError("interrupted joint cleanup identity or branch changed; retain recovery evidence")
                receipt = retained_receipt

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
                    if receipt_path.exists() and _read(receipt_path) != receipt:
                        raise ValueError("joint cleanup journal changed during admission")
                    current_receipt, proof = _capture(members, configs)
                    if not _compatible(current_receipt, receipt):
                        raise ValueError("joint cleanup Git family, location or branch changed")
                    for member, head in zip(members, current_receipt["branch_heads"]):
                        if Path(member.worktree_path).exists() and head["head"] is None:
                            raise ValueError("joint cleanup cannot prove the extant checkout's branch")
                    # Preserve the exact operation across one-half removal or
                    # branch/record failures; no filesystem-wide atomicity claim.
                    flush: Callable[[Iterable[Path]], None] = checkout_durability.files
                    record_paths = (primary_path, sibling_path)
                    flush(record_paths)
                    _write(receipt_path, receipt)
                    retained_token = _retained.set(proof)
                    token = _admitted.set(frozenset(_key(member) for member in members))
                    try:
                        for member, info, conf, path in zip(
                            members, (current_info, child_info), configs, (primary_path, sibling_path),
                        ):
                            # Recheck both execution identities at each physical
                            # boundary, not just the first admission.
                            observed_receipt, _ = _capture(members, configs)
                            if not _compatible(observed_receipt, receipt):
                                raise ValueError("joint cleanup branch or allocation changed before removal")
                            for subject in members:
                                _inactive(subject)
                            failures, warnings = cleanup_reap._reap_locked(
                                member, info, conf.default_repo, path.parent,
                                before_remove=lambda: flush(record_paths),
                            )
                            result["removed"] = all(_absent(subject.worktree_path) for subject in members)
                            if failures:
                                return failures, warnings
                        for member, conf in zip(members, configs):
                            if member.branch != conf.default_repo.default_branch and _branch_head(member, conf) is not None:
                                return 1, ["joint checkout removal complete; a branch remains and history is retained"]
                        for member, path in zip(members, (primary_path, sibling_path)):
                            if not tracking.retire_record(member, path.parent, retain_pair_history=True):
                                return 1, ["joint removal complete; record retirement remains retryable"]
                        flush(record_paths)
                        completed = {
                            **receipt, "phase": "completed", "operation_members": receipt["members"],
                            "members": [_subject(tracking.load_record(path)) for path in (primary_path, sibling_path)],
                        }
                        _write(receipt_path, completed)
                        result.update(ok=True, removed=True)
                        return 0, []
                    finally:
                        _admitted.reset(token)
                        _retained.reset(retained_token)

                child_result = cleanup_gc_cli._revalidate_cleanup_safety(
                    sibling.worktree_id, repo=configs[1].default_repo,
                    tracking_path=sibling_path.parent, include_unused=include_unused,
                    include_conversations=include_conversations, reap=remove_companion,
                )
                if not child_result.cleanable or child_result.failures:
                    return 1, child_result.warnings or [child_result.reason]
                return 0, child_result.warnings

            # A retained receipt restores identity evidence, not deletion
            # permission. Both canonical lifecycle admissions must still pass.
            with _retained_scope((primary, sibling), configs, receipt_path):
                primary_result = cleanup_gc_cli._revalidate_cleanup_safety(
                    primary.worktree_id, repo=configs[0].default_repo,
                    tracking_path=primary_path.parent, include_unused=include_unused,
                    include_conversations=include_conversations, reap=remove_both,
                )
            if not result["ok"]:
                result["reason"] = "; ".join(primary_result.warnings) or primary_result.reason
                result["warnings"] = primary_result.warnings
    except _REPAIR_ERRORS as exc:
        result["reason"] = str(exc)
    return result
