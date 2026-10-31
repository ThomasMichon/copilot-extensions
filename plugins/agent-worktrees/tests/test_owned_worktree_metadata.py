"""Owned worktrees retain their own completion and native-reference semantics."""

from __future__ import annotations

import copy
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_worktrees import resource_producer as producer
from agent_worktrees import tracking


def pending() -> dict:
    return {
        "version": 3,
        "lifetime": "owned-worktree",
        "namespace": "worktree-allocation",
        "token": "a" * 32,
        "request_id": "request-one",
        "creator": "origin/harness/owner",
        "phase": "pending",
        "recovery": {"machine": "target", "project": "product"},
    }


def bound(*, confirmed: bool = False) -> dict:
    return {
        **pending(),
        "phase": "confirmed" if confirmed else "bound",
        "identity": {
            "ref": "target/product/child",
            "creation_nonce": "b" * 32,
            "path": "/target/child",
            "branch": "worktree/child",
        },
    }


def concluded(value: dict) -> dict:
    return {
        "schema": "copilot-extensions.worktree-allocation",
        "version": 1,
        "origin": value["creator"],
        "request_id": value["request_id"],
        "token": value["token"],
        "target": value["recovery"],
        "identity": value.get("identity"),
        "status": "finalized" if "identity" in value else "cancelled",
        "allocation_finished": True,
    }


def test_owned_worktree_pending_is_a_recognized_protected_lifetime() -> None:
    value = pending()
    assert producer.valid_metadata(value)
    assert producer.claim_ref(value).startswith("worktree-allocation:w1.")
    assert producer.wire_metadata(value) == value


@pytest.mark.parametrize("confirmed", [False, True])
def test_bound_worktree_claim_uses_the_actual_qualified_child_reference(
    confirmed: bool,
) -> None:
    value = bound(confirmed=confirmed)
    assert producer.valid_metadata(value)
    assert producer.claim_ref(value) == "target/product/child"
    claim = tracking.ResourceClaim(kind="worktree", ref=producer.claim_ref(value), producer=value)
    assert claim.is_unsettled


def test_exact_worktree_conclusion_settles_without_claiming_directory_absence() -> None:
    value = bound(confirmed=True)
    closed = {**value, "phase": "closed", "closeout": concluded(value)}
    assert producer.valid_metadata(closed)
    claim = tracking.ResourceClaim(kind="worktree", ref="target/product/child", producer=closed)
    assert not claim.is_unsettled


def test_original_sealed_cancellation_retains_the_pending_subject() -> None:
    value = pending()
    closed = {**value, "phase": "closed", "closeout": concluded(value)}
    assert producer.valid_metadata(closed)
    assert producer.claim_ref(closed) == producer.claim_ref(value)
    assert not tracking.ResourceClaim(
        kind="worktree", ref=producer.claim_ref(value), producer=closed,
    ).is_unsettled


@pytest.mark.parametrize("change", [
    {"version": True}, {"version": 2}, {"phase": "retired"},
    {"creator": "unqualified"}, {"request_id": ""}, {"recovery": {}},
    {"identity": {}}, {"unknown": True},
    {"creator": "origin name/harness/owner"},
    {"recovery": {"machine": "target\\guest", "project": "product"}},
])
def test_malformed_owned_worktree_metadata_remains_visible_and_blocking(change: dict) -> None:
    value = {**pending(), **change}
    assert not producer.valid_metadata(value)
    assert producer.wire_metadata(value) == {"error": "invalid-producer-metadata"}
    assert tracking.ResourceClaim(kind="worktree", ref="opaque", producer=value).is_unsettled


@pytest.mark.parametrize("proof", [
    {"exists": False},
    {"producer_verification": {"version": 1, "absent": True}},
    {"relationship_verification": {"version": 1, "retired": True, "quiescent": True}},
])
def test_absence_and_conversation_retirement_are_not_child_conclusion(proof: dict) -> None:
    value = bound(confirmed=True)
    assert not producer.valid_metadata({**value, "phase": "closed", "closeout": proof})


@pytest.mark.parametrize("field", [
    "origin", "request_id", "token", "target", "identity", "allocation_finished",
])
def test_conclusion_must_match_every_original_allocation_identity(field: str) -> None:
    value = bound(confirmed=True)
    proof = concluded(value)
    proof.pop(field)
    assert not producer.valid_metadata({**value, "phase": "closed", "closeout": proof})


@pytest.mark.parametrize("status", [True, {}, [], "retired", "absent", "active"])
def test_unknown_or_wrong_typed_lifecycle_status_is_not_conclusion(status: object) -> None:
    value = bound(confirmed=True)
    proof = {**concluded(value), "status": status}
    assert not producer.valid_metadata({**value, "phase": "closed", "closeout": proof})


def test_pending_worktree_cannot_freeze_its_parent_even_through_a_generic_save() -> None:
    value = pending()
    claim = tracking.ResourceClaim(kind="worktree", ref=producer.claim_ref(value), producer=value)
    current = SimpleNamespace(status="active", resources=[claim])
    proposed = copy.deepcopy(current)
    proposed.status = "finalized"
    with pytest.raises(ValueError, match="owned worktree"):
        producer.preserve_claims(proposed, current, Path("owner.yaml"))
