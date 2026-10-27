"""Pure custody/proof decisions; transport, ledger and reference parsing are boundaries."""

from __future__ import annotations

import copy
import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "plugins/agent-worktrees/src/agent_worktrees"


@pytest.fixture
def api(monkeypatch):
    name = "promotion_owned_worktree"
    package = ModuleType(name)
    package.__path__ = []
    monkeypatch.setitem(sys.modules, name, package)

    def stub(child, **fields):
        module = ModuleType(f"{name}.{child}")
        module.__dict__.update(fields)
        monkeypatch.setitem(sys.modules, module.__name__, module)
        setattr(package, child, module)
        return module

    def reference(value):
        # Grammar is the existing tracking owner's contract. These units give
        # allocation decisions already-parsed exact worktree references.
        if value not in {"origin/project/parent", "target/repo/child", "target/repo/other"}:
            return None
        machine, project, worktree_id = value.split("/")
        return SimpleNamespace(
            machine=machine, project=project, worktree_id=worktree_id,
            is_qualified=True, session=None, is_anchor=False, canonical=lambda: value,
        )

    stub("tracking", parse_claim_ref=reference)
    stub("claim_providers")
    stub("producer_allocation")
    for child in ("activity", "claim_history", "owned_worktree_remote"):
        stub(child)
    stub("tracking_write", register_verb=lambda *args: None)
    stub("execution_spaces", ExecutionSpaceError=type("ExecutionSpaceError", (ValueError,), {}))
    stub("tracking_producer_write", _owned_record=None, _require_mutable_owner=None)

    def load(child):
        spec = importlib.util.spec_from_file_location(f"{name}.{child}", SOURCE / f"{child}.py")
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, spec.name, module)
        setattr(package, child, module)
        spec.loader.exec_module(module)
        return module

    producer = load("resource_producer")
    load("resource_relationship")
    allocation = load("owned_worktree")
    writer = load("tracking_allocation_write")
    return SimpleNamespace(producer=producer, allocation=allocation, writer=writer)


def metadata():
    return {
        "version": 3, "lifetime": "owned-worktree", "namespace": "worktree-allocation",
        "creator": "origin/project/parent", "request_id": "original", "token": "a" * 32,
        "phase": "pending", "recovery": {"machine": "target", "project": "repo"},
    }


def identity():
    return {
        "ref": "target/repo/child", "creation_nonce": "original-nonce",
        "path": "/synthetic/child", "branch": "worktree/child",
    }


def observation(*, status="allocated", finished=True):
    value = metadata()
    return {
        "schema": "copilot-extensions.worktree-allocation", "version": 1,
        "origin": value["creator"], "request_id": value["request_id"],
        "token": value["token"], "target": value["recovery"],
        "identity": identity(), "status": status, "allocation_finished": finished,
    }


def test_pending_and_bound_use_distinct_existing_ledger_subjects(api):
    pending = metadata()
    assert api.allocation.valid_metadata(pending)
    assert api.producer.valid_metadata(pending)
    ref = api.producer.claim_ref(pending)
    assert ref.startswith("worktree-allocation:w1.")
    bound = {**pending, "phase": "bound", "identity": identity()}
    assert api.producer.valid_metadata(bound)
    assert api.producer.claim_ref(bound) == "target/repo/child"
    assert metadata() == pending


@pytest.mark.parametrize("change", [
    {"version": True}, {"version": 4}, {"phase": "confirmed"},
    {"creator": "unqualified"}, {"token": "short"},
    {"identity": identity()}, {"closeout": {}}, {"unknown": True},
])
def test_malformed_pending_metadata_never_becomes_settled(api, change):
    assert api.allocation.valid_metadata({**metadata(), **copy.deepcopy(change)}) is False


@pytest.mark.parametrize("status", ["unknown", "pending", "absent", "retired"])
def test_absence_or_incomplete_receiver_never_binds_or_closes(api, status):
    proof = observation(status=status, finished=False)
    pending = metadata()
    assert not api.allocation.verified_observation(proof, pending)
    assert not api.allocation.verified_conclusion(proof, pending)
    claim = SimpleNamespace(producer=pending, ref=api.allocation.claim_ref(pending), state="active")
    before = copy.deepcopy(vars(claim))
    with pytest.raises(ValueError, match="exact original"):
        api.writer._transition(claim, "bind", proof)
    assert vars(claim) == before


def test_durable_child_can_bind_before_optional_origin_acknowledgement(api):
    claim = SimpleNamespace(producer=metadata(), ref="", state="active")
    api.writer._transition(claim, "bind", observation())
    assert claim.producer["phase"] == "bound"
    assert claim.ref == identity()["ref"]
    assert api.allocation.valid_metadata(claim.producer)
    assert claim.state == "active"
    with pytest.raises(ValueError, match="acknowledge"):
        api.writer._transition(claim, "confirm", observation())
    api.writer._transition(claim, "confirm", observation(status="confirmed"))
    assert claim.producer["phase"] == "confirmed"


@pytest.mark.parametrize("status", ["finalized", "archived"])
def test_only_exact_original_receiving_lifecycle_closes_bound_responsibility(api, status):
    bound = {**metadata(), "phase": "bound", "identity": identity()}
    claim = SimpleNamespace(producer=bound, ref=identity()["ref"], state="active")
    proof = observation(status=status)
    api.writer._transition(claim, "close", proof)
    assert claim.state == "released" and claim.producer["phase"] == "closed"
    assert api.allocation.valid_metadata(claim.producer)
    altered = copy.deepcopy(claim.producer)
    altered["closeout"]["identity"]["creation_nonce"] = "replacement"
    assert not api.allocation.valid_metadata(altered)


@pytest.mark.parametrize("field,value", [
    ("origin", "target/repo/other"), ("request_id", "other"), ("token", "b" * 32),
    ("version", True), ("allocation_finished", False),
    ("identity", {**identity(), "creation_nonce": "replacement"}),
])
def test_changed_proof_cannot_mutate_original_bound_claim(api, field, value):
    bound = {**metadata(), "phase": "bound", "identity": identity()}
    proof = {**observation(status="finalized"), field: copy.deepcopy(value)}
    claim = SimpleNamespace(producer=bound, ref=identity()["ref"], state="active")
    before = copy.deepcopy(vars(claim))
    with pytest.raises(ValueError):
        api.writer._transition(claim, "close", proof)
    assert vars(claim) == before


def test_pending_admission_reserves_capacity_for_duplicated_lifecycle_proof(api):
    value = metadata()
    value["recovery"]["options"] = {"name": "x" * 8000}
    assert api.allocation.valid_target(value["recovery"])
    assert len(api.producer.canonical_json(value)) < api.producer.MAX_INPUT_BYTES
    with pytest.raises(ValueError, match="lifecycle"):
        api.allocation.require_lifecycle_capacity(
            value["creator"], value["request_id"], value["token"], value["recovery"],
        )
    assert not api.allocation.valid_metadata(value)


def test_identity_limit_counts_canonical_escaped_bytes_not_path_characters(api):
    value = identity()
    value["path"] = "/" + "\U0010ffff" * 340
    assert len(value["path"]) < 4096
    with pytest.raises(ValueError, match="identity exceeds lifecycle"):
        api.allocation.AllocationIdentity.from_dict(value)
    value["path"] = "/"
    value["path"] += "x" * (4096 - len(api.producer.canonical_json(value)))
    assert len(api.producer.canonical_json(value)) == 4096
    assert api.allocation.AllocationIdentity.from_dict(value).to_dict() == value


def test_exact_wire_capacity_includes_transport_newline(api):
    value = {"value": ""}
    value["value"] = "x" * (api.producer.MAX_INPUT_BYTES - len(api.allocation.wire_json(value)))
    assert len(api.allocation.wire_json(value)) == api.producer.MAX_INPUT_BYTES
    value["value"] += "x"
    with pytest.raises(ValueError, match="wire response"):
        api.allocation.wire_json(value)


def test_reconcile_recovers_each_original_phase_without_allocating(api):
    claim = SimpleNamespace(producer=metadata(), ref="", state="active")
    assert api.writer._reconcile(claim, observation())
    assert claim.producer["phase"] == "bound" and claim.ref == identity()["ref"]
    assert api.writer._reconcile(claim, observation(status="confirmed"))
    assert claim.producer["phase"] == "confirmed"
    assert api.writer._reconcile(claim, observation(status="archived"))
    assert claim.producer["phase"] == "closed" and claim.state == "released"
    assert api.allocation.valid_metadata(claim.producer)
    cancelled = SimpleNamespace(producer=metadata(), ref="", state="active")
    assert api.writer._reconcile(cancelled, {**observation(status="cancelled"), "identity": None})
    assert cancelled.producer["phase"] == "closed"
    assert api.allocation.valid_metadata(cancelled.producer)


def test_reconcile_preserves_active_claim_until_exact_conclusion(api):
    value = {**metadata(), "phase": "confirmed", "identity": identity()}
    claim = SimpleNamespace(producer=value, ref=identity()["ref"], state="active")
    before = copy.deepcopy(vars(claim))
    assert api.writer._reconcile(claim, observation()) is False
    assert vars(claim) == before


def test_reconcile_rejects_uncertain_or_changed_proof_without_mutation(api):
    for changed in (
        {"status": "unknown", "allocation_finished": False},
        {"request_id": "replacement"},
        {"identity": {**identity(), "creation_nonce": "replacement"}},
    ):
        value = {**metadata(), "phase": "bound", "identity": identity()}
        claim = SimpleNamespace(producer=value, ref=identity()["ref"], state="active")
        before = copy.deepcopy(vars(claim))
        with pytest.raises(ValueError, match="unresolved"):
            api.writer._reconcile(claim, {**observation(), **changed})
        assert vars(claim) == before
