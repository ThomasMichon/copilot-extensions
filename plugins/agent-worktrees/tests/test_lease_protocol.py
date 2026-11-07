from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from agent_worktrees.lease_protocol import (
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
    ref = ref_for("refs/agent-worktrees/leases/v1", item)
    assert ref.count("/") == 5
    assert resource_from_ref("refs/agent-worktrees/leases/v1", ref) == item
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


def _return_proof_payload() -> dict:
    value = record()
    raw = json.loads(serialize_record(value).split("\n", 1)[1])
    raw.update(
        schema_version=2, state="released", event="release", ttl_seconds=0,
        expires_at=value.renewed_at,
        returned_generations=[{
            "lease_id": value.lease_id, "holder": value.holder,
            "released_at": value.renewed_at,
        }],
    )
    return raw


def _message(raw: dict) -> str:
    return "agent-leases-envelope-v1\n" + json.dumps(raw, sort_keys=True, separators=(",", ":"))


def test_return_proof_payload_round_trips_without_history() -> None:
    message = _message(_return_proof_payload())
    assert serialize_record(parse_record(message)) == message


@pytest.mark.parametrize("corruption", [
    "duplicate", "identity", "holder", "future", "order", "terminal", "leased", "unbounded",
])
def test_return_proof_payload_rejects_unqualified_evidence(corruption):
    raw = _return_proof_payload()
    proof = raw["returned_generations"][0]
    if corruption == "duplicate":
        raw["returned_generations"].append(dict(proof))
    elif corruption == "identity":
        proof["lease_id"] = 12345678901234567890123456789012
    elif corruption == "holder":
        proof["holder"] = ""
    elif corruption == "future":
        proof["released_at"] = "2026-08-06T20:01:00Z"
    elif corruption == "order":
        raw["returned_generations"].insert(0, {**proof, "lease_id": "b" * 32})
        proof["released_at"] = "2026-08-06T19:59:59Z"
    elif corruption == "terminal":
        proof["lease_id"] = "b" * 32
    elif corruption == "leased":
        raw.update(state="leased", event="renew", ttl_seconds=60, expires_at="2026-08-06T20:01:00Z")
    else:
        raw["returned_generations"] = [
            {**proof, "lease_id": f"{number:032x}"} for number in range(17)
        ]
    with pytest.raises(ProtocolError):
        parse_record(_message(raw))


@pytest.mark.parametrize("version", [True, 1.0, False, 2.0])
def test_lease_payload_version_must_be_a_raw_integer(version):
    raw = json.loads(serialize_record(record()).split("\n", 1)[1])
    raw["schema_version"] = version
    with pytest.raises(ProtocolError, match="schema_version"):
        parse_record(_message(raw))
