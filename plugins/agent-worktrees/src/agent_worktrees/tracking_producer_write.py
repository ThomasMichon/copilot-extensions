"""Registered reserve/bind/inspect/close transactions on the existing ledger."""

from __future__ import annotations

import copy
import logging
import secrets
from pathlib import Path

import yaml

from . import activity, claim_history, claim_providers, resource_producer as producer
from . import tracking, tracking_write
from .execution_spaces import (
    ExecutionSpaceError, project_record_config, require_owner_identity, require_record_mutation,
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
        "bind": {"token", "identity"}, "inspect": {"token"}, "close": {"token"},
    }
    if operation not in keys or not isinstance(payload, dict) or set(payload) != keys[operation]:
        raise ValueError(f"{operation}: expected exactly {sorted(keys.get(operation, set()))}")
    producer.canonical_json(payload)
    if operation == "reserve":
        if not isinstance(payload["kind"], str) or not claim_providers.is_safe_argument(payload["kind"]):
            raise ValueError("reserve: kind must be a safe nonempty token")
        if not isinstance(payload["namespace"], str) or not claim_providers.is_safe_argument(payload["namespace"]):
            raise ValueError("reserve: namespace must be a safe nonempty token")
        if not isinstance(payload["recovery"], dict):
            raise ValueError("reserve: recovery must be a JSON object")
    else:
        if not isinstance(payload["token"], str) or not producer._TOKEN.fullmatch(payload["token"]):
            raise ValueError("invalid producer token")
        if operation == "bind" and not isinstance(payload["identity"], dict):
            raise ValueError("bind: identity must be a JSON object")
    return payload


def _owned_record(args: dict, path: Path, *, mutable: bool = True):
    from . import config as cfg

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
    if path.resolve() != (cfg.project_dir(owner.project) / "worktrees" / f"{owner.worktree_id}.yaml").resolve():
        raise ValueError("producer record path does not match owning-project authority")
    if mutable and record.status in producer.FROZEN_OWNER_STATUSES:
        raise ValueError(f"producer owner is {record.status}; ownership is frozen")
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
        if args.get("schema") != producer.SCHEMA or type(args.get("version")) is not int or args["version"] != 1:
            raise ValueError("unsupported resource-producer schema/version")
        operation = args["operation"]
        payload = _input(operation, args["input"])
        path = Path(args["yaml_path"])
        close_subject, close_result = None, None
        if operation == "close":
            with tracking._RecordLock(path, require_sidecar=True):
                snapshot, _ = _owned_record(args, path)
                candidate = _matched_claim(snapshot, payload["token"])
                if candidate.producer["phase"] in {"pending", "bound"}:
                    if tracking.claim_handoff_reservation(snapshot, candidate):
                        raise ValueError("producer claim is reserved by an offered handoff")
                    close_subject = candidate.ref
            if close_subject is not None:
                # Provider I/O is outside RecordLock; the final RMW validates
                # that its exact immutable subject is still owned and unchanged.
                close_result = claim_providers.resolve_claim_status(close_subject)
        with tracking._RecordLock(path, require_sidecar=True):
            record, owner = _owned_record(args, path, mutable=operation != "inspect")
            if operation == "reserve":
                metadata = {
                    "version": 1, "lifetime": "disposable-owned",
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
                if phase not in {"pending", "bound"}:
                    raise ValueError(f"producer claim is {phase}; mutation refused")
                if operation == "bind":
                    identity = copy.deepcopy(payload["identity"])
                    if phase == "bound":
                        if producer.canonical_json(claim.producer["identity"]) != producer.canonical_json(identity):
                            raise ValueError("bound producer identity is immutable")
                        return _response(record, claim)
                    metadata = {**claim.producer, "phase": "bound", "identity": identity}
                    ref = producer.claim_ref(metadata)
                    if any(c is not claim and c.ref == ref for c in record.resources):
                        raise ValueError("producer namespace/ref collision")
                    claim.ref, claim.producer = ref, metadata
                else:
                    result = close_result
                    if claim.ref != close_subject or not isinstance(result, dict) or result.get("available") is not True or not producer.verified_absence(result, claim.ref):
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
        return _response(record, claim)
    except (ValueError, TypeError, KeyError, OSError, ExecutionSpaceError, yaml.YAMLError, RecursionError) as exc:
        logging.getLogger(__name__).warning("Resource producer transaction refused: %s", exc)
        return {"schema": producer.SCHEMA, "version": 1, "error": "rejected", "message": str(exc)}


tracking_write.register_verb("resource_producer", apply_resource_producer)
