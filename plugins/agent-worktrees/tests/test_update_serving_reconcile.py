"""Current skips require selected footprints; Bridge still reconciles serving."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_worktrees import reconcile, update_runtime, update_runtime_plan


@pytest.fixture
def current_payload(tmp_path, monkeypatch):
    payload = tmp_path / "agent-bridge"
    (payload / "scripts").mkdir(parents=True)
    for extension in ("sh", "ps1"):
        (payload / "scripts" / f"install.{extension}").write_text("exit 0\n")
    (payload / "plugin.json").write_text(json.dumps({
        "name": "agent-bridge", "version": "1.2.3",
        "runtimeScope": "universal", "zeroDowntimeUpdate": True,
    }))
    root = tmp_path / "runtime"
    root.mkdir()
    (root / "deploy-manifest.json").write_text('{"source":{"version":"1.2.3"}}')
    (root / "current-version").write_text("1.2.3")
    slot = root / "versions" / "1.2.3"
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True)
    python.touch()
    (slot / ".install-complete.json").write_text(json.dumps({
        "version": "1.2.3", "completed_at": "2026-01-01", "pid": 0,
    }))
    library = Path(__file__).resolve().parents[3] / "libs/versioned-runtime"
    for helper in library.glob("*.py"):
        shutil.copyfile(helper, payload / "scripts" / helper.name)
    monkeypatch.setattr(reconcile, "core_installed_payload_dir", lambda name: payload)
    monkeypatch.setattr(reconcile, "runtime_installer_environment", lambda *a: (dict(os.environ), root))
    monkeypatch.setattr(update_runtime, "_machine_gate_blocks", lambda *a: False)
    monkeypatch.setattr(update_runtime, "_installer_timeout", lambda *a: 30)
    monkeypatch.setattr(reconcile, "hook_shims_drifted", lambda *a: False)
    return payload, root


def _plan(kind, payload, name="agent-bridge"):
    platform = "windows" if os.name == "nt" else "linux"
    if kind == "engine":
        return update_runtime_plan.plan_engine(payload, platform, force=False)
    if kind == "module":
        return update_runtime_plan.plan_module(name, payload, platform, force=False)
    return update_runtime_plan.plan_registered(name, platform, force=False)


@pytest.mark.parametrize("kind", ["module", "registered"])
def test_current_bridge_build_does_not_skip_serving_owner(current_payload, kind):
    payload, root = current_payload
    before = (root / "deploy-manifest.json").read_bytes()
    step = _plan(kind, payload)
    assert step.status == ""
    assert step.runtime_root == root
    assert "update" in step.argv
    assert not step.fallback_argv
    assert (root / "deploy-manifest.json").read_bytes() == before


@pytest.mark.parametrize("kind", ["engine", "module", "registered"])
def test_non_bridge_current_build_retains_quick_skip(current_payload, kind):
    payload, _root = current_payload
    assert _plan(kind, payload, "agent-example").status == "SKIPPED (current)"


@pytest.mark.parametrize("kind", ["engine", "module", "registered"])
@pytest.mark.parametrize("fault", ["interpreter", "completion", "selector", "helper"])
def test_current_manifest_without_selected_footprint_reaches_installer(current_payload, kind, fault):
    payload, root = current_payload
    slot = root / "versions" / "1.2.3"
    if fault == "interpreter":
        (slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")).unlink()
    elif fault == "completion":
        (slot / ".install-complete.json").write_text("{}")
    elif fault == "selector":
        (root / "current-version").write_text("older")
        (root / "last-known-good").write_text("1.2.3")
    else:
        (payload / "scripts/versioned_runtime.py").unlink()
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    step = _plan(kind, payload, "agent-example")
    assert step.status == "" and "update" in step.argv
    assert before == {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}


@pytest.mark.parametrize("kind", ["engine", "module", "registered"])
def test_current_legacy_runtime_without_selector_retains_reuse(current_payload, kind):
    payload, root = current_payload
    (root / "current-version").unlink()
    python = root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True)
    python.touch()
    assert _plan(kind, payload, "agent-example").status == "SKIPPED (current)"
    python.unlink()
    assert _plan(kind, payload, "agent-example").status == ""


def test_failed_bridge_serving_reconcile_cannot_be_laundered_by_install(current_payload, monkeypatch):
    payload, _root = current_payload
    calls = []
    monkeypatch.setattr(reconcile, "runtime_deployed_version", lambda *a, **kw: "1.2.2")

    def run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(status="OK" if argv[-1] == "install" else "installer exited 75")

    monkeypatch.setattr(update_runtime, "_run_human_installer", run)
    assert update_runtime._execute_runtime_step(_plan("module", payload)) == "installer exited 75"
    assert len(calls) == 1
    assert "update" in calls[0]


def _native_hosts():
    if os.name != "nt":
        return [("posix", shutil.which("bash"))]
    return [(name, shutil.which(name)) for name in ("powershell", "pwsh")]


@pytest.mark.real_installer_processes
@pytest.mark.parametrize("kind,probe,serving", [
    ("module", 0, 0), ("registered", 10, 0),
    ("module", 10, 17), ("registered", 17, 0),
])
@pytest.mark.parametrize("shell,executable", _native_hosts())
def test_outer_update_reaches_native_completed_slot_serving_contract(
    current_payload, tmp_path, monkeypatch, kind, probe, serving, shell, executable,
):
    if executable is None:
        pytest.skip(f"native {shell} is unavailable")
    payload, _root = current_payload
    helper_path = (
        Path(__file__).resolve().parents[2] / "agent-bridge" / "tests"
        / "test_install_completed_slot_admission.py"
    )
    spec = importlib.util.spec_from_file_location("bridge_completed_slot_fixture", helper_path)
    owner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(owner)
    witness = tmp_path / "native"
    witness.mkdir()
    if shell == "posix":
        script = owner._sh_harness(
            witness, "match-active",
            f"export SERVING_PROBE_EXIT_CODE={probe} SERVING_EXIT_CODE={serving}\ndo_update",
            libraries=False,
        )
        extension = "sh"
    else:
        script = owner._ps_harness(
            witness, "match-active",
            f"$env:SERVING_PROBE_EXIT_CODE='{probe}'\n$env:SERVING_EXIT_CODE='{serving}'\nInvoke-Update",
            libraries=False,
        )
        extension = "ps1"
        original_which = shutil.which
        monkeypatch.setattr(
            shutil, "which",
            lambda name, *a, **kw: executable if name in {"pwsh", "powershell"} else original_which(name, *a, **kw),
        )
    shutil.copyfile(script, payload / "scripts" / f"install.{extension}")
    selected_root = witness / "install"
    selected_root.mkdir()
    manifest = selected_root / "deploy-manifest.json"
    manifest.write_text('{"source":{"version":"1.2.3"}}')
    monkeypatch.setattr(
        reconcile, "runtime_installer_environment", lambda *a: (dict(os.environ), selected_root),
    )
    before_manifest = manifest.read_bytes()
    step = _plan(kind, payload)
    assert step.runtime_root == selected_root
    status = update_runtime._execute_runtime_step(step)
    events = owner._events(witness)
    assert status == ("OK" if probe == 0 or (probe == 10 and serving == 0) else "installer exited 1")
    assert events.count("START") == 1
    assert events.count("PROBE") == 1
    assert ("SERVING" in events) is (probe == 10)
    assert not set(events) & {"DRAINED", "PROCEEDED", "BUILT", "PRUNED"}
    assert owner._tree(witness / "install" / "versions" / "1.2.3") == owner._tree(witness / "before-slot")
    assert manifest.read_bytes() == before_manifest
    assert not (witness / "install" / "update-in-progress").exists()
