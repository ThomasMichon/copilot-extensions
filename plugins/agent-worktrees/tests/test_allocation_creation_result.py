"""Ordinary results come from the original receiver, not a planned child path."""

from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest

from agent_worktrees import allocation_creation_result as results, creation_requests
from agent_worktrees import owned_worktree as allocation, tracking
from agent_worktrees import owned_worktree_remote as remote
from agent_worktrees import worktree_allocation_receiver as receiver_api
from test_receiving_allocation import create, receiver as receiver


def payload(admission):
    return {
        "schema": allocation.SCHEMA, "version": allocation.VERSION,
        **admission.to_fields(), "identity": None,
    }


def metadata(admission):
    return {
        "version": allocation.METADATA_VERSION, "lifetime": allocation.LIFETIME,
        "namespace": allocation.NAMESPACE, "creator": admission.origin,
        "request_id": admission.request_id, "token": admission.token,
        "phase": "pending", "recovery": admission.target,
    }


def test_result_is_actual_original_sessionless_journal_and_never_acknowledges(receiver, monkeypatch):
    config, admission, records = receiver
    first, record = create(receiver)
    original = creation_requests.inspect(config, admission.key)
    before = record.yaml_path.read_bytes()
    monkeypatch.setattr(
        receiver_api.worktree_creation, "_create_worktree_core",
        lambda *a, **k: pytest.fail("result inspection created a replacement"),
    )
    value = receiver_api.receive("result", payload(admission), config)
    actual = results.validate(value, metadata(admission))
    assert actual["worktree"] == first["worktree"] == original["worktree"]["worktree"]
    assert actual["creation"]["replayed"] is True and "launch" not in actual
    assert value["allocation"]["status"] == "allocated"
    assert record.yaml_path.read_bytes() == before
    assert len(list(records.glob("*.yaml"))) == 1
    assert tracking.load_record(record.yaml_path).sessions == []


def test_absent_original_result_remains_unknown_without_creating_history(receiver):
    config, admission, records = receiver
    value = receiver_api.receive("result", payload(admission), config)
    assert value["result"] is None
    assert value["allocation"]["status"] == "unknown"
    assert results.validate(value, metadata(admission)) is None
    assert creation_requests.inspect(config, admission.key) is None
    assert not list(records.glob("*.yaml"))


@pytest.mark.parametrize("field,value", [
    ("id", "replacement"), ("repo", "other"), ("path", "/other/child"),
    ("branch", "worktree/other"), ("creation_nonce", "changed"),
    ("execution_machine", "other"), ("owner_ref", "remote/parent/other"),
])
def test_consumer_rejects_an_ordinary_row_from_another_incarnation(receiver, field, value):
    config, admission, _ = receiver
    create(receiver)
    result = receiver_api.receive("result", payload(admission), config)
    result["result"]["worktree"][field] = value
    with pytest.raises(ValueError, match="different receiving incarnation"):
        results.validate(result, metadata(admission))


def test_result_wire_bound_is_separate_from_the_unchanged_lifecycle_proof(receiver):
    config, admission, _ = receiver
    create(receiver)
    value = receiver_api.receive("result", payload(admission), config)
    value["result"]["worktree"]["title"] = "x" * allocation.producer.MAX_INPUT_BYTES
    decoded = results.validate(json.loads(results.wire_json(value)), metadata(admission))
    assert decoded == value["result"]
    assert len(results.wire_json(value)) > allocation.producer.MAX_INPUT_BYTES
    assert len(allocation.wire_json(value["allocation"])) <= allocation.producer.MAX_INPUT_BYTES
    with pytest.raises(ValueError, match="size limit"):
        allocation.wire_json(value)
    oversized = copy.deepcopy(value)
    oversized["result"]["worktree"]["title"] = "x" * results.MAX_RESPONSE_BYTES
    with pytest.raises(ValueError, match="negotiated response bound"):
        results.wire_json(oversized)
    worktree = value["result"]["worktree"]
    worktree["title"] = ""
    base_size = len(json.dumps(worktree, sort_keys=True, separators=(",", ":"), ensure_ascii=True))
    worktree["title"] = "x" * (results.MAX_ROW_BYTES - base_size)
    results.wire_json(value)
    worktree["title"] += "x"
    with pytest.raises(ValueError, match="ordinary row bound"):
        results.wire_json(value)


def test_result_rejects_unmatched_receiving_request_and_extra_executable_fields(receiver):
    config, admission, _ = receiver
    create(receiver)
    value = receiver_api.receive("result", payload(admission), config)
    value["allocation"]["request_id"] = "other"
    with pytest.raises(ValueError, match="original request"):
        results.validate(value, metadata(admission))
    value = receiver_api.receive("result", payload(admission), config)
    value["result"]["launch"] = {"cmd": "not-a-launch-interface"}
    with pytest.raises(ValueError, match="sessionless"):
        results.validate(value, metadata(admission))


@pytest.mark.parametrize("conclusion", ["cancelled", "finalized", "archived"])
def test_concluded_operation_never_returns_a_reusable_ordinary_creation(receiver, conclusion):
    config, admission, _ = receiver
    if conclusion == "cancelled":
        receiver_api.receive("cancel", payload(admission), config)
    else:
        _, record = create(receiver)
        record.status = conclusion
        tracking.save_record(record, record.yaml_path)
    value = receiver_api.receive("result", payload(admission), config)
    assert value["allocation"]["status"] == conclusion and value["result"] is None
    assert results.validate(value, metadata(admission)) is None


@pytest.mark.parametrize("creation", [
    None, [], {}, {"version": True, "status": "complete", "replayed": True},
    {"version": 1, "status": "complete", "replayed": 1},
])
def test_invalid_ordinary_completion_marker_is_not_a_usable_result(receiver, creation):
    config, admission, _ = receiver
    create(receiver)
    value = receiver_api.receive("result", payload(admission), config)
    value["result"]["creation"] = creation
    with pytest.raises(ValueError, match="sessionless"):
        results.validate(value, metadata(admission))


def test_original_journal_record_evidence_is_required_before_returning_a_result(receiver):
    config, admission, _ = receiver
    _, record = create(receiver)
    row = creation_requests.inspect(config, admission.key)
    row["worktree"].pop("record_identity")
    proof = receiver_api.receive("inspect", payload(admission), config)
    with pytest.raises(ValueError, match="record identity"):
        results.response(proof, row, record)


@pytest.mark.parametrize("status", ["pending", "unknown"])
@pytest.mark.parametrize("identity_mode", ["absent", "new", "bound", "bound-absent"])
def test_transport_preserves_valid_unfinished_status_without_returning_a_worktree(
    receiver, monkeypatch, status, identity_mode,
):
    config, admission, _ = receiver
    _, record = create(receiver)
    original = metadata(admission)
    value = receiver_api.receive("result", payload(admission), config)
    identity = value["allocation"]["identity"]
    value["allocation"].update(status=status, allocation_finished=False)
    value["allocation"]["identity"] = None if identity_mode in {"absent", "bound-absent"} else identity
    value["result"] = None
    if identity_mode.startswith("bound"):
        original.update(phase="bound", identity=identity)
    monkeypatch.setattr(remote, "_destination", lambda *a: ("receiving-alias", "named result"))
    monkeypatch.setattr(remote, "run_caller_owned", lambda *a, **k: SimpleNamespace(
        returncode=0, stdout=json.dumps(value), stderr="",
    ))
    before = record.yaml_path.read_bytes()
    received = remote.request("result", original)
    assert received == value and results.validate(received, original) is None
    assert received["allocation"]["status"] == status
    assert record.yaml_path.read_bytes() == before


@pytest.mark.parametrize("change", [
    {"schema": "other"}, {"version": True}, {"origin": "remote/parent/other"},
    {"request_id": "other"}, {"token": "b" * 32}, {"allocation_finished": True},
    {"target": {"machine": "other", "project": "demo-repo"}}, {"extra": True},
    {"identity": {"ref": "other/demo-repo/child", "creation_nonce": "a", "path": "/child", "branch": "worktree/child"}},
])
@pytest.mark.parametrize("status", ["pending", "unknown"])
def test_unfinished_transport_rejects_mismatched_or_malformed_original_proof(
    receiver, monkeypatch, status, change,
):
    config, admission, _ = receiver
    create(receiver)
    value = receiver_api.receive("result", payload(admission), config)
    value["allocation"].update(status=status, allocation_finished=False)
    value["allocation"].update(change)
    value["result"] = None
    monkeypatch.setattr(remote, "_destination", lambda *a: ("receiving-alias", "named result"))
    monkeypatch.setattr(remote, "run_caller_owned", lambda *a, **k: SimpleNamespace(
        returncode=0, stdout=json.dumps(value), stderr="",
    ))
    with pytest.raises(ValueError, match="unfinished"):
        remote.request("result", metadata(admission))


@pytest.mark.parametrize("status", ["pending", "unknown"])
def test_unfinished_proof_cannot_carry_an_ordinary_result(receiver, status):
    config, admission, _ = receiver
    create(receiver)
    value = receiver_api.receive("result", payload(admission), config)
    value["allocation"].update(status=status, allocation_finished=False)
    with pytest.raises(ValueError, match="unfinished"):
        results.validate(value, metadata(admission))


@pytest.mark.parametrize("status", ["pending", "unknown"])
def test_unfinished_provided_identity_cannot_replace_the_already_bound_incarnation(receiver, status):
    config, admission, _ = receiver
    create(receiver)
    value = receiver_api.receive("result", payload(admission), config)
    original = {
        **metadata(admission), "phase": "bound",
        "identity": copy.deepcopy(value["allocation"]["identity"]),
    }
    value["allocation"].update(status=status, allocation_finished=False)
    value["allocation"]["identity"]["creation_nonce"] = "different"
    value["result"] = None
    with pytest.raises(ValueError, match="unfinished"):
        results.validate(value, original)
