"""Artifact disposition settles responsibility without asserting byte absence."""

from __future__ import annotations

import copy

import pytest
import yaml

from agent_worktrees import claim_providers, finalize, resource_producer as producer, tracking
from agent_worktrees.producer_views import ObservedClaim, ownership_snapshot
from test_resource_producer import absence, bind, call, reserve
from test_resource_producer import owner as owner


def artifact(path):
    reservation = call(path, "reserve", {
        "kind": "artifact", "namespace": "scratch",
        "lifetime": producer.ARTIFACT_LIFETIME, "recovery": {"allocation": "opaque"},
    })
    assert reservation.get("ok"), reservation
    return bind(path, reservation)


def retention(bound):
    return {
        "available": True, "exists": True,
        "producer_verification": {
            "version": 1, "subject": bound["ref"],
            "identity": copy.deepcopy(bound["producer"]["identity"]),
            "allocation_finished": True, "work_settled": True,
            "retention": {"class": "disposable-scratch", "cleanup": "explicit-owner-pass"},
        },
    }


def test_retention_allows_finalize_with_bytes_and_preserves_receipt_on_stale_save(owner, monkeypatch):
    path, _ = owner
    stale = tracking.load_record(path)
    bound = artifact(path)
    allocated = path.parent / "retained-diagnostics"
    allocated.mkdir()
    (allocated / "failure.txt").write_text("bounded failure diagnostic")
    assert producer.decode_ref(bound["ref"])["lifetime"] == producer.ARTIFACT_LIFETIME
    monkeypatch.setattr(claim_providers, "resolve_claim_status", lambda ref: retention(bound))
    result = call(path, "retain", {"token": bound["token"]})
    assert result.get("ok") and result["phase"] == "retained", result
    tracking.save_record(stale, path)
    record = tracking.load_record(path)
    claim = record.resources[0]
    assert claim.producer == result["producer"] and not claim.is_unsettled
    assert finalize._assert_obligations_settled(record, "owner", abandon=False)
    assert (allocated / "failure.txt").read_text() == "bounded failure diagnostic"
    monkeypatch.setattr(claim_providers, "resolve_claim_status", lambda *a: pytest.fail("replay probed"))
    before = path.read_bytes()
    assert call(path, "retain", {"token": bound["token"]}) == result
    assert path.read_bytes() == before


def test_retained_allocation_stays_discoverable_and_duplicate_identity_is_uncertain(owner, monkeypatch):
    path, _ = owner
    bound = artifact(path)
    monkeypatch.setattr(claim_providers, "resolve_claim_status", lambda ref: retention(bound))
    result = call(path, "retain", {"token": bound["token"]})
    assert result.get("ok"), result
    claim = tracking.load_record(path).resources[0]
    row = ObservedClaim("m/p/owner", claim.kind, claim.ref, claim.state, "", claim.producer)
    view = ownership_snapshot([row], [])
    assert view["complete"] and view["entries"][0]["phase"] == "retained"
    assert view["entries"][0]["current_owner"] == "m/p/owner"
    assert view["cleanup_authority"] is False
    duplicate = copy.deepcopy(claim.producer)
    duplicate.update(token="x" * 32, phase="bound")
    duplicate.pop("closeout")
    other = ObservedClaim("m/p/owner", claim.kind, producer.claim_ref(duplicate), "live", "", duplicate)
    view = ownership_snapshot([row, other], [])
    assert not view["complete"]
    assert any(issue["code"] == "duplicate-identity" for issue in view["issues"])


@pytest.mark.parametrize("damage", ["available", "allocation_finished", "work_settled", "identity", "retention"])
def test_malformed_persisted_retention_still_blocks_finalize(owner, monkeypatch, damage):
    path, _ = owner
    bound = artifact(path)
    monkeypatch.setattr(claim_providers, "resolve_claim_status", lambda ref: retention(bound))
    assert call(path, "retain", {"token": bound["token"]}).get("ok")
    raw = yaml.safe_load(path.read_text())
    proof = raw["resources"][0]["producer"]["closeout"]
    if damage == "available":
        proof[damage] = False
    else:
        proof["producer_verification"][damage] = None
    path.write_text(yaml.safe_dump(raw))
    record = tracking.load_record(path)
    assert record.resources[0].is_unsettled
    assert not finalize._assert_obligations_settled(record, "owner", abandon=False)


@pytest.mark.parametrize("damage", [
    "available", "exists", "version", "subject", "identity",
    "allocation_finished", "work_settled", "retention", "numeric_identity",
])
def test_retention_requires_exact_provider_identity_and_quiescence(owner, monkeypatch, damage):
    path, _ = owner
    bound = artifact(path)
    proof = retention(bound)
    if damage in {"available", "exists"}:
        proof[damage] = None
    elif damage == "version":
        proof["producer_verification"][damage] = True
    elif damage == "numeric_identity":
        raw = yaml.safe_load(path.read_text())
        claim = raw["resources"][0]
        claim["producer"]["identity"] = {"number": 1}
        claim["ref"] = producer.claim_ref(claim["producer"])
        path.write_text(yaml.safe_dump(raw))
        proof = retention({
            **bound, "ref": claim["ref"], "producer": claim["producer"],
        })
        proof["producer_verification"]["identity"] = {"number": True}
    else:
        proof["producer_verification"][damage] = None
    monkeypatch.setattr(claim_providers, "resolve_claim_status", lambda ref: proof)
    before = path.read_bytes()
    result = call(path, "retain", {"token": bound["token"]})
    assert result.get("error"), result
    assert path.read_bytes() == before
    assert tracking.load_record(path).resources[0].is_unsettled


def test_legacy_disposable_cannot_retain_and_close_still_requires_absence(owner, monkeypatch):
    path, _ = owner
    bound = bind(path, reserve(path))
    monkeypatch.setattr(claim_providers, "resolve_claim_status", lambda ref: retention(bound))
    before = path.read_bytes()
    assert call(path, "retain", {"token": bound["token"]}).get("error")
    assert call(path, "close", {"token": bound["token"]}).get("error")
    assert path.read_bytes() == before
    assert "lifetime" not in producer.decode_ref(bound["ref"])


@pytest.mark.parametrize("allocating", [False, True])
def test_unbound_allocation_cannot_retain(owner, monkeypatch, allocating):
    path, _ = owner
    pending = call(path, "reserve", {
        "kind": "artifact", "namespace": "scratch",
        "lifetime": producer.ARTIFACT_LIFETIME, "recovery": {},
    })
    if allocating:
        assert call(path, "begin", {"token": pending["token"]}).get("ok")
    monkeypatch.setattr(claim_providers, "resolve_claim_status", lambda *a: pytest.fail("unbound probe"))
    before = path.read_bytes()
    assert call(path, "retain", {"token": pending["token"]}).get("error")
    assert path.read_bytes() == before


def test_retained_replay_and_later_absence_close_after_owner_finalized(owner, monkeypatch):
    path, _ = owner
    bound = artifact(path)
    monkeypatch.setattr(claim_providers, "resolve_claim_status", lambda ref: retention(bound))
    retained = call(path, "retain", {"token": bound["token"]})
    assert retained.get("ok"), retained
    record = tracking.load_record(path)
    record.status = "finalized"
    tracking.save_record(record, path)
    monkeypatch.setattr(claim_providers, "resolve_claim_status", lambda *a: pytest.fail("replay probed"))
    before = path.read_bytes()
    assert call(path, "retain", {"token": bound["token"]}) == retained
    assert path.read_bytes() == before
    monkeypatch.setattr(claim_providers, "resolve_claim_status", lambda ref: absence(ref))
    closed = call(path, "close", {"token": bound["token"]})
    assert closed.get("ok") and closed["phase"] == "closed", closed
    assert not tracking.load_record(path).resources[0].is_unsettled


def test_unavailable_provider_and_caller_supplied_retention_never_settle(owner, monkeypatch):
    path, _ = owner
    bound = artifact(path)
    monkeypatch.setattr(claim_providers, "discover_claim_providers", lambda *a: ({}, ()))
    before = path.read_bytes()
    assert call(path, "retain", {"token": bound["token"]}).get("error")
    assert call(path, "retain", {"token": bound["token"], "proof": retention(bound)}).get("error")
    assert path.read_bytes() == before


def test_retention_provider_race_does_not_overwrite_newer_phase(owner, monkeypatch):
    path, _ = owner
    bound = artifact(path)
    def raced(ref):
        raw = yaml.safe_load(path.read_text())
        raw["resources"][0]["producer"]["recovery"]["newer"] = True
        raw["resources"][0]["ref"] = producer.claim_ref(raw["resources"][0]["producer"])
        path.write_text(yaml.safe_dump(raw))
        return retention(bound)
    monkeypatch.setattr(claim_providers, "resolve_claim_status", raced)
    assert call(path, "retain", {"token": bound["token"]}).get("error")
    current = tracking.load_record(path).resources[0]
    assert current.is_unsettled and current.producer["recovery"]["newer"] is True
