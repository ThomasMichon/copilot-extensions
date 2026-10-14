"""Allocation permission, quiescence recovery and stale-absence regressions."""

from __future__ import annotations

import copy
import json

import pytest

from agent_worktrees import claim_providers, producer_allocation as allocation
from agent_worktrees import resource_producer as producer, tracking
from test_resource_producer import absence, call, reserve
from test_resource_producer import owner as owner

IDENTITY = {"immutable_id": "full-id", "daemon": "opaque"}


def begin(path, reservation):
    result = call(path, "begin", {"token": reservation["token"]})
    assert result.get("ok"), result
    return result


def complete(path, permit):
    return call(path, "complete", {
        "token": permit["token"], "allocation_fence": permit["allocation_fence"],
    })


def finished(ref, *, exists=False, identity=None):
    result = absence(ref)
    result["exists"] = exists
    result["producer_verification"]["allocation_finished"] = True
    if exists:
        result["producer_verification"].pop("absent")
        result["producer_verification"]["identity"] = identity
    return result


def test_begin_durably_fences_allocation_without_persisting_private_key(owner):
    path, _ = owner
    pending = reserve(path)
    permit = begin(path, pending)
    claim = tracking.load_record(path).resources[0]
    assert claim.is_unsettled and claim.producer["phase"] == "allocating"
    assert permit["allocation_fence"]["generation"] == 1
    assert allocation.matches_fence(claim.producer, permit["allocation_fence"])
    assert producer.decode_ref(claim.ref)["allocation_epoch"] == claim.producer["allocation_epoch"]
    assert producer.decode_ref(claim.ref)["phase"] == "allocating"
    assert claim.ref != pending["ref"]
    assert permit["allocation_fence"]["key"] not in path.read_text()
    inspected = call(path, "inspect", {"token": permit["token"]})
    assert "allocation_fence" not in inspected
    assert permit["allocation_fence"]["key"] not in json.dumps(inspected)


def test_close_before_begin_revokes_permission_to_allocate(owner, monkeypatch):
    path, _ = owner
    pending = reserve(path)
    monkeypatch.setattr(claim_providers, "resolve_claim_status", absence)
    assert call(path, "close", {"token": pending["token"]})["phase"] == "closed"
    before = path.read_bytes()
    assert call(path, "begin", {"token": pending["token"]})["error"]
    assert path.read_bytes() == before


def test_close_cannot_discharge_allocating_even_when_provider_would_report_absence(owner, monkeypatch):
    path, _ = owner
    permit = begin(path, reserve(path))
    monkeypatch.setattr(claim_providers, "resolve_claim_status", lambda *a: pytest.fail("allocating close probed absence"))
    before = path.read_bytes()
    result = call(path, "close", {"token": permit["token"]})
    assert result["error"] and "progress" in result["message"]
    assert path.read_bytes() == before and tracking.load_record(path).resources[0].is_unsettled


def test_allocation_after_unlocked_absence_before_bind_keeps_obligation(owner, monkeypatch):
    path, _ = owner
    pending = reserve(path)
    permits = []
    def probe(ref):
        permits.append(begin(path, pending))
        # External creation now has durable permission, but binding has not run.
        return absence(ref)
    monkeypatch.setattr(claim_providers, "resolve_claim_status", probe)
    assert call(path, "close", {"token": pending["token"]})["error"]
    assert tracking.load_record(path).resources[0].producer["phase"] == "allocating"
    bound = call(path, "bind", {
        "token": pending["token"], "identity": IDENTITY,
        "allocation_fence": permits[0]["allocation_fence"],
    })
    assert bound["phase"] == "bound" and tracking.load_record(path).resources[0].is_unsettled


def test_completed_attempt_changes_subject_and_old_absence_cannot_close(owner, monkeypatch):
    path, _ = owner
    pending = reserve(path)
    def probe(ref):
        permit = begin(path, pending)
        assert complete(path, permit)["phase"] == "pending"
        return absence(ref)
    monkeypatch.setattr(claim_providers, "resolve_claim_status", probe)
    assert call(path, "close", {"token": pending["token"]})["error"]
    claim = tracking.load_record(path).resources[0]
    assert claim.is_unsettled and claim.producer["phase"] == "pending"
    assert claim.ref != pending["ref"]
    monkeypatch.setattr(claim_providers, "resolve_claim_status", absence)
    assert call(path, "close", {"token": pending["token"]})["phase"] == "closed"


def test_repeated_key_cannot_create_epoch_aba_or_complete_later_attempt(owner, monkeypatch):
    path, _ = owner
    pending = reserve(path)
    monkeypatch.setattr(allocation.secrets, "token_urlsafe", lambda n: "k" * 43)
    first = begin(path, pending)
    assert complete(path, first)["phase"] == "pending"
    before_second = call(path, "inspect", {"token": pending["token"]})
    monkeypatch.setattr(claim_providers, "resolve_claim_status", absence)
    second = begin(path, pending)
    assert first["allocation_fence"]["key"] == second["allocation_fence"]["key"]
    assert second["allocation_fence"]["generation"] == 2
    assert complete(path, first)["error"]
    assert complete(path, second)["phase"] == "pending"
    after_second = call(path, "inspect", {"token": pending["token"]})
    assert after_second["ref"] != before_second["ref"]
    assert after_second["producer"]["allocation_epoch"]["generation"] == 2


def test_retry_requires_confirmed_no_allocation_not_just_holder_completion(owner, monkeypatch):
    path, _ = owner
    permit = begin(path, reserve(path))
    assert complete(path, permit)["phase"] == "pending"
    before = path.read_bytes()
    for result in ({"available": False}, {"available": True, "exists": True}, {"available": True, "exists": False}):
        monkeypatch.setattr(claim_providers, "resolve_claim_status", lambda ref, value=result: value)
        assert call(path, "begin", {"token": permit["token"]})["error"]
        assert path.read_bytes() == before


@pytest.mark.parametrize("fence", [
    {"generation": 1, "key": "x" * 43},
    {"generation": 2, "key": "k" * 43},
    {"generation": True, "key": "k" * 43},
    {"generation": 1, "key": "bad"},
    {"generation": 1, "key": "k" * 43, "extra": True},
])
def test_wrong_or_malformed_fence_cannot_complete_or_bind(owner, fence):
    path, _ = owner
    permit = begin(path, reserve(path))
    before = path.read_bytes()
    for operation, extra in (("complete", {}), ("bind", {"identity": IDENTITY})):
        result = call(path, operation, {"token": permit["token"], "allocation_fence": fence, **extra})
        assert result["error"] and path.read_bytes() == before


def test_completion_is_idempotent_and_does_not_discharge_owned_resource(owner, monkeypatch):
    path, _ = owner
    permit = begin(path, reserve(path))
    result = complete(path, permit)
    before = path.read_bytes()
    assert complete(path, permit) == result and path.read_bytes() == before
    monkeypatch.setattr(claim_providers, "resolve_claim_status", lambda ref: {"available": True, "exists": True})
    assert call(path, "close", {"token": permit["token"]})["error"]
    assert tracking.load_record(path).resources[0].is_unsettled


def test_bound_attempt_cannot_complete_and_erase_identity(owner):
    path, _ = owner
    permit = begin(path, reserve(path))
    bound = call(path, "bind", {
        "token": permit["token"], "identity": IDENTITY,
        "allocation_fence": permit["allocation_fence"],
    })
    before = path.read_bytes()
    assert complete(path, permit)["error"]
    assert path.read_bytes() == before
    assert tracking.load_record(path).resources[0].producer == bound["producer"]


@pytest.mark.parametrize("operation", ["complete", "bind"])
def test_lost_fence_recovery_requires_quiescence_and_exact_subject(owner, monkeypatch, operation):
    path, _ = owner
    permit = begin(path, reserve(path))
    payload = {"token": permit["token"], **({"identity": IDENTITY} if operation == "bind" else {})}
    before = path.read_bytes()
    valid = finished(permit["ref"], exists=operation == "bind", identity=IDENTITY)
    cases = [absence(permit["ref"]), {"available": False}, copy.deepcopy(valid), copy.deepcopy(valid)]
    cases[-2]["producer_verification"]["subject"] = "wrong:subject"
    cases[-1]["producer_verification"]["allocation_finished"] = 1
    for result in cases:
        monkeypatch.setattr(claim_providers, "resolve_claim_status", lambda ref, value=result: value)
        assert call(path, operation, payload)["error"] and path.read_bytes() == before
    monkeypatch.setattr(claim_providers, "resolve_claim_status", lambda ref: valid)
    recovered = call(path, operation, payload)
    assert recovered["phase"] == ("bound" if operation == "bind" else "pending")
    assert tracking.load_record(path).resources[0].is_unsettled
    assert call(path, operation, payload) == recovered


def test_recovery_binding_rejects_provider_identity_mismatch(owner, monkeypatch):
    path, _ = owner
    permit = begin(path, reserve(path))
    monkeypatch.setattr(claim_providers, "resolve_claim_status", lambda ref: finished(ref, exists=True, identity={"other": "id"}))
    before = path.read_bytes()
    assert call(path, "bind", {"token": permit["token"], "identity": IDENTITY})["error"]
    assert path.read_bytes() == before


def test_recovery_completion_subject_changed_by_allocator_bind_is_not_applied(owner, monkeypatch):
    path, _ = owner
    permit = begin(path, reserve(path))
    def probe(ref):
        assert call(path, "bind", {
            "token": permit["token"], "identity": IDENTITY,
            "allocation_fence": permit["allocation_fence"],
        })["phase"] == "bound"
        return finished(ref)
    monkeypatch.setattr(claim_providers, "resolve_claim_status", probe)
    assert call(path, "complete", {"token": permit["token"]})["error"]
    assert tracking.load_record(path).resources[0].producer["phase"] == "bound"


def test_generation_exhaustion_refuses_without_wrapping_or_record_change(owner, monkeypatch):
    path, _ = owner
    permit = begin(path, reserve(path))
    assert complete(path, permit)["phase"] == "pending"
    raw = tracking.load_record(path)
    metadata = {**raw.resources[0].producer, "allocation_epoch": {
        **raw.resources[0].producer["allocation_epoch"], "generation": allocation.MAX_GENERATION,
    }}
    with producer.transaction(path):
        allocation.set_metadata(raw.resources[0], metadata)
        tracking.save_record(raw, path)
    before = path.read_bytes()
    monkeypatch.setattr(claim_providers, "resolve_claim_status", absence)
    assert call(path, "begin", {"token": permit["token"]})["error"]
    assert path.read_bytes() == before
