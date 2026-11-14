"""Explicit legacy-child finalization under the retired allocator's custody."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import os
from pathlib import Path

from . import config as cfg, record_machine_identity, tracking


@dataclass(frozen=True)
class _Admission:
    child: tuple[object, ...]
    child_nonce: str | None
    owner_ref: str
    parent: tuple[object, ...]
    parent_nonce: str | None
    parent_path: Path
    claim: dict


_admitted: ContextVar[_Admission | None] = ContextVar("retired_owner_finalization", default=None)


def _proof(record: tracking.WorktreeRecord, config: cfg.Config, owner_ref: str) -> _Admission:
    from . import claimant, execution_spaces, finalize, record_reporting, retained_checkout, sessions, tracking_lookup

    error = execution_spaces.ExecutionSpaceError
    owner = tracking.parse_claim_ref(owner_ref)
    if (
        owner is None or not owner.is_qualified or owner.session
        or record.owner_ref != owner_ref or record.is_paired
        or record.receiving_allocation_present
    ):
        raise error("retired-owner recovery requires the exact original ordinary legacy owner")
    record_reporting.require_current_subject(record, config)
    if not retained_checkout.is_local(record, config):
        raise error("retired-owner recovery requires the actual local linked child checkout")
    if not cfg._PROJECT_NAME_RE.fullmatch(owner.project) or (
        Path(owner.worktree_id).name != owner.worktree_id
        or owner.worktree_id in {"", ".", "..", "@anchor"}
        or "/" in owner.worktree_id or "\\" in owner.worktree_id
    ):
        raise error("retired-owner recovery has an invalid original parent")
    parent_path = cfg.project_dir(owner.project) / "worktrees" / f"{owner.worktree_id}.yaml"
    if parent_path.is_symlink() or not parent_path.is_file():
        raise error("retired-owner recovery requires the original receiving parent record")
    parent = tracking.load_record(parent_path)
    receiving = execution_spaces.project_record_config(parent)
    record_reporting.require_current_subject(parent, receiving)
    if (
        parent.status != "archived" or parent.owner_ref or parent.is_paired
        or Path(parent.worktree_path).exists() or Path(parent.worktree_path).is_symlink()
        or not tracking_lookup.local_receiving_subjects(record, parent)
    ):
        raise error("retired-owner recovery requires an absent archived local root parent")
    current = tracking.load_record(record.yaml_path)
    claim = tracking_lookup.original_owned_claim(current, parent)
    if (
        claim is None or claim.state not in {"abandoned", "released", "at-rest"}
        or claim.handoff_bundle
        or sum(
            tracking.parse_claim_ref(candidate.ref) == tracking.parse_claim_ref(claim.ref)
            for candidate in parent.resources
        ) != 1
    ):
        raise error("retired-owner recovery requires the unique settled original child claim")
    live = sessions.verify_worktree_active(parent, strict=True)
    if (
        not live.probes_ok or live.active or finalize._has_live_session(parent)
        or claimant._confirmed_record_death(parent, receiving) is not False
    ):
        raise error("retired-owner recovery requires positively inactive original ownership")
    return _Admission(
        record_machine_identity.subject_fence(record, record.yaml_path), record.creation_nonce, owner_ref,
        record_machine_identity.subject_fence(parent, parent_path), parent.creation_nonce,
        parent_path, claim.to_dict(),
    )


def _checked_proof(record: tracking.WorktreeRecord, config: cfg.Config, owner_ref: str) -> _Admission:
    from .execution_spaces import ExecutionSpaceError

    try:
        return _proof(record, config, owner_ref)
    except ExecutionSpaceError:
        raise
    except (OSError, ValueError, RuntimeError, TypeError, cfg.yaml.YAMLError) as exc:
        raise ExecutionSpaceError(f"retired-owner receiving proof is unavailable: {exc}") from exc


@contextmanager
def scope(
    worktree_id: str, config: cfg.Config, owner_ref: str | None,
) -> Iterator[None]:
    """Keep admission command-local; never create a receipt or change ownership."""
    if owner_ref is None:
        yield
        return
    path = cfg.tracking_dir(config.repo_name) / f"{worktree_id}.yaml"
    try:
        record = tracking.load_record(path)
    except (OSError, ValueError, RuntimeError, TypeError, cfg.yaml.YAMLError) as exc:
        from .execution_spaces import ExecutionSpaceError

        raise ExecutionSpaceError(f"retired-owner child proof is unavailable: {exc}") from exc
    admission = _checked_proof(record, config, owner_ref)
    token = _admitted.set(admission)
    try:
        yield
    finally:
        _admitted.reset(token)


def selected(record: tracking.WorktreeRecord) -> bool:
    admission = _admitted.get()
    return admission is not None and (
        os.path.normcase(os.path.abspath(record.yaml_path)) == admission.child[0]
    )


def authorizes(record: tracking.WorktreeRecord, config: cfg.Config) -> bool:
    if not selected(record):
        return False
    admission = _admitted.get()
    if _checked_proof(record, config, admission.owner_ref) != admission:
        from .execution_spaces import ExecutionSpaceError

        raise ExecutionSpaceError("retired-owner recovery admission changed")
    return True


@contextmanager
def removal_guard(path: Path) -> Iterator[None]:
    """Exclude parent claim reactivation across the existing bounded cleanup."""
    admission = _admitted.get()
    if admission is None or os.path.normcase(os.path.abspath(path)) != admission.child[0]:
        yield
        return
    with tracking._RecordLock(admission.parent_path, require_sidecar=True):
        yield


def capabilities() -> dict[str, object]:
    return {
        "schema": "agent-worktrees.finalize-recovery",
        "version": 1,
        "retired_owner": {"option": "--recover-retired-owner", "scope": "finalize"},
    }
