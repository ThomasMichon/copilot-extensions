"""Actual installed-owner planning keeps profile targets separate from source pins."""
from __future__ import annotations

import importlib.util
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
from agent_worktrees.update_runtime_target import TARGET_ENV, plan_target

ROOT = Path(__file__).resolve().parents[3]


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
    return owner, artifact, runtime, source, helper


def _plan(selected, *, force=False):
    return update_runtime_plan.plan_registered(
        "agent-containers", "linux", force=force, source=selected[3],
    )


def _completed(selected, target):
    owner, artifact, root, source, helper = selected
    slot = root / "versions" / target.version
    slot.mkdir(parents=True)
    (slot / ".install-complete.json").write_text(json.dumps({
        "version": target.version, "completed_at": "2026-01-01T00:00:00Z",
        "pid": os.getpid(), "payload_hash": "d" * 64,
    }), encoding="utf-8")
    fields = json.loads(source.identity)
    (slot / "runtime-profile.json").write_text(json.dumps({
        "profile": target.profile, "packageVersion": source.package_version,
        "runtimeSource": fields["runtime_source"], "payloadHash": "d" * 64,
    }), encoding="utf-8")
    (root / "deploy-manifest.json").write_text(json.dumps({
        **fields, "service": "agent-containers", "runtime_version": target.version,
        "runtime_profile": target.profile, "venv": str(slot),
    }), encoding="utf-8")
    (root / "current-version").write_text(target.version, encoding="utf-8")
    return slot


def test_owner_process_returns_profile_target_but_keeps_base_pin_and_owner(selected):
    owner, artifact, root, source, helper = selected
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    step = _plan(selected)
    assert not step.blocked, step.status
    assert step.source_target.profile == "provider-exec"
    assert step.target_version == helper.runtime_version(
        "1.0.0+provider-exec", "a" * 40, json.loads(source.identity)["runtime_source"]["assembly_hash"],
    )
    assert step.target_version != source.version
    assert step.environment[PIN_ENV] == source.identity
    assert step.environment[TARGET_ENV] == step.source_target.document
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
    step = update_runtime_plan.plan_registered(
        "agent-containers", "linux", force=False, source=source,
    )
    assert not step.blocked, step.status
    assert step.source_target.profile == "provider-exec"


@pytest.mark.parametrize("fault", [
    "", "outer-profile", "outer-version", "outer-source-type", "slot-profile", "slot-source",
    "slot-source-type", "slot-hash", "completion",
])
def test_currentness_requires_actual_profile_source_selector_and_completion(selected, fault):
    step = _plan(selected)
    slot = _completed(selected, step.source_target)
    root = selected[2]
    path = root / "deploy-manifest.json" if fault.startswith("outer") else slot / "runtime-profile.json"
    if fault:
        data = json.loads(path.read_text())
        if fault == "outer-profile":
            data["runtime_profile"] = "base"
        elif fault == "outer-version":
            data["runtime_version"] = selected[3].version
        elif fault == "outer-source-type":
            data["runtime_source"]["version"] = True
        elif fault == "slot-profile":
            data["profile"] = "base"
        elif fault == "slot-source":
            data["runtimeSource"]["source_tree"] = "c" * 40
        elif fault == "slot-source-type":
            data["runtimeSource"]["version"] = True
        elif fault == "slot-hash":
            data["payloadHash"] = "e" * 64
        else:
            path = slot / ".install-complete.json"
            data = {}
        path.write_text(json.dumps(data), encoding="utf-8")
    assert _plan(selected).status == ("" if fault else "SKIPPED (current)")


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
    assert step.blocked and "target" in step.status
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


def test_execution_rechecks_profile_before_launch_and_on_retry(selected, monkeypatch):
    step = _plan(selected)
    calls = []

    def execute(argv, **kwargs):
        calls.append((argv, kwargs))
        assert kwargs["env"][TARGET_ENV] == step.source_target.document
        assert kwargs["env"][PIN_ENV] == selected[3].identity
        assert kwargs["target_version"] == step.target_version
        (selected[2] / "runtime-profile.json").write_text(json.dumps({
            "schema": "copilot-extensions.agent-containers.runtime-profile",
            "version": 1, "profile": "base",
        }), encoding="utf-8")
        return SimpleNamespace(status="installer exited 1")

    import dataclasses

    step = dataclasses.replace(step, fallback_argv=step.argv)
    monkeypatch.setattr(update_runtime, "_run_human_installer", execute)
    assert "Source target changed" in update_runtime._execute_runtime_step(step)
    assert len(calls) == 1


def test_ordinary_environment_drops_an_inherited_profile_target(monkeypatch):
    monkeypatch.setenv(TARGET_ENV, "foreign")
    assert TARGET_ENV not in reconcile.clean_runtime_installer_environment()
