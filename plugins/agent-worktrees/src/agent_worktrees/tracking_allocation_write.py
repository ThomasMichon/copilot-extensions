"""Origin-owned allocation transactions in the existing protected resource ledger."""

from __future__ import annotations

import copy
import logging
import secrets
from pathlib import Path

import yaml

from . import activity, claim_history, owned_worktree as allocation
from . import owned_worktree_remote, resource_producer as producer, tracking, tracking_write
from .execution_spaces import ExecutionSpaceError
from .resource_relationship import valid_request_id
from .tracking_producer_write import _owned_record, _require_mutable_owner


def _input(operation: object, payload: object) -> dict:
    fields = {
        "reserve": ({"request_id", "target"},),
        "inspect": ({"token"}, {"request_id"}),
        "bind": ({"token"},), "confirm": ({"token"},),
        "close": ({"token"},), "cancel": ({"token"},),
    }
    if not isinstance(operation, str) or operation not in fields:
        raise ValueError("unsupported worktree allocation operation")
    if not isinstance(payload, dict) or set(payload) not in fields[operation]:
        raise ValueError(f"{operation}: invalid worktree allocation input fields")
    producer.canonical_json(payload)
    if "request_id" in payload and not valid_request_id(payload["request_id"]):
        raise ValueError("invalid worktree allocation request_id")
    if "token" in payload and not producer.is_valid_token(payload["token"]):
        raise ValueError("invalid worktree allocation token")
    if operation == "reserve" and not allocation.valid_target(payload["target"]):
        raise ValueError("invalid worktree allocation target")
    return payload


def _matches(record: tracking.WorktreeRecord, payload: dict) -> tracking.ResourceClaim:
    field = "token" if "token" in payload else "request_id"
    matches = [
        claim for claim in record.resources
        if claim.has_producer and isinstance(claim.producer, dict)
        and claim.producer.get("lifetime") == allocation.LIFETIME
        and claim.producer.get(field) == payload[field]
    ]
    if len(matches) != 1:
        raise ValueError("worktree allocation handle is missing or ambiguous")
    claim = matches[0]
    if (
        claim.kind != "worktree" or not allocation.valid_metadata(claim.producer)
        or allocation.claim_ref(claim.producer) != claim.ref
        or claim.producer["creator"] != tracking.format_claim_ref(
            record.execution_machine, record.repo, record.worktree_id,
        )
    ):
        raise ValueError("worktree allocation metadata/ref is malformed; responsibility retained")
    return claim


def _response(record: tracking.WorktreeRecord, claim: tracking.ResourceClaim) -> dict:
    return allocation.origin_response(claim.producer, claim.ref)


def _reserve(
    record: tracking.WorktreeRecord, owner: tracking.ClaimRef, payload: dict,
) -> tuple[tracking.ResourceClaim, bool]:
    from . import claims_cli, config as cfg

    for claim in record.resources:
        if claim.metadata_opaque or claim.has_producer and not producer.valid_metadata(claim.producer):
            raise ValueError("opaque or malformed claim storage prevents allocation reservation")
    existing = [
        claim for claim in record.resources
        if claim.has_producer and isinstance(claim.producer, dict)
        and claim.producer.get("lifetime") == allocation.LIFETIME
        and claim.producer.get("request_id") == payload["request_id"]
    ]
    if existing:
        claim = _matches(record, payload)
        if producer.canonical_json(claim.producer["recovery"]) != producer.canonical_json(payload["target"]):
            raise ValueError("allocation request_id conflicts with its original target facts")
        return claim, False
    _require_mutable_owner(record)
    readiness = claims_cli._coordination_readiness_for_owner_ref(
        owner.canonical(), cfg.load_project_config(owner.project),
    )
    if not readiness.ready:
        raise ValueError(readiness.error or readiness.code)
    metadata = {
        "version": allocation.METADATA_VERSION, "lifetime": allocation.LIFETIME,
        "namespace": allocation.NAMESPACE, "token": secrets.token_urlsafe(32),
        "creator": owner.canonical(), "request_id": payload["request_id"],
        "phase": "pending", "recovery": copy.deepcopy(payload["target"]),
    }
    allocation.require_lifecycle_capacity(
        metadata["creator"], metadata["request_id"], metadata["token"], metadata["recovery"],
    )
    ref = allocation.claim_ref(metadata)
    if any(claim.ref == ref or (
        claim.has_producer and isinstance(claim.producer, dict)
        and claim.producer.get("token") == metadata["token"]
    ) for claim in record.resources):
        raise ValueError("worktree allocation ref/token collision")
    claim = tracking.ResourceClaim(
        kind="worktree", ref=ref, created_at=tracking._now_iso(),
        producer=metadata, producer_present=True,
    )
    record.resources.append(claim)
    return claim, True


def _already_done(operation: str, phase: str) -> bool:
    return (
        operation == "inspect"
        or operation == "bind" and phase in {"bound", "confirmed"}
        or operation == "confirm" and phase == "confirmed"
        or operation in {"close", "cancel"} and phase == "closed"
    )


def _transition(claim: tracking.ResourceClaim, operation: str, proof: dict) -> None:
    metadata = claim.producer
    if not allocation.verified_observation(proof, metadata):
        raise ValueError("receiving owner did not verify the exact original allocation")
    if operation == "bind":
        if proof["status"] not in {"allocated", "confirmed", "finalized", "archived"}:
            raise ValueError("receiving allocation is not durably complete; responsibility retained")
        identity = allocation.AllocationIdentity.from_dict(proof["identity"])
        claim.producer = {**metadata, "phase": "bound", "identity": identity.to_dict()}
        claim.ref = identity.ref
    elif operation == "confirm":
        if proof["status"] != "confirmed":
            raise ValueError("receiving owner did not acknowledge the origin's exact child claim")
        claim.producer = {**metadata, "phase": "confirmed"}
    else:
        if not allocation.verified_conclusion(proof, metadata):
            raise ValueError("receiving owner did not verify exact worktree conclusion")
        claim.producer = {**metadata, "phase": "closed", "closeout": copy.deepcopy(proof)}
        claim.state = "released"


def apply_worktree_allocation(args: dict) -> dict:
    """Reserve before effects; observe outside the lock and compare before commit."""
    try:
        fields = {"schema", "version", "operation", "input", "owner_ref", "yaml_path"}
        if not isinstance(args, dict) or set(args) != fields:
            raise ValueError("worktree allocation request has missing or unknown fields")
        if args["schema"] != allocation.SCHEMA or type(args["version"]) is not int or args["version"] != allocation.VERSION:
            raise ValueError("unsupported worktree allocation schema/version")
        operation = args["operation"]
        payload = _input(operation, args["input"])
        for field in ("owner_ref", "yaml_path"):
            if not isinstance(args[field], str) or not args[field].strip():
                raise ValueError(f"worktree allocation {field} must be a nonempty string")
        path = Path(args["yaml_path"])
        snapshot, proof = None, None
        if operation not in {"reserve", "inspect"}:
            with tracking._RecordLock(path, require_sidecar=True):
                record, _ = _owned_record(args, path, mutable=False)
                claim = _matches(record, payload)
                if _already_done(operation, claim.producer["phase"]):
                    return _response(record, claim)
                _require_mutable_owner(record)
                if tracking.claim_handoff_reservation(record, claim):
                    raise ValueError("worktree allocation is reserved by a handoff")
                if (
                    operation == "bind" and claim.producer["phase"] != "pending"
                    or operation == "confirm" and claim.producer["phase"] != "bound"
                    or operation == "cancel" and claim.producer["phase"] != "pending"
                ):
                    raise ValueError("worktree allocation phase does not permit this operation")
                snapshot = copy.deepcopy(claim.producer)
            remote_operation = {"bind": "inspect", "close": "inspect"}.get(operation, operation)
            proof = owned_worktree_remote.request(remote_operation, snapshot)
        with tracking._RecordLock(path, require_sidecar=True):
            record, owner = _owned_record(args, path, mutable=False)
            if operation == "reserve":
                claim, changed = _reserve(record, owner, payload)
                if not changed:
                    return _response(record, claim)
            else:
                claim = _matches(record, payload)
                if operation == "inspect":
                    return _response(record, claim)
                _require_mutable_owner(record)
                if tracking.claim_handoff_reservation(record, claim):
                    raise ValueError("worktree allocation is reserved by a handoff")
                if producer.canonical_json(claim.producer) != producer.canonical_json(snapshot):
                    raise ValueError("worktree allocation changed during receiving operation; inspect again")
                _transition(claim, operation, proof)
                if any(other is not claim and other.ref == claim.ref for other in record.resources):
                    raise ValueError("worktree allocation ref collides with an existing claim")
            if not allocation.valid_metadata(claim.producer):
                raise ValueError("invalid worktree allocation transition; responsibility retained")
            with producer.transaction(path):
                tracking.save_record(record, path)
        activity.log_event(
            "worktree_allocation_" + operation, worktree_id=record.worktree_id,
            kind=claim.kind, ref=claim.ref, phase=claim.producer["phase"],
        )
        claim_history.record_event(
            kind=claim.kind, ref=claim.ref, worktree_id=record.worktree_id,
            machine=record.execution_machine, event="claimed" if operation == "reserve" else operation,
            project=record.repo,
        )
        return _response(record, claim)
    except (ValueError, TypeError, KeyError, OSError, RuntimeError, ExecutionSpaceError, yaml.YAMLError, RecursionError) as exc:
        logging.getLogger(__name__).warning("Worktree allocation transaction refused: %s", exc)
        return {
            "schema": allocation.SCHEMA, "version": allocation.VERSION,
            "error": "rejected", "message": str(exc),
        }


tracking_write.register_verb("worktree_allocation", apply_worktree_allocation)
