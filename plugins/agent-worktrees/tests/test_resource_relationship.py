"""Retained responsibility never borrows disposable absence semantics."""

from __future__ import annotations

import argparse
import copy

import pytest
import yaml

from agent_worktrees import claim_handoffs, claim_providers, finalize, tracking
from agent_worktrees import resource_producer as producer
from agent_worktrees import resource_relationship as relationship
from agent_worktrees.tracking_relationship_write import apply_resource_relationship
from test_resource_producer import absence, owner as owner
from test_resource_producer import call as disposable_call


def call(path, operation, payload, owner_ref="m/p/owner"):
    return apply_resource_relationship({
        "schema": relationship.SCHEMA, "version": 1, "operation": operation,
        "input": payload, "owner_ref": owner_ref, "yaml_path": str(path),
    })


def reserve(path, request_id="relation-one"):
    result = call(path, "reserve", {
        "kind": "bridge", "namespace": "bridge-remote", "request_id": request_id,
        "recovery": {"authority": "test-installation", "binding": "test-target"},
    })
    assert result.get("ok"), result
    return result


def retired(ref):
    return {
        "available": True,
        "exists": True,
        "relationship_verification": {
            "version": 1, "subject": ref, "retired": True, "quiescent": True,
        },
    }


def test_reservation_is_durable_replayable_and_distinct_from_disposable(owner):
    path, _ = owner
    pending = reserve(path)
    before = path.read_bytes()
    assert reserve(path) == pending
    assert path.read_bytes() == before
    claim = tracking.load_record(path).resources[0]
    assert claim.kind == "bridge" and claim.is_unsettled
    assert claim.to_dict()["producer"] == pending["producer"]
    assert pending["producer"]["version"] == 2
    assert pending["producer"]["lifetime"] == "retained-relationship"
    assert relationship.decode_ref(pending["ref"])["request_id"] == "relation-one"
    with pytest.raises(ValueError, match="unsupported"):
        producer.decode_ref(pending["ref"])
    assert call(path, "inspect", {"request_id": "relation-one"}) == pending
    assert call(path, "inspect", {"token": pending["token"]}) == pending
    payload = {
        "kind": "session", "namespace": "bridge-remote", "request_id": "relation-one",
        "recovery": {"authority": "test-installation", "binding": "test-target"},
    }
    assert call(path, "reserve", payload)["error"]
    assert path.read_bytes() == before


def test_binding_retains_request_identity_and_cannot_rebind_or_use_disposable_api(owner):
    path, _ = owner
    pending = reserve(path)
    payload = {"token": pending["token"], "identity": {"binding": "test-target", "native_id": "conversation"}}
    bound = call(path, "bind", payload)
    assert bound["phase"] == "bound"
    before = path.read_bytes()
    assert call(path, "bind", payload) == bound
    assert reserve(path) == bound
    assert call(path, "bind", {**payload, "identity": {"native_id": "other"}})["error"]
    for operation in ("begin", "complete", "close", "inspect"):
        assert disposable_call(path, operation, {"token": pending["token"]})["error"]
    assert path.read_bytes() == before


@pytest.mark.parametrize("proof", [
    {"available": False},
    {"available": True, "exists": False},
    absence("wrong-subject"),
    retired("wrong-subject"),
    {"available": True, "relationship_verification": {"version": True, "retired": True, "quiescent": True}},
])
def test_unavailable_absence_or_wrong_retirement_proof_cannot_close(owner, monkeypatch, proof):
    path, _ = owner
    pending = reserve(path)
    before = path.read_bytes()
    monkeypatch.setattr(claim_providers, "resolve_claim_status", lambda ref: proof)
    assert call(path, "close", {"token": pending["token"]})["error"]
    assert path.read_bytes() == before
    assert tracking.load_record(path).resources[0].is_unsettled


def test_exact_registered_retirement_closes_and_replay_is_read_only(owner, monkeypatch):
    path, _ = owner
    pending = reserve(path)
    seen = []

    def callback(ref):
        seen.append(ref)
        return retired(ref)

    monkeypatch.setattr(claim_providers, "resolve_claim_status", callback)
    closed = call(path, "close", {"token": pending["token"]})
    assert closed["phase"] == "closed" and seen == [pending["ref"]]
    assert not tracking.load_record(path).resources[0].is_unsettled
    record = tracking.load_record(path)
    record.status = "finalized"
    tracking.save_record(record, path)
    before = path.read_bytes()
    assert call(path, "close", {"token": pending["token"]}) == closed
    assert call(path, "inspect", {"request_id": "relation-one"}) == closed
    assert path.read_bytes() == before and seen == [pending["ref"]]


def test_probe_race_and_caller_supplied_retirement_never_settle(owner, monkeypatch):
    path, _ = owner
    pending = reserve(path)

    def callback(ref):
        assert call(path, "bind", {"token": pending["token"], "identity": {"native_id": "new"}})["ok"]
        return retired(ref)

    monkeypatch.setattr(claim_providers, "resolve_claim_status", callback)
    assert call(path, "close", {"token": pending["token"]})["error"]
    before = path.read_bytes()
    assert call(path, "close", {"token": pending["token"], "retired": True})["error"]
    assert path.read_bytes() == before


@pytest.mark.parametrize("status", sorted(producer.FROZEN_OWNER_STATUSES))
def test_frozen_owner_is_not_reopened(owner, status):
    path, _ = owner
    record = tracking.load_record(path)
    record.status = status
    tracking.save_record(record, path)
    before = path.read_bytes()
    result = call(path, "reserve", {
        "kind": "bridge", "namespace": "bridge-remote", "request_id": "new", "recovery": {},
    })
    assert result["error"] and path.read_bytes() == before


def test_wrong_authority_and_foreign_record_refuse(owner):
    path, _ = owner
    before = path.read_bytes()
    payload = {"kind": "bridge", "namespace": "bridge-remote", "request_id": "new", "recovery": {}}
    assert call(path, "reserve", payload, owner_ref="m/p/consumer")["error"]
    assert call(path, "reserve", payload, owner_ref="remote/p/owner")["error"]
    assert path.read_bytes() == before


def test_generic_stale_saves_and_terminal_transition_cannot_drop_relationship(owner):
    path, _ = owner
    stale = tracking.load_record(path)
    pending = reserve(path)
    tracking.save_record(stale, path)
    assert tracking.load_record(path).resources[0].producer == pending["producer"]
    before = path.read_bytes()
    stale.status = "finalizing"
    with pytest.raises(ValueError, match="relationship"):
        tracking.save_record(stale, path)
    assert path.read_bytes() == before
    record = tracking.load_record(path)
    record.resources[0].producer = None
    record.resources[0].producer_present = False
    record.resources[0].state = "released"
    tracking.save_record(record, path)
    claim = tracking.load_record(path).resources[0]
    assert claim.producer == pending["producer"] and claim.is_unsettled


@pytest.mark.parametrize("state", ["released", "at-rest", "abandoned"])
def test_generic_state_and_abandon_do_not_bypass_finalize(owner, state):
    path, _ = owner
    reserve(path)
    claim = tracking.load_record(path).resources[0]
    claim.state = state
    assert not finalize._assert_obligations_settled(
        argparse.Namespace(resources=[claim]), "owner", abandon=True, handoff_to="recipient",
    )


def test_unknown_relationship_metadata_is_preserved_as_blocking(owner):
    path, _ = owner
    reserve(path)
    raw = yaml.safe_load(path.read_text())
    raw["resources"][0]["producer"]["version"] = 99
    path.write_text(yaml.safe_dump(raw))
    record = tracking.load_record(path)
    assert record.resources[0].is_unsettled
    assert record.resources[0].to_dict()["producer"] == {"error": "invalid-producer-metadata"}
    metadata = copy.deepcopy(record.resources[0].producer)
    tracking.save_record(record, path)
    assert tracking.load_record(path).resources[0].producer == metadata


def test_unacknowledged_disposable_handoff_cannot_transfer_relationship(owner):
    path, _ = owner
    pending = reserve(path)
    bound = call(path, "bind", {"token": pending["token"], "identity": {"native_id": "conversation"}})
    before = path.read_bytes()
    with pytest.raises(claim_handoffs.ClaimHandoffError, match="relationship"):
        claim_handoffs.offer("m/p/owner", "m/p/consumer", [bound["ref"]], machine="m")
    assert path.read_bytes() == before


def test_new_reservation_requires_readiness_but_inspect_and_close_do_not(owner, monkeypatch):
    from agent_worktrees import claims_cli

    path, _ = owner
    pending = reserve(path)
    monkeypatch.setattr(
        claims_cli, "_coordination_readiness_for_owner_ref",
        lambda *a: argparse.Namespace(ready=False, error="binding unavailable", code="unbound"),
    )
    before = path.read_bytes()
    assert call(path, "reserve", {
        "kind": "bridge", "namespace": "bridge-remote", "request_id": "new", "recovery": {},
    })["error"]
    assert path.read_bytes() == before
    assert call(path, "inspect", {"token": pending["token"]}) == pending
    monkeypatch.setattr(claim_providers, "resolve_claim_status", retired)
    assert call(path, "close", {"token": pending["token"]})["phase"] == "closed"


def test_generic_claim_writers_cannot_remove_release_settle_or_overwrite(owner):
    from agent_worktrees import tracking_claim_write

    path, _ = owner
    pending = reserve(path)
    before = path.read_bytes()
    args = {"worktree_id": "owner", "yaml_path": str(path), "ref": pending["ref"]}
    for remove in (False, True):
        assert tracking_claim_write.apply_claim_release({**args, "remove": remove})["error"]
    assert tracking_claim_write.apply_claim_settle({**args, "disposition": "at-rest"})["error"]
    assert tracking_claim_write.apply_claim_add({**args, "kind": "bridge"})["error"]
    assert path.read_bytes() == before


@pytest.mark.parametrize("initial,replay", [
    ({"number": 1}, {"number": True}),
    ({"number": 1}, {"number": 1.0}),
    ({"nested": [True]}, {"nested": [1]}),
])
def test_replayed_recovery_requires_canonical_json_identity(owner, initial, replay):
    path, _ = owner
    payload = {"kind": "bridge", "namespace": "bridge-remote", "request_id": "typed", "recovery": initial}
    assert call(path, "reserve", payload)["ok"]
    before = path.read_bytes()
    assert call(path, "reserve", {**payload, "recovery": replay})["error"]
    assert path.read_bytes() == before
