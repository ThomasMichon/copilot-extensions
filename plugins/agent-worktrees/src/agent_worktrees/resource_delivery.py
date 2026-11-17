"""Bounded provider-verified historical handover receipts."""

from __future__ import annotations

from datetime import datetime
import re
from typing import Any

LIFETIME = "user-deliverable"
MAX_FILES = 16
MAX_BYTES = 64 * 1024 * 1024
_HASH = re.compile(r"^[0-9a-f]{64}\Z")


def valid_summary(value: object) -> bool:
    """Validate a bounded content summary, not an inventory of destinations."""
    return (
        isinstance(value, dict) and set(value) == {"files", "bytes", "sha256"}
        and type(value["files"]) is int and 1 <= value["files"] <= MAX_FILES
        and type(value["bytes"]) is int and 0 <= value["bytes"] <= MAX_BYTES
        and isinstance(value["sha256"], str) and _HASH.fullmatch(value["sha256"]) is not None
    )


def verified_delivery(result: object, ref: str, identity: dict[str, Any]) -> bool:
    """Require the registered provider's exact subject, identity and handover."""
    from .resource_producer import canonical_json

    if not isinstance(result, dict) or result.get("available") is not True or result.get("exists") is not True:
        return False
    proof = result.get("producer_verification")
    if (
        not isinstance(proof, dict) or type(proof.get("version")) is not int or proof["version"] != 1
        or proof.get("subject") != ref or proof.get("allocation_finished") is not True
        or proof.get("work_settled") is not True or not isinstance(proof.get("identity"), dict)
        or canonical_json(proof["identity"]) != canonical_json(identity)
    ):
        return False
    receipt = proof.get("delivery")
    if (
        not isinstance(receipt, dict)
        or set(receipt) != {"destination", "delivered_at", "content", "result"}
        or not isinstance(receipt["destination"], str) or not 1 <= len(receipt["destination"]) <= 2048
        or "\x00" in receipt["destination"] or receipt["result"] != "verified-local"
        or not valid_summary(receipt["content"]) or not isinstance(receipt["delivered_at"], str)
    ):
        return False
    try:
        return datetime.fromisoformat(receipt["delivered_at"]).tzinfo is not None
    except ValueError:
        return False
