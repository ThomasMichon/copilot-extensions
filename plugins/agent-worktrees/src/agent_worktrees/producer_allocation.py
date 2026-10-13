"""Attempt-specific allocation authority for provider-neutral producers."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import re
import secrets
from typing import TYPE_CHECKING

from . import resource_producer as producer

if TYPE_CHECKING:
    from .tracking_claims import ResourceClaim

MAX_GENERATION = (1 << 63) - 1
_HASH = re.compile(r"^[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class AllocationFence:
    generation: int
    key: str

    def to_dict(self) -> dict:
        return {"generation": self.generation, "key": self.key}

    def epoch(self) -> dict:
        return {
            "generation": self.generation,
            "key_hash": hashlib.sha256(self.key.encode("ascii")).hexdigest(),
        }


def parse_fence(value: object) -> AllocationFence:
    """Validate a private completion capability, never echoing it in errors."""
    if not isinstance(value, dict) or set(value) != {"generation", "key"}:
        raise ValueError("allocation_fence must contain generation and key")
    generation, key = value["generation"], value["key"]
    if type(generation) is not int or not 1 <= generation <= MAX_GENERATION:
        raise ValueError("invalid allocation fence generation")
    if not producer.is_valid_token(key):
        raise ValueError("invalid allocation fence key")
    return AllocationFence(generation, key)


def valid_epoch(value: object) -> bool:
    """Persist only the capability digest and a non-repeating generation."""
    return (
        isinstance(value, dict)
        and set(value) == {"generation", "key_hash"}
        and type(value["generation"]) is int
        and 1 <= value["generation"] <= MAX_GENERATION
        and isinstance(value["key_hash"], str)
        and bool(_HASH.fullmatch(value["key_hash"]))
    )


def matches_fence(metadata: dict, value: object) -> bool:
    """A key from an older attempt cannot complete a newer generation."""
    fence = parse_fence(value)
    epoch = metadata.get("allocation_epoch")
    return (
        valid_epoch(epoch)
        and epoch["generation"] == fence.generation
        and hmac.compare_digest(epoch["key_hash"], fence.epoch()["key_hash"])
    )


def set_metadata(claim: ResourceClaim, metadata: dict) -> None:
    """Keep the callback subject synchronized with every allocation transition."""
    ref = producer.claim_ref(metadata)
    claim.producer, claim.ref = metadata, ref


def begin(claim: ResourceClaim) -> AllocationFence:
    """Arm one allocation before external creation and return its private fence."""
    if claim.producer["phase"] != "pending":
        raise ValueError("allocation can begin only from a pending reservation")
    prior = claim.producer.get("allocation_epoch")
    generation = prior["generation"] + 1 if prior is not None else 1
    if generation > MAX_GENERATION:
        raise ValueError("allocation generation exhausted; obligation retained")
    fence = AllocationFence(generation, secrets.token_urlsafe(32))
    metadata = {
        **claim.producer, "phase": "allocating", "allocation_epoch": fence.epoch(),
    }
    metadata.pop("allocation_completion", None)
    set_metadata(claim, metadata)
    return fence


def verified_finished(result: object, ref: str) -> bool:
    """Quiescence is a separate authoritative assertion, not inferred absence."""
    if not isinstance(result, dict) or result.get("available") is not True:
        return False
    proof = result.get("producer_verification")
    return (
        isinstance(proof, dict)
        and type(proof.get("version")) is int and proof["version"] == producer.VERSION
        and proof.get("subject") == ref
        and proof.get("allocation_finished") is True
    )


def verified_binding(result: object, ref: str, identity: dict) -> bool:
    """A lost fence requires provider-verified identity and allocation quiescence."""
    if not verified_finished(result, ref) or result.get("exists") is not True:
        return False
    actual = result["producer_verification"].get("identity")
    return isinstance(actual, dict) and producer.canonical_json(actual) == producer.canonical_json(identity)


def valid_completion(metadata: dict) -> bool:
    """Retained recovery receipts refer to the allocating subject, not its successor."""
    if "allocation_completion" not in metadata:
        return True
    if (
        metadata["phase"] not in {"pending", "closed"}
        or "identity" in metadata
        or not valid_epoch(metadata.get("allocation_epoch"))
    ):
        return False
    receipt = metadata["allocation_completion"]
    subject = producer.claim_ref({**metadata, "phase": "allocating"})
    return verified_finished(receipt, subject) and producer.verified_absence(receipt, subject)


def bind(claim: ResourceClaim, identity: dict, *, fence: object = None, result: object = None) -> None:
    """Bind only an armed attempt, or its exact provider-verified recovery."""
    if fence is not None:
        if claim.producer["phase"] != "allocating":
            raise ValueError("begin allocation before binding its immutable identity")
        if not matches_fence(claim.producer, fence):
            raise ValueError("allocation fence does not match the current attempt")
    elif claim.producer["phase"] not in {"pending", "allocating"} or not verified_binding(result, claim.ref, identity):
        raise ValueError("provider did not verify exact identity and allocation quiescence; obligation retained")
    metadata = {**claim.producer, "phase": "bound", "identity": identity}
    metadata.pop("allocation_completion", None)
    set_metadata(claim, metadata)


def complete(claim: ResourceClaim, *, fence: object = None, result: object = None) -> bool:
    """Seal an ended attempt without fabricating any resource-absence evidence."""
    phase = claim.producer["phase"]
    if fence is not None:
        if not matches_fence(claim.producer, fence):
            raise ValueError("allocation fence does not match the current attempt")
        if phase == "pending":
            return False
        if phase != "allocating":
            raise ValueError("only an unbound allocation attempt can complete")
    elif (
        phase != "allocating"
        or not verified_finished(result, claim.ref)
        or not producer.verified_absence(result, claim.ref)
    ):
        if phase == "pending" and "allocation_completion" in claim.producer and valid_completion(claim.producer):
            return False
        raise ValueError("provider did not verify allocation quiescence and exact absence; obligation retained")
    metadata = {**claim.producer, "phase": "pending"}
    if fence is None:
        metadata["allocation_completion"] = result
    set_metadata(claim, metadata)
    return True
