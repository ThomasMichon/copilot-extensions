"""Original-request admission against actor-free queue and writer boundaries."""

from __future__ import annotations

import copy
import ast
import argparse
import importlib.util
import sys
from contextlib import nullcontext
from dataclasses import asdict, replace
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = "promotion_relationship_request"


def load_configured_admission(monkeypatch):
    qualified = f"{PACKAGE}.assignment_admission"
    spec = importlib.util.spec_from_file_location(
        qualified, ROOT / "plugins/agent-dispatch/src/agent_dispatch/assignment_admission.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, qualified, module)
    spec.loader.exec_module(module)
    return module


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
    diagnostic_spec = importlib.util.spec_from_file_location(
        "agent_procutil.diagnostics",
        ROOT / "libs/agent-procutil/src/agent_procutil/diagnostics.py",
    )
    assert diagnostic_spec and diagnostic_spec.loader
    diagnostics = importlib.util.module_from_spec(diagnostic_spec)
    monkeypatch.setitem(sys.modules, diagnostic_spec.name, diagnostics)
    diagnostic_spec.loader.exec_module(diagnostics)
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


@pytest.mark.parametrize("carrier", ["structured", "stderr"])
def test_selected_writer_refusal_filters_known_credential_before_its_text_cap(
    boundary, monkeypatch: pytest.MonkeyPatch, carrier: str,
) -> None:
    secret = "sensitive-boundary-value-" + "x" * 600
    monkeypatch.setenv("UNIT_API_TOKEN", secret)
    text = "selected owner refused " + "p" * 440 + " " + secret
    assert text.index(secret) < 512 < text.index(secret) + len(secret)
    detail = boundary.module._refusal_detail(
        ["resource-relationship", "reserve"],
        {"error": text} if carrier == "structured" else None,
        text if carrier == "stderr" else None,
    )
    assert detail.startswith(" (reserve: selected owner refused")
    assert "[REDACTED]" in detail
    assert "sensitive-boundary-value-" not in detail
    assert len(detail) <= len(" (reserve: )") + 512


@pytest.mark.parametrize("selection", [
    (None, None), ("", "local"), (None, "local"), ("policy:factory", None),
    ("policy:factory", "legacy"), ("policy..module:factory", "local"),
])
def test_configured_admission_never_imports_absent_or_invalid_selection(boundary, selection):
    b = boundary
    module = load_configured_admission(b.monkeypatch)

    def forbidden(*args):
        raise AssertionError("selection cannot import an admission owner")

    b.monkeypatch.setattr(module.importlib, "import_module", forbidden)
    if selection == (None, None):
        assert module.configured_writer(*selection) is None
    else:
        with pytest.raises(ValueError):
            module.configured_writer(*selection)


def test_configured_admission_carries_both_actual_independent_owners(boundary):
    b = boundary
    module = load_configured_admission(b.monkeypatch)
    policy = ModuleType("promotion_policy")
    admission = module.RelationshipAdmission(
        b.writer.admit_workspace, b.writer._admit_request,
    )
    policy.factory = lambda: admission
    b.monkeypatch.setitem(sys.modules, policy.__name__, policy)
    selected = []

    def retain(**kwargs):
        selected.append(kwargs)
        return b.make(kwargs["admit_request"])

    b.monkeypatch.setattr(b.module.WorktreeRelationshipWriter, "selected", retain)
    writer = module.configured_writer("promotion_policy:factory", "local")
    bound = writer.bind_request(b.request)
    assert bound(b.workspace, "inspect", {"request_id": "original-request", "allow_missing": True})["absent"]
    assert len(b.admitted) == 1
    assert selected == [{
        "admit_workspace": admission.admit_workspace, "admit_request": admission.admit_request,
    }]
    policy.factory = lambda: True
    with pytest.raises(ValueError, match="independent admission owners"):
        module.configured_writer("promotion_policy:factory", "local")
    assert len(selected) == 1


def test_actual_serve_command_preserves_admission_selection(boundary):
    b = boundary
    path = ROOT / "plugins/agent-dispatch/src/agent_dispatch/coordinator_cli.py"
    command = next(
        node for node in ast.parse(path.read_text(encoding="utf-8")).body
        if isinstance(node, ast.FunctionDef) and node.name == "_cmd_serve"
    )
    command.decorator_list = []
    configured = SimpleNamespace(
        token=None, port=9847, db_path="/queue", sweep_interval=0,
        orphan_grace=17.0, handoff_fallback_enabled=True, handoff_fallback_grace=29.0,
        pool_publication_enabled=False, relationship_admission="policy:factory",
        relationship_authority="local",
    )
    calls = []
    for name, attributes in {
        "logging_setup": {"configure_file_logging": lambda *args: None},
        "server": {
            "CoordinatorAlreadyLiveError": RuntimeError,
            "serve": lambda cfg, **kwargs: calls.append((cfg, kwargs)),
        },
    }.items():
        stub = ModuleType(f"{PACKAGE}.{name}")
        stub.__dict__.update(attributes)
        b.monkeypatch.setitem(sys.modules, stub.__name__, stub)
    scope = {
        "argparse": argparse,
        "__name__": f"{PACKAGE}.coordinator_cli", "__package__": PACKAGE,
        "_config": SimpleNamespace(
            load_config=lambda: configured, resolve_control_token=lambda: None,
        ),
        "_config_class": lambda: SimpleNamespace,
        "_resolve_serve_host": lambda *args: "127.0.0.1",
        "_reroot_serve_cwd": lambda: None,
    }
    exec(compile(ast.Module(body=[command], type_ignores=[]), str(path), "exec"), scope)
    args = SimpleNamespace(token=None, port=None, db=None, passive=True, force=False)
    assert scope["_cmd_serve"](args) == 0
    cfg, flags = calls[0]
    assert cfg.relationship_admission == configured.relationship_admission
    assert cfg.relationship_authority == configured.relationship_authority
    assert cfg.orphan_grace == configured.orphan_grace
    assert cfg.handoff_fallback_enabled is True
    assert cfg.handoff_fallback_grace == configured.handoff_fallback_grace
    assert flags == {"passive": True, "force": False}


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
    queue._relationship_writer = b.writer
    queue.get = lambda ref: SimpleNamespace(kind="task") if ref == b.request["task_id"] else None
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


@pytest.mark.parametrize("change", [
    None, "missing", "task_id", "worktree", "allocation_project", "worktree_path",
    "execution_machine", "creation_nonce", "task_relationship_event_id",
    "task_relationship_generation", "task_relationship_required",
])
def test_original_preparation_is_requeried_without_recapturing_another_reservation(boundary, change):
    b = boundary
    request = {**b.request, "reservation_key": "original-reservation"}
    snapshot = {
        "queue_identity": request["recovery"]["queue_identity"],
        "assignment": {"event_id": 7, "worker_id": b.workspace.worker_id},
        "relationship": copy.deepcopy(request),
    }
    prepared = {
        "task_id": request["task_id"], "worktree": b.workspace.worktree,
        "allocation_project": b.workspace.project, "worktree_path": b.workspace.path,
        "execution_machine": b.workspace.machine.upper(),
        "creation_nonce": b.workspace.creation_nonce, "task_relationship_required": 1,
        "task_relationship_event_id": 7, "task_relationship_generation": 1,
    }
    if change == "missing":
        prepared = None
    elif change is not None:
        prepared[change] = 999 if type(prepared[change]) is int else "replacement"
    reads = []

    def execute(sql, parameters):
        assert "FROM spawn_reservations WHERE key=?" in sql
        reads.append(parameters)
        return SimpleNamespace(fetchone=lambda: prepared)

    queue = b.relationships.QueueTaskRelationshipsMixin()
    queue._relationship_authority = "local"
    queue._relationship_writer = b.writer
    queue.get = lambda ref: SimpleNamespace(kind="task") if ref == b.request["task_id"] else None
    queue._connect = lambda: nullcontext(SimpleNamespace(execute=execute))
    b.monkeypatch.setattr(
        sys.modules[f"{PACKAGE}.task_obligations"], "read_snapshot",
        lambda *args, **kwargs: snapshot, raising=False,
    )
    if change is None:
        assert queue._admit_original_relationship_request(request) == snapshot
    else:
        with pytest.raises(b.Refused, match="original prepared workspace"):
            queue._admit_original_relationship_request(request)
    assert reads == [("original-reservation",)] and b.calls == []


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


@pytest.mark.parametrize("change", [
    None, "contract", "nonce", "identity", "foreign-installation", "resident", "version",
])
def test_actual_resident_observation_precedes_each_required_relationship(boundary, change):
    b = boundary
    observations = []
    generation = ["first-resident"]
    support = b.writer._run(["resource-relationship", "capabilities", "--json"])

    def run(self, arguments, payload=None):
        observations.append((arguments, copy.deepcopy(payload)))
        if "capabilities" in arguments:
            return {**support, "resident_inspect": 1}
        result = {
            "schema": "copilot-extensions.resource-relationship", "version": 1,
            "ok": True, "owner": b.workspace.owner_ref,
            "creation_nonce": b.workspace.creation_nonce, "writer_contract": b.relationships.WRITER,
            "writer_identity": {**b.identity, "runtime_generation": "d" * 32},
            "request_id": b.request["request_id"], "absent": True,
            "resident": {"generation": generation[0], "runtime_generation": "resident-build"},
        }
        if "--resident-only" in arguments:
            assert payload == {
                "request_id": b.request["request_id"], "allow_missing": True,
                "expected_creation_nonce": b.workspace.creation_nonce,
            }
            if change == "contract":
                result["writer_contract"] = {}
            elif change == "nonce":
                result["creation_nonce"] = "replacement"
            elif change == "identity":
                result["writer_identity"]["runtime_generation"] = None
            elif change == "foreign-installation":
                result["writer_identity"]["installation_root"] = "/another/installation"
            elif change == "resident":
                result.pop("resident")
            elif change == "version":
                result["version"] = True
        elif "record" in arguments:
            result = {"ok": True, "worktree": {
                "id": b.workspace.worktree, "repo": b.workspace.project, "path": b.workspace.path,
                "creation_nonce": b.workspace.creation_nonce,
            }}
        else:
            result["writer_identity"] = b.identity.copy()
        return result

    b.monkeypatch.setattr(b.module.WorktreeRelationshipWriter, "_run", run)
    writer = b.module.WorktreeRelationshipWriter(
        ["retained-build"], admit_workspace=lambda *a: None, require_resident=True,
    )
    if change is not None:
        with pytest.raises(b.Refused, match="resident is unconfirmed"):
            writer(b.workspace, "inspect", {"request_id": b.request["request_id"]})
        assert len(observations) == 2 and "--resident-only" in observations[-1][0]
    else:
        for selected in ("first-resident", "requalified-resident"):
            generation[0] = selected
            assert writer(b.workspace, "inspect", {"request_id": b.request["request_id"]})["ok"]
        assert sum("--resident-only" in args for args, _ in observations) == 2
        assert len(observations) == 7


def test_selected_factory_retains_real_selection_and_independent_admission(boundary):
    b = boundary
    selected = []
    helper = ModuleType(f"{PACKAGE}.worktree_owner_launch")

    def retain():
        selected.append("selected")
        return ("retained-command", "original-build")

    helper.retained_selection = retain
    b.monkeypatch.setitem(sys.modules, helper.__name__, helper)
    support = b.writer._run(["resource-relationship", "capabilities", "--json"])
    b.monkeypatch.setattr(
        b.module.WorktreeRelationshipWriter, "_run",
        lambda *a, **k: {**support, "resident_inspect": 1},
    )

    def unqualified(*args):
        raise b.Refused("other participating owner is unqualified")

    writer = b.module.WorktreeRelationshipWriter.selected(
        admit_workspace=unqualified, admit_request=unqualified,
    )
    assert writer.prefix == ("retained-command", "original-build") and selected == ["selected"]
    with pytest.raises(b.Refused, match="other participating owner"):
        writer.bind_request(b.request)(b.workspace, "inspect", {
            "request_id": b.request["request_id"], "allow_missing": True,
        })
    with pytest.raises(b.Refused, match="bound original request"):
        writer(b.workspace, "inspect", {"request_id": b.request["request_id"]})
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
