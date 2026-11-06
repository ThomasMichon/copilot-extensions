"""The updater's installer runs are judged by the runtime receipt.

An installer that exits 0 without publishing the payload version in its
runtime's ``deploy-manifest.json`` is a failure for the registered-runtime
reconcile, the module update and agent-worktrees' own runtime installer.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from types import SimpleNamespace
from pathlib import Path

import pytest

from agent_worktrees import reconcile, update_events
from agent_worktrees import update_runtime as runtime

pytestmark = pytest.mark.installer_receipts


@pytest.fixture
def example(tmp_path, monkeypatch):
    plugin = tmp_path / "agent-worktrees"
    plugin.mkdir()
    (plugin / "modules.json").write_text(json.dumps({
        "modules": [{"name": "agent-example", "source": "agent-example"}],
    }))
    payload = tmp_path / "agent-example"
    (payload / "scripts").mkdir(parents=True)
    (payload / "scripts" / "install.sh").write_text("#!/bin/sh\n")
    (payload / "plugin.json").write_text(json.dumps({"name": "agent-example"}))
    environment = {k: v for k, v in os.environ.items() if k != "COPILOT_EXTENSIONS_CONTEXT"}
    deployed = {"version": "1.0.0"}
    monkeypatch.setattr(reconcile, "core_installed_payload_dir", lambda name: payload)
    monkeypatch.setattr(reconcile, "manifest_runtime_scope", lambda path: "universal")
    monkeypatch.setattr(reconcile, "payload_version", lambda path: "2.0.0")
    monkeypatch.setattr(reconcile, "runtime_deployed_version",
                        lambda *a, **k: deployed["version"])
    monkeypatch.setattr(reconcile, "runtime_installer_environment",
                        lambda *a, **k: (environment, tmp_path / "runtime"))
    monkeypatch.setattr(runtime.subprocess, "run",
                        lambda argv, **k: subprocess.CompletedProcess(argv, 0))
    return plugin, deployed


def test_runtime_reconcile_fails_an_exit_0_that_left_the_old_receipt(example):
    status = runtime._reconcile_one_runtime("agent-example", "linux", force=True)
    assert status == ("runtime not at target after a successful installer exit "
                      "(deployed 1.0.0, expected 2.0.0)")
    assert update_events.classify_status(status)[0] == "command_failed"


def test_runtime_reconcile_accepts_the_published_receipt(example, monkeypatch):
    _plugin, deployed = example

    def install(argv, **_k):
        deployed["version"] = "2.0.0"
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(runtime.subprocess, "run", install)
    assert runtime._reconcile_one_runtime("agent-example", "linux", force=True) == "OK"


@pytest.fixture
def inactive_runtime(example, monkeypatch):
    from agent_worktrees import update_runtime_readiness as readiness

    plugin, deployed = example
    payload = plugin.parent / "agent-example"
    (payload / "plugin.json").write_text(json.dumps({
        "name": "agent-example", "runtimeScope": "universal",
        "installerReadiness": "installer-readiness.json",
        "runtimeActivation": {
            "schema": "copilot-extensions.runtime-activation", "version": 1,
            "module": "agent-example/runtime", "path": "scripts/activation.py",
        },
    }))
    (payload / "scripts" / "activation.py").write_text("raise SystemExit(3)\n")
    (payload / "scripts" / "readiness.sh").write_text("#!/bin/sh\n")
    contract = {
        "schema": "copilot-extensions.installer-readiness", "version": 1,
        "owner": {"plugin": "agent-example"}, "state": "supported",
        "modules": [{
            "id": "agent-example/runtime", "platforms": ["linux"],
            "classification": "required",
            "installer": {"linux": {
                "kind": "payload-script", "path": "scripts/install.sh", "arguments": ["update"],
            }},
            "readiness": {
                "schema": "copilot-extensions.module-readiness", "version": 1,
                "configurationEmpty": "satisfied",
                "invocations": {"linux": {
                    "kind": "payload-script", "path": "scripts/readiness.sh",
                }},
            },
            "dependsOn": [], "restart": "none",
        }],
    }
    (payload / "installer-readiness.json").write_text(json.dumps(contract))
    observation = {
        "schema": "copilot-extensions.module-readiness", "version": 1,
        "module": "agent-example/runtime", "state": "configuration-empty",
    }
    probes = []

    def probe(argv, **kwargs):
        probes.append((argv, kwargs))
        code = 2 if observation["state"] == "configuration-empty" else 0
        return subprocess.CompletedProcess(argv, code, json.dumps(observation), "")

    monkeypatch.setattr(readiness, "run_caller_owned", probe)
    return payload, deployed, contract, observation, probes


@pytest.mark.parametrize("retained", [None, "1.0.0"])
@pytest.mark.parametrize("kind", ["registered", "module"])
def test_inactive_runtime_skips_without_certifying_or_replacing_receipt(
    inactive_runtime, monkeypatch, retained, kind,
):
    from agent_worktrees import update_runtime_plan as plan

    payload, deployed, _, _, probes = inactive_runtime
    deployed["version"] = retained
    monkeypatch.setattr(runtime, "_run_human_installer",
                        lambda *a, **k: pytest.fail("inactive runtime installed"))
    if kind == "registered":
        step = plan.plan_registered("agent-example", "linux", force=True, gate_context=payload.parent)
    else:
        step = plan.plan_module("agent-example", payload, "linux", force=True,
                                gate_context=payload.parent)
    assert step.status == "SKIPPED (configuration-empty)"
    assert not step.required
    assert runtime._execute_runtime_step(step) == step.status
    assert deployed["version"] == retained
    assert len(probes) == 2
    assert all(probe[1]["cwd"] == payload.parent for probe in probes)
    assert all(probe[1]["env"]["PYTHONDONTWRITEBYTECODE"] == "1" for probe in probes)


@pytest.mark.parametrize("state", ["configured", "not-ready", "failed"])
def test_active_or_unready_runtime_still_requires_current_receipt(inactive_runtime, state):
    _, _, _, observation, _ = inactive_runtime
    observation["state"] = state
    status = runtime._reconcile_one_runtime("agent-example", "linux", force=True)
    assert status.startswith(
        "runtime not at target" if state == "configured" else "runtime readiness invalid",
    )


@pytest.mark.parametrize("mutation", ["identity", "schema", "unsatisfied", "duplicate"])
def test_empty_readiness_cannot_bypass_its_contract(inactive_runtime, mutation):
    payload, _, contract, observation, _ = inactive_runtime
    if mutation == "identity":
        observation["module"] = "agent-other/runtime"
    elif mutation == "schema":
        observation["version"] = True
    elif mutation == "duplicate":
        contract["modules"].append(contract["modules"][0])
        (payload / "installer-readiness.json").write_text(json.dumps(contract))
    else:
        contract["modules"][0]["readiness"]["configurationEmpty"] = "unsatisfied"
        (payload / "installer-readiness.json").write_text(json.dumps(contract))
    status = runtime._reconcile_one_runtime("agent-example", "linux", force=True)
    assert status.startswith(
        "runtime not at target" if mutation == "unsatisfied" else "runtime readiness invalid",
    )


def test_inactive_preflight_cannot_hide_later_activation(inactive_runtime, monkeypatch):
    from agent_worktrees import update_runtime_plan as plan

    _, _, _, observation, _ = inactive_runtime
    step = plan.plan_registered("agent-example", "linux", force=True)
    observation["state"] = "configured"
    monkeypatch.setattr(runtime, "_run_human_installer",
                        lambda *a, **k: pytest.fail("unplanned runtime installed"))
    assert runtime._execute_runtime_step(step) == (
        "runtime configuration changed since preflight; repeat update"
    )


@pytest.mark.parametrize("primary_blocked", [False, True])
@pytest.mark.parametrize("entrypoint", ["portfolio", "target-registered", "target-module"])
def test_shared_runtime_selects_configured_repository(
    inactive_runtime, monkeypatch, primary_blocked, entrypoint,
):
    from agent_worktrees import update_runtime_plan as plan
    from agent_worktrees import update_runtime_readiness as readiness

    payload, _, _, observation, _ = inactive_runtime
    second = payload.parent / "second-project"
    second.mkdir()
    monkeypatch.setattr(runtime, "_machine_gate_blocks",
                        lambda name, scope, context: primary_blocked and context == payload.parent)
    probes, installs = [], []

    def probe(argv, **kwargs):
        project = kwargs["cwd"]
        probes.append(project)
        value = {**observation, "state": "configured" if project == second else "configuration-empty"}
        return subprocess.CompletedProcess(argv, 0 if project == second else 2, json.dumps(value), "")

    monkeypatch.setattr(readiness, "run_caller_owned", probe)
    monkeypatch.setattr(runtime, "_run_human_installer", lambda *a, **k: (
        installs.append(k["env"]["COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT"])
        or SimpleNamespace(status="OK")
    ))
    targets = {"agent-example": runtime._RegisteredPluginTarget(
        payload.parent, runtime._PluginActivation.ACTIVE, (payload.parent, second),
    )}
    if entrypoint == "portfolio":
        _, modules, registered = plan.plan_portfolio(
            payload.parent / "agent-worktrees", "linux", None, force=True, targets=targets,
            force_runtimes=(), selected_plugins=("agent-example",),
        )
        step, = (*modules, *registered)
    else:
        from agent_worktrees import installation_updates

        engine = payload.parent / "agent-worktrees"
        monkeypatch.setattr(installation_updates.config, "detect_platform", lambda: "linux")
        monkeypatch.setattr(reconcile, "core_installed_payload_dir",
                            lambda name: engine if name == "agent-worktrees" else payload)
        if entrypoint == "target-registered":
            (engine / "modules.json").unlink()
        step = installation_updates.target_plan("agent-example", targets["agent-example"])
    assert step.project_root == second and not step.status
    assert step.repository_contexts == (payload.parent, second)
    assert runtime._execute_runtime_step(step) == "OK"
    assert installs == [str(second)]
    assert (payload.parent in probes) is not primary_blocked
    assert second in probes


@pytest.mark.parametrize("context_count", [1, 2])
def test_execution_preserves_refreshed_machine_gate_skip(inactive_runtime, monkeypatch, context_count):
    from agent_worktrees import update_runtime_plan as plan

    payload, _, _, observation, _ = inactive_runtime
    observation["state"] = "configured"
    second = payload.parent / "second-project"
    second.mkdir()
    monkeypatch.setattr(runtime, "_machine_gate_blocks", lambda *a: False)
    step = plan.plan_registered(
        "agent-example", "linux", force=True, gate_context=payload.parent,
        repository_contexts=(payload.parent, second)[:context_count],
    )
    assert not step.status
    monkeypatch.setattr(runtime, "_machine_gate_blocks", lambda *a: True)
    monkeypatch.setattr(runtime, "_run_human_installer",
                        lambda *a, **k: pytest.fail("machine-excluded runtime installed"))
    assert runtime._execute_runtime_step(step) == runtime.MACHINE_GATED_SKIP


def test_changed_prepared_source_is_rejected_before_inactive_probe(inactive_runtime, monkeypatch):
    import dataclasses
    from agent_worktrees import update_runtime_plan as plan
    from agent_worktrees import update_runtime_readiness as readiness

    step = plan.plan_registered("agent-example", "linux", force=True)

    def changed(*args):
        raise ValueError("prepared content changed")

    step = dataclasses.replace(step, source=SimpleNamespace(validate=changed))
    monkeypatch.setattr(readiness, "run_caller_owned",
                        lambda *a, **k: pytest.fail("unvalidated payload executed"))
    assert runtime._execute_runtime_step(step) == (
        "prepared source execution refused: prepared content changed"
    )


@pytest.mark.parametrize("platform", ["linux", "windows", "macos"])
def test_activation_probe_uses_owned_python_on_every_platform(inactive_runtime, platform):
    from agent_worktrees import update_runtime_plan as plan
    from agent_worktrees import update_runtime_readiness as readiness

    payload, _, _, _, probes = inactive_runtime
    step = plan.plan_registered("agent-example", platform, force=True, gate_context=payload.parent)
    assert step.status == readiness.INACTIVE
    assert probes[0][0] == [
        sys.executable, "-I", "-B", str(payload / "scripts" / "activation.py"),
        "--cwd", str(payload.parent), "--runtime-root", str(payload.parent / "runtime"),
        "--context", "",
    ]


def test_inactive_activation_uses_real_payload_script_in_project_context(
    inactive_runtime, monkeypatch,
):
    from agent_procutil.owned_process import run_caller_owned
    from agent_worktrees import update_runtime_readiness as readiness

    payload, deployed, _, observation, _ = inactive_runtime
    script = payload / "scripts" / "activation.py"
    script.write_text(
        "import os, sys\n"
        "assert os.getcwd() == sys.argv[sys.argv.index('--cwd') + 1]\n"
        f"print({json.dumps(observation)!r})\n"
        "raise SystemExit(2)\n",
    )
    monkeypatch.setattr(readiness, "run_caller_owned", run_caller_owned)
    monkeypatch.setattr(runtime, "_run_human_installer",
                        lambda *a, **k: pytest.fail("inactive runtime installed"))
    assert runtime._reconcile_one_runtime(
        "agent-example", "linux", force=True, gate_context=payload.parent,
    ) == readiness.INACTIVE
    assert deployed["version"] == "1.0.0"


@pytest.mark.parametrize("name", ["agent-vault", "agent-containers"])
def test_service_configuration_empty_without_participation_contract_never_skips(
    inactive_runtime, monkeypatch, name,
):
    from agent_worktrees import update_runtime_readiness as readiness

    payload, _, _, _, _ = inactive_runtime
    manifest = json.loads((payload / "plugin.json").read_text())
    del manifest["runtimeActivation"]
    manifest["name"] = name
    (payload / "plugin.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(readiness, "run_caller_owned",
                        lambda *a, **k: pytest.fail("general service readiness is not activation"))
    assert runtime._reconcile_one_runtime(name, "linux", force=True).startswith("runtime not at target")


def test_legacy_owner_selected_repository_is_not_replaced_by_an_allowed_peer(
    inactive_runtime, monkeypatch,
):
    from agent_worktrees.update_runtime_plan import RuntimeUpdateStep

    payload, _, _, _, _ = inactive_runtime
    manifest = json.loads((payload / "plugin.json").read_text())
    del manifest["runtimeActivation"]
    (payload / "plugin.json").write_text(json.dumps(manifest))
    selected, peer = payload.parent / "host-project", payload.parent / "client-project"
    step = RuntimeUpdateStep(
        "agent-example", "runtime_reconcile", payload,
        environment={"COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT": str(selected)},
        runtime_root=payload.parent / "runtime", project_root=selected, gate_context=selected,
        repository_contexts=(selected, peer), readiness_platform="linux", argv=("installer",),
    )
    observed = []
    monkeypatch.setattr(runtime, "_machine_gate_blocks",
                        lambda name, scope, context: observed.append(context) or context == selected)
    monkeypatch.setattr(runtime, "_run_human_installer",
                        lambda *a, **k: pytest.fail("owner-selected role was replaced"))
    assert runtime._execute_runtime_step(step) == runtime.MACHINE_GATED_SKIP
    assert observed == [selected]


@pytest.mark.parametrize("code,state", [(0, "configuration-empty"), (2, "configured"), (3, "failed")])
def test_activation_exit_and_decision_must_agree(inactive_runtime, monkeypatch, code, state):
    from agent_worktrees import update_runtime_readiness as readiness

    _, _, _, observation, _ = inactive_runtime
    observation["state"] = state
    monkeypatch.setattr(readiness, "run_caller_owned", lambda argv, **k: (
        subprocess.CompletedProcess(argv, code, json.dumps(observation), "")
    ))
    assert runtime._reconcile_one_runtime(
        "agent-example", "linux", force=True,
    ).startswith("runtime readiness invalid")


@pytest.mark.parametrize("mutation", ["null", "unsupported", "escape"])
def test_invalid_activation_declaration_never_executes(inactive_runtime, monkeypatch, mutation):
    from agent_worktrees import update_runtime_readiness as readiness

    payload, _, _, _, _ = inactive_runtime
    manifest = json.loads((payload / "plugin.json").read_text())
    if mutation == "null":
        manifest["runtimeActivation"] = None
    elif mutation == "unsupported":
        manifest["runtimeActivation"]["version"] = 2
    else:
        manifest["runtimeActivation"]["path"] = "../activation.py"
    (payload / "plugin.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(readiness, "run_caller_owned",
                        lambda *a, **k: pytest.fail("invalid reader executed"))
    assert runtime._reconcile_one_runtime(
        "agent-example", "linux", force=True,
    ).startswith("runtime readiness invalid")


@pytest.mark.parametrize("enabled", [False, True])
def test_updater_consumes_actual_vault_participation_without_database(tmp_path, enabled):
    from agent_worktrees.update_runtime_plan import RuntimeUpdateStep
    from agent_worktrees.update_runtime_readiness import configuration_empty

    home, project = tmp_path / "home", tmp_path / "project"
    home.mkdir()
    project.mkdir()
    (project / ".agent-vault.json").write_text(json.dumps({"enabled": enabled}))
    payload = Path(__file__).resolve().parents[2] / "agent-vault"
    environment = {
        key: value for key, value in os.environ.items()
        if key in {"PATH", "SystemRoot", "WINDIR", "COMSPEC", "PATHEXT", "SYSTEMDRIVE"}
    }
    environment.update(
        HOME=str(home), USERPROFILE=str(home), APPDATA=str(home / "appdata"),
        XDG_CONFIG_HOME=str(home / "config"), XDG_STATE_HOME=str(home / "state"),
        COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT=str(project),
    )
    root = home / ".agent-vault"
    step = RuntimeUpdateStep(
        "agent-vault", "runtime_reconcile", payload,
        environment=environment, runtime_root=root, project_root=project,
    )
    assert configuration_empty(step, "windows" if os.name == "nt" else "linux") is not enabled
    assert not root.exists()


def test_module_update_fails_an_exit_0_that_left_the_old_receipt(example):
    plugin, _deployed = example
    targets = {"agent-example": runtime._RegisteredPluginTarget(
        context=None, activation=runtime._PluginActivation.ACTIVE,
    )}
    assert not runtime._update_modules(plugin, "linux", None, force=True, targets=targets)


def test_engine_installer_fails_an_exit_0_that_left_the_old_receipt(
    example, tmp_path, monkeypatch,
):
    plugin, _deployed = example
    errors: list[str] = []
    monkeypatch.setattr(runtime.output, "err", errors.append)
    rc = runtime._run_engine_installer(
        ["bash", "install.sh", "update"], plugin, 60, dict(os.environ),
        runtime_root=tmp_path / "aw-runtime",
    )
    assert rc == 1
    # The installer's own output looked successful, so the reason must be shown.
    assert errors == ["agent-worktrees installer failed: runtime not at target after a "
                      "successful installer exit (deployed 1.0.0, expected 2.0.0)"]


@pytest.mark.parametrize("project_context", [False, True])
def test_engine_installer_preserves_its_project_only_marketplace_source(
    example, tmp_path, monkeypatch, project_context,
):
    plugin, _deployed = example
    project = tmp_path / "owning-project"
    project.mkdir()
    monkeypatch.setattr(
        runtime, "_invocation_update_context", lambda: project if project_context else None,
    )
    observed = []
    monkeypatch.setattr(runtime, "_run_human_installer", lambda argv, **kwargs: (
        observed.append(kwargs["cwd"]) or SimpleNamespace(status="OK", rc=0)
    ))
    assert runtime._run_engine_installer(["installer"], plugin, 60, {}) == 0
    assert observed == [project if project_context else plugin]
