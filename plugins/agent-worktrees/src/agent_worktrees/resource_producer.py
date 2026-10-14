"""Provider-neutral disposable ownership metadata and write fencing."""

from __future__ import annotations

import base64
import copy
import json
import re
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Iterator

SCHEMA = "copilot-extensions.resource-producer"
VERSION = 1
MAX_REF_BYTES = 4096
MAX_INPUT_BYTES = 16384
FROZEN_OWNER_STATUSES = frozenset({
    "finalizing", "finalized", "orphaned", "archived", "complete", "completed",
})
_TOKEN = re.compile(r"^[A-Za-z0-9_-]{32,128}\Z")
_write_paths: ContextVar[frozenset[str]] = ContextVar("producer_write_paths", default=frozenset())


def is_valid_token(value: object) -> bool:
    """Validate public opaque-token syntax without exposing its implementation."""
    return isinstance(value, str) and _TOKEN.fullmatch(value) is not None


def canonical_json(value: object) -> str:
    """Validate actual JSON values and encode deterministically, without NaN."""
    def check(item: object, depth: int = 0) -> None:
        if depth > 32:
            raise ValueError("producer JSON exceeds maximum nesting")
        if item is None or type(item) in (str, bool, int, float):
            return
        if type(item) is list:
            for child in item:
                check(child, depth + 1)
            return
        if type(item) is dict and all(type(key) is str for key in item):
            for child in item.values():
                check(child, depth + 1)
            return
        raise ValueError("producer metadata must contain only JSON values")

    check(value)
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    if len(encoded.encode("ascii")) > MAX_INPUT_BYTES:
        raise ValueError("producer JSON exceeds size limit")
    return encoded


def claim_ref(metadata: dict) -> str:
    """Encode an opaque callback envelope; terminal receipts retain its subject."""
    envelope = {key: metadata[key] for key in ("version", "token", "creator", "recovery")}
    envelope["phase"] = "allocating" if metadata["phase"] == "allocating" else (
        "bound" if "identity" in metadata else "pending"
    )
    if "allocation_epoch" in metadata:
        envelope["allocation_epoch"] = metadata["allocation_epoch"]
    if "identity" in metadata:
        envelope["identity"] = metadata["identity"]
    encoded = base64.urlsafe_b64encode(canonical_json(envelope).encode("ascii")).decode("ascii").rstrip("=")
    ref = f"{metadata['namespace']}:r1.{encoded}"
    if len(ref.encode("ascii")) > MAX_REF_BYTES:
        raise ValueError("producer callback ref exceeds size limit")
    return ref


def decode_ref(ref: str) -> dict:
    """Decode a canonical version-1 callback ref, validating only generic types."""
    from . import claim_providers

    split = claim_providers.split_namespaced_ref(ref)
    if split is None or len(ref.encode("utf-8")) > MAX_REF_BYTES:
        raise ValueError("invalid producer callback ref")
    namespace, bare = split
    if not bare.startswith("r1."):
        raise ValueError("unsupported producer callback ref version")
    try:
        encoded = bare[3:]
        data = json.loads(base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True))
    except (ValueError, UnicodeError) as exc:
        raise ValueError("invalid producer callback envelope") from exc
    metadata = {**data, "namespace": namespace, "lifetime": "disposable-owned"} if isinstance(data, dict) else {}
    if not valid_metadata(metadata) or claim_ref(metadata) != ref:
        raise ValueError("invalid or noncanonical producer callback envelope")
    return data


def valid_metadata(value: object) -> bool:
    """Unknown/malformed metadata is never interpreted as safely discharged."""
    from . import claim_providers, producer_allocation, tracking

    if not isinstance(value, dict):
        return False
    required = {"version", "lifetime", "token", "namespace", "phase", "creator", "recovery"}
    if not required.issubset(value) or set(value) - (
        required | {"identity", "closeout", "transfer", "allocation_epoch", "allocation_completion"}
    ):
        return False
    if type(value["version"]) is not int or value["version"] != VERSION or value["lifetime"] != "disposable-owned":
        return False
    if not is_valid_token(value["token"]):
        return False
    namespace = value["namespace"]
    if not isinstance(namespace, str) or not claim_providers.is_safe_argument(namespace):
        return False
    creator = value["creator"]
    if not isinstance(creator, str):
        return False
    parsed = tracking.parse_claim_ref(creator)
    if parsed is None or not parsed.is_qualified or parsed.session or parsed.canonical() != creator:
        return False
    phase = value["phase"]
    if phase not in ("pending", "allocating", "bound", "closed", "transferred") or not isinstance(value["recovery"], dict):
        return False
    if phase in ("bound", "transferred") and not isinstance(value.get("identity"), dict):
        return False
    if phase in {"pending", "allocating"} and "identity" in value:
        return False
    if ("allocation_epoch" in value and not producer_allocation.valid_epoch(value["allocation_epoch"])) or (
        phase == "allocating" and "allocation_epoch" not in value
    ):
        return False
    if "identity" in value and not isinstance(value["identity"], dict):
        return False
    try:
        canonical_json(value)
        ref = claim_ref(value)
        if not producer_allocation.valid_completion(value):
            return False
    except (ValueError, TypeError, UnicodeError, RecursionError):
        return False
    if phase == "closed":
        proof = value.get("closeout")
        if not isinstance(proof, dict) or proof.get("available") is not True or not verified_absence(proof, ref):
            return False
    elif "closeout" in value:
        return False
    if phase == "transferred":
        transfer = value.get("transfer")
        if not isinstance(transfer, dict) or set(transfer) != {"accepted", "bundle", "source", "consumer"}:
            return False
        if transfer["accepted"] is not True or not all(isinstance(transfer[key], str) and transfer[key] for key in ("bundle", "source", "consumer")):
            return False
        for key in ("source", "consumer"):
            target = tracking.parse_claim_ref(transfer[key])
            if target is None or not target.is_qualified or target.session:
                return False
    elif "transfer" in value:
        return False
    return True


def verified_absence(result: object, ref: str) -> bool:
    """Require an available callback's exact versioned subject and absence proof."""
    if not isinstance(result, dict) or result.get("available") is not True or result.get("exists") is not False:
        return False
    proof = result.get("producer_verification")
    return (
        isinstance(proof, dict)
        and type(proof.get("version")) is int and proof["version"] == VERSION
        and proof.get("subject") == ref and proof.get("absent") is True
    )


def wire_metadata(value: object) -> dict:
    """Expose invalid raw YAML as an explicit error, never as wire authority."""
    if isinstance(value, dict) and valid_metadata(value):
        return copy.deepcopy(value)
    return {"error": "invalid-producer-metadata"}


def unsettled(claim) -> bool:
    """A producer's phase, never its generic state, controls finalization."""
    value = claim.producer
    return (
        not valid_metadata(value)
        or value["phase"] not in ("closed", "transferred")
        or claim_ref(value) != claim.ref
    )


@contextmanager
def transaction(*paths: Path) -> Iterator[None]:
    """Authorize metadata changes only inside an already-locked transaction."""
    token = _write_paths.set(frozenset(str(path.resolve()) for path in paths))
    try:
        yield
    finally:
        _write_paths.reset(token)


def preserve_claims(record, current, path: Path) -> None:
    """Fence generic full-record writers, including stale saves and repairs."""
    sides = (record.resources, current.resources if current is not None else [])
    if not any(claim.has_producer for claims in sides for claim in claims):
        return
    for claims in sides:
        seen: dict[str, bool] = {}
        tokens: set[str] = set()
        for claim in claims:
            if claim.ref in seen and (seen[claim.ref] or claim.has_producer):
                raise ValueError("ambiguous duplicate producer reference")
            seen[claim.ref] = seen.get(claim.ref, False) or claim.has_producer
            if claim.has_producer and isinstance(claim.producer, dict):
                token = claim.producer.get("token")
                if isinstance(token, str):
                    if token in tokens:
                        raise ValueError("ambiguous duplicate producer token")
                    tokens.add(token)
    if str(path.resolve()) in _write_paths.get():
        return
    authoritative = {
        claim.ref: claim for claim in current.resources if claim.has_producer
    } if current is not None else {}
    by_token = {
        c.producer.get("token"): c.ref for c in authoritative.values()
        if isinstance(c.producer, dict) and isinstance(c.producer.get("token"), str)
    }
    merged = []
    for claim in record.resources:
        prior = authoritative.pop(claim.ref, None)
        if prior is None and claim.has_producer and isinstance(claim.producer, dict):
            ref = by_token.get(claim.producer.get("token"))
            prior = authoritative.pop(ref, None)
        if prior is not None:
            retained = copy.deepcopy(prior)
            retained.note = claim.note
            retained.handoff_bundle = claim.handoff_bundle
            merged.append(retained)
        elif claim.has_producer:
            raise ValueError("producer metadata may only be written by a producer transaction")
        else:
            merged.append(claim)
    merged.extend(authoritative.values())
    record.resources = merged
