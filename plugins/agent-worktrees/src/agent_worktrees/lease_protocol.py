"""Strict versioned lease record format and resource-ref mapping."""

from __future__ import annotations

import base64
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

SENTINEL = "agent-leases-envelope-v1"
SCHEMA_VERSION = 1
_KIND_RE = re.compile(r"[a-z][a-z0-9-]{0,62}\Z")
_OID_RE = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?\Z")
_TIME_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z")
_LEASE_ID_RE = re.compile(r"[0-9a-f]{32}\Z")
_TOP_LEVEL_KEYS = {
    "schema_version",
    "resource",
    "state",
    "event",
    "lease_id",
    "holder",
    "issued_at",
    "renewed_at",
    "expires_at",
    "ttl_seconds",
    "context",
}
_RESOURCE_KEYS = {"identity", "kind", "key"}


class ProtocolError(ValueError):
    """Remote lease state is malformed or violates protocol invariants."""


@dataclass(frozen=True)
class Resource:
    """Canonical resource identity and its one-to-one remote ref."""

    kind: str
    key: str
    identity: str


@dataclass(frozen=True)
class LeaseRecord:
    """Canonical payload stored in a synthetic commit message."""

    schema_version: int
    resource: dict[str, str]
    state: str
    event: str
    lease_id: str
    holder: str
    issued_at: str
    renewed_at: str
    expires_at: str
    ttl_seconds: int
    context: dict[str, str | int | bool]

    def expires(self) -> datetime:
        return parse_timestamp(self.expires_at)


def format_timestamp(value: datetime) -> str:
    """Format an aware timestamp in the protocol's UTC-second representation."""
    if value.tzinfo is None:
        raise ProtocolError("timestamps must be timezone-aware")
    return value.astimezone(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_timestamp(value: object) -> datetime:
    """Parse one strict UTC protocol timestamp."""
    if not isinstance(value, str) or not _TIME_RE.fullmatch(value):
        raise ProtocolError("lease timestamps must use YYYY-MM-DDTHH:MM:SSZ")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ProtocolError(f"invalid lease timestamp: {value}") from exc


def validate_oid(value: str) -> str:
    """Validate a SHA-1 or SHA-256 object ID."""
    if not _OID_RE.fullmatch(value):
        raise ProtocolError("fencing token must be a full lowercase Git object ID")
    return value


def encode_key(key: str) -> str:
    """Encode a UTF-8 resource key into one Git-ref-safe path component."""
    if not isinstance(key, str) or not key or len(key) > 512:
        raise ProtocolError("resource key must contain 1 to 512 characters")
    if any(ord(char) < 32 or ord(char) == 127 for char in key):
        raise ProtocolError("resource key must not contain control characters")
    return base64.urlsafe_b64encode(key.encode("utf-8")).decode("ascii").rstrip("=")


def decode_key(encoded: str) -> str:
    """Decode and validate a canonical encoded resource key."""
    if not encoded or not re.fullmatch(r"[A-Za-z0-9_-]+", encoded):
        raise ProtocolError("resource ref contains an invalid encoded key")
    try:
        value = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise ProtocolError("resource ref contains an invalid UTF-8 key") from exc
    if encode_key(value) != encoded:
        raise ProtocolError("resource ref key is not canonically encoded")
    return value


def resource(kind: str, key: str) -> Resource:
    """Build a validated canonical resource identity."""
    if not isinstance(kind, str) or not _KIND_RE.fullmatch(kind):
        raise ProtocolError("resource kind must match [a-z][a-z0-9-]{0,62}")
    encoded = encode_key(key)
    return Resource(kind=kind, key=key, identity=f"{kind}:{encoded}")


def ref_for(prefix: str, item: Resource) -> str:
    """Return the sole ref representing a canonical resource."""
    encoded = item.identity.split(":", 1)[1]
    return f"{prefix}/{item.kind}/{encoded}"


def resource_from_ref(prefix: str, ref: str) -> Resource:
    """Decode a resource from a lease ref and reject noncanonical paths."""
    lead = prefix + "/"
    if not ref.startswith(lead):
        raise ProtocolError(f"ref {ref!r} is outside lease namespace")
    parts = ref[len(lead) :].split("/")
    if len(parts) != 2:
        raise ProtocolError(f"ref {ref!r} is not a canonical resource ref")
    item = resource(parts[0], decode_key(parts[1]))
    if ref_for(prefix, item) != ref:
        raise ProtocolError(f"ref {ref!r} is not canonical")
    return item


def validate_holder(holder: str) -> str:
    """Validate an opaque, non-secret holder/client identity."""
    if not isinstance(holder, str) or not holder or len(holder) > 256:
        raise ProtocolError("holder must contain 1 to 256 characters")
    if any(ord(char) < 32 or ord(char) == 127 for char in holder):
        raise ProtocolError("holder must not contain control characters")
    return holder


def validate_context(value: object) -> dict[str, str | int | bool]:
    """Validate bounded, non-nested diagnostic context."""
    if value is None:
        return {}
    if not isinstance(value, dict) or len(value) > 16:
        raise ProtocolError("context must be an object with at most 16 entries")
    result: dict[str, str | int | bool] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key or len(key) > 64:
            raise ProtocolError("context keys must contain 1 to 64 characters")
        if isinstance(item, str):
            if len(item) > 256 or any(ord(char) < 32 or ord(char) == 127 for char in item):
                raise ProtocolError(f"context value {key!r} is invalid")
        elif not isinstance(item, (int, bool)):
            raise ProtocolError("context values must be strings, integers, or booleans")
        result[key] = item
    if len(canonical_json(result)) > 4096:
        raise ProtocolError("context exceeds the 4096-byte encoded limit")
    return result


def canonical_json(value: object) -> str:
    """Serialize canonical compact ASCII JSON."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def serialize_record(record: LeaseRecord) -> str:
    """Serialize a record as a strict sentinel plus canonical JSON message."""
    return f"{SENTINEL}\n{canonical_json(asdict(record))}"


def parse_record(message: str, expected: Resource | None = None) -> LeaseRecord:
    """Parse and strictly validate a lease commit message."""
    prefix = SENTINEL + "\n"
    if not message.startswith(prefix) or message.count("\n") != 1:
        raise ProtocolError("lease commit message has an invalid envelope")
    body = message[len(prefix) :]
    try:
        raw = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ProtocolError("lease commit payload is not valid JSON") from exc
    if not isinstance(raw, dict) or set(raw) != _TOP_LEVEL_KEYS:
        raise ProtocolError("lease payload has missing or unknown fields")
    if canonical_json(raw) != body:
        raise ProtocolError("lease payload JSON is not canonical")
    if raw["schema_version"] != SCHEMA_VERSION:
        raise ProtocolError("unsupported lease schema_version")
    resource_raw = raw["resource"]
    if not isinstance(resource_raw, dict) or set(resource_raw) != _RESOURCE_KEYS:
        raise ProtocolError("lease resource identity is malformed")
    item = resource(resource_raw.get("kind"), resource_raw.get("key"))
    if resource_raw.get("identity") != item.identity:
        raise ProtocolError("payload resource identity does not match kind and key")
    if expected is not None and item != expected:
        raise ProtocolError("payload resource identity does not match its ref")

    state = raw["state"]
    event = raw["event"]
    if state not in {"leased", "released"}:
        raise ProtocolError("lease state must be 'leased' or 'released'")
    if event not in {"acquire", "takeover", "renew", "release"}:
        raise ProtocolError("lease event is invalid")
    if (state == "released") != (event == "release"):
        raise ProtocolError("release events and released state must match")
    lease_id = raw["lease_id"]
    if not isinstance(lease_id, str) or not _LEASE_ID_RE.fullmatch(lease_id):
        raise ProtocolError("lease_id must be a lowercase UUID hex value")
    holder = validate_holder(raw["holder"])
    issued = parse_timestamp(raw["issued_at"])
    renewed = parse_timestamp(raw["renewed_at"])
    expires = parse_timestamp(raw["expires_at"])
    ttl = raw["ttl_seconds"]
    if isinstance(ttl, bool) or not isinstance(ttl, int) or ttl < 0 or ttl > 604800:
        raise ProtocolError("ttl_seconds is outside the protocol bound")
    if renewed < issued:
        raise ProtocolError("renewed_at precedes issued_at")
    if event in {"acquire", "takeover"} and issued != renewed:
        raise ProtocolError("acquire and takeover events must issue and renew together")
    if state == "leased" and expires != renewed + timedelta(seconds=ttl):
        raise ProtocolError("expires_at does not equal renewed_at plus ttl_seconds")
    if state == "released" and (ttl != 0 or expires != renewed):
        raise ProtocolError("release tombstones must expire at renewed_at with TTL zero")
    context = validate_context(raw["context"])
    return LeaseRecord(
        schema_version=SCHEMA_VERSION,
        resource={"identity": item.identity, "kind": item.kind, "key": item.key},
        state=state,
        event=event,
        lease_id=lease_id,
        holder=holder,
        issued_at=format_timestamp(issued),
        renewed_at=format_timestamp(renewed),
        expires_at=format_timestamp(expires),
        ttl_seconds=ttl,
        context=context,
    )
