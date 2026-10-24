"""Paired knowledge worktrees are non-transferable worktree obligations."""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from typing import TYPE_CHECKING

from . import obligations

if TYPE_CHECKING:
    from .tracking import WorktreeRecord


def paired_ref(record: WorktreeRecord | None) -> str | None:
    if (
        record is not None
        and getattr(record, "pair_role", None) == "harness"
        and getattr(record, "pair_kind", None) == "worktree"
    ):
        return record.pair_ref
    return None


def reciprocal_sibling(record: WorktreeRecord) -> WorktreeRecord | None:
    """Resolve an exact reciprocal worktree pair in either direction."""
    from . import tracking

    try:
        ref = record.pair_claim_ref
        if (
            not record.pair_id or record.pair_kind != "worktree"
            or record.pair_role not in {"harness", "knowledge"}
            or ref is None or not ref.is_qualified
            or ref.machine != record.execution_machine
        ):
            return None
        sibling = tracking.find_paired_record(record)
        if sibling is not None and sibling.status == "finalized" and not sibling.is_paired:
            from . import terminal_pair_progress
            sibling = terminal_pair_progress.reciprocal_snapshot(
                record, sibling, sibling.yaml_path,
            )
        valid = (
            sibling is not None
            and sibling.repo == ref.project
            and sibling.worktree_id == ref.worktree_id
            and sibling.execution_machine == record.execution_machine
            and sibling.platform == record.platform
            and sibling.pair_id == record.pair_id
            and {record.pair_role, sibling.pair_role} == {"harness", "knowledge"}
            and sibling.pair_kind == "worktree"
            and sibling.pair_ref == tracking.format_claim_ref(
                record.execution_machine, record.repo, record.worktree_id,
            )
        )
        return sibling if valid else None
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def sibling_finalized(record: WorktreeRecord) -> bool:
    """Read the exact reciprocal sibling's conclusion, never infer it from Git."""
    sibling = reciprocal_sibling(record)
    return sibling is not None and sibling.status == "finalized"


def require_clearable(record: WorktreeRecord, ref: str) -> None:
    """Generic claim operations cannot bypass the paired sibling's lifecycle."""
    if ref == paired_ref(record) and not sibling_finalized(record):
        raise ValueError(
            f"paired knowledge worktree {ref} must be finalized before its "
            "worktree claim can be settled, released, removed, or transferred"
        )


def require_transferable(record: WorktreeRecord, refs, *, error_type) -> None:
    """Reject premature paired transfers before changing bundle state."""
    for ref in refs:
        try:
            require_clearable(record, ref)
        except ValueError as exc:
            raise error_type(str(exc)) from exc


def ensure_claim(
    record: WorktreeRecord, *, save: bool = True, require_active: bool = False,
) -> None:
    """Reconcile the exact pair; recovery may conservatively require an active claim."""
    from . import tracking

    ref = paired_ref(record)
    if not ref:
        if record.pair_role == "harness" and record.pair_kind == "worktree":
            raise ValueError("paired knowledge worktree identity is not verifiable")
        return
    parsed = record.pair_claim_ref
    if (
        not record.pair_id or parsed is None or not parsed.is_qualified
        or parsed.machine != record.execution_machine
    ):
        raise ValueError("paired knowledge worktree identity is not verifiable")
    state = obligations.ACTIVE if require_active or not sibling_finalized(record) else obligations.AT_REST
    existing = next((claim for claim in record.resources if claim.ref == ref), None)
    if existing is not None and existing.kind != "worktree":
        raise ValueError("paired knowledge worktree claim has a conflicting kind")
    if existing is not None and (existing.has_producer or existing.metadata_opaque):
        raise ValueError("paired knowledge worktree reconciliation cannot replace producer metadata")
    if existing is not None and existing.state == obligations.RELEASED and state == obligations.AT_REST:
        return
    reopened = state == obligations.ACTIVE and tracking.reopen_finalized_owner(
        record, reason="unfinished paired knowledge worktree",
    )
    if (
        state == obligations.ACTIVE and record.kind in tracking.MANAGED_KINDS
        and record.status in {"complete", "completed"}
    ):
        tracking.update_status(record, "active", save=False)
        reopened = True
    if existing is not None and existing.state == state:
        if reopened and save:
            tracking.save_record(record)
        return
    if record.status in {"finalizing", "orphaned"}:
        raise ValueError("paired knowledge worktree reconciliation cannot change a frozen owner")
    if existing is not None:
        from .tracking_claims import claim_handoff_reservation
        if claim_handoff_reservation(record, existing):
            raise ValueError("paired knowledge worktree claim is reserved by a handoff")
        existing.state = state
    else:
        record.resources.append(tracking.ResourceClaim(
            kind="worktree", ref=ref, state=state,
            created_at=tracking._now_iso(),
            note="Paired knowledge worktree; finalize the sibling before the harness.",
        ))
    if save:
        tracking.save_record(record)


def _proof_record(record: WorktreeRecord, current: WorktreeRecord | None) -> WorktreeRecord:
    if current is not None and current.pair_role == "harness" and current.pair_kind == "worktree":
        return current
    return record


def _proof_records(record: WorktreeRecord, current: WorktreeRecord | None) -> list[WorktreeRecord]:
    owner = _proof_record(record, current)
    proofs = [owner]
    ref = paired_ref(record)
    if owner is not record and ref and any(
        getattr(record, name) != getattr(owner, name)
        for name in ("pair_id", "pair_role", "pair_ref", "pair_kind")
    ) and any(claim.ref == ref and not claim.is_unsettled for claim in record.resources):
        proofs.append(record)
    return proofs


def _needs_finalized_proof(record: WorktreeRecord, current: WorktreeRecord | None) -> bool:
    owner = _proof_record(record, current)
    if owner.pair_role != "harness" or owner.pair_kind != "worktree":
        return False
    ref = paired_ref(owner)
    if current is None or current.pair_role != "harness" or current.pair_kind != "worktree":
        return any(
            claim.ref == ref and not claim.is_unsettled for claim in record.resources
        )
    if any(
        getattr(record, name) != getattr(current, name)
        for name in ("pair_id", "pair_role", "pair_ref", "pair_kind")
    ) or (
        record.status in {"finalizing", "finalized"} and record.status != current.status
    ):
        return True
    old = next((claim for claim in current.resources if claim.ref == ref), None)
    claims = [claim for claim in record.resources if claim.ref == ref]
    if old is None:
        return any(not claim.is_unsettled for claim in claims)
    return bool(len(claims) != 1 or claims[0].kind != old.kind or (
        claims[0].state != old.state and not claims[0].is_unsettled
    ))


def validate_save(record: WorktreeRecord, current: WorktreeRecord | None) -> None:
    """A stale full-record writer cannot erase a protected paired claim."""
    if not _needs_finalized_proof(record, current):
        return
    for owner in _proof_records(record, current):
        ref = paired_ref(owner)
        require_clearable(owner, ref)
        if (
            record.status in {"finalizing", "finalized"}
            and any(claim.ref == ref and claim.is_unsettled for claim in record.resources)
        ):
            raise ValueError(
                f"paired knowledge worktree {ref} must be finalized and its claim "
                "settled before harness finalization"
            )


@contextmanager
def lifecycle_guard(record: WorktreeRecord):
    """Fence a sibling probe through the caller's destructive lifecycle action."""
    from . import config as cfg, tracking

    if not record.is_paired or record.pair_kind == "anchor":
        yield
        return
    parsed = record.pair_claim_ref
    if parsed is None or not parsed.is_qualified or parsed.machine != record.execution_machine:
        raise ValueError("paired knowledge worktree finalization cannot be verified")
    sibling_path = cfg.project_dir(parsed.project) / "worktrees" / f"{parsed.worktree_id}.yaml"
    with ExitStack() as stack:
        try:
            # The parent lock is already held; never wait indefinitely on
            # a sibling that may itself be concluding and settling its parent.
            stack.enter_context(tracking._RecordLock(
                sibling_path, timeout=0.5, require_sidecar=True,
            ))
        except TimeoutError as exc:
            raise ValueError(
                "paired knowledge worktree finalization is changing; retry after its lifecycle settles"
            ) from exc
        yield


@contextmanager
def save_guard(record: WorktreeRecord, current: WorktreeRecord | None):
    """Fence the sibling conclusion through a claim-clearing record write."""
    if _needs_finalized_proof(record, current):
        with ExitStack() as stack:
            for owner in sorted(_proof_records(record, current), key=lambda r: paired_ref(r) or ""):
                stack.enter_context(lifecycle_guard(owner))
            yield
    else:
        yield


def assert_settled(record: WorktreeRecord | None, *, emit_error) -> bool:
    ref = paired_ref(record)
    if ref and any(claim.ref == ref and claim.is_unsettled for claim in record.resources):
        emit_error(
            f"Paired knowledge worktree {ref} is not finalized; its worktree "
            "claim cannot be abandoned or handed off. Finalize the sibling first."
        )
        return False
    return True
