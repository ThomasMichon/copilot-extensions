"""Prepared update inputs retain the installed owner and exact source target."""
from __future__ import annotations

import dataclasses
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
from agent_worktrees.update_runtime_application import PreparedApplication

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def selected(tmp_path, monkeypatch):
    owner, artifact, runtime = (tmp_path / name for name in ("owner", "artifact", "runtime"))
    owner.mkdir()
    scripts = owner / "scripts"
    scripts.mkdir()
    for path in (ROOT / "libs/versioned-runtime").glob("versioned_runtime*.py"):
        shutil.copyfile(path, scripts / path.name)
    (scripts / "command_binding.py").write_text("# owner lifecycle fixture\n")
    (owner / "plugin.json").write_text(json.dumps({
        "name": "agent-worktrees", "version": "1.0.0", "runtimeScope": "universal",
    }))
    shutil.copytree(owner, artifact)
    (artifact / "scripts/install.sh").write_text("exit 0\n")
    (artifact / "scripts/install.ps1").write_text("exit 0\n")
    (artifact / "scripts/install_source.py").write_text(
        'APPLICATION_INSTALL_CONTRACT = '
        '{"schema": "copilot-extensions.prepared-application-install", "version": 1}\n',
    )
    helper = ROOT / "libs/versioned-runtime/versioned_runtime_source.py"
    spec = importlib.util.spec_from_file_location("_update_source_fixture", helper)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    digest = module.assembly_hash(artifact)
    identity = module.SourceIdentity(
        "agent-worktrees", "1.0.0", "a" * 40, "b" * 40, digest,
        module.runtime_version("1.0.0", "a" * 40, digest),
    )
    (artifact / "runtime-source.json").write_text(json.dumps(identity.document()))
    application = tmp_path / "application"
    application.mkdir()
    (application / "application.json").write_text(json.dumps({
        "name": "agent-worktrees", "module": "agent_worktrees",
        "construction": {
            "identity": module.manifest_fields(identity),
            "runtime_version": identity.runtime_version + ".profile." + "e" * 16,
            "payload_hash": "9" * 64, "extras": [], "local_packages": ["agent-worktrees"],
        },
    }))
    (tmp_path / "install-source.json").write_text(json.dumps({
        "schema": "copilot-extensions.install-source", "version": 1, "kind": "source",
        "source_commit": identity.source_commit, "source_tree": identity.source_tree,
        "main_head": "f" * 40, "composition": {"main_seed": "f" * 40, "tree": "a" * 40},
        "projects": {"agent-worktrees": {
            "payload": "artifact", "source": identity.document(), "profile": "profile.json",
            "wheel_inputs": "wheels", "artifact": {}, "application": "application",
        }},
    }))
    prepared = PreparedApplication(tmp_path, "agent-worktrees", "null")
    source = PreparedRuntimeSource(artifact, json.dumps(module.manifest_fields(identity)), prepared)
    original = subprocess.run

    def run(argv, **kwargs):
        if "--native-inputs" in argv:
            return SimpleNamespace(returncode=0, stdout=prepared.selection, stderr="")
        return original(argv, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(shutil, "which", lambda name: sys.executable if name == "uv" else name)
    monkeypatch.setattr(
        reconcile, "runtime_installer_environment",
        lambda name, payload: (dict(os.environ), runtime),
    )
    monkeypatch.setattr(reconcile, "core_installed_payload_dir", lambda name: owner)
    monkeypatch.setattr(update_runtime, "_machine_gate_blocks", lambda *args: False)
    return owner, artifact, runtime, source


def plan(kind, owner, source):
    if kind == "engine":
        return update_runtime_plan.plan_engine(owner, "linux", force=False, source=source)
    if kind == "module":
        return update_runtime_plan.plan_module(
            "agent-worktrees", owner, "linux", force=False, source=source,
        )
    return update_runtime_plan.plan_registered(
        "agent-worktrees", "linux", force=False, source=source,
    )


@pytest.mark.parametrize("action", ["install", "repair"])
def test_native_engine_fresh_action_keeps_the_selected_artifact(selected, action):
    owner, artifact, _, source = selected
    step = update_runtime_plan.plan_engine(owner, "linux", force=True, source=source, action=action)
    assert not step.status
    assert step.argv[2] == action
    assert step.artifact == artifact
    assert step.environment[PIN_ENV] == source.identity


@pytest.mark.parametrize("kind", ["engine", "module", "registered"])
@pytest.mark.parametrize("custom_entry", [False, True])
def test_native_profile_flows_through_existing_plan_owner(
    selected, kind, monkeypatch, tmp_path, custom_entry,
):
    owner, artifact, _, source = selected
    script = "native_source" if custom_entry else "install"
    if custom_entry:
        for extension in ("sh", "ps1"):
            (artifact / "scripts" / f"{script}.{extension}").write_text("exit 0\n")
    (artifact / "source-install.json").write_text(json.dumps({
        "schema": "copilot-extensions.native-source-install", "version": 1,
        "windows": {"script": f"scripts/{script}.ps1", "argument": "-InstallSource"},
        "posix": {"script": f"scripts/{script}.sh", "argument": "--install-source"},
    }))
    snapshot = tmp_path / "prepared"
    snapshot.mkdir()
    native = {
        "identity": json.loads(source.identity),
        "runtime_version": source.version + ".profile." + "d" * 16,
        "payload_hash": "e" * 64,
    }
    source = dataclasses.replace(source, install_source=snapshot, native=json.dumps(native))
    monkeypatch.setattr(PreparedRuntimeSource, "validate", lambda *args: None)
    step = plan(kind, owner, source)
    assert not step.status
    assert step.target_version == native["runtime_version"]
    assert any(Path(argument) == artifact / "scripts" / f"{script}.sh" for argument in step.argv)
    index = step.argv.index("--install-source")
    assert step.argv[index + 1] == str(snapshot)
    assert step.environment[PIN_ENV] == source.identity
    assert step.fallback_argv == ()


@pytest.mark.parametrize("kind", ["engine", "module", "registered"])
def test_source_selection_does_not_skip_on_matching_package(selected, kind, monkeypatch):
    owner, artifact, runtime, source = selected
    monkeypatch.setattr(reconcile, "runtime_deployed_version", lambda *args, **kw: "1.0.0")
    step = plan(kind, owner, source)
    assert step.status == ""
    assert step.payload == owner
    assert step.artifact == artifact
    assert step.runtime_root == runtime
    assert step.target_version == source.application.version
    assert step.environment[PIN_ENV] == source.identity
    assert step.environment["COPILOT_PLUGIN_STAGED_FROM"] == str(owner)
    assert "--install-application" in step.argv
    assert str(artifact / "scripts/install_source.py") in step.argv
    assert str(owner / "scripts/install.sh") not in step.argv


@pytest.mark.parametrize("kind", ["engine", "module", "registered"])
@pytest.mark.parametrize("content", [b"{", b'{"value":"\xff"}', b"[]",
                                   b'{"service":"agent-worktrees","service":"agent-worktrees"}'])
def test_unrecognized_source_receipt_plans_reconstruction_without_writes(selected, kind, content):
    owner, _, runtime, source = selected
    runtime.mkdir()
    path = runtime / "deploy-manifest.json"
    path.write_bytes(content)
    step = plan(kind, owner, source)
    assert not step.blocked and step.status == ""
    assert path.read_bytes() == content
    assert list(runtime.iterdir()) == [path]


@pytest.mark.parametrize("kind", ["engine", "module", "registered"])
@pytest.mark.parametrize("fault", ["storage", "foreign-service", "directory"])
def test_legacy_receipt_cannot_veto_application_reconstruction(selected, kind, fault, monkeypatch):
    owner, _, runtime, source = selected
    runtime.mkdir()
    path = runtime / "deploy-manifest.json"
    if fault == "directory":
        path.mkdir()
    else:
        path.write_text('{"service":"another-plugin"}' if fault == "foreign-service" else "{")
    if fault == "storage":
        original = Path.read_text

        def unreadable(target, *args, **kwargs):
            if target == path:
                raise PermissionError("fixture storage failure")
            return original(target, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", unreadable)
    assert not plan(kind, owner, source).blocked


@pytest.mark.parametrize("kind", ["engine", "module", "registered"])
@pytest.mark.parametrize("change", [
    "none", "commit", "tree", "selector", "version", "slot", "venv", "completion", "service",
    "interpreter",
])
def test_only_complete_selected_source_receipt_skips(selected, kind, change):
    owner, artifact, runtime, source = selected
    runtime.mkdir()
    version = source.application.version
    slot = runtime / "versions" / version
    slot.mkdir(parents=True)
    python = slot / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True)
    if change != "interpreter":
        python.touch()
    (slot / ".install-complete.json").write_text(json.dumps({
        "version": version, "completed_at": "2026-01-01T00:00:00Z", "pid": os.getpid(),
        "payload_hash": "9" * 64,
    }))
    declaration = json.loads(source.application.declaration)
    identity = json.loads(source.identity)["runtime_source"]
    receipt = {"state": "installed", "version": version}
    if change in {"commit", "tree"}:
        identity[f"source_{change}"] = "c" * 40
    if change == "version":
        receipt["version"] = "another-slot"
    if change == "venv":
        python.unlink()
    if change == "completion":
        (slot / ".install-complete.json").write_text("{}")
    if change == "service":
        declaration["name"] = "another-runtime"
    (slot / "install-success.json").write_text(json.dumps(receipt))
    (slot / "application.json").write_text(json.dumps(declaration))
    (slot / "runtime-source.json").write_text(json.dumps(identity))
    if change == "slot":
        slot.rename(slot.with_name("unselected"))
    (runtime / "current-version").write_text(
        "another-slot" if change == "selector" else version,
    )
    (runtime / "last-known-good").write_text(version)
    step = plan(kind, owner, source)
    assert step.status == ("SKIPPED (current)" if change == "none" else "")


def test_old_prepared_helper_refuses_instead_of_ignoring_pin(selected):
    owner, artifact, runtime, source = selected
    helper = artifact / "scripts/versioned_runtime_source.py"
    helper.write_text("print('{}')\n")
    step = plan("engine", owner, source)
    assert step.blocked
    assert "does not support transaction source pinning" in step.status
    assert not runtime.exists()


@pytest.mark.parametrize("kind", ["engine", "module", "registered"])
@pytest.mark.parametrize("fault", ["", "source", "completion", "selector"])
def test_selected_repair_generation_preserves_exact_source_currentness(selected, kind, fault):
    owner, artifact, runtime, source = selected
    version = source.application.version + ".repair." + "d" * 32
    slot = runtime / "versions" / version
    slot.mkdir(parents=True)
    python = slot / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True)
    python.touch()
    (slot / ".install-complete.json").write_text(json.dumps(
        {} if fault == "completion" else {
            "version": version, "completed_at": "2026-01-01T00:00:00Z", "pid": os.getpid(),
            "payload_hash": "9" * 64,
        },
    ))
    identity = json.loads(source.identity)["runtime_source"]
    if fault == "source":
        identity["source_commit"] = "b" * 40
    (slot / "runtime-source.json").write_text(json.dumps(identity))
    (slot / "application.json").write_text(source.application.declaration)
    (slot / "install-success.json").write_text(json.dumps({"state": "installed", "version": version}))
    (runtime / "current-version").write_text("foreign" if fault == "selector" else version)
    (runtime / "last-known-good").write_text(version)
    assert plan(kind, owner, source).status == ("" if fault else "SKIPPED (current)")


@pytest.mark.parametrize("kind", ["engine", "module", "registered"])
def test_prepared_source_cannot_weaken_its_own_machine_gate(selected, kind, monkeypatch):
    owner, artifact, runtime, source = selected
    monkeypatch.setattr(reconcile, "manifest_runtime_scope", lambda path: (
        "machine-gated" if path == artifact else "universal"
    ))
    monkeypatch.setattr(
        update_runtime, "_machine_gate_blocks", lambda name, scope, project: scope == "machine-gated",
    )
    step = plan(kind, owner, source)
    assert step.status == update_runtime.MACHINE_GATED_SKIP
    assert not step.eligible and not runtime.exists()


@pytest.mark.parametrize("kind", ["engine", "module", "registered"])
def test_source_input_mutation_blocks_planning(selected, kind):
    owner, artifact, runtime, source = selected
    (artifact / "changed.py").write_text("CHANGED = True\n")
    step = plan(kind, owner, source)
    assert step.blocked and "payload bytes" in step.status
    assert not runtime.exists()


def test_source_execution_revalidates_without_legacy_install_retry(selected, monkeypatch):
    owner, artifact, runtime, source = selected
    step = plan("module", owner, source)
    assert not step.fallback_argv
    step = dataclasses.replace(step, fallback_argv=step.argv)
    captured = []

    def run(argv, **kwargs):
        captured.append((argv, kwargs))
        return SimpleNamespace(status="installer exited 1")

    monkeypatch.setattr(update_runtime, "_run_human_installer", run)
    assert update_runtime._execute_runtime_step(step) == "installer exited 1"
    assert len(captured) == 1
    for argv, kwargs in captured:
        assert argv[3] == str(owner / "scripts/command_binding.py")
        assert "--managed-deployment" in argv
        assert "--deferred-runtime" in argv
        assert argv[argv.index("--source-payload-root") + 1] == str(owner)
        assert kwargs["cwd"] == artifact
        assert kwargs["env"][PIN_ENV] == source.identity
        assert kwargs["target_version"] == source.application.version
        assert kwargs["receipt"]() is None
    assert "--install-application" in captured[0][0]


def test_source_execution_refuses_changed_inputs_before_launch(selected, monkeypatch):
    owner, artifact, runtime, source = selected
    step = plan("engine", owner, source)
    (artifact / "changed.py").write_text("CHANGED = True\n")
    monkeypatch.setattr(
        update_runtime, "_run_human_installer", lambda *a, **kw: pytest.fail("must not launch"),
    )
    assert "payload bytes" in update_runtime._execute_runtime_step(step)
    assert not runtime.exists()


@pytest.mark.parametrize("change", ["declaration", "composition"])
def test_complete_application_handoff_rejects_changed_prepared_inputs(selected, monkeypatch, change):
    owner, artifact, runtime, source = selected
    step = plan("engine", owner, source)
    if change == "declaration":
        path = source.application.snapshot / "application" / "application.json"
        document = json.loads(path.read_text())
        document["module"] = "another_application"
    else:
        path = source.application.snapshot / "install-source.json"
        document = json.loads(path.read_text())
        document["composition"]["tree"] = "c" * 40
    path.write_text(json.dumps(document))
    monkeypatch.setattr(
        update_runtime, "_run_human_installer", lambda *a, **kw: pytest.fail("must not launch"),
    )
    assert "inputs changed after selection" in update_runtime._execute_runtime_step(step)
    assert not runtime.exists()


@pytest.mark.parametrize("kind", ["engine", "module", "registered"])
def test_forced_source_install_selects_canonical_repair_without_legacy_retry(selected, kind):
    owner, artifact, runtime, source = selected
    if kind == "engine":
        step = update_runtime_plan.plan_engine(owner, "linux", force=True, source=source)
    elif kind == "module":
        step = update_runtime_plan.plan_module(
            "agent-worktrees", owner, "linux", force=True, source=source,
        )
    else:
        step = update_runtime_plan.plan_registered(
            "agent-worktrees", "linux", force=True, source=source,
        )
    assert not step.blocked, step.status
    assert "--repair" in step.argv and "--install-application" in step.argv
    assert not step.fallback_argv
    assert not runtime.exists()


def test_raw_source_identity_alone_cannot_reenter_retired_installer(selected):
    owner, artifact, runtime, source = selected
    step = plan("engine", owner, dataclasses.replace(source, application=None))
    assert step.blocked and "Complete application inputs are required" in step.status
    assert not step.argv and not runtime.exists()


@pytest.mark.parametrize(("status", "exit_code"), [
    ("OK", 0), ("installer exited 75", 75), ("installer exited 76", 76),
    ("timed out (deadline hit during build)", 1),
])
def test_source_step_retains_engine_outcome_recording(selected, monkeypatch, status, exit_code):
    owner, artifact, runtime, source = selected
    step = plan("engine", owner, source)
    calls = []
    monkeypatch.setattr(update_runtime, "_execute_runtime_step", lambda selected: (
        calls.append(selected) or status
    ))
    monkeypatch.setattr(update_runtime.update_events, "record", lambda *a, **kw: calls.append(a))
    assert update_runtime._run_engine_installer(
        list(step.argv), owner, step.deadline_s, step.environment,
        runtime_root=runtime, target_version=source.version, source_step=step,
    ) == exit_code
    assert calls[0] is step and calls[1][0] == "engine_runtime"


def test_ordinary_runtime_environment_drops_inherited_source_pin(monkeypatch):
    monkeypatch.setenv(PIN_ENV, "foreign selection")
    assert PIN_ENV not in reconcile.clean_runtime_installer_environment()


def test_malformed_selected_source_is_not_an_untyped_key_error(selected):
    *_, source = selected
    for invalid in ({}, {"runtime_source": {}, "runtime_version": "version"}):
        with pytest.raises(ValueError, match="complete source selection"):
            dataclasses.replace(source, identity=json.dumps(invalid))


@pytest.mark.parametrize("kind", ["engine", "module", "registered", "execute"])
def test_completion_probe_timeout_is_an_attributable_failed_step(selected, monkeypatch, kind):
    owner, artifact, runtime, source = selected
    runtime.mkdir()
    version = source.application.version
    slot = runtime / "versions" / version
    slot.mkdir(parents=True)
    python = slot / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True)
    python.touch()
    (slot / "runtime-source.json").write_text(json.dumps(json.loads(source.identity)["runtime_source"]))
    (slot / "application.json").write_text(source.application.declaration)
    (slot / "install-success.json").write_text(json.dumps({"state": "installed", "version": version}))
    (runtime / "current-version").write_text(version)
    (runtime / "last-known-good").write_text(version)
    original = subprocess.run

    def run(argv, **kwargs):
        if "is-complete" in argv:
            raise subprocess.TimeoutExpired(argv, 30)
        return original(argv, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    if kind == "execute":
        step = update_runtime_plan.RuntimeUpdateStep(
            "agent-worktrees", "engine_runtime", owner, source=source,
            runtime_root=runtime, project_root=ROOT, target_version=source.version,
            environment={**os.environ, PIN_ENV: source.identity},
            argv=(sys.executable, "-c", "raise AssertionError('must not run')"),
        )

        def installer(argv, **kwargs):
            kwargs["receipt"]()
            pytest.fail("timed-out receipt must refuse execution")

        monkeypatch.setattr(update_runtime, "_run_human_installer", installer)
        status = update_runtime._execute_runtime_step(step)
    else:
        step = plan(kind, owner, source)
        assert step.blocked
        status = step.status
    assert "Application completion probe failed" in status
