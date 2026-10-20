"""Pure retained-lifetime envelope and proof validation."""

from __future__ import annotations

import copy

import pytest

from agent_worktrees import resource_producer as producer
from agent_worktrees import resource_relationship as relationship


def metadata():
    return {
        "version": 2, "lifetime": "retained-relationship",
        "token": "a" * 32, "namespace": "example", "phase": "pending",
        "creator": "machine/project/owner", "request_id": "one", "recovery": {},
    }


@pytest.mark.parametrize("change", [
    {"version": True}, {"version": 1}, {"version": 99}, {"lifetime": "disposable-owned"},
    {"phase": "allocating"}, {"phase": "transferred"}, {"phase": "closed"},
    {"creator": "unqualified"}, {"request_id": ""}, {"request_id": "x" * 129},
    {"namespace": "--apply"}, {"recovery": []}, {"identity": {}},
    {"allocation_epoch": {"generation": 1}}, {"unknown": True},
])
def test_invalid_relationship_metadata_is_not_a_settled_producer(change):
    value = {**metadata(), **change}
    assert not relationship.valid_metadata(value)
    assert not producer.valid_metadata(value)
    assert producer.wire_metadata(value) == {"error": "invalid-producer-metadata"}


def test_pending_and_bound_subjects_roundtrip_and_distinguish_identity():
    pending = metadata()
    assert producer.valid_metadata(pending)
    ref = producer.claim_ref(pending)
    assert ref.startswith("example:l1.")
    assert relationship.decode_ref(ref)["request_id"] == "one"
    bound = {**pending, "phase": "bound", "identity": {"target": "alpha", "native_id": "one"}}
    other = {**bound, "identity": {"target": "beta", "native_id": "one"}}
    assert producer.valid_metadata(bound)
    assert producer.claim_ref(bound) != ref
    assert producer.claim_ref(bound) != producer.claim_ref(other)
    assert relationship.decode_ref(producer.claim_ref(bound))["identity"] == bound["identity"]


@pytest.mark.parametrize("field", ["version", "subject", "retired", "quiescent"])
def test_closed_subject_requires_each_exact_provider_proof_field(field):
    value = metadata()
    ref = producer.claim_ref(value)
    result = {
        "available": True, "exists": True,
        "relationship_verification": {
            "version": 1, "subject": ref, "retired": True, "quiescent": True,
        },
    }
    closed = {**value, "phase": "closed", "closeout": result}
    assert producer.valid_metadata(closed)
    damaged = copy.deepcopy(closed)
    damaged["closeout"]["relationship_verification"].pop(field)
    assert not producer.valid_metadata(damaged)
    assert producer.claim_ref(closed) == ref


def test_absence_boolean_and_disposable_verification_are_never_retirement():
    ref = producer.claim_ref(metadata())
    assert not relationship.verified_retirement({
        "available": True, "exists": False,
        "producer_verification": {"version": 1, "subject": ref, "absent": True},
    }, ref)
