"""Read a finalized worker allocation without finalizing or retiring anything."""

from __future__ import annotations

from pathlib import Path

from . import config as cfg, finalized_checkout, sessions, tracking
from .dispatch_worker_closeout import CONTRACT, _text
from .dispatch_worker_sessions import require_no_foreign_hold
from .execution_spaces import require_record_mutation
from .record_read import read_exact_record, read_workspace_record


def describe_contract() -> dict:
    """Advertise this runtime's explicit, read-only observation boundary."""
    return {
        "contract": CONTRACT, "version": 1, "operations": ["observe-finalized"],
        "platforms": ["linux", "wsl"], "paired_closeout": False,
        "task_release": False, "process_retirement": False,
    }


def observe(worktree_id: str, task_id: str, reservation_key: str, native_id: str) -> dict:
    """Recheck exact project/allocation/head and actual finalized checkout."""
    for name, value in (
        ("task ID", task_id), ("reservation key", reservation_key), ("native ID", native_id),
    ):
        _text(value, name)
    metadata = read_exact_record(worktree_id)
    path = cfg.tracking_dir() / f"{worktree_id}.yaml"
    with tracking._RecordLock(path, require_sidecar=True):
        record = tracking.load_record(path)
        config = cfg.load_config()
        if config.platform not in {"linux", "wsl"}:
            raise ValueError("Finalized worker observation is not qualified on this platform")
        require_record_mutation(record, config)
        allocation = record.dispatch_attempt
        entry = record.session_entry(native_id)
        if (
            record.repo != config.repo_name or record.worktree_id != worktree_id
            or record.worktree_path != metadata["path"]
            or record.creation_nonce != metadata["creation_nonce"]
            or not record.creation_nonce or not record.checkout_managed
            or allocation is None or record.dispatch_attempt_opaque
            or allocation.task_id != task_id or allocation.reservation_key != reservation_key
            or allocation.creator_machine.casefold() != record.execution_machine.casefold()
            or record.resolved_head_session != native_id
            or record.head_session not in {None, native_id}
            or entry is None or entry.state != "active"
            or record.pending_handoffs or tracking.effective_open_follow_up_count(record)
            or record.active_effort is not None or record.is_paired
            or record.active_controllers or record.controller_metadata_opaque
            or record.execution_leg_opaque or record.session_backend_opaque
            or any(session.session_id != native_id and session.state == "active"
                   for session in record.sessions or [])
        ):
            raise ValueError("Finalized worker observation retains changed or protected custody")
        context = read_workspace_record(str(Path(record.worktree_path).resolve(strict=True)))
        workspace = context["worktree"]
        if not isinstance(workspace, dict) or (
            workspace["id"] != worktree_id or workspace["creation_nonce"] != record.creation_nonce
        ):
            raise ValueError("Finalized worker observation requires its actual managed checkout")
        own_ref = tracking.format_claim_ref(
            record.execution_machine, record.repo, worktree_id, session=native_id,
        )
        if any(claim.is_unsettled and not (claim.kind == "session" and claim.ref == own_ref)
               for claim in record.resources):
            raise ValueError("Finalized worker observation retains unsettled outbound responsibility")
        sources = [
            activation.source_identity for activation in entry.activations
            if activation.source_identity and not activation.source_ambiguous
            and activation.ended_at is None
        ]
        if len(sources) != 1:
            raise ValueError("Finalized worker observation requires its canonical native source")
        require_no_foreign_hold(record, native_id, sources[0])
        live = sessions.scan_sessions_fast([record]).active_sessions.get(
            sessions._normalize_path(record.worktree_path), [],
        )
        if any(native != native_id for native in live):
            raise ValueError("Finalized worker observation retains another live workspace session")
        if not finalized_checkout.matches(record):
            raise ValueError("Finalized worker checkout is not currently clean and finalized")
        return {
            "contract": CONTRACT, "version": 1, "operation": "observe-finalized",
            "task_id": task_id, "reservation_key": reservation_key, "native_id": native_id,
            "worktree_id": worktree_id, "creation_nonce": record.creation_nonce,
            "project": record.repo, "path": record.worktree_path,
            "machine": record.execution_machine, "head_revision": record.head_revision,
            "allocation": allocation.to_dict(), "checkout": record.finalized_checkout,
            "status": "finalized", "checkout_retained": True,
            "task_release": False, "process_retirement": False,
        }
