"""Read-only absence is distinct from missing, malformed or ambiguous authority."""

from __future__ import annotations

import pytest
import yaml

from agent_worktrees import resource_relationship as relationship, tracking
from test_resource_producer import owner as owner
from test_resource_relationship import call, reserve


@pytest.mark.parametrize("status", ["active", "finalizing", "finalized"])
def test_explicit_absence_query_is_read_only_on_frozen_owners(owner, status):
    path, _ = owner
    record = tracking.load_record(path)
    record.status = status
    tracking.save_record(record, path)
    before = path.read_bytes()
    assert call(path, "inspect", {"request_id": "not-reserved"})["error"]
    assert call(path, "inspect", {"request_id": "not-reserved", "allow_missing": True}) == {
        "schema": relationship.SCHEMA, "version": 1, "ok": True,
        "owner": "m/p/owner", "request_id": "not-reserved", "absent": True,
    }
    assert path.read_bytes() == before and tracking.load_record(path).resources == []


def test_absence_query_returns_existing_claim_without_mutation(owner):
    path, _ = owner
    pending = reserve(path)
    before = path.read_bytes()
    assert call(path, "inspect", {
        "request_id": pending["request_id"], "allow_missing": True,
    }) == pending
    assert path.read_bytes() == before


@pytest.mark.parametrize("value", [False, 1, "true", None])
def test_absence_mode_requires_explicit_true(owner, value):
    path, _ = owner
    before = path.read_bytes()
    assert call(path, "inspect", {"request_id": "not-reserved", "allow_missing": value})["error"]
    assert path.read_bytes() == before


def test_malformed_protected_metadata_cannot_prove_absence(owner):
    path, _ = owner
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["resources"] = [{
        "kind": "bridge", "ref": "bridge-remote:unknown", "state": "active",
        "producer": {"version": 99, "request_id": "not-reserved"},
    }]
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    before = path.read_bytes()
    assert call(path, "inspect", {"request_id": "not-reserved", "allow_missing": True})["error"]
    assert path.read_bytes() == before


def test_duplicate_relationships_cannot_prove_absence(owner):
    path, _ = owner
    pending = reserve(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["resources"].append(data["resources"][0].copy())
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    before = path.read_bytes()
    assert call(path, "inspect", {
        "request_id": pending["request_id"], "allow_missing": True,
    })["error"]
    assert path.read_bytes() == before


@pytest.mark.parametrize("owner_ref", ["m/p/consumer", "remote/p/owner"])
def test_unresolved_or_foreign_owner_cannot_prove_absence(owner, owner_ref):
    path, _ = owner
    before = path.read_bytes()
    assert call(path, "inspect", {
        "request_id": "not-reserved", "allow_missing": True,
    }, owner_ref=owner_ref)["error"]
    assert path.read_bytes() == before


@pytest.mark.parametrize("raw", [
    {"unexpected": "collection"},
    "not-a-list",
    ["opaque-item"],
    [None],
    [{"kind": "bridge", "ref": "unreadable", "state": "invalid"}],
    [{"kind": "bridge", "ref": "unreadable", "state": "released", "metadata_opaque": True}],
], ids=["mapping", "scalar", "string-item", "null-item", "invalid-state", "opaque-released"])
def test_opaque_claim_storage_cannot_establish_reservation_absence(owner, raw):
    path, _ = owner
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["resources"] = raw
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    record = tracking.load_record(path)
    assert any(claim.metadata_opaque and not claim.has_producer for claim in record.resources)
    before = path.read_bytes()
    result = call(path, "inspect", {"request_id": "not-reserved", "allow_missing": True})
    assert result.get("error") and "absent" not in result
    assert path.read_bytes() == before
    assert call(path, "reserve", {
        "kind": "bridge", "namespace": "bridge-remote",
        "request_id": "new-reservation", "recovery": {},
    }).get("error")
    assert path.read_bytes() == before
