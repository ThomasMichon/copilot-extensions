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
    source = PreparedRuntimeSource(artifact, json.dumps(module.manifest_fields(identity)))
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


@pytest.mark.parametrize("kind", ["engine", "module", "registered"])
def test_source_selection_does_not_skip_on_matching_package(selected, kind, monkeypatch):
    owner, artifact, runtime, source = selected
    monkeypatch.setattr(reconcile, "runtime_deployed_version", lambda *args, **kw: "1.0.0")
    step = plan(kind, owner, source)
    assert step.status == ""
    assert step.payload == owner
    assert step.artifact == artifact
    assert step.runtime_root == runtime
    assert step.target_version == source.version
    assert step.environment[PIN_ENV] == source.identity
    assert step.environment["COPILOT_PLUGIN_STAGED_FROM"] == str(owner)
    assert any(Path(argument) == artifact / "scripts/install.sh" for argument in step.argv)
    assert str(owner / "scripts/install.sh") not in step.argv


@pytest.mark.parametrize("kind", ["engine", "module", "registered"])
@pytest.mark.parametrize("change", [
    "none", "commit", "tree", "selector", "version", "slot", "venv", "completion",
])
def test_only_complete_selected_source_receipt_skips(selected, kind, change):
    owner, artifact, runtime, source = selected
    runtime.mkdir()
    manifest = json.loads(source.identity)
    slot = runtime / "versions" / source.version
    slot.mkdir(parents=True)
    (slot / ".install-complete.json").write_text(json.dumps({
        "version": source.version, "completed_at": "2026-01-01T00:00:00Z", "pid": os.getpid(),
    }))
    manifest.update(service="agent-worktrees", venv=str(slot))
    if change in {"commit", "tree"}:
        manifest["runtime_source"][f"source_{change}"] = "c" * 40
    if change == "version":
        manifest["runtime_version"] = "another-slot"
    if change == "slot":
        slot.rename(slot.with_name("unselected"))
    if change == "venv":
        manifest["venv"] = str(runtime / "another-slot")
    if change == "completion":
        (slot / ".install-complete.json").write_text("{}")
    (runtime / "deploy-manifest.json").write_text(json.dumps(manifest))
    (runtime / "current-version").write_text(
        "another-slot" if change == "selector" else source.version,
    )
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


def test_source_execution_revalidates_and_keeps_retry_pin(selected, monkeypatch):
    owner, artifact, runtime, source = selected
    step = plan("module", owner, source)
    captured = []

    def run(argv, **kwargs):
        captured.append((argv, kwargs))
        return SimpleNamespace(status="installer exited 1" if len(captured) == 1 else "OK")

    monkeypatch.setattr(update_runtime, "_run_human_installer", run)
    assert update_runtime._execute_runtime_step(step) == "OK (installed)"
    assert len(captured) == 2
    for argv, kwargs in captured:
        assert argv[3] == str(owner / "scripts/command_binding.py")
        assert "--managed-deployment" in argv
        assert argv[argv.index("--source-payload-root") + 1] == str(owner)
        assert kwargs["cwd"] == artifact
        assert kwargs["env"][PIN_ENV] == source.identity
        assert kwargs["target_version"] == source.version
        assert kwargs["receipt"]() is None
    assert captured[0][0][-1] == "update"
    assert captured[1][0][-1] == "install"


def test_source_execution_refuses_changed_inputs_before_launch(selected, monkeypatch):
    owner, artifact, runtime, source = selected
    step = plan("engine", owner, source)
    (artifact / "changed.py").write_text("CHANGED = True\n")
    monkeypatch.setattr(
        update_runtime, "_run_human_installer", lambda *a, **kw: pytest.fail("must not launch"),
    )
    assert "payload bytes" in update_runtime._execute_runtime_step(step)
    assert not runtime.exists()


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
    slot = runtime / "versions" / source.version
    slot.mkdir(parents=True)
    manifest = json.loads(source.identity)
    manifest.update(service="agent-worktrees", venv=str(slot))
    (runtime / "deploy-manifest.json").write_text(json.dumps(manifest))
    (runtime / "current-version").write_text(source.version)
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
    assert "Source completion probe failed" in status
