"""Retained relationship metadata; never authority to delete a remote peer."""

from __future__ import annotations

import base64
import json
import re

from . import resource_producer as producer

SCHEMA = "copilot-extensions.resource-relationship"
VERSION = 1
METADATA_VERSION = 2
LIFETIME = "retained-relationship"
_REQUEST_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}\Z")


def valid_request_id(value: object) -> bool:
    return isinstance(value, str) and _REQUEST_ID.fullmatch(value) is not None


def claim_ref(metadata: dict) -> str:
    """Keep a terminal receipt tied to the pending/bound subject it retired."""
    envelope = {
        key: metadata[key]
        for key in ("version", "token", "creator", "request_id", "recovery")
    }
    envelope["phase"] = "bound" if "identity" in metadata else "pending"
    if "identity" in metadata:
        envelope["identity"] = metadata["identity"]
    encoded = base64.urlsafe_b64encode(
        producer.canonical_json(envelope).encode("ascii"),
    ).decode("ascii").rstrip("=")
    ref = f"{metadata['namespace']}:l1.{encoded}"
    if len(ref.encode("ascii")) > producer.MAX_REF_BYTES:
        raise ValueError("relationship callback ref exceeds size limit")
    return ref


def decode_ref(ref: str) -> dict:
    """Decode only canonical retained-relationship callback subjects."""
    from . import claim_providers

    split = claim_providers.split_namespaced_ref(ref)
    if split is None or len(ref.encode("utf-8")) > producer.MAX_REF_BYTES:
        raise ValueError("invalid relationship callback ref")
    namespace, bare = split
    if not bare.startswith("l1."):
        raise ValueError("unsupported relationship callback ref version")
    try:
        encoded = bare[3:]
        data = json.loads(base64.b64decode(
            encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True,
        ))
    except (ValueError, UnicodeError) as exc:
        raise ValueError("invalid relationship callback envelope") from exc
    metadata = {**data, "namespace": namespace, "lifetime": LIFETIME} if isinstance(data, dict) else {}
    if not valid_metadata(metadata) or claim_ref(metadata) != ref:
        raise ValueError("invalid or noncanonical relationship callback envelope")
    return data


def verified_retirement(result: object, ref: str) -> bool:
    """Require exact provider-owned local retirement, not remote absence."""
    if (
        not isinstance(result, dict) or result.get("available") is not True
        or type(result.get("exists")) is not bool
    ):
        return False
    proof = result.get("relationship_verification")
    return (
        isinstance(proof, dict)
        and type(proof.get("version")) is int and proof["version"] == VERSION
        and proof.get("subject") == ref
        and proof.get("retired") is True and proof.get("quiescent") is True
    )


def valid_metadata(value: object) -> bool:
    """Unknown lifetimes/phases remain blocking, including unshipped transfer."""
    from . import claim_providers, tracking

    required = {"version", "lifetime", "token", "namespace", "phase", "creator", "request_id", "recovery"}
    if not isinstance(value, dict) or not required.issubset(value) or set(value) - (required | {"identity", "closeout"}):
        return False
    if type(value["version"]) is not int or value["version"] != METADATA_VERSION or value["lifetime"] != LIFETIME:
        return False
    if not producer.is_valid_token(value["token"]) or not valid_request_id(value["request_id"]):
        return False
    if not isinstance(value["namespace"], str) or not claim_providers.is_safe_argument(value["namespace"]):
        return False
    creator = value["creator"]
    if not isinstance(creator, str):
        return False
    parsed = tracking.parse_claim_ref(creator)
    if parsed is None or not parsed.is_qualified or parsed.session or parsed.canonical() != creator:
        return False
    phase = value["phase"]
    if phase not in ("pending", "bound", "closed") or not isinstance(value["recovery"], dict):
        return False
    if phase == "bound" and not isinstance(value.get("identity"), dict):
        return False
    if phase == "pending" and "identity" in value:
        return False
    if "identity" in value and not isinstance(value["identity"], dict):
        return False
    try:
        producer.canonical_json(value)
        ref = claim_ref(value)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        return False
    if phase == "closed":
        return verified_retirement(value.get("closeout"), ref)
    return "closeout" not in value
