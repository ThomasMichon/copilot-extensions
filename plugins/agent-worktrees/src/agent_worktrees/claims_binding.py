"""Exact creator-claim selectors; never task-terminal or settlement authority."""

from __future__ import annotations

import argparse
from typing import Any

from . import config as cfg, output, tracking
from .tracking_claims import ResourceClaim

CONTRACT = "context-handoff-creator-claim/v1"


def for_claim(record: tracking.WorktreeRecord, claim: ResourceClaim) -> dict[str, Any] | None:
    """Project an already-persisted task claim's exact incarnation."""
    if (
        claim.kind != "task" or claim.has_producer or claim.metadata_opaque
        or not record.creation_nonce or not claim.claim_nonce
        or not record.execution_machine or not record.repo or not record.worktree_id
    ):
        return None
    return {
        "contract": CONTRACT,
        "machine": record.execution_machine,
        "project": record.repo,
        "worktree_id": record.worktree_id,
        "creation_nonce": record.creation_nonce,
        "claim_nonce": claim.claim_nonce,
        "kind": claim.kind,
        "ref": claim.ref,
    }


def cmd_binding(args: argparse.Namespace, ref: str) -> int:
    """Inspect, or explicitly qualify, one owner-selected legacy task claim."""
    from . import claims_cli, resident_recovery, tracking_write

    try:
        owner_ref = getattr(args, "claim_owner_ref", None)
        if not owner_ref:
            raise ValueError("claims binding requires a qualified --owner-ref")
        path, worktree_id, error = claims_cli._resolve_owner_ref_record_path(owner_ref, cfg.load_config())
        if error or path is None:
            raise ValueError(error or "creator claim binding requires its local machine owner")
        if args.apply:
            result = resident_recovery.dispatch_claim("claim_bind", {
                "yaml_path": str(path), "worktree_id": worktree_id, "ref": ref,
            })
            if result.get("error"):
                raise ValueError(result.get("message", "creator claim binding is unavailable"))
            binding = result.get("claim_binding")
        else:
            record = tracking.load_record(path)
            claim = next((claim for claim in record.resources if claim.ref == ref), None)
            binding = for_claim(record, claim) if claim is not None else None
        if binding is None:
            raise ValueError("creator claim has no verified binding; an explicit owner-qualified --apply is required")
    except tracking_write.ResidentRecoveryHeld as exc:
        return tracking_write.refuse_recovery(exc, json_output=args.json)
    except (OSError, ValueError, tracking_write.AmbiguousWriteOutcome) as exc:
        if args.json:
            return output._json_error(str(exc))
        output.err(str(exc))
        return 1
    if args.json:
        output._json_output({"claim_binding": binding})
    else:
        print(f"creator claim {ref}: {binding['machine']}/{binding['project']}/{binding['worktree_id']}")
    return 0
