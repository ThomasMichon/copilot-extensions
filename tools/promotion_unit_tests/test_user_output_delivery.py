"""Actor-free delivery-proof decisions with the existing reference-owner boundary."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from tools.promotion_unit_tests.test_owned_worktree import api as api


def receipt() -> tuple[dict[str, Any], str, dict[str, Any]]:
    subject = "unit-output:exact-subject"
    identity = {"generation": 1, "allocation": "original"}
    return {
        "available": True, "exists": True,
        "producer_verification": {
            "version": 1, "subject": subject, "identity": copy.deepcopy(identity),
            "allocation_finished": True, "work_settled": True,
            "delivery": {
                "destination": "selected-user-output", "delivered_at": "2026-01-02T03:04:05+00:00",
                "content": {"files": 1, "bytes": 7, "sha256": "a" * 64}, "result": "verified-local",
            },
        },
    }, subject, identity


def test_exact_provider_delivery_accepts_the_bounded_historical_result(api: Any) -> None:
    api.load("resource_delivery")
    result, subject, identity = receipt()
    before = copy.deepcopy(result)
    assert api.producer.verified_delivery(result, subject, identity)
    assert result == before


@pytest.mark.parametrize("damage", [
    "available", "version", "subject", "identity-json-type",
    "quiescence", "naive-time", "file-budget", "extra-receipt",
])
def test_unverified_delivery_never_becomes_a_completion(api: Any, damage: str) -> None:
    api.load("resource_delivery")
    result, subject, identity = receipt()
    proof = result["producer_verification"]
    if damage == "available":
        result["available"] = 1
    elif damage == "version":
        proof["version"] = True
    elif damage == "subject":
        proof["subject"] = "another-subject"
    elif damage == "identity-json-type":
        proof["identity"]["generation"] = True
    elif damage == "quiescence":
        proof["work_settled"] = False
    elif damage == "naive-time":
        proof["delivery"]["delivered_at"] = "2026-01-02"
    elif damage == "file-budget":
        proof["delivery"]["content"]["files"] = 17
    else:
        proof["delivery"]["destination_inventory"] = []
    assert not api.producer.verified_delivery(result, subject, identity)
