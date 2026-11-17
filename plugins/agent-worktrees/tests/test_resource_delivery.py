"""Delivery closes exact responsibility without tracking later user moves."""

import copy
from pathlib import Path
from typing import Any, Callable

import pytest

from agent_worktrees import claim_providers, config as cfg, finalize, producer_views, resource_producer as producer, tracking
from test_resource_producer import bind as _bind, call as _call, owner as owner, reserve as _reserve

call: Callable[..., dict[str, Any]] = _call
bind: Callable[[Path, dict[str, Any]], dict[str, Any]] = _bind
reserve: Callable[[Path], dict[str, Any]] = _reserve
Owner = tuple[Path, cfg.Config]


def allocation(path: Path) -> dict[str, Any]:
    reserved = call(path, "reserve", {
        "kind": "artifact", "namespace": "test-output", "recovery": {"request": "fixture"},
        "lifetime": producer.USER_OUTPUT_LIFETIME,
    })
    assert reserved.get("ok"), reserved
    return bind(path, reserved)


def delivery(bound: dict[str, Any]) -> dict[str, Any]:
    return {
        "available": True, "exists": True,
        "producer_verification": {
            "version": 1, "subject": bound["ref"], "identity": bound["producer"]["identity"],
            "allocation_finished": True, "work_settled": True,
            "delivery": {
                "destination": "fixture-output", "delivered_at": "2026-01-02T03:04:05+00:00",
                "content": {"files": 1, "bytes": 7, "sha256": "a" * 64}, "result": "verified-local",
            },
        },
    }


def test_delivered_receipt_is_terminal_historical_and_exact_retry_needs_no_storage(
    owner: Owner, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, _ = owner
    bound = allocation(path)
    monkeypatch.setattr(claim_providers, "resolve_claim_status", lambda ref: delivery(bound))
    result = call(path, "deliver", {"token": bound["token"]})
    assert result.get("ok") and result["phase"] == "delivered", result
    claim = tracking.load_record(path).resources[0]
    assert not claim.is_unsettled
    assert finalize._assert_obligations_settled(tracking.load_record(path), "owner", abandon=False)
    snapshot = producer_views.ownership_snapshot([
        producer_views.ObservedClaim("m/p/owner", claim.kind, claim.ref, claim.state, "", claim.producer),
    ], [])
    assert snapshot["complete"]
    assert snapshot["entries"][0]["current_owner"] is None
    assert snapshot["entries"][0]["resolution"] == "history"
    record = tracking.load_record(path)
    record.status = "finalized"
    tracking.save_record(record, path)
    monkeypatch.setattr(claim_providers, "resolve_claim_status",
                        lambda ref: pytest.fail("completed delivery must not probe a user destination"))
    assert call(path, "deliver", {"token": bound["token"]}) == result


@pytest.mark.parametrize("fault", [
    "unavailable", "absent", "subject", "identity", "active", "time", "over-budget", "hash", "extra",
])
def test_unverified_delivery_preserves_original_responsibility(
    owner: Owner, monkeypatch: pytest.MonkeyPatch, fault: str,
) -> None:
    path, _ = owner
    bound = allocation(path)
    result = copy.deepcopy(delivery(bound))
    proof = result["producer_verification"]
    if fault == "unavailable":
        result["available"] = False
    elif fault == "absent":
        result["exists"] = False
    elif fault == "subject":
        proof["subject"] = "another-subject"
    elif fault == "identity":
        proof["identity"] = {"other": True}
    elif fault == "active":
        proof["work_settled"] = False
    elif fault == "time":
        proof["delivery"]["delivered_at"] = "2026-01-02"
    elif fault == "over-budget":
        proof["delivery"]["content"]["files"] = 17
    elif fault == "hash":
        proof["delivery"]["content"]["sha256"] = "not-a-hash"
    else:
        proof["delivery"]["inventory"] = []
    monkeypatch.setattr(claim_providers, "resolve_claim_status", lambda ref: result)
    before = path.read_bytes()
    assert call(path, "deliver", {"token": bound["token"]}).get("error")
    assert path.read_bytes() == before and tracking.load_record(path).resources[0].is_unsettled


def test_delivery_cannot_repurpose_scratch_or_absence(owner: Owner, monkeypatch: pytest.MonkeyPatch) -> None:
    from test_resource_producer import absence

    path, _ = owner
    scratch = bind(path, reserve(path))
    output = allocation(path)
    monkeypatch.setattr(claim_providers, "resolve_claim_status", absence)
    assert call(path, "deliver", {"token": scratch["token"]}).get("error")
    assert call(path, "retain", {"token": output["token"]}).get("error")
    assert call(path, "close", {"token": output["token"]}).get("error")


def test_concurrent_delivery_returns_the_already_committed_receipt(
    owner: Owner, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, _ = owner
    bound = allocation(path)
    committed: list[dict[str, Any]] = []
    def raced(ref: str) -> dict[str, Any]:
        fresh = delivery(bound)
        fresh["producer_verification"]["delivery"]["delivered_at"] = "2026-01-03T03:04:05+00:00"
        with monkeypatch.context() as patch:
            patch.setattr(claim_providers, "resolve_claim_status", lambda ref: fresh)
            committed.append(call(path, "deliver", {"token": bound["token"]}))
        return delivery(bound)
    monkeypatch.setattr(claim_providers, "resolve_claim_status", raced)
    assert call(path, "deliver", {"token": bound["token"]}) == committed[0]
    assert committed[0]["producer"]["closeout"]["producer_verification"]["delivery"]["delivered_at"].startswith(
        "2026-01-03",
    )


def test_user_output_handoff_cannot_transfer_unverified_delivery(owner: Owner) -> None:
    from agent_worktrees import claim_handoffs

    path, _ = owner
    bound = allocation(path)
    before = path.read_bytes()
    with pytest.raises(claim_handoffs.ClaimHandoffError, match="delivery support"):
        claim_handoffs.offer("m/p/owner", "m/p/consumer", [bound["ref"]], machine="m")
    assert path.read_bytes() == before and tracking.load_record(path).resources[0].is_unsettled
