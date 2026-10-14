"""Canonical quiescence version and actionable top-level request errors."""

from __future__ import annotations

import pytest

from agent_worktrees import producer_allocation as allocation
from agent_worktrees import resource_producer as producer, tracking_producer_write as writer


def request() -> dict:
    return {
        "schema": producer.SCHEMA, "version": 1, "operation": "inspect",
        "input": {"token": "a" * 32}, "yaml_path": "/unused/owner.yaml",
        "owner_ref": "m/p/owner",
    }


@pytest.mark.parametrize("field", ["operation", "input", "yaml_path", "owner_ref"])
def test_missing_request_field_reports_name_before_record_io(field: str) -> None:
    args = request()
    args.pop(field)
    result = writer.apply_resource_producer(args)
    assert result["error"] == "rejected"
    assert result["message"] == f"missing required resource-producer field: {field}"


@pytest.mark.parametrize("field", ["yaml_path", "owner_ref"])
@pytest.mark.parametrize("value", [None, "", " ", 1, [], {}])
def test_invalid_path_or_owner_type_is_explicit(field: str, value: object) -> None:
    args = request()
    args[field] = value
    result = writer.apply_resource_producer(args)
    assert result["error"] == "rejected"
    assert result["message"] == f"resource-producer field {field} must be a nonempty string"


@pytest.mark.parametrize("args", [None, [], "request"])
def test_request_must_be_an_object(args: object) -> None:
    result = writer.apply_resource_producer(args)
    assert result["error"] == "rejected"
    assert result["message"] == "resource-producer request must be an object"


def test_quiescence_uses_canonical_version_not_a_duplicate_literal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ref = "raw-resource:subject"
    proof = {
        "available": True,
        "producer_verification": {
            "version": producer.VERSION, "subject": ref, "allocation_finished": True,
        },
    }
    assert allocation.verified_finished(proof, ref)
    monkeypatch.setattr(producer, "VERSION", 2)
    assert not allocation.verified_finished(proof, ref)
    proof["producer_verification"]["version"] = 2
    assert allocation.verified_finished(proof, ref)
    proof["producer_verification"]["version"] = True
    assert not allocation.verified_finished(proof, ref)
