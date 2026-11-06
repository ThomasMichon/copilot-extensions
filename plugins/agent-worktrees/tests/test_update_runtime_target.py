"""Actual installed-owner planning keeps profile targets separate from source pins."""
from __future__ import annotations

import importlib.util
import dataclasses
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

from agent_worktrees import reconcile, update_runtime, update_runtime_plan
from agent_worktrees.update_runtime_source import PIN_ENV, PreparedRuntimeSource
from agent_worktrees.update_runtime_application import PreparedApplication
from agent_worktrees.update_runtime_target import TARGET_ENV, plan_target

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("closed_inputs", [False, True])
@pytest.mark.parametrize("fault", ["", "target", "footprint"])
def test_native_source_execution_retains_target_revalidation_and_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, closed_inputs: bool, fault: str,
) -> None:
    from agent_worktrees import update_runtime_target

    owner, root, project = (tmp_path / name for name in ("owner", "runtime", "project"))
    target, events = object(), []
    source = SimpleNamespace(
        application=None, install_source=tmp_path / "snapshot" if closed_inputs else None,
    )

    def validate(name, payload, environment):
        assert (name, payload, environment) == ("agent-example", owner, {"fixture": "selected"})
        events.append("source")

    def check(expected, actual, name, payload, installation, environment):
        assert expected is target and actual is source
        assert (name, payload, installation, environment) == (
            "agent-example", owner, root, {"fixture": "selected"},
        )
        events.append("target")
        if fault == "target":
            raise ValueError("selected target changed")

    def deployed(installation, payload, *, target):
        assert (installation, payload) == (root, owner) and target is step.source_target
        events.append("receipt")
        return "qualified-generation"

    source.validate = validate
    source.deployed_version = deployed
    source.lifecycle_argv = lambda *args: ["native-installer", "--qualified"]
    step = SimpleNamespace(
        status="", name="agent-example", payload=owner, runtime_root=root, project_root=project,
        source=source, source_target=target, artifact=tmp_path / "artifact",
        environment={"fixture": "selected"}, deadline_s=90, target_version="qualified-generation",
        argv=("native-installer",), fallback_argv=("legacy-installer",),
    )

    def run(command, **options):
        assert command == ["native-installer", "--qualified"]
        assert options["receipt"]() == options["target_version"] == "qualified-generation"
        events.append("installer")
        return SimpleNamespace(status="OK")

    monkeypatch.setattr(update_runtime_target, "check_target", check)
    monkeypatch.setattr(update_runtime, "_run_human_installer", run)
    monkeypatch.setattr(
        update_runtime_plan, "_runtime_footprint_current", lambda actual: fault != "footprint",
    )
    status = update_runtime._execute_runtime_step(step)
    if fault == "target" and not closed_inputs:
        assert status == "prepared source execution refused: selected target changed"
        assert events == ["source", "target"]
    else:
        assert status == (
            "source deployment receipt invalid: selected runtime footprint changed"
            if fault == "footprint" else "OK"
        )
        assert events == ["source", *([] if closed_inputs else ["target"]), "receipt", "installer"]


@pytest.mark.parametrize("fault", [
    None, "profile", "plugin", "root", "version", "extras", "duplicate", "foreign", "failed", "large",
])
def test_application_profile_query_uses_only_installed_owner_and_rejects_foreign_inputs(
    tmp_path, monkeypatch, fault,
):
    from agent_worktrees import update_runtime_target as target

    owner, root = tmp_path / "owner", tmp_path / "runtime"
    (owner / "scripts").mkdir(parents=True)
    planner = owner / "scripts" / "runtime_profile.py"
    planner.write_text("# installed owner fixture\n")
    (owner / "runtime-source-target.json").write_text(json.dumps({
        "schema": target.PROVIDER_SCHEMA, "version": 1, "planner": "scripts/runtime_profile.py",
    }))
    expected = {
        "schema": target.APPLICATION_PROFILE_SCHEMA, "version": 1,
        "plugin": "agent-containers", "runtime_root": str(root),
        "profile": "provider-exec", "extras": ["provider-exec"],
    }
    value = dict(expected)
    if fault == "profile":
        value["profile"] = "../escape"
    elif fault == "plugin":
        value["plugin"] = "another"
    elif fault == "root":
        value["runtime_root"] = str(tmp_path / "foreign")
    elif fault == "version":
        value["version"] = True
    elif fault == "extras":
        value["extras"] = [[], "provider-exec"]
    elif fault == "duplicate":
        value["extras"] *= 2
    elif fault == "foreign":
        value["extra"] = "unsupported"
    calls = []

    def run(command, **options):
        calls.append(command)
        assert command == [
            sys.executable, "-I", "-B", str(planner), "--application-profile", "--root", str(root),
        ]
        assert options["env"] == {"OWNER_ENV": "selected"}
        assert options["stdin"] is subprocess.DEVNULL and options["timeout"] == 30
        return SimpleNamespace(
            returncode=1 if fault == "failed" else 0,
            stdout=" " * 65537 if fault == "large" else json.dumps(value),
            stderr="unsupported query" if fault == "failed" else "",
        )

    monkeypatch.setattr(target.subprocess, "run", run)
    if fault:
        with pytest.raises(ValueError, match="invalid selection|query failed|document bound"):
            target.application_profile("agent-containers", owner, root, {"OWNER_ENV": "selected"})
    else:
        assert target.application_profile("agent-containers", owner, root, {"OWNER_ENV": "selected"}) == expected
    assert len(calls) == 1
    assert not root.exists()


def test_unprofiled_application_needs_no_query_and_missing_containers_owner_refuses(tmp_path, monkeypatch):
    from agent_worktrees import update_runtime_target as target

    monkeypatch.setattr(target.subprocess, "run", lambda *a, **k: pytest.fail("unowned profile query"))
    assert target.application_profile("agent-bridge", tmp_path, tmp_path / "runtime", {}) is None
    with pytest.raises(ValueError, match="owner is unavailable"):
        target.application_profile("agent-containers", tmp_path, tmp_path / "runtime", {})


@pytest.fixture
def selected(tmp_path, monkeypatch):
    owner, artifact, runtime = (tmp_path / name for name in ("owner", "artifact", "runtime"))
    scripts = owner / "scripts"
    scripts.mkdir(parents=True)
    for path in (ROOT / "libs/versioned-runtime").glob("versioned_runtime*.py"):
        shutil.copyfile(path, scripts / path.name)
    shutil.copyfile(
        ROOT / "plugins/agent-containers/scripts/runtime_profile.py", scripts / "runtime_profile.py",
    )
    shutil.copyfile(
        ROOT / "plugins/agent-containers/runtime-source-target.json", owner / "runtime-source-target.json",
    )
    (scripts / "command_binding.py").write_text("# owner lifecycle fixture\n", encoding="utf-8")
    (scripts / "init.sh").write_text("exit 0\n", encoding="utf-8")
    (owner / "plugin.json").write_text(json.dumps({
        "name": "agent-containers", "version": "1.0.0", "runtimeScope": "universal",
    }), encoding="utf-8")
    (owner / "pyproject.toml").write_text('version = "1.0.0"\n', encoding="utf-8")
    shutil.copytree(owner, artifact)
    (artifact / "scripts/install_source.py").write_text(
        'APPLICATION_INSTALL_CONTRACT = '
        '{"schema": "copilot-extensions.prepared-application-install", "version": 1}\n',
    )
    spec = importlib.util.spec_from_file_location("_target_source_fixture", scripts / "versioned_runtime_source.py")
    assert spec and spec.loader
    helper = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, helper)
    spec.loader.exec_module(helper)
    digest = helper.assembly_hash(artifact)
    identity = helper.SourceIdentity(
        "agent-containers", "1.0.0", "a" * 40, "b" * 40, digest,
        helper.runtime_version("1.0.0", "a" * 40, digest),
    )
    (artifact / "runtime-source.json").write_text(json.dumps(identity.document()), encoding="utf-8")
    source = PreparedRuntimeSource(artifact, json.dumps(helper.manifest_fields(identity)))
    runtime.mkdir()
    (runtime / "runtime-profile.json").write_text(json.dumps({
        "schema": "copilot-extensions.agent-containers.runtime-profile",
        "version": 1, "profile": "provider-exec",
    }), encoding="utf-8")
    monkeypatch.setattr(
        reconcile, "runtime_installer_environment",
        lambda *args: (reconcile.clean_runtime_installer_environment(), runtime),
    )
    monkeypatch.setattr(reconcile, "core_installed_payload_dir", lambda name: owner)
    monkeypatch.setattr(update_runtime, "_machine_gate_blocks", lambda *args: False)
    original = subprocess.run

    def run(argv, **kwargs):
        if "--native-inputs" in argv:
            application = PreparedApplication(tmp_path, "agent-containers", json.dumps({
                "schema": "copilot-extensions.runtime-application-profile", "version": 1,
                "plugin": "agent-containers", "runtime_root": str(runtime),
                "profile": "provider-exec", "extras": ["provider-exec"],
            }, sort_keys=True))
            return SimpleNamespace(returncode=0, stdout=application.selection, stderr="")
        return original(argv, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(shutil, "which", lambda name: sys.executable if name == "uv" else name)
    return _application((owner, artifact, runtime, source, helper))


def _application(selected):
    owner, artifact, runtime, source, helper = selected
    snapshot = artifact.parent
    app = snapshot / "application"
    app.mkdir(exist_ok=True)
    (app / "application.json").write_text(json.dumps({
        "name": "agent-containers", "module": "agent_containers",
        "construction": {
            "identity": json.loads(source.identity),
            "runtime_version": source.version + ".profile." + "e" * 16,
            "payload_hash": "d" * 64, "extras": ["provider-exec"],
            "local_packages": ["agent-containers"],
        },
    }))
    (snapshot / "install-source.json").write_text(json.dumps({
        "schema": "copilot-extensions.install-source", "version": 1, "kind": "source",
        "source_commit": "a" * 40, "source_tree": "b" * 40, "main_head": "f" * 40,
        "composition": {"main_seed": "f" * 40, "tree": "a" * 40},
        "projects": {"agent-containers": {
            "payload": "artifact", "source": json.loads(source.identity)["runtime_source"],
            "profile": "profile.json", "wheel_inputs": "wheels", "artifact": {},
            "application": "application",
        }},
    }))
    profile = {
        "schema": "copilot-extensions.runtime-application-profile", "version": 1,
        "plugin": "agent-containers", "runtime_root": str(runtime),
        "profile": "provider-exec", "extras": ["provider-exec"],
    }
    return owner, artifact, runtime, dataclasses.replace(source, application=PreparedApplication(
        snapshot, "agent-containers", json.dumps(profile, sort_keys=True),
    )), helper


def _plan(selected, *, force=False):
    if selected[3].application is None:
        selected = _application(selected)
    return update_runtime_plan.plan_registered(
        "agent-containers", "linux", force=force, source=selected[3],
    )


def _completed(selected):
    owner, artifact, root, source, helper = selected
    version = source.application.version
    slot = root / "versions" / version
    slot.mkdir(parents=True)
    python = slot / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True)
    python.touch()
    (slot / ".install-complete.json").write_text(json.dumps({
        "version": version, "completed_at": "2026-01-01T00:00:00Z",
        "pid": os.getpid(), "payload_hash": "d" * 64,
    }), encoding="utf-8")
    (slot / "runtime-source.json").write_text(json.dumps(json.loads(source.identity)["runtime_source"]))
    (slot / "application.json").write_text(source.application.declaration)
    (slot / "install-success.json").write_text(json.dumps({"state": "installed", "version": version}))
    (root / "current-version").write_text(version, encoding="utf-8")
    (root / "last-known-good").write_text(version, encoding="utf-8")
    return slot


def test_owner_process_returns_profile_target_but_keeps_base_pin_and_owner(selected):
    owner, artifact, root, source, helper = selected
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    step = _plan(selected)
    assert not step.blocked, step.status
    assert step.source_target is None
    assert step.target_version == source.application.version
    assert step.target_version != json.loads(source.identity)["runtime_version"]
    assert step.environment[PIN_ENV] == source.identity
    assert TARGET_ENV not in step.environment
    assert step.payload == owner and step.artifact == artifact and step.runtime_root == root
    assert step.environment["COPILOT_PLUGIN_STAGED_FROM"] == str(owner)
    assert {p: p.read_bytes() for p in root.rglob("*") if p.is_file()} == before


def test_prepared_profile_code_never_becomes_the_planning_authority(selected):
    owner, artifact, root, source, helper = selected
    (artifact / "scripts/runtime_profile.py").write_text(
        "raise AssertionError('prepared code must not execute during planning')\n", encoding="utf-8",
    )
    digest = helper.assembly_hash(artifact)
    identity = helper.SourceIdentity(
        "agent-containers", "1.0.0", "a" * 40, "b" * 40, digest,
        helper.runtime_version("1.0.0", "a" * 40, digest),
    )
    (artifact / "runtime-source.json").write_text(json.dumps(identity.document()), encoding="utf-8")
    source = PreparedRuntimeSource(artifact, json.dumps(helper.manifest_fields(identity)))
    source = _application((owner, artifact, root, source, helper))[3]
    step = update_runtime_plan.plan_registered(
        "agent-containers", "linux", force=False, source=source,
    )
    assert not step.blocked, step.status
    assert step.source_target is None
    assert json.loads(step.source.application.selection)["extras"] == ["provider-exec"]


@pytest.mark.parametrize("fault", [
    "", "success-version", "selector", "last-good", "application-profile", "slot-source",
    "slot-source-type", "slot-hash", "completion", "interpreter",
])
def test_currentness_requires_actual_profile_source_selector_and_completion(selected, fault):
    slot = _completed(selected)
    root = selected[2]
    path = slot / "application.json"
    if fault == "interpreter":
        (slot / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")).unlink()
    elif fault in {"selector", "last-good"}:
        (root / ("current-version" if fault == "selector" else "last-known-good")).write_text("foreign")
    elif fault:
        data = json.loads(path.read_text())
        if fault == "success-version":
            path = slot / "install-success.json"
            data = {"state": "installed", "version": "another-slot"}
        elif fault == "application-profile":
            data["construction"]["extras"] = []
        elif fault in {"slot-source", "slot-source-type"}:
            path = slot / "runtime-source.json"
            data = json.loads(path.read_text())
            data["source_tree" if fault == "slot-source" else "version"] = (
                "c" * 40 if fault == "slot-source" else True
            )
        elif fault == "slot-hash":
            data["construction"]["payload_hash"] = "e" * 64
        else:
            path = slot / ".install-complete.json"
            data = {}
        path.write_text(json.dumps(data), encoding="utf-8")
    assert _plan(selected).status == ("" if fault else "SKIPPED (current)")


@pytest.mark.parametrize("profile", ["base", "provider-exec"])
@pytest.mark.parametrize("fault", ["", "intent", "completion", "interpreter"])
def test_marketplace_profile_currentness_uses_owning_target_policy(selected, profile, fault):
    owner, _, root, _, _ = selected
    version = f"1.0.0+{profile}"
    slot = root / "versions" / version
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True)
    if fault != "interpreter":
        python.touch()
    (slot / ".install-complete.json").write_text(json.dumps(
        {} if fault == "completion" else {
            "version": version, "completed_at": "2026-01-01", "pid": 0,
        },
    ))
    (root / "current-version").write_text(version)
    (root / "deploy-manifest.json").write_text(json.dumps({
        "source": {"version": "1.0.0"}, "runtime_version": version,
        "runtime_profile": profile, "venv": str(slot),
    }))
    (root / "runtime-profile.json").write_text(json.dumps({
        "schema": "copilot-extensions.agent-containers.runtime-profile",
        "version": 1,
        "profile": ("provider-exec" if profile == "base" else "base") if fault == "intent" else profile,
    }))
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    step = update_runtime_plan.plan_registered("agent-containers", "linux", force=False)
    assert step.status == ("" if fault else "SKIPPED (current)")
    assert before == {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}


@pytest.mark.parametrize("fault", [
    "missing-owner", "both-missing", "old-version", "unsafe-path", "boolean-version",
])
def test_provider_skew_refuses_before_any_root_effect(selected, fault):
    owner, artifact, root, source, helper = selected
    path = owner / "runtime-source-target.json"
    value = json.loads(path.read_text())
    if fault in {"missing-owner", "both-missing"}:
        path.unlink()
        if fault == "both-missing":
            (artifact / "runtime-source-target.json").unlink()
            digest = helper.assembly_hash(artifact)
            identity = helper.SourceIdentity(
                "agent-containers", "1.0.0", "a" * 40, "b" * 40, digest,
                helper.runtime_version("1.0.0", "a" * 40, digest),
            )
            (artifact / "runtime-source.json").write_text(json.dumps(identity.document()), encoding="utf-8")
            source = PreparedRuntimeSource(artifact, json.dumps(helper.manifest_fields(identity)))
            selected = owner, artifact, root, source, helper
    elif fault == "unsafe-path":
        value["planner"] = "../outside.py"
    else:
        value["version"] = True if fault == "boolean-version" else 2
    if fault not in {"missing-owner", "both-missing"}:
        path.write_text(json.dumps(value), encoding="utf-8")
    step = _plan(selected)
    assert step.blocked and ("target" in step.status or "profile" in step.status)
    assert not (root / "versions").exists()


@pytest.mark.parametrize("tamper", [
    "plugin", "runtime_root", "source", "source-type", "version", "runtime_version",
])
def test_installed_planner_response_is_bound_to_root_and_full_source(selected, monkeypatch, tamper):
    owner, artifact, root, source, helper = selected
    document = json.loads(plan_target(source, "agent-containers", owner, root, {}).document)
    if tamper == "source-type":
        document["source"]["runtime_source"]["version"] = True
    else:
        document[tamper] = (
            True if tamper == "version" else "../foreign" if tamper == "runtime_version" else "foreign"
        )
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: SimpleNamespace(
        returncode=0, stdout=json.dumps(document), stderr="",
    ))
    with pytest.raises(ValueError, match="different or invalid"):
        plan_target(source, "agent-containers", owner, root, {})


def test_execution_rechecks_profile_before_launch_without_legacy_retry(selected, monkeypatch):
    step = _plan(selected)
    calls = []

    def execute(argv, **kwargs):
        calls.append((argv, kwargs))
        assert TARGET_ENV not in kwargs["env"]
        assert kwargs["env"][PIN_ENV] == selected[3].identity
        assert kwargs["target_version"] == step.target_version
        (selected[2] / "runtime-profile.json").write_text(json.dumps({
            "schema": "copilot-extensions.agent-containers.runtime-profile",
            "version": 1, "profile": "base",
        }), encoding="utf-8")
        return SimpleNamespace(status="installer exited 1")

    monkeypatch.setattr(update_runtime, "_run_human_installer", execute)
    assert update_runtime._execute_runtime_step(step) == "installer exited 1"
    assert "Application profile changed" in update_runtime._execute_runtime_step(step)
    assert len(calls) == 1


def test_ordinary_environment_drops_an_inherited_profile_target(monkeypatch):
    monkeypatch.setenv(TARGET_ENV, "foreign")
    assert TARGET_ENV not in reconcile.clean_runtime_installer_environment()
