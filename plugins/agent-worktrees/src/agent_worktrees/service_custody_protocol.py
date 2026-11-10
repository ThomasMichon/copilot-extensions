"""Receiver-only permanent service custody in the resource coordination slot."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, fields

from .lease_protocol import (
    LeaseRecord,
    ProtocolError,
    Resource,
    canonical_json,
    parse_timestamp,
    resource,
    validate_holder,
    validate_oid,
)
from .provider_effect_protocol import ProviderEffectRecord

SENTINEL = "agent-service-custody-envelope-v1"
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_ID = re.compile(r"[0-9a-f]{32}\Z")


@dataclass(frozen=True)
class ServiceCustodyRecord:
    """Accepted service identity; digests reference evidence, not permission."""

    schema_version: int
    resource: dict[str, str]
    state: str
    event: str
    custody_id: str
    fleet_id: str
    controller_id: str
    provider_id: str
    provider_resource_id: str
    allocation_id: str
    adoption_id: str
    predecessor: str
    approved_digest: str
    provider_receipt: str
    controller_receipt: str
    accepted_at: str
    acceptance_margin_seconds: int


CoordinationRecord = LeaseRecord | ServiceCustodyRecord | ProviderEffectRecord


def serialize_custody(record: ServiceCustodyRecord) -> str:
    """Encode without creating or authorizing a custody transition."""
    return f"{SENTINEL}\n{canonical_json(asdict(record))}"


def parse_custody(message: str, expected: Resource | None = None) -> ServiceCustodyRecord:
    """Strictly validate the service envelope independently of task leases."""
    prefix = SENTINEL + "\n"
    if not message.startswith(prefix) or message.count("\n") != 1:
        raise ProtocolError("service custody commit message has an invalid envelope")
    body = message[len(prefix):]
    try:
        raw = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ProtocolError("service custody payload is not valid JSON") from exc
    if not isinstance(raw, dict) or set(raw) != {field.name for field in fields(ServiceCustodyRecord)}:
        raise ProtocolError("service custody payload has missing or unknown fields")
    if canonical_json(raw) != body:
        raise ProtocolError("service custody payload JSON is not canonical")
    if type(raw["schema_version"]) is not int or raw["schema_version"] != 1:
        raise ProtocolError("unsupported service custody schema_version")
    value = raw["resource"]
    if not isinstance(value, dict) or set(value) != {"identity", "kind", "key"}:
        raise ProtocolError("service custody resource identity is malformed")
    item = resource(value["kind"], value["key"])
    if value["identity"] != item.identity or (expected is not None and item != expected):
        raise ProtocolError("service custody resource identity does not match its ref")
    if raw["state"] != "service-owned" or raw["event"] != "adopt":
        raise ProtocolError("unsupported service custody state or event")
    if not isinstance(raw["custody_id"], str) or not _ID.fullmatch(raw["custody_id"]):
        raise ProtocolError("custody_id must be a lowercase UUID hex value")
    for name in (
        "fleet_id", "controller_id", "provider_id", "provider_resource_id",
        "allocation_id", "adoption_id",
    ):
        validate_holder(raw[name])
    if not isinstance(raw["predecessor"], str):
        raise ProtocolError("service custody predecessor must be a full Git object ID")
    validate_oid(raw["predecessor"])
    for name in ("approved_digest", "provider_receipt", "controller_receipt"):
        if not isinstance(raw[name], str) or not _DIGEST.fullmatch(raw[name]):
            raise ProtocolError(f"service custody {name} must be a SHA-256 digest")
    parse_timestamp(raw["accepted_at"])
    margin = raw["acceptance_margin_seconds"]
    if type(margin) is not int or not 0 <= margin <= 604800:
        raise ProtocolError("service custody acceptance margin is outside the protocol bound")
    return ServiceCustodyRecord(**raw)
