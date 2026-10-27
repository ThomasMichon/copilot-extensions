"""Real temporary ledgers and receiving-owner doubles for origin responsibility."""

from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from agent_worktrees import owned_worktree as allocation, owned_worktree_remote as remote
from agent_worktrees import resource_producer as producer, tracking, tracking_claim_write, tracking_write
from agent_worktrees.tracking_allocation_write import apply_worktree_allocation
from test_resource_producer import owner as owner


TARGET = {"machine": "remote", "project": "target", "options": {"no_pair": True}}
IDENTITY = {
    "ref": "remote/target/child", "creation_nonce": "a1b2c3",
    "path": "/receiving/child", "branch": "worktree/child",
}


def call(path: Path, operation: str, payload: dict, owner_ref: str = "m/p/owner") -> dict:
    return apply_worktree_allocation({
        "schema": allocation.SCHEMA, "version": allocation.VERSION,
        "operation": operation, "input": payload, "owner_ref": owner_ref, "yaml_path": str(path),
    })


def reserve(path: Path) -> dict:
    result = call(path, "reserve", {"request_id": "one", "target": TARGET})
    assert result.get("ok"), result
    return result


def observation(metadata: dict, status: str = "allocated") -> dict:
    return {
        "schema": allocation.SCHEMA, "version": allocation.VERSION,
        "origin": metadata["creator"], "request_id": metadata["request_id"],
        "token": metadata["token"], "target": copy.deepcopy(metadata["recovery"]),
        "identity": None if status == "cancelled" else copy.deepcopy(IDENTITY),
        "status": status, "allocation_finished": True,
    }


def test_reserve_is_durable_idempotent_and_concurrent_before_any_remote_effect(owner, monkeypatch):
    path, _ = owner

    def forbidden(*args: object) -> None:
        pytest.fail("origin reservation must not call the receiving owner")

    monkeypatch.setattr(remote, "request", forbidden)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: reserve(path), range(8)))
    assert all(result == results[0] for result in results)
    record = tracking.load_record(path)
    assert len(record.resources) == 1 and record.resources[0].is_unsettled
    assert record.resources[0].producer == results[0]["producer"]
    before = path.read_bytes()
    assert call(path, "inspect", {"request_id": "one"}) == results[0]
    assert call(path, "inspect", {"token": results[0]["token"]}) == results[0]
    assert reserve(path) == results[0] and path.read_bytes() == before


@pytest.mark.parametrize("target", [
    {**TARGET, "project": "other"},
    {**TARGET, "options": {"no_pair": 1}},
    {**TARGET, "options": {}},
])
def test_request_cannot_be_reinterpreted_as_other_target_facts(owner, target):
    path, _ = owner
    reserve(path)
    before = path.read_bytes()
    assert call(path, "reserve", {"request_id": "one", "target": target})["error"]
    assert path.read_bytes() == before


def test_bound_native_ref_confirm_and_exact_conclusion_are_distinct(owner, monkeypatch):
    path, _ = owner
    pending = reserve(path)
    seen = []

    def receive(operation: str, metadata: dict) -> dict:
        seen.append((operation, metadata["phase"]))
        assert tracking.load_record(path).resources[0].producer == metadata
        return observation(metadata, "confirmed" if operation == "confirm" else "allocated")

    monkeypatch.setattr(remote, "request", receive)
    handle = {"token": pending["token"]}
    bound = call(path, "bind", handle)
    assert bound.get("ok") and bound["phase"] == "bound"
    assert bound["ref"] == IDENTITY["ref"]
    assert reserve(path) == bound
    assert call(path, "bind", handle) == bound
    confirmed = call(path, "confirm", handle)
    assert confirmed["phase"] == "confirmed"
    assert call(path, "confirm", handle) == confirmed
    before = path.read_bytes()
    assert call(path, "close", handle)["error"]
    assert path.read_bytes() == before
    assert seen == [("inspect", "pending"), ("confirm", "bound"), ("inspect", "confirmed")]
    monkeypatch.setattr(remote, "request", lambda op, meta: observation(meta, "finalized"))
    closed = call(path, "close", handle)
    assert closed["phase"] == "closed"
    assert not tracking.load_record(path).resources[0].is_unsettled
    record = tracking.load_record(path)
    record.status = "finalized"
    tracking.save_record(record, path)
    before = path.read_bytes()
    assert call(path, "close", handle) == closed
    assert reserve(path) == closed
    assert call(path, "inspect", handle) == closed and path.read_bytes() == before
    assert call(path, "confirm", handle)["error"]


@pytest.mark.parametrize("changed", [
    {"schema": "other"}, {"version": True}, {"origin": "m/p/other"},
    {"request_id": "other"}, {"token": "b" * 32}, {"target": {**TARGET, "project": "other"}},
    {"identity": {**IDENTITY, "ref": "other/target/child"}},
    {"identity": {**IDENTITY, "creation_nonce": ""}},
    {"allocation_finished": False}, {"allocation_finished": 1},
    {"status": "pending"}, {"status": "absent"}, {"unknown": True},
])
def test_bind_requires_exact_complete_receiving_evidence(owner, monkeypatch, changed):
    path, _ = owner
    pending = reserve(path)
    monkeypatch.setattr(remote, "request", lambda op, meta: {**observation(meta), **changed})
    before = path.read_bytes()
    assert call(path, "bind", {"token": pending["token"]})["error"]
    assert path.read_bytes() == before


def test_callers_cannot_supply_identity_confirmation_or_conclusion(owner):
    path, _ = owner
    pending = reserve(path)
    before = path.read_bytes()
    for operation in ("bind", "confirm", "close", "cancel"):
        assert call(path, operation, {
            "token": pending["token"], "proof": observation(pending["producer"], "finalized"),
        })["error"]
    assert path.read_bytes() == before


def test_pending_closes_only_from_original_sealed_cancellation(owner, monkeypatch):
    path, _ = owner
    pending = reserve(path)
    before = path.read_bytes()
    monkeypatch.setattr(remote, "request", lambda op, meta: observation(meta, "finalized"))
    assert call(path, "close", {"token": pending["token"]})["error"]
    assert path.read_bytes() == before
    monkeypatch.setattr(remote, "request", lambda op, meta: observation(meta, "cancelled"))
    assert call(path, "cancel", {"token": pending["token"]})["phase"] == "closed"
    assert len(tracking.load_record(path).resources) == 1


def test_oversized_future_closeout_is_rejected_before_reservation_or_receiving_io(owner, monkeypatch):
    path, _ = owner
    before = path.read_bytes()
    target = {**TARGET, "authority": "x" * 1024, "options": {"name": "x" * 7000}}
    assert allocation.valid_target(target)
    monkeypatch.setattr(remote, "request", lambda *args: pytest.fail("oversized reservation made remote effects"))
    result = call(path, "reserve", {"request_id": "large", "target": target})
    assert result["error"] and "lifecycle" in result["message"]
    assert path.read_bytes() == before and not tracking.load_record(path).resources


@pytest.mark.parametrize("conclusion", ["cancelled", "finalized", "archived"])
def test_maximum_admitted_target_and_identity_can_close_within_actual_wire_limit(owner, monkeypatch, conclusion):
    path, _ = owner
    monkeypatch.setattr("agent_worktrees.tracking_allocation_write.secrets.token_urlsafe", lambda n: "t" * 43)
    low, high = 0, producer.MAX_INPUT_BYTES
    while low + 1 < high:
        middle = (low + high) // 2
        candidate = {**TARGET, "options": {"name": "x" * middle}}
        try:
            allocation.require_lifecycle_capacity("m/p/owner", "edge", "t" * 43, candidate)
        except ValueError:
            high = middle
        else:
            low = middle
    target = {**TARGET, "options": {"name": "x" * low}}
    pending = call(path, "reserve", {"request_id": "edge", "target": target})
    assert pending.get("ok"), pending
    before = path.read_bytes()
    refused = call(path, "reserve", {
        "request_id": "edge", "target": {**TARGET, "options": {"name": "x" * high}},
    })
    assert refused["error"] and path.read_bytes() == before
    identity = {
        "ref": "remote/target/" + "\U0010ffff" * 256,
        "creation_nonce": "n" * 128, "branch": "b" * 128, "path": "/x",
    }
    identity["path"] += "x" * (allocation.MAX_IDENTITY_BYTES - len(producer.canonical_json(identity)))
    assert len(producer.canonical_json(identity)) == allocation.MAX_IDENTITY_BYTES
    allocation.AllocationIdentity.from_dict(identity)

    def receive(operation: str, metadata: dict) -> dict:
        proof = observation(metadata, "allocated" if operation == "inspect" and metadata["phase"] == "pending" else conclusion)
        proof["identity"] = None if conclusion == "cancelled" else identity
        producer.canonical_json(proof)
        return proof

    monkeypatch.setattr(remote, "request", receive)
    handle = {"token": pending["token"]}
    if conclusion != "cancelled":
        bound = call(path, "bind", handle)
        assert bound.get("ok"), bound
        producer.canonical_json(bound)
    closed = call(path, "cancel" if conclusion == "cancelled" else "close", handle)
    assert closed.get("ok") and closed["phase"] == "closed", closed
    assert len(allocation.wire_json(closed)) <= producer.MAX_INPUT_BYTES
    claim = tracking.load_record(path).resources[0]
    assert producer.valid_metadata(claim.producer) and not claim.is_unsettled
    assert call(path, "inspect", handle) == closed


def test_receiving_io_is_outside_lock_and_concurrent_change_is_not_overwritten(owner, monkeypatch):
    path, _ = owner
    pending = reserve(path)
    handle = {"token": pending["token"]}

    def race(operation: str, metadata: dict) -> dict:
        monkeypatch.setattr(remote, "request", lambda op, meta: observation(meta))
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(call, path, "bind", handle).result(timeout=3)["phase"] == "bound"
        return observation(metadata)

    monkeypatch.setattr(remote, "request", race)
    result = call(path, "bind", handle)
    assert result["error"] and "changed" in result["message"]
    assert tracking.load_record(path).resources[0].producer["phase"] == "bound"


def test_lost_confirmation_response_retains_bound_claim_and_recovers_same_identity(owner, monkeypatch):
    path, _ = owner
    pending = reserve(path)
    handle = {"token": pending["token"]}
    monkeypatch.setattr(remote, "request", lambda op, meta: observation(meta))
    bound = call(path, "bind", handle)
    before = path.read_bytes()

    def lost(*args: object) -> dict:
        raise RuntimeError("response lost after receiving acknowledgement")

    monkeypatch.setattr(remote, "request", lost)
    assert call(path, "confirm", handle)["error"]
    assert path.read_bytes() == before
    monkeypatch.setattr(remote, "request", lambda op, meta: observation(meta, "confirmed"))
    confirmed = call(path, "confirm", handle)
    assert confirmed["phase"] == "confirmed"
    assert confirmed["producer"]["identity"] == bound["producer"]["identity"]
    assert len(tracking.load_record(path).resources) == 1


def test_stale_and_generic_writers_cannot_drop_or_release_new_responsibility(owner):
    path, _ = owner
    stale = tracking.load_record(path)
    pending = reserve(path)
    tracking.save_record(stale, path)
    assert tracking.load_record(path).resources[0].producer == pending["producer"]
    before = path.read_bytes()
    args = {"worktree_id": "owner", "yaml_path": str(path), "ref": pending["ref"]}
    assert tracking_claim_write.apply_claim_release({**args, "remove": True})["error"]
    assert tracking_claim_write.apply_claim_settle({**args, "disposition": "at-rest"})["error"]
    assert path.read_bytes() == before


@pytest.mark.parametrize("status", sorted(producer.FROZEN_OWNER_STATUSES))
def test_terminal_origin_cannot_start_responsibility(owner, status):
    path, _ = owner
    record = tracking.load_record(path)
    record.status = status
    tracking.save_record(record, path)
    before = path.read_bytes()
    assert call(path, "reserve", {"request_id": "new", "target": TARGET})["error"]
    assert path.read_bytes() == before


@pytest.mark.parametrize("owner_ref", ["m/p/consumer", "remote/p/owner", "owner"])
def test_other_record_or_foreign_authority_cannot_reserve(owner, owner_ref):
    path, _ = owner
    before = path.read_bytes()
    assert call(path, "reserve", {"request_id": "new", "target": TARGET}, owner_ref)["error"]
    assert path.read_bytes() == before


def test_new_verb_is_registered_for_fresh_process_dispatch(owner):
    path, _ = owner
    result = tracking_write.run_direct("worktree_allocation", {
        "schema": allocation.SCHEMA, "version": allocation.VERSION, "operation": "reserve",
        "input": {"request_id": "registered", "target": TARGET},
        "owner_ref": "m/p/owner", "yaml_path": str(path),
    }, reason="isolated allocation transaction regression")
    assert result.get("ok") and result["request_id"] == "registered"
