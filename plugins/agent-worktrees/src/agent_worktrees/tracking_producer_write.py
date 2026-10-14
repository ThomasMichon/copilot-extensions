"""Registered reservation, allocation-fence and close transactions."""

from __future__ import annotations

import copy
import logging
import secrets
from pathlib import Path

import yaml

from . import activity, claim_history, claim_providers, producer_allocation as allocation
from . import resource_producer as producer
from . import tracking, tracking_write
from .execution_spaces import (
    ExecutionSpaceError, project_record_config, require_owner_identity, require_record_mutation,
    resolve_owner_record_path,
)


def _response(record, claim) -> dict:
    return {
        "schema": producer.SCHEMA, "version": producer.VERSION, "ok": True,
        "owner": tracking.format_claim_ref(record.machine, record.repo, record.worktree_id),
        "creator": claim.producer["creator"], "token": claim.producer["token"],
        "ref": claim.ref, "phase": claim.producer["phase"],
        "producer": copy.deepcopy(claim.producer),
    }


def _input(operation: str, payload: object) -> dict:
    keys = {
        "reserve": {"kind", "namespace", "recovery"},
        "begin": {"token"}, "complete": {"token"},
        "bind": {"token", "identity"}, "inspect": {"token"}, "close": {"token"},
    }
    if not isinstance(operation, str) or operation not in keys:
        raise ValueError(f"unsupported producer operation {operation!r}; expected one of {sorted(keys)}")
    allowed = [keys[operation]]
    if operation in {"bind", "complete"}:
        allowed.append(keys[operation] | {"allocation_fence"})
    if not isinstance(payload, dict) or set(payload) not in allowed:
        options = " or ".join(str(sorted(option)) for option in allowed)
        raise ValueError(f"{operation}: expected exactly {options}")
    producer.canonical_json(payload)
    if operation == "reserve":
        if not isinstance(payload["kind"], str) or not claim_providers.is_safe_argument(payload["kind"]):
            raise ValueError("reserve: kind must be a safe nonempty token")
        if not isinstance(payload["namespace"], str) or not claim_providers.is_safe_argument(payload["namespace"]):
            raise ValueError("reserve: namespace must be a safe nonempty token")
        if not isinstance(payload["recovery"], dict):
            raise ValueError("reserve: recovery must be a JSON object")
    else:
        if not producer.is_valid_token(payload["token"]):
            raise ValueError("invalid producer token")
        if operation == "bind" and not isinstance(payload["identity"], dict):
            raise ValueError("bind: identity must be a JSON object")
        if "allocation_fence" in payload:
            allocation.parse_fence(payload["allocation_fence"])
    return payload


def _require_mutable_owner(record: tracking.WorktreeRecord) -> None:
    if record.status in producer.FROZEN_OWNER_STATUSES:
        raise ValueError(f"producer owner is {record.status}; ownership is frozen")


def _owned_record(args: dict, path: Path, *, mutable: bool = True):
    record = tracking.load_record(path)
    authority = project_record_config(record)
    require_record_mutation(record, authority)
    if not require_owner_identity(record.machine, authority):
        raise ValueError("producer record belongs to a different local execution-space authority")
    owner = tracking.parse_claim_ref(args["owner_ref"])
    if owner is None or not owner.is_qualified or owner.session or (
        owner.machine, owner.project, owner.worktree_id
    ) != (record.machine, record.repo, record.worktree_id):
        raise ValueError("producer owner does not match the current record authority")
    resolved_path, resolved_id, error = resolve_owner_record_path(owner.canonical(), authority)
    if error or resolved_path is None or resolved_id != owner.worktree_id:
        raise ValueError(error or "producer owner ledger cannot be resolved by owning-project authority")
    if path.resolve() != resolved_path.resolve():
        raise ValueError("producer record path does not match owning-project authority")
    if mutable:
        _require_mutable_owner(record)
    return record, owner


def _matched_claim(record, token):
    matches = [c for c in record.resources if c.has_producer and isinstance(c.producer, dict) and c.producer.get("token") == token]
    if len(matches) != 1:
        raise ValueError("producer token is missing or ambiguous in this owner's ledger")
    claim = matches[0]
    if not producer.valid_metadata(claim.producer) or producer.claim_ref(claim.producer) != claim.ref:
        raise ValueError("producer metadata/ref is malformed or unsupported; obligation retained")
    return claim


def apply_resource_producer(args: dict) -> dict:
    """Perform one version-1 producer operation, returning explicit errors."""
    try:
        if not isinstance(args, dict):
            raise ValueError("resource-producer request must be an object")
        if args.get("schema") != producer.SCHEMA or type(args.get("version")) is not int or args["version"] != producer.VERSION:
            raise ValueError("unsupported resource-producer schema/version")
        for field in ("operation", "input", "yaml_path", "owner_ref"):
            if field not in args:
                raise ValueError(f"missing required resource-producer field: {field}")
        operation = args["operation"]
        payload = _input(operation, args["input"])
        for field in ("yaml_path", "owner_ref"):
            if not isinstance(args[field], str) or not args[field].strip():
                raise ValueError(f"resource-producer field {field} must be a nonempty string")
        path = Path(args["yaml_path"])
        probe_subject, probe_result, granted_fence = None, None, None
        if operation in {"close", "begin", "bind", "complete"}:
            with tracking._RecordLock(path, require_sidecar=True):
                snapshot, _ = _owned_record(args, path, mutable=operation != "close")
                candidate = _matched_claim(snapshot, payload["token"])
                phase = candidate.producer["phase"]
                if operation == "close":
                    if tracking.claim_handoff_reservation(snapshot, candidate):
                        raise ValueError("producer claim is reserved by an offered handoff")
                    if phase == "closed":
                        return _response(snapshot, candidate)
                    _require_mutable_owner(snapshot)
                if operation == "close" and phase == "allocating":
                    raise ValueError("allocation is in progress; complete or recover it before close")
                if (
                    operation == "close" and phase in {"pending", "bound"}
                    or operation == "begin" and phase == "pending" and "allocation_epoch" in candidate.producer
                    or operation == "bind" and phase in {"pending", "allocating"} and "allocation_fence" not in payload
                    or operation == "complete" and phase == "allocating" and "allocation_fence" not in payload
                ):
                    if tracking.claim_handoff_reservation(snapshot, candidate):
                        raise ValueError("producer claim is reserved by an offered handoff")
                    probe_subject = candidate.ref
            if probe_subject is not None:
                # Provider I/O is outside RecordLock; the final RMW validates
                # the subject including phase and non-repeating allocation epoch.
                probe_result = claim_providers.resolve_claim_status(probe_subject)
        with tracking._RecordLock(path, require_sidecar=True):
            record, owner = _owned_record(args, path, mutable=operation not in {"inspect", "close"})
            if operation == "reserve":
                metadata = {
                    "version": producer.VERSION, "lifetime": "disposable-owned",
                    "token": secrets.token_urlsafe(32), "namespace": payload["namespace"],
                    "phase": "pending", "creator": owner.canonical(),
                    "recovery": copy.deepcopy(payload["recovery"]),
                }
                ref = producer.claim_ref(metadata)
                if any(c.ref == ref or (isinstance(c.producer, dict) and c.producer.get("token") == metadata["token"]) for c in record.resources):
                    raise ValueError("producer namespace/ref or token collision")
                claim = tracking.ResourceClaim(
                    kind=payload["kind"], ref=ref, created_at=tracking._now_iso(),
                    producer=metadata, producer_present=True,
                )
                record.resources.append(claim)
            else:
                claim = _matched_claim(record, payload["token"])
                if operation == "inspect":
                    return _response(record, claim)
                if tracking.claim_handoff_reservation(record, claim):
                    raise ValueError("producer claim is reserved by an offered handoff")
                phase = claim.producer["phase"]
                if phase == "closed" and operation == "close":
                    return _response(record, claim)
                if operation == "close":
                    _require_mutable_owner(record)
                if phase not in {"pending", "allocating", "bound"}:
                    raise ValueError(f"producer claim is {phase}; mutation refused")
                if operation == "begin":
                    if "allocation_epoch" in claim.producer and (
                        claim.ref != probe_subject or not isinstance(probe_result, dict)
                        or probe_result.get("available") is not True
                        or not producer.verified_absence(probe_result, claim.ref)
                    ):
                        raise ValueError("provider did not verify no allocation before retry; obligation retained")
                    granted_fence = allocation.begin(claim)
                elif operation == "bind":
                    identity = copy.deepcopy(payload["identity"])
                    if phase == "bound":
                        if "allocation_fence" in payload and not allocation.matches_fence(claim.producer, payload["allocation_fence"]):
                            raise ValueError("allocation fence does not match the current attempt")
                        if producer.canonical_json(claim.producer["identity"]) != producer.canonical_json(identity):
                            raise ValueError("bound producer identity is immutable")
                        return _response(record, claim)
                    if "allocation_fence" not in payload and claim.ref != probe_subject:
                        raise ValueError("allocation recovery subject changed; obligation retained")
                    allocation.bind(
                        claim, identity, fence=payload.get("allocation_fence"), result=probe_result,
                    )
                    if any(c is not claim and c.ref == claim.ref for c in record.resources):
                        raise ValueError("producer namespace/ref collision")
                elif operation == "complete":
                    if "allocation_fence" not in payload and phase == "allocating" and claim.ref != probe_subject:
                        raise ValueError("allocation recovery subject changed; obligation retained")
                    if not allocation.complete(
                        claim, fence=payload.get("allocation_fence"), result=copy.deepcopy(probe_result),
                    ):
                        return _response(record, claim)
                else:
                    result = probe_result
                    if phase == "allocating":
                        raise ValueError("allocation is in progress; obligation retained")
                    if claim.ref != probe_subject or not isinstance(result, dict) or result.get("available") is not True or not producer.verified_absence(result, claim.ref):
                        raise ValueError("registered provider did not verify exact producer absence; obligation retained")
                    claim.producer = {**claim.producer, "phase": "closed", "closeout": copy.deepcopy(result)}
                    claim.state = "released"
            if not producer.valid_metadata(claim.producer):
                raise ValueError("producer transition metadata is malformed or exceeds limits; obligation retained")
            with producer.transaction(path):
                tracking.save_record(record, path)
        activity.log_event(
            "resource_producer_" + operation, worktree_id=record.worktree_id,
            kind=claim.kind, ref=claim.ref, phase=claim.producer["phase"],
        )
        claim_history.record_event(
            kind=claim.kind, ref=claim.ref, worktree_id=record.worktree_id,
            machine=record.machine, event="claimed" if operation == "reserve" else operation,
            project=record.repo,
        )
        response = _response(record, claim)
        if granted_fence is not None:
            response["allocation_fence"] = granted_fence.to_dict()
        return response
    except (ValueError, TypeError, KeyError, OSError, ExecutionSpaceError, yaml.YAMLError, RecursionError) as exc:
        logging.getLogger(__name__).warning("Resource producer transaction refused: %s", exc)
        return {"schema": producer.SCHEMA, "version": producer.VERSION, "error": "rejected", "message": str(exc)}


tracking_write.register_verb("resource_producer", apply_resource_producer)
