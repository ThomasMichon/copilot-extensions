"""Locked, replayable retained-relationship transactions in the claim ledger."""

from __future__ import annotations

import copy
import logging
import secrets
from pathlib import Path

import yaml

from . import activity, claim_history, claim_providers
from . import resource_producer as producer, resource_relationship as relationship
from . import tracking, tracking_write
from .execution_spaces import ExecutionSpaceError
from .tracking_producer_write import _owned_record, _require_mutable_owner


def _input(operation: str, payload: object) -> dict:
    keys = {
        "reserve": ({"kind", "namespace", "request_id", "recovery"},),
        "inspect": ({"token"}, {"request_id"}),
        "bind": ({"token", "identity"},),
        "close": ({"token"},),
    }
    if not isinstance(operation, str) or operation not in keys:
        raise ValueError("unsupported relationship operation")
    if not isinstance(payload, dict) or set(payload) not in keys[operation]:
        raise ValueError(f"{operation}: invalid relationship input fields")
    producer.canonical_json(payload)
    if "token" in payload and not producer.is_valid_token(payload["token"]):
        raise ValueError("invalid relationship token")
    if "request_id" in payload and not relationship.valid_request_id(payload["request_id"]):
        raise ValueError("invalid relationship request_id")
    if operation == "reserve":
        for key in ("kind", "namespace"):
            if not isinstance(payload[key], str) or not claim_providers.is_safe_argument(payload[key]):
                raise ValueError(f"reserve: invalid relationship {key}")
        if not isinstance(payload["recovery"], dict):
            raise ValueError("reserve: recovery must be a JSON object")
    if operation == "bind" and not isinstance(payload["identity"], dict):
        raise ValueError("bind: identity must be a JSON object")
    return payload


def _matches(record, payload):
    field = "token" if "token" in payload else "request_id"
    matches = [
        claim for claim in record.resources
        if claim.has_producer and isinstance(claim.producer, dict)
        and claim.producer.get("lifetime") == relationship.LIFETIME
        and claim.producer.get(field) == payload[field]
    ]
    if len(matches) != 1:
        raise ValueError("relationship handle is missing or ambiguous in this owner's ledger")
    claim = matches[0]
    if not relationship.valid_metadata(claim.producer) or relationship.claim_ref(claim.producer) != claim.ref:
        raise ValueError("relationship metadata/ref is malformed; obligation retained")
    return claim


def _response(record, claim):
    return {
        "schema": relationship.SCHEMA, "version": relationship.VERSION, "ok": True,
        "owner": tracking.format_claim_ref(record.machine, record.repo, record.worktree_id),
        **{key: claim.producer[key] for key in ("creator", "request_id", "token", "phase")},
        "ref": claim.ref, "producer": copy.deepcopy(claim.producer),
    }


def _reserve(record, owner, payload):
    from . import claims_cli, config as cfg

    for claim in record.resources:
        if claim.has_producer and not producer.valid_metadata(claim.producer):
            raise ValueError("malformed protected obligation prevents relationship reservation")
    existing = [
        claim for claim in record.resources
        if claim.has_producer and isinstance(claim.producer, dict)
        and claim.producer.get("lifetime") == relationship.LIFETIME
        and claim.producer.get("request_id") == payload["request_id"]
    ]
    if existing:
        claim = _matches(record, {"request_id": payload["request_id"]})
        if (
            claim.kind != payload["kind"] or claim.producer["namespace"] != payload["namespace"]
            or producer.canonical_json(claim.producer["recovery"]) != producer.canonical_json(payload["recovery"])
        ):
            raise ValueError("relationship request_id conflicts with its original reservation")
        return claim, False
    readiness = claims_cli._coordination_readiness_for_owner_ref(
        owner.canonical(), cfg.load_project_config(owner.project),
    )
    if not readiness.ready:
        raise ValueError(readiness.error or readiness.code)
    metadata = {
        "version": relationship.METADATA_VERSION, "lifetime": relationship.LIFETIME,
        "token": secrets.token_urlsafe(32), "creator": owner.canonical(), "phase": "pending",
        **{key: copy.deepcopy(payload[key]) for key in ("namespace", "request_id", "recovery")},
    }
    ref = relationship.claim_ref(metadata)
    if any(claim.ref == ref or (
        claim.has_producer and isinstance(claim.producer, dict)
        and claim.producer.get("token") == metadata["token"]
    ) for claim in record.resources):
        raise ValueError("relationship ref/token collision")
    claim = tracking.ResourceClaim(
        kind=payload["kind"], ref=ref, created_at=tracking._now_iso(),
        producer=metadata, producer_present=True,
    )
    record.resources.append(claim)
    return claim, True


def apply_resource_relationship(args: dict) -> dict:
    """Execute one exact owner transaction, never a peer allocation/deletion."""
    try:
        fields = {"schema", "version", "operation", "input", "owner_ref", "yaml_path"}
        if not isinstance(args, dict) or set(args) != fields:
            raise ValueError("relationship request has missing or unknown fields")
        if args["schema"] != relationship.SCHEMA or type(args["version"]) is not int or args["version"] != relationship.VERSION:
            raise ValueError("unsupported relationship schema/version")
        payload = _input(args["operation"], args["input"])
        for field in ("owner_ref", "yaml_path"):
            if not isinstance(args[field], str) or not args[field].strip():
                raise ValueError(f"relationship {field} must be a nonempty string")
        operation = args["operation"]
        path = Path(args["yaml_path"])
        probe_subject, proof = None, None
        if operation == "close":
            with tracking._RecordLock(path, require_sidecar=True):
                record, _ = _owned_record(args, path, mutable=False)
                claim = _matches(record, payload)
                if claim.producer["phase"] == "closed":
                    return _response(record, claim)
                _require_mutable_owner(record)
                if tracking.claim_handoff_reservation(record, claim):
                    raise ValueError("relationship is reserved by a handoff")
                probe_subject = claim.ref
            proof = claim_providers.resolve_claim_status(probe_subject)
        with tracking._RecordLock(path, require_sidecar=True):
            record, owner = _owned_record(args, path, mutable=operation not in {"inspect", "close"})
            if operation == "reserve":
                claim, changed = _reserve(record, owner, payload)
                if not changed:
                    return _response(record, claim)
            else:
                claim = _matches(record, payload)
                if operation == "inspect" or operation == "close" and claim.producer["phase"] == "closed":
                    return _response(record, claim)
                _require_mutable_owner(record)
                if tracking.claim_handoff_reservation(record, claim):
                    raise ValueError("relationship is reserved by a handoff")
                if operation == "bind":
                    if claim.producer["phase"] == "bound":
                        if producer.canonical_json(claim.producer["identity"]) != producer.canonical_json(payload["identity"]):
                            raise ValueError("bound relationship identity is immutable")
                        return _response(record, claim)
                    if claim.producer["phase"] != "pending":
                        raise ValueError("retired relationship cannot be rebound")
                    claim.producer = {
                        **claim.producer, "phase": "bound", "identity": copy.deepcopy(payload["identity"]),
                    }
                    claim.ref = relationship.claim_ref(claim.producer)
                    if any(other is not claim and other.ref == claim.ref for other in record.resources):
                        raise ValueError("relationship ref collision")
                else:
                    if claim.ref != probe_subject or not relationship.verified_retirement(proof, claim.ref):
                        raise ValueError("provider did not verify exact local relationship retirement")
                    claim.producer = {**claim.producer, "phase": "closed", "closeout": copy.deepcopy(proof)}
                    claim.state = "released"
            if not relationship.valid_metadata(claim.producer):
                raise ValueError("invalid relationship transition; obligation retained")
            with producer.transaction(path):
                tracking.save_record(record, path)
        activity.log_event(
            "resource_relationship_" + operation, worktree_id=record.worktree_id,
            kind=claim.kind, ref=claim.ref, phase=claim.producer["phase"],
        )
        claim_history.record_event(
            kind=claim.kind, ref=claim.ref, worktree_id=record.worktree_id, machine=record.machine,
            event="claimed" if operation == "reserve" else operation, project=record.repo,
        )
        return _response(record, claim)
    except (ValueError, TypeError, KeyError, OSError, RuntimeError, ExecutionSpaceError, yaml.YAMLError, RecursionError) as exc:
        logging.getLogger(__name__).warning("Relationship transaction refused: %s", exc)
        return {
            "schema": relationship.SCHEMA, "version": relationship.VERSION,
            "error": "rejected", "message": str(exc),
        }


tracking_write.register_verb("resource_relationship", apply_resource_relationship)
