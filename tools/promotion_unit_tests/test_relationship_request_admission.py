"""Original-request admission against actor-free queue and writer boundaries."""

from __future__ import annotations

import copy
import importlib.util
import sys
from dataclasses import asdict, replace
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = "promotion_relationship_request"


@pytest.fixture
def boundary(monkeypatch):
    root = ROOT / "plugins/agent-dispatch/src/agent_dispatch"

    def stub(name, **attributes):
        module = ModuleType(name)
        module.__dict__.update(attributes)
        monkeypatch.setitem(sys.modules, name, module)

    def load(name):
        qualified = f"{PACKAGE}.{name}"
        spec = importlib.util.spec_from_file_location(qualified, root / f"{name}.py")
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, qualified, module)
        spec.loader.exec_module(module)
        return module

    class Refused(RuntimeError):
        pass

    stub(PACKAGE, __path__=[str(root)])
    stub(f"{PACKAGE}.queue_records", TaskError=Refused, Status=SimpleNamespace())
    stub(f"{PACKAGE}.queue_common", Task=object)
    stub(f"{PACKAGE}.client_errors", DispatchError=Refused)
    stub(f"{PACKAGE}.task_obligations", valid_snapshot=lambda *a, **k: False)
    stub(f"{PACKAGE}.procutil", agent_worktrees_environment=lambda: {})
    stub("agent_procutil", __path__=[])
    stub("agent_procutil.owned_process", OwnedCleanupError=Refused)
    calls = []

    def capture(*args, **kwargs):
        raise AssertionError("request unit attempted a native capture")

    sys.modules[f"{PACKAGE}.procutil"].run_background_capture = capture
    workspace_module = load("assignment_workspace")
    relationships = load("queue_task_relationships")
    load("task_relationships")
    owner_module = load("assignment_relationship_owner")
    workspace = workspace_module.AssignmentWorkspace(
        "host", "example", "workspace", "/example/workspace", "original-nonce",
    )
    identity = {"installation_root": "/example/installation", "runtime_generation": "a" * 32}
    assigned = {"assignment_event_id": 7, "worker_id": workspace.worker_id}
    request = {
        "request_id": "original-request", "event_id": 7, "task_id": "b" * 32,
        "generation": 1, "workspace": asdict(workspace), "reservation_key": None,
        "authority": "local", "namespace": "dispatch-local-task", "identity": assigned,
        "recovery": {
            "schema": "dispatch-task-assignment", "version": 2, "authority": "local",
            "queue_identity": "c" * 32, "task_id": "b" * 32, **assigned,
        },
    }

    def run(self, arguments, payload=None):
        assert payload is None and arguments == ["resource-relationship", "capabilities", "--json"]
        return {
            "schema": "copilot-extensions.resource-relationship", "version": 1,
            "operations": ["reserve", "bind", "inspect", "close"], "inspect_absence": True,
            "expected_creation_nonce": True, "expected_writer_identity": True,
            "writer_identity": identity.copy(), "writer_scope": "executing-process",
            "writer_contract": relationships.WRITER, "metadata_version": 2,
        }

    monkeypatch.setattr(owner_module.WorktreeRelationshipWriter, "_run", run)

    def effect(workspace, operation, payload):
        calls.append((workspace, operation, copy.deepcopy(payload)))
        return {
            "schema": relationships.SCHEMA, "version": 1, "ok": True,
            "owner": workspace.owner_ref, "creation_nonce": workspace.creation_nonce,
            "writer_contract": relationships.WRITER, "request_id": payload["request_id"],
            "absent": True,
        }

    admitted = []

    def admit(workspace, selected, original):
        admitted.append((workspace, copy.deepcopy(selected), copy.deepcopy(original)))
        if selected != identity or original != request:
            raise Refused("original request or selected writer changed")
        selected.clear()
        original["recovery"].clear()
        original["workspace"].clear()

    def make(admission=admit):
        writer = owner_module.WorktreeRelationshipWriter(
            ["selected"], admit_workspace=lambda *a: None, admit_request=admission,
        )
        monkeypatch.setattr(writer, "_write", effect)
        return writer

    return SimpleNamespace(
        writer=make(), make=make, request=request, workspace=workspace, identity=identity, calls=calls,
        admitted=admitted, Refused=Refused, module=owner_module, monkeypatch=monkeypatch,
        relationships=relationships,
    )


@pytest.mark.parametrize(
    "change", [None, "queue", "authority", "assignment", "request", "typed-generation", "missing"],
)
def test_original_queue_admission_uses_its_own_exact_request_snapshot(boundary, change):
    b = boundary
    snapshot = {
        "queue_identity": b.request["recovery"]["queue_identity"],
        "assignment": {"event_id": 7, "worker_id": b.workspace.worker_id},
        "relationship": copy.deepcopy(b.request),
    }
    queue = b.relationships.QueueTaskRelationshipsMixin()
    queue._relationship_authority = "local"
    if change == "queue":
        snapshot["queue_identity"] = "d" * 32
    elif change == "authority":
        queue._relationship_authority = "central"
    elif change == "assignment":
        snapshot["assignment"]["worker_id"] = "other/workspace"
    elif change == "request":
        snapshot["relationship"]["reservation_key"] = "replacement"
    elif change == "typed-generation":
        snapshot["relationship"]["generation"] = True
    elif change == "missing":
        snapshot = None
    reads = []

    def read(owner, task_id, *, assignment_event_id):
        reads.append((owner, task_id, assignment_event_id))
        return snapshot

    b.monkeypatch.setattr(sys.modules[f"{PACKAGE}.task_obligations"], "read_snapshot", read, raising=False)
    if change is None:
        assert queue._admit_original_relationship_request(b.request) == snapshot
    else:
        with pytest.raises(b.Refused, match="original queue"):
            queue._admit_original_relationship_request(b.request)
    assert reads == [(queue, b.request["task_id"], 7)]
    assert b.calls == []


def test_bound_snapshot_isolated_from_caller_and_admission_mutation(boundary):
    b = boundary
    caller = copy.deepcopy(b.request)
    writer = b.writer.bind_request(caller)
    caller["recovery"].clear()
    payload = {"request_id": b.request["request_id"], "allow_missing": True}
    for _ in range(2):
        assert writer(b.workspace, "inspect", payload)["absent"] is True
    assert len(b.admitted) == 2 and len(b.calls) == 2
    assert all(value == (b.workspace, b.identity, b.request) for value in b.admitted)
    assert payload == {"request_id": "original-request", "allow_missing": True}


def test_required_admission_rejects_before_any_owner_effect(boundary):
    b = boundary

    def refuse(*args):
        raise b.Refused("original queue/cohort is unqualified")

    writer = b.make(refuse).bind_request(b.request)
    with pytest.raises(b.Refused, match="unqualified"):
        writer(b.workspace, "inspect", {"request_id": "original-request", "allow_missing": True})
    assert b.calls == []


@pytest.mark.parametrize("change", ["authority", "event", "workspace", "queue", "request"])
def test_conflicting_context_cannot_inherit_original_admission(boundary, change):
    b = boundary
    request = copy.deepcopy(b.request)
    if change == "authority":
        request["authority"] = "central"
    elif change == "event":
        request["event_id"] += 1
    elif change == "workspace":
        request["workspace"]["worktree"] = "other"
    elif change == "queue":
        request["recovery"]["queue_identity"] = "d" * 32
    else:
        request["request_id"] = "replacement"
    with pytest.raises(b.Refused):
        writer = b.writer.bind_request(request)
        writer(b.workspace, "inspect", {"request_id": request["request_id"], "allow_missing": True})
    assert b.calls == []


@pytest.mark.parametrize("change", [
    "workspace", "request", "payload", "token", "reserve", "identity", "nonce", "writer",
])
def test_bound_operation_facts_are_checked_before_admission(boundary, change):
    b = boundary
    writer = b.writer.bind_request(b.request)
    workspace = replace(b.workspace, creation_nonce="replacement") if change == "workspace" else b.workspace
    payload = {"request_id": "original-request", "allow_missing": True}
    operation = "inspect"
    if change == "request":
        payload["request_id"] = "replacement"
    elif change == "payload":
        payload["recovery"] = {"queue_identity": "d" * 32}
    elif change == "token":
        operation, payload = "close", {"token": "foreign-token"}
    elif change == "reserve":
        operation, payload = "reserve", {
            "kind": "task", "namespace": "dispatch-local-task", "request_id": "original-request",
            "recovery": {**b.request["recovery"], "queue_identity": "d" * 32},
        }
    elif change == "identity":
        operation, payload = "bind", {"token": "foreign-token", "identity": {}}
    elif change == "nonce":
        payload["expected_creation_nonce"] = "replacement"
    elif change == "writer":
        payload["expected_writer_identity"] = {**b.identity, "runtime_generation": "d" * 32}
    with pytest.raises(b.Refused, match="conflicts"):
        writer(workspace, operation, payload)
    assert b.admitted == [] and b.calls == []


def test_interleaved_bound_requests_do_not_share_current_request_state(boundary):
    b = boundary
    observed = []
    writer = b.make(lambda workspace, identity, request: observed.append(request))
    other = {**b.request, "request_id": "second-request"}
    first, second = writer.bind_request(b.request), writer.bind_request(other)
    for selected, request in ((first, b.request), (second, other), (first, b.request)):
        selected(b.workspace, "inspect", {
            "request_id": request["request_id"], "allow_missing": True,
        })
    assert observed == [b.request, other, b.request]


def test_unbound_and_missing_request_owner_cannot_silently_downgrade(boundary):
    b = boundary
    with pytest.raises(b.Refused, match="bound original"):
        b.writer(b.workspace, "reserve", {})
    legacy = b.make(None)
    with pytest.raises(b.Refused, match="no original request admission"):
        legacy.bind_request(b.request)
    assert b.calls == []
    assert legacy(
        b.workspace, "inspect", {"request_id": "original-request", "allow_missing": True},
    )["absent"] is True
