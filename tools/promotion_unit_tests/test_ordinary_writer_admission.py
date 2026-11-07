"""Actor-free actual-observation admission; no installed-cohort assertion."""

from __future__ import annotations

import copy
import argparse
import importlib.util
import io
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from contextlib import redirect_stdout

import pytest

TaskError = RuntimeError


@pytest.fixture
def admission(monkeypatch):
    root = Path(__file__).resolve().parents[2] / "plugins/agent-dispatch/src/agent_dispatch"
    package = "promotion_ordinary_writer"
    stub = ModuleType(package)
    stub.__path__ = [str(root)]
    monkeypatch.setitem(sys.modules, package, stub)
    for name, attributes in {
        "queue_records": {"TaskError": TaskError, "Status": SimpleNamespace()},
        "queue_common": {"Task": object},
    }.items():
        stub = ModuleType(f"{package}.{name}")
        stub.__dict__.update(attributes)
        monkeypatch.setitem(sys.modules, stub.__name__, stub)
    modules = {}
    for name in (
        "assignment_workspace", "queue_task_relationships", "worktree_attribution",
        "ordinary_agent_facts", "ordinary_writer_admission",
    ):
        qualified = f"{package}.{name}"
        spec = importlib.util.spec_from_file_location(qualified, root / f"{name}.py")
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, qualified, module)
        spec.loader.exec_module(module)
        modules[name] = module
    requirement = {
        "version": 1, "creation_nonce": "original",
        "identity": {"installation_root": "/worktrees", "runtime_generation": "a" * 32},
    }
    owner = modules["ordinary_writer_admission"].OrdinaryWriterAdmission(requirement, {
        "worktree": "tree", "allocation_project": "project", "worktree_path": "/workspace",
    })
    health = {
        "service": "agent-bridge", "status": "ok", "ready": True, "draining": False,
        "slot": {"role": "active", "pid": 42, "active": {"pid": 42, "generation": 7}},
        "capabilities": {
            "worktree_record_delegation": 1, "retained_workspace_creation_writer": 1,
            "retained_workspace_creation_writer_inspection": 1,
        },
        "worktree_record_writer": copy.deepcopy(requirement["identity"]),
    }
    support = {
        "schema": "agent-bridge.cli-contracts", "version": 1,
        "contracts": {
            "agent-bridge.create": {"version": 1, "capabilities": ["keyed-worktree-writer"]},
            "agent-bridge.status": {
                "version": 1, "capabilities": ["health-json", "exact-session-worktree-writer"],
            },
            "agent-bridge.resume": {"version": 1, "capabilities": ["keyed-worktree-writer"]},
        },
    }
    info = {
        "session_id": "bridge-session", "acp_session_id": "native", "durable_session_id": "native",
        "project": "project", "target_dir": "/workspace", "worktree_id": "tree",
        "fact_kind": "resumed", "status": "idle", "read_only": False,
        "at_rest": True, "liveness": None,
    }
    observation = {
        "schema": "copilot-extensions.creation-worktree-writer", "version": 1,
        "session_id": "bridge-session", "native_session_id": "native",
        "project": "project", "worktree_path": "/workspace", "worktree_id": "tree",
        "worktree_writer": copy.deepcopy(requirement), "writer_scope": "executing-process",
        "writer_contract": dict(modules["queue_task_relationships"].WRITER),
        "retained_session_writes": 1,
        "bridge_instance": {"installation_root": "/bridge", "pid": 42, "generation": 7},
    }
    calls = []

    def run(argv, **kwargs):
        assert kwargs == {"read_only": True}
        calls.append(argv)
        value = support if argv[1:] == ["version", "--contracts-json"] else health if len(argv) == 2 else {
            **info, "worktree_writer_observation": observation,
        }
        return subprocess.CompletedProcess(argv, 0, json.dumps(value), "")

    return owner, health, support, info, observation, calls, run


def test_future_admission_needs_no_impossible_prior_journal(admission):
    owner, _, _, _, _, calls, run = admission
    owner.admit_create(run)
    assert calls == [["--json", "version", "--contracts-json"], ["--json", "status"]]


def test_selected_writer_is_observed_before_workspace_identity_exists(admission):
    owner, health, _, _, _, calls, run = admission
    selected = type(owner).selected_writer(run)
    assert selected == owner.requirement["identity"]
    health["worktree_record_writer"]["runtime_generation"] = "b" * 32
    assert selected == owner.requirement["identity"]
    assert calls == [["--json", "version", "--contracts-json"], ["--json", "status"]]


@pytest.mark.parametrize("identity", [
    None, {}, {"installation_root": "/writer", "runtime_generation": True},
    {"installation_root": "", "runtime_generation": "a" * 32},
], ids=["missing", "empty", "generation", "root"])
def test_selected_writer_refuses_malformed_identity_before_preparation(admission, identity):
    owner, health, _, _, _, _, run = admission
    health["worktree_record_writer"] = identity
    with pytest.raises(TaskError, match="selected writer identity"):
        type(owner).selected_writer(run)


def test_prepared_requirement_preserves_original_expectation_without_agent_or_journal(admission):
    owner, *_ = admission
    module = sys.modules[type(owner).__module__]
    row = {
        "worktree_writer_requirement": None, "task_relationship_required": True,
        "task_relationship_event_id": None, "driver": "ordinary-bridge",
        "creation_key": "original-key", "agent_info": None, "session_handle": None,
        "creation_nonce": None,
    }
    facts = dict(worktree="tree", project="project", path="/workspace", creation_nonce="original")
    retained = module.prepared_requirement(row, **facts, value=owner.requirement)
    row.update(worktree_writer_requirement=retained, task_relationship_event_id=7,
               creation_nonce="original")
    assert module.prepared_requirement(row, **facts, value=None) == retained
    assert json.loads(retained) == owner.requirement
    with pytest.raises(TaskError, match="cannot change"):
        module.prepared_requirement(
            row, **facts, value={**owner.requirement, "identity": {
                "installation_root": "/different", "runtime_generation": "b" * 32,
            }},
        )


@pytest.mark.parametrize("change", ["legacy", "nonce", "historical", "native"])
def test_prepared_requirement_refuses_unproven_original_attachment(admission, change):
    owner, *_ = admission
    module = sys.modules[type(owner).__module__]
    row = {
        "worktree_writer_requirement": None, "task_relationship_required": change != "legacy",
        "task_relationship_event_id": 7 if change == "historical" else None,
        "driver": "ordinary-bridge", "creation_key": "original-key",
        "agent_info": None, "session_handle": "native" if change == "native" else None,
        "creation_nonce": None,
    }
    with pytest.raises(TaskError, match="writer requirement|workspace incarnation"):
        module.prepared_requirement(
            row, worktree="tree", project="project", path="/workspace",
            creation_nonce="changed" if change == "nonce" else "original", value=owner.requirement,
        )


def test_discovery_argv_executes_the_shipped_bridge_parser_and_contract(admission, monkeypatch):
    root = Path(__file__).resolve().parents[2] / "plugins/agent-bridge/src/agent_bridge"
    package = "promotion_ordinary_bridge_cli"

    def stub(name, **attributes):
        value = ModuleType(name)
        value.__dict__.update(attributes)
        monkeypatch.setitem(sys.modules, name, value)
        return value

    stub(package, __path__=[str(root)], __version__="0.synthetic")
    stub("uvicorn")
    stub("agent_procutil", __path__=[])
    stub("agent_procutil.daemon_instances",
         daemon_entrypoint=lambda *a, **kw: lambda function: function)
    stub(f"{package}.diagnostic_logs", register_log_commands=lambda sub: None)
    stub(f"{package}.__main__", _add_stream_args=lambda parser: None)
    spec = importlib.util.spec_from_file_location(f"{package}.service_start_cli", root / "service_start_cli.py")
    assert spec and spec.loader
    service = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, service)
    spec.loader.exec_module(service)
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    service.register_service_start_commands(parser.add_subparsers(required=True))
    owner, _, _, _, _, calls, run = admission

    def actual(argv, **kwargs):
        if argv[1] != "version":
            return run(argv, **kwargs)
        calls.append(argv)
        output = io.StringIO()
        with redirect_stdout(output):
            args = parser.parse_args(argv)
            args.func(args)
        return subprocess.CompletedProcess(argv, 0, output.getvalue(), "")

    owner.admit_create(actual)
    assert calls == [["--json", "version", "--contracts-json"], ["--json", "status"]]


@pytest.mark.parametrize("change", [
    "cli-version", "create-support", "status-support", "resume-support", "old-daemon", "not-ready",
    "passive", "unknown-instance", "selected-writer", "selected-build", "draining",
])
def test_future_admission_refuses_unknown_cli_daemon_or_selected_writer(admission, change):
    owner, health, support, _, _, calls, run = admission
    if change == "cli-version":
        support["version"] = True
    elif change == "create-support":
        support["contracts"]["agent-bridge.create"]["capabilities"] = []
    elif change == "status-support":
        support["contracts"]["agent-bridge.status"]["version"] = 0
    elif change == "resume-support":
        support["contracts"]["agent-bridge.resume"]["capabilities"] = []
    elif change == "old-daemon":
        health["capabilities"]["retained_workspace_creation_writer"] = True
    elif change == "not-ready":
        health["ready"] = False
    elif change == "passive":
        health["slot"]["role"] = "passive"
    elif change == "unknown-instance":
        health["slot"]["active"]["generation"] = True
    elif change == "selected-writer":
        health["worktree_record_writer"]["installation_root"] = "/foreign"
    elif change == "selected-build":
        health["worktree_record_writer"]["runtime_generation"] = None
    else:
        health["draining"] = True
    with pytest.raises(TaskError):
        owner.admit_create(run)
    assert all(call[1] in {"version", "status"} for call in calls)


def test_continuation_uses_original_journal_not_changed_dynamic_selection(admission):
    owner, health, _, info, observed, calls, run = admission
    health["worktree_record_writer"] = {"installation_root": "/new", "runtime_generation": "b" * 32}
    before = copy.deepcopy(observed)
    response = owner.inspect_session(run, info)
    assert response["worktree_writer_observation"] == before == observed
    assert calls == [
        ["--json", "status"],
        ["--json", "status", "bridge-session", "--full", "--worktree-writer"],
    ]


@pytest.mark.parametrize("change", [
    "journal", "nonce", "build", "requirement-version", "native", "workspace",
    "instance", "instance-generation", "contract", "retained-writes", "scope",
])
def test_original_session_observation_refuses_changed_or_incomplete_custody(admission, change):
    owner, _, _, info, observed, _, run = admission
    if change == "journal":
        observed.clear()
    elif change == "nonce":
        observed["worktree_writer"]["creation_nonce"] = "changed"
    elif change == "build":
        observed["worktree_writer"]["identity"]["runtime_generation"] = "b" * 32
    elif change == "requirement-version":
        observed["worktree_writer"]["version"] = True
    elif change == "native":
        observed["native_session_id"] = "other-native"
    elif change == "workspace":
        observed["worktree_path"] = "/foreign"
    elif change == "instance":
        observed["bridge_instance"]["pid"] = 43
    elif change == "instance-generation":
        observed["bridge_instance"]["generation"] = 8
    elif change == "contract":
        observed["writer_contract"]["creation_nonce"] = True
    elif change == "retained-writes":
        observed["retained_session_writes"] = False
    else:
        observed["writer_scope"] = "selected-root"
    with pytest.raises(TaskError):
        owner.inspect_session(run, info)
