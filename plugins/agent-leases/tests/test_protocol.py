from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from agent_leases.protocol import (
    LeaseRecord,
    ProtocolError,
    decode_key,
    encode_key,
    format_timestamp,
    parse_record,
    ref_for,
    resource,
    resource_from_ref,
    serialize_record,
)


def record() -> LeaseRecord:
    now = datetime(2026, 8, 6, 20, 0, tzinfo=timezone.utc)
    item = resource("remote-worktree", "owner/repo:feature/with spaces")
    return LeaseRecord(
        schema_version=1,
        resource={"identity": item.identity, "kind": item.kind, "key": item.key},
        state="leased",
        event="acquire",
        lease_id="a" * 32,
        holder="machine/worktree/session",
        issued_at=format_timestamp(now),
        renewed_at=format_timestamp(now),
        expires_at=format_timestamp(now + timedelta(seconds=60)),
        ttl_seconds=60,
        context={"attempt": 1, "purpose": "test"},
    )


def test_resource_ref_round_trip_is_canonical_and_safe() -> None:
    item = resource("remote-worktree", "owner/repo:feature/with spaces")
    ref = ref_for("refs/heads/copilot-leases/v1", item)
    assert ref.count("/") == 5
    assert resource_from_ref("refs/heads/copilot-leases/v1", ref) == item
    encoded = encode_key(item.key)
    assert decode_key(encoded) == item.key
    assert all(char.isalnum() or char in "_-" for char in encoded)


def test_record_round_trip_requires_canonical_envelope() -> None:
    value = record()
    assert parse_record(serialize_record(value)) == value
    noncanonical = serialize_record(value).replace('{"context":', '{ "context":', 1)
    with pytest.raises(ProtocolError, match="not canonical"):
        parse_record(noncanonical)


def test_payload_identity_must_match_ref() -> None:
    value = record()
    other = resource("remote-worktree", "different")
    with pytest.raises(ProtocolError, match="does not match its ref"):
        parse_record(serialize_record(value), other)


def test_release_is_a_strict_tombstone() -> None:
    value = record()
    bad = replace(value, state="released", event="release")
    with pytest.raises(ProtocolError, match="tombstones"):
        parse_record(serialize_record(bad))
