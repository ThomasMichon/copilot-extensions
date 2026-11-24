"""Receipt facts shared by native identity readers; no cleanup actors on import."""

from __future__ import annotations

from collections.abc import Mapping
from contextvars import ContextVar
from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, cast

if TYPE_CHECKING:
    from . import config as cfg, tracking


@dataclass(frozen=True)
class ReceiptProof:
    members: frozenset[tuple[str | None, ...]]
    families: Mapping[str, tuple[int, int]]
    paths: Mapping[tuple[str, str], str]


retained: ContextVar[ReceiptProof | None] = ContextVar("paired_cleanup_retained", default=None)


def needs_retirement(record: tracking.WorktreeRecord, sibling: tracking.WorktreeRecord | None) -> bool:
    """Select an unfinished pair tombstone, never a completed retained history."""
    return bool(
        sibling is not None and not completed_history(record, sibling)
        and sibling.status == "finalized" and sibling.reaped_at
    )


def key(record: tracking.WorktreeRecord) -> tuple[str | None, ...]:
    return (
        record.repo, record.worktree_id, record.creation_nonce, record.worktree_path,
        record.branch, record.pair_id, record.pair_role, record.pair_kind, record.pair_ref,
        record.owner_ref,
    )


def subject(record: tracking.WorktreeRecord) -> dict[str, Any]:
    from . import terminal_pair_progress, tracking

    primary_identity = cast(
        Callable[[tracking.WorktreeRecord], dict[str, Any]], terminal_pair_progress.primary_identity,
    )
    return {
        **primary_identity(record), "creation_nonce": record.creation_nonce,
        "owner_ref": record.owner_ref,
    }


def journal_path(record: tracking.WorktreeRecord) -> Path:
    from . import config as cfg

    return cfg.project_dir(record.repo) / "worktrees" / f"{record.worktree_id}.paired-cleanup.json"


def completed_path(record: tracking.WorktreeRecord) -> Path:
    return journal_path(record)


def family(config: cfg.Config) -> tuple[int, int]:
    from .retained_checkout import _git_directory

    identity = _git_directory(Path(config.default_repo.anchor)).stat()
    if not identity.st_ino:
        raise ValueError("receiving Git family identity is unavailable")
    return identity.st_dev, identity.st_ino


def physical(path: str) -> str:
    return os.path.normcase(os.path.normpath(str(Path(path).resolve())))


def absent(path: str) -> bool:
    try:
        Path(path).lstat()
    except FileNotFoundError:
        return True
    return False


def read_receipt(path: Path) -> dict[str, Any]:
    if path.is_symlink() or path.stat().st_size > 64 * 1024:
        raise ValueError("joint cleanup receipt is redirected or oversized")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or type(value.get("version")) is not int or value["version"] != 2:
        raise ValueError("joint cleanup receipt version is unsupported")
    return value


def _matches_proof(record: tracking.WorktreeRecord, config: cfg.Config) -> bool:
    proof = retained.get()
    if proof is None or key(record) not in proof.members or record.repo != config.repo_name:
        return False
    from . import config as cfg, tracking

    path = cfg.project_dir(record.repo) / "worktrees" / f"{record.worktree_id}.yaml"
    if path.is_symlink() or record.yaml_path.resolve() != path.resolve():
        return False
    current = tracking.load_record(path)
    return (
        key(current) == key(record) and family(config) == proof.families.get(record.repo)
        and physical(record.worktree_path) == proof.paths.get((record.repo, record.worktree_id))
    )


def authorizes_retired_member(record: tracking.WorktreeRecord, config: cfg.Config) -> bool:
    """Recognize only this journal's positively absent original allocation."""
    if retained.get() is None:
        return False
    return absent(record.worktree_path) and _matches_proof(record, config)


def authorizes_retired_parent(record: tracking.WorktreeRecord, config: cfg.Config) -> bool:
    """Keep the original paired-child permission after this pass removes its parent."""
    if (
        retained.get() is None or record.repo != config.repo_name
        or record.pair_role != "knowledge" or record.pair_kind != "worktree"
        or not record.owner_ref or record.owner_ref != record.pair_ref
    ):
        return False
    ref = record.pair_claim_ref
    if ref is None or not ref.is_qualified:
        return False
    from . import config as cfg, terminal_pair_progress, tracking

    parent = tracking.load_record(cfg.project_dir(ref.project) / "worktrees" / f"{ref.worktree_id}.yaml")
    receiving = cfg.load_project_config(parent.repo, include_control_plane_related_pr=False)
    return (
        terminal_pair_progress.reciprocal_identity_valid(subject(record), subject(parent))
        and _matches_proof(record, config) and authorizes_retired_member(parent, receiving)
    )


def retained_pair_subjects_local(
    record: tracking.WorktreeRecord, sibling: tracking.WorktreeRecord,
) -> bool:
    if retained.get() is None:
        return False
    from . import config as cfg
    from .record_reporting import same_reporter
    from .retained_checkout import recorded_branch_matches

    if not same_reporter(record, sibling):
        return False
    for member in (record, sibling):
        receiving = cfg.load_project_config(member.repo, include_control_plane_related_pr=False)
        if not _matches_proof(member, receiving):
            return False
        if not authorizes_retired_member(member, receiving) and recorded_branch_matches(member, receiving) is not True:
            return False
    return True


def completed_history(record: tracking.WorktreeRecord, sibling: tracking.WorktreeRecord) -> bool:
    """Advisory selection only; a completion receipt never authorizes removal."""
    parent, child = (record, sibling) if record.pair_role == "harness" else (sibling, record)
    try:
        receipt = read_receipt(completed_path(parent))
        return (
            receipt.get("phase") == "completed"
            and receipt.get("members") == [subject(parent), subject(child)]
            and all(member.status == "finalized" and member.reaped_at
                    and not Path(member.worktree_path).exists() for member in (parent, child))
        )
    except (OSError, ValueError, RuntimeError, TypeError):
        return False
