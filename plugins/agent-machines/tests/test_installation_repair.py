from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
from agent_procutil import no_window_kwargs

from agent_machines import installation_repair as repair_api
from agent_machines import installation_repair_admission as admission_api
from agent_machines.installation_repair_admission import (
    RepairAdmissionError, admit_host, ordinary_environment,
)
from agent_machines.installation_repair_process import run_action

pytestmark = pytest.mark.guard


@pytest.mark.parametrize("containerized,expected", [(True, "linux"), (False, "wsl")])
def test_installation_repair_platform_uses_userspace_not_container_host_kernel(
    monkeypatch, containerized, expected,
):
    monkeypatch.setattr(repair_api.sys, "platform", "linux")
    monkeypatch.setattr(repair_api.host_platform, "release", lambda: "6.6-microsoft-standard-WSL2")
    monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
    monkeypatch.delenv("container", raising=False)
    monkeypatch.setattr(Path, "is_file", lambda path: containerized)
    assert repair_api.current_platform().value == expected


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


@pytest.fixture
def repair_fixture(tmp_path):
    home, project = tmp_path / "home", tmp_path / "project"
    home.mkdir()
    project.mkdir()
    market = project / ".ai"
    payload = market / "agent-example"
    payload.mkdir(parents=True)
    write_json(market / ".claude-plugin" / "marketplace.json", {
        "name": "fixture",
        "plugins": [{"name": "agent-example", "source": "./agent-example"}],
    })
    write_json(project / ".github" / "copilot" / "settings.json", {
        "extraKnownMarketplaces": {
            "fixture": {"source": {"source": "directory", "path": "./.ai"}},
        },
        "enabledPlugins": {"agent-example@fixture": True},
    })
    write_json(payload / "plugin.json", {
        "name": "agent-example", "version": "1.0.0",
        "installerReadiness": "installer-readiness.json",
    })
    write_json(payload / "payload-invocation.json", {
        "schema": "copilot-extensions.payload-invocation", "version": 2,
        "command": "agent-example", "module": "agent_example",
        "legacyRuntimeRoot": ".agent-example", "installationContext": "required",
        "noSelfProvisionEnv": "AGENT_EXAMPLE_NO_SELFPROVISION",
        "payloadRootEnv": "AGENT_EXAMPLE_PAYLOAD_ROOT", "purpose": "Fixture runtime",
    })
    scripts = payload / "scripts"
    scripts.mkdir()
    # Actual native owner actions: readiness may read but never create its state.
    (scripts / "action.ps1").write_text(
        "param([string]$Action, [string]$Module)\n"
        "$state = Join-Path $env:HOME ($Module.Replace('/', '-') + '.ready')\n"
        "if ($Action -eq 'install') {\n"
        "  if ($env:AGENT_EXAMPLE_NO_SELFPROVISION) { exit 8 }\n"
        "  [IO.File]::WriteAllText($state, 'ready'); exit 0\n"
        "}\n"
        "if ($env:AGENT_EXAMPLE_NO_SELFPROVISION -ne '1') { exit 9 }\n"
        "$value = if (Test-Path -LiteralPath $state) { 'ready' } else { 'not-ready' }\n"
        "@{schema='copilot-extensions.module-readiness'; version=1; "
        "module=$Module; state=$value; detail='fixture-private-detail'} "
        "| ConvertTo-Json -Compress\n",
        encoding="utf-8",
    )
    (scripts / "action.sh").write_text(
        '#!/usr/bin/env bash\nset -eu\n'
        'state="$HOME/${2//\\//-}.ready"\n'
        'if [ "$1" = install ]; then\n'
        '  [ -z "${AGENT_EXAMPLE_NO_SELFPROVISION:-}" ] || exit 8\n'
        '  printf ready > "$state"; exit 0\nfi\n'
        '[ "${AGENT_EXAMPLE_NO_SELFPROVISION:-}" = 1 ] || exit 9\n'
        'value=not-ready; [ ! -f "$state" ] || value=ready\n'
        'printf \'{"schema":"copilot-extensions.module-readiness","version":1,'
        '"module":"%s","state":"%s","detail":"fixture-private-detail"}\\n\' "$2" "$value"\n',
        encoding="utf-8",
    )

    def configure(*, restart="none", classification="required", dependencies=()):
        modules = []
        for name, deps in (("base", ()), ("child", dependencies)):
            local_id = "agent-example/" + name
            invocations = {}
            installers = {}
            for platform in ("windows", "linux", "wsl", "macos"):
                target = "scripts/action." + ("ps1" if platform == "windows" else "sh")
                invocations[platform] = {
                    "kind": "payload-script", "path": target,
                    "arguments": ["probe", local_id],
                }
                installers[platform] = {
                    "kind": "payload-script", "path": target,
                    "arguments": ["install", local_id],
                }
            modules.append({
                "id": local_id, "platforms": list(invocations),
                "classification": classification if name == "base" else "required",
                "installer": installers, "readiness": {
                    "schema": "copilot-extensions.module-readiness", "version": 1,
                    "configurationEmpty": "satisfied", "invocations": invocations,
                },
                "dependsOn": list(deps), "restart": restart if name == "base" else "none",
            })
        write_json(payload / "installer-readiness.json", {
            "schema": "copilot-extensions.installer-readiness", "version": 1,
            "owner": {"plugin": "agent-example"}, "state": "supported", "modules": modules,
        })

    configure(dependencies=("agent-example/base",))
    host = admit_host(project, home=home)
    assert not host.refused
    assert not host.report.findings
    identity = next(iter(host.bindings.values())).installation.provenance.marketplace_id
    return home, project, payload, configure, identity


def invoke(fixture, *, apply=False, digest=None, selection=("child",)):
    home, project, _, _, identity = fixture
    return repair_api.repair(
        project, home=home,
        modules=[identity + "::agent-example/" + name for name in selection],
        apply=apply, expected_plan_digest=digest,
    )


def test_installation_repair_discovery_and_plan_never_install(repair_fixture):
    home, project, _, _, _ = repair_fixture
    discovery = repair_api.repair(project, home=home)
    assert discovery["status"] == "discovered"
    assert discovery["outcomes"] == []
    plan = invoke(repair_fixture)
    assert plan["status"] == "observed"
    assert [row["observedBefore"]["state"] for row in plan["outcomes"]] == [
        "not-ready", "not-ready",
    ]
    assert not list(home.glob("*.ready"))
    assert not (home / ".agent-example").exists()
    assert "fixture-private-detail" not in json.dumps(plan)


@pytest.mark.timeout(60 if os.name == "nt" else 30)
def test_installation_repair_native_apply_freshly_probes_dependencies(repair_fixture):
    plan = invoke(repair_fixture)
    result = invoke(repair_fixture, apply=True, digest=plan["planDigest"])
    assert result["status"] == "ready"
    assert result["requiredSatisfied"]
    assert [row["attempted"] for row in result["outcomes"]] == [True, True]
    assert [row["observedAfter"]["state"] for row in result["outcomes"]] == ["ready", "ready"]
    again = invoke(repair_fixture, apply=True, digest=result["planDigest"])
    assert again["status"] == "ready"
    assert not any(row["attempted"] for row in again["outcomes"])


@pytest.mark.parametrize("initial_ready,prerequisite_makes_ready", [
    (True, False), (False, True), (True, True),
])
def test_installation_repair_refreshes_dependent_after_native_prerequisite(
    repair_fixture, initial_ready, prerequisite_makes_ready,
):
    home, _, payload, _, _ = repair_fixture
    child_state = home / "agent-example-child.ready"
    if initial_ready:
        child_state.write_text("ready", encoding="utf-8")
    script = payload / "scripts" / "action.ps1"
    child_action = (
        "[IO.File]::WriteAllText($child, 'ready')"
        if prerequisite_makes_ready else
        "if (Test-Path -LiteralPath $child) { Remove-Item -LiteralPath $child }"
    )
    script.write_text(script.read_text(encoding="utf-8").replace(
        "  [IO.File]::WriteAllText($state, 'ready'); exit 0",
        "  if ($Module -eq 'agent-example/base') {\n"
        "    $child = Join-Path $env:HOME 'agent-example-child.ready'\n"
        f"    {child_action}\n"
        "  }\n"
        "  [IO.File]::WriteAllText($state, 'ready'); exit 0",
    ), encoding="utf-8")
    script = payload / "scripts" / "action.sh"
    child_action = (
        'printf ready > "$HOME/agent-example-child.ready"'
        if prerequisite_makes_ready else 'rm -f "$HOME/agent-example-child.ready"'
    )
    script.write_text(script.read_text(encoding="utf-8").replace(
        '  printf ready > "$state"; exit 0',
        '  if [ "$2" = agent-example/base ]; then\n'
        f"    {child_action}\n"
        "  fi\n"
        '  printf ready > "$state"; exit 0',
    ), encoding="utf-8")
    plan = invoke(repair_fixture)
    result = invoke(repair_fixture, apply=True, digest=plan["planDigest"])
    base, child = result["outcomes"]
    assert result["status"] == "ready" and result["requiredSatisfied"]
    assert base["attempted"]
    assert child["observedBefore"]["state"] == ("ready" if initial_ready else "not-ready")
    assert child["attempted"] is not prerequisite_makes_ready
    assert child["observedAfter"]["state"] == "ready"
    assert child_state.read_text(encoding="utf-8") == "ready"


@pytest.mark.parametrize("stage", ["admission", "closure", "hashing", "readiness"])
@pytest.mark.parametrize("boundary", ["post-install", "dependent"])
@pytest.mark.timeout(60 if os.name == "nt" else 30)
def test_installation_repair_revalidation_cancellation_retains_completed_outcomes(
    repair_fixture, monkeypatch, stage, boundary,
):
    home, _, _, _, _ = repair_fixture
    target = {
        "admission": "admit_host", "closure": "_closure",
        "hashing": "_binding_inputs", "readiness": "_invoke",
    }[stage]
    original = getattr(repair_api, target)
    passed_post_install = False

    def interrupted(*args, **kwargs):
        nonlocal passed_post_install
        if (home / "agent-example-base.ready").exists() and (
            stage != "readiness" or (
                kwargs["readonly"] and args[0].module_id == (
                    "agent-example/base" if boundary == "post-install" else "agent-example/child"
                )
            )
        ):
            if boundary == "dependent" and stage != "readiness" and not passed_post_install:
                passed_post_install = True
            else:
                raise KeyboardInterrupt
        return original(*args, **kwargs)

    monkeypatch.setattr(repair_api, target, interrupted)
    plan = invoke(repair_fixture)
    result = invoke(repair_fixture, apply=True, digest=plan["planDigest"])
    base, child = result["outcomes"]
    assert result["interrupted"] and result["status"] == "incomplete"
    assert not result["requiredSatisfied"]
    assert base["attempted"] and base["installer"] == {"status": "exited", "exitCode": 0}
    assert not child["attempted"] and child["installer"] is None
    assert child["observedBefore"]["state"] == "not-ready"
    cancelled = base if boundary == "post-install" else child
    assert cancelled["observedAfter"] == {
        "state": "unknown",
        "reason": "readiness-interrupted" if stage == "readiness"
        else "owner-revalidation-interrupted",
    }
    if boundary == "post-install":
        assert child["observedAfter"] is None
        assert child["blockedBy"] == [base["module"]]
    else:
        assert base["observedAfter"]["state"] == "ready"
    assert not (home / "agent-example-child.ready").exists()


@pytest.mark.parametrize("selection,changed_module", [
    (("base",), "base"), (("child",), "child"), (("child",), "base"),
])
@pytest.mark.timeout(60 if os.name == "nt" else 30)
def test_installation_repair_refuses_binding_changed_during_post_install_readiness(
    repair_fixture, monkeypatch, selection, changed_module,
):
    home, _, payload, _, _ = repair_fixture
    original = repair_api._invoke
    final_module = "agent-example/" + changed_module

    def changing(module, owner, platform, *, readonly, timeout):
        result = original(module, owner, platform, readonly=readonly, timeout=timeout)
        if (
            readonly and module.module_id == final_module
            and (home / (module.module_id.replace("/", "-") + ".ready")).exists()
        ):
            manifest = payload / "plugin.json"
            value = json.loads(manifest.read_text(encoding="utf-8"))
            value["version"] = "1.0.1"
            write_json(manifest, value)
        return result

    monkeypatch.setattr(repair_api, "_invoke", changing)
    plan = invoke(repair_fixture, selection=selection)
    result = invoke(repair_fixture, apply=True, digest=plan["planDigest"], selection=selection)
    assert result["status"] == "incomplete" and not result["requiredSatisfied"]
    assert not result["interrupted"]
    outcome = next(row for row in result["outcomes"] if row["module"].endswith(final_module))
    assert outcome["attempted"]
    assert outcome["installer"] == {"status": "exited", "exitCode": 0}
    assert outcome["observedAfter"] == {"state": "unknown", "reason": "owner-binding-changed"}
    assert (home / (final_module.replace("/", "-") + ".ready")).exists()
    if selection == ("child",) and changed_module == "base":
        child = result["outcomes"][-1]
        assert not child["attempted"] and child["installer"] is None
        assert child["blockedBy"] == [outcome["module"]]
        assert not (home / "agent-example-child.ready").exists()


def test_installation_repair_admits_manifest_declared_readiness_path(repair_fixture):
    _, _, payload, _, _ = repair_fixture
    declaration = payload / "contracts" / "readiness.json"
    declaration.parent.mkdir()
    (payload / "installer-readiness.json").rename(declaration)
    manifest = payload / "plugin.json"
    value = json.loads(manifest.read_text(encoding="utf-8"))
    value["installerReadiness"] = "contracts/readiness.json"
    write_json(manifest, value)
    plan = invoke(repair_fixture)
    assert all(row["observedBefore"]["state"] == "not-ready" for row in plan["outcomes"])
    result = invoke(repair_fixture, apply=True, digest=plan["planDigest"])
    assert result["status"] == "ready"
    assert all(row["attempted"] for row in result["outcomes"])


@pytest.mark.parametrize("probe_ready", [False, True])
def test_installation_repair_refuses_binding_changed_during_refreshed_readiness(
    repair_fixture, monkeypatch, probe_ready,
):
    home, _, payload, _, _ = repair_fixture
    original = repair_api._invoke

    def changing(module, owner, platform, *, readonly, timeout):
        refreshed = (
            readonly and module.module_id == "agent-example/child"
            and (home / "agent-example-base.ready").exists()
        )
        if refreshed and probe_ready:
            (home / "agent-example-child.ready").write_text("ready", encoding="utf-8")
        result = original(module, owner, platform, readonly=readonly, timeout=timeout)
        if refreshed:
            manifest = payload / "plugin.json"
            value = json.loads(manifest.read_text(encoding="utf-8"))
            value["version"] = "1.0.1"
            write_json(manifest, value)
        return result

    monkeypatch.setattr(repair_api, "_invoke", changing)
    plan = invoke(repair_fixture)
    result = invoke(repair_fixture, apply=True, digest=plan["planDigest"])
    base, child = result["outcomes"]
    assert result["status"] == "incomplete" and not result["requiredSatisfied"]
    assert base["attempted"] and base["observedAfter"]["state"] == "ready"
    assert not child["attempted"]
    assert child["installer"] == {"status": "refused", "reason": "owner-binding-changed"}
    assert child["observedAfter"]["state"] == ("ready" if probe_ready else "not-ready")
    assert (home / "agent-example-child.ready").exists() is probe_ready


@pytest.mark.skipif(os.name != "nt", reason="native Windows execution-policy contract")
def test_installation_repair_powershell_fallback_has_process_scoped_policy(
    repair_fixture, monkeypatch,
):
    original = shutil.which
    if not original("powershell"):
        pytest.skip("Windows PowerShell is unavailable")
    monkeypatch.setattr(
        repair_api.shutil, "which", lambda name: None if name == "pwsh" else original(name),
    )
    monkeypatch.setenv("PSExecutionPolicyPreference", "Restricted")
    plan = invoke(repair_fixture)
    assert all(row["observedBefore"]["state"] == "not-ready" for row in plan["outcomes"])
    result = invoke(repair_fixture, apply=True, digest=plan["planDigest"])
    assert result["status"] == "ready"
    assert all(row["observedAfter"]["state"] == "ready" for row in result["outcomes"])


@pytest.mark.parametrize("failure", ["installer", "restart"])
def test_installation_repair_ready_middle_cannot_hide_transitive_blocker(
    repair_fixture, monkeypatch, failure,
):
    from agent_machines.installation_repair_process import ActionResult

    home, _, payload, _, _ = repair_fixture
    declaration = payload / "installer-readiness.json"
    value = json.loads(declaration.read_text(encoding="utf-8"))
    if failure == "restart":
        value["modules"][0]["restart"] = "session"
    leaf = json.loads(json.dumps(value["modules"][1]))
    leaf["id"] = "agent-example/leaf"
    leaf["dependsOn"] = ["agent-example/child"]
    for action in leaf["installer"].values():
        action["arguments"][-1] = leaf["id"]
    for action in leaf["readiness"]["invocations"].values():
        action["arguments"][-1] = leaf["id"]
    value["modules"].append(leaf)
    write_json(declaration, value)
    original = repair_api._invoke

    def invoke_owner(module, owner, platform, *, readonly, timeout):
        if readonly and module.module_id == "agent-example/child":
            return ActionResult("exited", 0, json.dumps({
                "schema": "copilot-extensions.module-readiness", "version": 1,
                "module": module.module_id, "state": "ready",
            }).encode())
        result = original(module, owner, platform, readonly=readonly, timeout=timeout)
        if failure == "installer" and not readonly and module.module_id == "agent-example/base":
            return ActionResult("exited", 7)
        return result

    monkeypatch.setattr(repair_api, "_invoke", invoke_owner)
    plan = invoke(repair_fixture, selection=("leaf",))
    result = invoke(
        repair_fixture, selection=("leaf",), apply=True, digest=plan["planDigest"],
    )
    base, middle, leaf_outcome = result["outcomes"]
    assert result["status"] == "incomplete"
    assert middle["observedBefore"]["state"] == "ready"
    assert middle["blockedBy"] == [base["module"]]
    assert leaf_outcome["blockedBy"] == [middle["module"]]
    assert not middle["attempted"] and not leaf_outcome["attempted"]
    assert not (home / "agent-example-leaf.ready").exists()


def test_installation_repair_stale_digest_refuses_before_execution(repair_fixture):
    plan = invoke(repair_fixture)
    home, _, payload, _, _ = repair_fixture
    (payload / "scripts" / "action.ps1").write_text("# changed\n", encoding="utf-8")
    with pytest.raises(RepairAdmissionError, match="inputs changed"):
        invoke(repair_fixture, apply=True, digest=plan["planDigest"])
    assert not list(home.glob("*.ready"))


@pytest.mark.parametrize("modules,digest", [((), None), (("child",), None), ((), "a" * 64)])
def test_installation_repair_apply_requires_explicit_bound_selection(repair_fixture, modules, digest):
    with pytest.raises(RepairAdmissionError, match="requires qualified modules"):
        invoke(repair_fixture, apply=True, digest=digest, selection=modules)


def test_installation_repair_restart_blocks_actual_dependents(repair_fixture):
    _, _, _, configure, _ = repair_fixture
    configure(restart="session", dependencies=("agent-example/base",))
    plan = invoke(repair_fixture)
    result = invoke(repair_fixture, apply=True, digest=plan["planDigest"])
    assert result["status"] == "incomplete"
    base, child = result["outcomes"]
    assert base["observedAfter"]["state"] == "ready"
    assert base["restartRequired"]
    assert not child["attempted"]
    assert child["blockedBy"] == [base["module"]]


@pytest.mark.parametrize("restart", ["shell", "session", "machine"])
@pytest.mark.timeout(60 if os.name == "nt" else 30)
def test_installation_repair_restart_survives_repeated_apply_until_exact_confirmation(
    repair_fixture, restart,
):
    home, project, _, configure, _ = repair_fixture
    configure(restart=restart, dependencies=("agent-example/base",))
    plan = invoke(repair_fixture)
    first = invoke(repair_fixture, apply=True, digest=plan["planDigest"])
    again = invoke(repair_fixture, apply=True, digest=plan["planDigest"])
    base, child = again["outcomes"]
    assert again["status"] == "incomplete" and not again["requiredSatisfied"]
    assert first["restarts"] == again["restarts"]
    assert first["outcomes"][0]["attempted"] and base["restartRequired"]
    assert not base["attempted"] and not child["attempted"]
    assert child["blockedBy"] == [base["module"]]
    assert not (home / "agent-example-child.ready").exists()
    with pytest.raises(RepairAdmissionError, match="batch changed"):
        repair_api.repair(project, home=home, confirm_restart="0" * 64)
    observed = invoke(repair_fixture)
    assert observed["restarts"] == first["restarts"]
    confirmed = repair_api.repair(
        project, home=home, confirm_restart=first["restarts"]["pendingDigest"],
    )
    assert confirmed["status"] == "restart-confirmed"
    assert confirmed["confirmation"] == "operator-attestation"
    assert confirmed["restarts"]["pending"] == []
    final = invoke(repair_fixture, apply=True, digest=plan["planDigest"])
    assert final["status"] == "ready" and final["requiredSatisfied"]
    assert not final["outcomes"][0]["attempted"]
    assert final["outcomes"][1]["attempted"]


@pytest.mark.timeout(60 if os.name == "nt" else 30)
def test_installation_repair_accumulates_independent_restart_steps(repair_fixture):
    _, _, payload, configure, identity = repair_fixture
    configure(restart="session", dependencies=())
    declaration = payload / "installer-readiness.json"
    value = json.loads(declaration.read_text(encoding="utf-8"))
    value["modules"][1]["restart"] = "machine"
    write_json(declaration, value)
    plan = invoke(repair_fixture, selection=("base", "child"))
    result = invoke(
        repair_fixture, selection=("base", "child"), apply=True, digest=plan["planDigest"],
    )
    assert result["status"] == "incomplete"
    assert all(row["attempted"] and row["restartRequired"] for row in result["outcomes"])
    pending = result["restarts"]["pending"]
    assert [(row["module"], row["restart"]) for row in pending] == [
        (identity + "::agent-example/base", "session"),
        (identity + "::agent-example/child", "machine"),
    ]
    again = invoke(
        repair_fixture, selection=("base", "child"), apply=True, digest=plan["planDigest"],
    )
    assert not any(row["attempted"] for row in again["outcomes"])
    assert again["restarts"] == result["restarts"]


def test_installation_repair_restart_is_recorded_before_interrupted_action(
    repair_fixture, monkeypatch,
):
    home, _, _, configure, _ = repair_fixture
    configure(restart="session", dependencies=("agent-example/base",))
    original = repair_api._invoke

    def interrupted(module, owner, platform, *, readonly, timeout):
        if not readonly:
            journal = repair_api.RestartJournal(home)
            journal.load()
            assert module.qualified_id in journal.pending
            raise KeyboardInterrupt
        return original(module, owner, platform, readonly=readonly, timeout=timeout)

    monkeypatch.setattr(repair_api, "_invoke", interrupted)
    plan = invoke(repair_fixture)
    result = invoke(repair_fixture, apply=True, digest=plan["planDigest"])
    assert result["interrupted"] and result["outcomes"][0]["restartRequired"]
    assert len(result["restarts"]["pending"]) == 1
    again = invoke(repair_fixture, apply=True, digest=plan["planDigest"])
    assert not any(row["attempted"] for row in again["outcomes"])
    assert again["restarts"] == result["restarts"]


def test_installation_repair_restart_publication_failure_prevents_installer(
    repair_fixture, monkeypatch,
):
    home, _, _, configure, _ = repair_fixture
    configure(restart="session", dependencies=("agent-example/base",))
    plan = invoke(repair_fixture)

    def fail_save(self):
        raise OSError("synthetic publication failure")

    monkeypatch.setattr(repair_api.RestartJournal, "_save", fail_save)
    with pytest.raises(RepairAdmissionError, match="cannot be accessed or persisted"):
        invoke(repair_fixture, apply=True, digest=plan["planDigest"])
    assert not list(home.glob("*.ready"))


def test_installation_repair_failed_installer_cannot_count_ready(repair_fixture, monkeypatch):
    from agent_machines.installation_repair_process import ActionResult

    original = repair_api._invoke

    def failed(module, owner, platform, *, readonly, timeout):
        result = original(module, owner, platform, readonly=readonly, timeout=timeout)
        return result if readonly else ActionResult("exited", 7)

    monkeypatch.setattr(repair_api, "_invoke", failed)
    plan = invoke(repair_fixture)
    result = invoke(repair_fixture, apply=True, digest=plan["planDigest"])
    assert result["status"] == "incomplete"
    base, child = result["outcomes"]
    assert base["observedAfter"]["state"] == "ready"
    assert base["installer"]["exitCode"] == 7
    assert not child["attempted"]
    assert child["blockedBy"] == [base["module"]]


def test_installation_repair_ordinary_environment_drops_inherited_core(tmp_path):
    clean = ordinary_environment({
        "COPILOT_EXTENSIONS_CONTEXT": "foreign", "COPILOT_EXTENSIONS_ACTION": "repair",
        "AGENT_EXAMPLE_CELL_ROOT": "foreign", "COPILOT_PLUGIN_STAGED_FROM": "foreign",
        "COPILOT_EXTENSIONS_TEST_CONTAINED": "1", "PATH": "fixture-path",
    }, home=tmp_path, payload=tmp_path / "payload")
    assert clean == {
        "HOME": str(tmp_path), "USERPROFILE": str(tmp_path),
        "COPILOT_PLUGIN_ROOT": str(tmp_path / "payload"),
        "COPILOT_EXTENSIONS_TEST_CONTAINED": "1", "PATH": "fixture-path",
    }


def test_installation_repair_missing_readonly_protocol_never_runs(repair_fixture):
    home, _, payload, _, _ = repair_fixture
    (payload / "payload-invocation.json").unlink()
    plan = invoke(repair_fixture)
    assert plan["outcomes"][0]["observedBefore"] == {
        "state": "unknown", "reason": "owner-readonly-protocol-not-declared",
    }
    result = invoke(repair_fixture, apply=True, digest=plan["planDigest"])
    assert result["status"] == "incomplete"
    assert not any(row["attempted"] for row in result["outcomes"])
    assert not list(home.glob("*.ready"))


def test_installation_repair_migration_permission_is_not_repair_permission(
    repair_fixture, monkeypatch,
):
    from agent_machines import installation_repair_admission as admission

    real_probe = admission.context.probe_legacy_entrypoint

    def migration(**kwargs):
        result = real_probe(**kwargs)
        result.update(status="migration-required", desiredMode="namespaced", allowMutation=True)
        return result

    monkeypatch.setattr(admission.context, "probe_legacy_entrypoint", migration)
    plan = invoke(repair_fixture)
    assert plan["outcomes"][0]["observedBefore"]["reason"] == (
        "ordinary-owner-mutation-not-admitted"
    )
    result = invoke(repair_fixture, apply=True, digest=plan["planDigest"])
    assert not any(row["attempted"] for row in result["outcomes"])


def test_installation_repair_changed_binding_after_probe_prevents_install(
    repair_fixture, monkeypatch,
):
    from agent_machines.installation_repair_process import ActionResult

    plan = invoke(repair_fixture)
    home, _, payload, _, _ = repair_fixture
    calls = []

    def changed(module, owner, platform, *, readonly, timeout):
        calls.append(readonly)
        assert readonly
        (payload / "plugin.json").write_text(
            (payload / "plugin.json").read_text(encoding="utf-8").replace("1.0.0", "1.0.1"),
            encoding="utf-8",
        )
        return ActionResult("exited", 0, json.dumps({
            "schema": "copilot-extensions.module-readiness", "version": 1,
            "module": module.module_id, "state": "not-ready",
        }).encode())

    monkeypatch.setattr(repair_api, "_invoke", changed)
    result = invoke(repair_fixture, apply=True, digest=plan["planDigest"])
    assert result["outcomes"][0]["installer"]["reason"] == "owner-binding-changed"
    assert not any(row["attempted"] for row in result["outcomes"])
    assert calls == [True, True]
    assert not list(home.glob("*.ready"))


def test_installation_repair_interruption_preserves_partial_outcomes(repair_fixture, monkeypatch):
    from agent_machines.installation_repair_process import ActionResult

    def interrupted(module, owner, platform, *, readonly, timeout):
        if not readonly:
            raise KeyboardInterrupt
        return ActionResult("exited", 0, json.dumps({
            "schema": "copilot-extensions.module-readiness", "version": 1,
            "module": module.module_id, "state": "not-ready",
        }).encode())

    monkeypatch.setattr(repair_api, "_invoke", interrupted)
    plan = invoke(repair_fixture)
    result = invoke(repair_fixture, apply=True, digest=plan["planDigest"])
    assert result["interrupted"]
    assert result["status"] == "incomplete"
    assert result["outcomes"][0]["installer"]["status"] == "interrupted"
    assert not result["outcomes"][1]["attempted"]


@pytest.mark.parametrize("output,state", [
    (b"not-json", "unknown"),
    (b'{"schema":"copilot-extensions.module-readiness","version":1,'
     b'"module":"foreign/runtime","state":"ready"}', "unknown"),
])
def test_installation_repair_invalid_probe_is_not_dependency_readiness(
    repair_fixture, monkeypatch, output, state,
):
    from agent_machines.installation_repair_process import ActionResult

    monkeypatch.setattr(
        repair_api, "_invoke",
        lambda *args, **kwargs: ActionResult("exited", 0, output),
    )
    plan = invoke(repair_fixture)
    result = invoke(repair_fixture, apply=True, digest=plan["planDigest"])
    base, child = result["outcomes"]
    assert base["observedAfter"]["state"] == state
    assert child["blockedBy"] == [base["module"]]
    assert not child["attempted"]
    assert result["status"] == "incomplete"


def test_installation_repair_independent_branch_continues(repair_fixture, monkeypatch):
    from agent_machines.installation_repair_process import ActionResult

    _, _, _, configure, _ = repair_fixture
    configure(dependencies=())
    real_invoke = repair_api._invoke

    def fail_base(module, owner, platform, *, readonly, timeout):
        if module.module_id.endswith("/base"):
            if not readonly:
                return ActionResult("exited", 3)
            return ActionResult("exited", 0, json.dumps({
                "schema": "copilot-extensions.module-readiness", "version": 1,
                "module": module.module_id, "state": "not-ready",
            }).encode())
        return real_invoke(module, owner, platform, readonly=readonly, timeout=timeout)

    monkeypatch.setattr(repair_api, "_invoke", fail_base)
    plan = invoke(repair_fixture, selection=("base", "child"))
    result = invoke(
        repair_fixture, selection=("base", "child"), apply=True, digest=plan["planDigest"],
    )
    assert result["status"] == "incomplete"
    assert result["outcomes"][1]["observedAfter"]["state"] == "ready"
    assert result["outcomes"][1]["attempted"]


def test_installation_repair_distinct_sources_refuse_shared_legacy_writer(repair_fixture):
    home, project, payload, _, _ = repair_fixture
    peer = project / ".peer"
    shutil.copytree(payload.parent, peer)
    manifest = peer / ".claude-plugin" / "marketplace.json"
    value = json.loads(manifest.read_text())
    value["name"] = "peer"
    write_json(manifest, value)
    settings = project / ".github" / "copilot" / "settings.json"
    value = json.loads(settings.read_text())
    value["extraKnownMarketplaces"]["peer"] = {
        "source": {"source": "directory", "path": "./.peer"},
    }
    value["enabledPlugins"]["agent-example@peer"] = True
    write_json(settings, value)
    host = admit_host(project, home=home)
    assert len(host.bindings) == 2
    assert len({owner.installation.provenance.source_fingerprint
                for owner in host.bindings.values()}) == 2
    assert all(owner.refusal == "ordinary-legacy-writer-ambiguous"
               for owner in host.bindings.values())
    plan = invoke(repair_fixture)
    result = invoke(repair_fixture, apply=True, digest=plan["planDigest"])
    assert result["status"] == "incomplete"
    assert not any(row["attempted"] for row in result["outcomes"])
    assert not list(home.glob("*.ready"))


def _directory_alias(path: Path, target: Path) -> None:
    if os.name != "nt":
        path.symlink_to(target, target_is_directory=True)
        return
    command = f'"{os.environ["ComSpec"]}" /d /c mklink /J "{path}" "{target}"'
    result = subprocess.run(
        command, capture_output=True, text=True, timeout=10, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("peer_mode", [
    "repairable", "no-readiness", "no-readiness-or-protocol", "no-protocol", "invalid-root",
    "aliased-roots", "aliased-ancestor", "mixed-scope-roots", "missing-provenance",
    "missing-enabled-payload",
])
def test_installation_repair_same_marketplace_plugins_refuse_shared_legacy_writer(
    repair_fixture, monkeypatch, peer_mode,
):
    home, project, payload, _, _ = repair_fixture
    peer = payload.parent / "agent-peer"
    shutil.copytree(payload, peer)
    for name in ("plugin.json", "payload-invocation.json", "installer-readiness.json"):
        path = peer / name
        value = json.loads(path.read_text().replace("agent-example", "agent-peer"))
        if name == "payload-invocation.json":
            value["legacyRuntimeRoot"] = (
                "../escape" if peer_mode == "invalid-root"
                else ".agent-peer" if peer_mode == "aliased-roots" else ".agent-example"
            )
            if peer_mode in ("no-readiness-or-protocol", "no-protocol"):
                value.pop("noSelfProvisionEnv")
        elif name == "plugin.json" and peer_mode.startswith("no-readiness"):
            value.pop("installerReadiness")
        write_json(path, value)
    manifest = payload.parent / ".claude-plugin" / "marketplace.json"
    value = json.loads(manifest.read_text())
    value["plugins"].append({"name": "agent-peer", "source": "./agent-peer"})
    write_json(manifest, value)
    settings = project / ".github" / "copilot" / "settings.json"
    value = json.loads(settings.read_text())
    peer_source = (
        "agent-peer@missing" if peer_mode == "missing-provenance"
        else "agent-absent@fixture" if peer_mode == "missing-enabled-payload"
        else "agent-peer@fixture"
    )
    value["enabledPlugins"][peer_source] = True
    write_json(settings, value)
    if peer_mode in ("mixed-scope-roots", "missing-provenance"):
        marketplace = "missing" if peer_mode == "missing-provenance" else "fixture"
        installed = home / ".copilot" / "installed-plugins" / marketplace / "agent-peer"
        shutil.copytree(peer, installed)
        if peer_mode == "mixed-scope-roots":
            write_json(home / ".copilot" / "settings.json", {
                "enabledPlugins": {"agent-peer@fixture": True},
            })
    if peer_mode == "aliased-roots":
        shared = home / "shared-runtime"
        shared.mkdir()
        for name in (".agent-example", ".agent-peer"):
            _directory_alias(home / name, shared)
        assert os.path.samefile(home / ".agent-example", home / ".agent-peer")
    elif peer_mode == "aliased-ancestor":
        alias = home.parent / "linked-home"
        _directory_alias(alias, home)
        home = alias
    host = admit_host(project, home=home)
    aliased = peer_mode.startswith("aliased-")
    unresolved_peer = peer_mode in (
        "mixed-scope-roots", "missing-provenance", "missing-enabled-payload",
    )
    assert bool(host.refused) == (
        aliased or unresolved_peer or peer_mode in ("no-protocol", "invalid-root")
    )
    assert not host.report.findings
    binding_count = 0 if aliased else 2 if peer_mode == "repairable" else 1
    assert len(host.bindings) == binding_count
    if aliased:
        assert len(host.refused) == 2
    assert len({owner.installation.provenance.source_fingerprint
                for owner in host.bindings.values()}) == int(binding_count > 0)
    assert len({owner.installation.owner_id for owner in host.bindings.values()}) == binding_count
    refusal = (
        "ordinary-legacy-ownership-unresolved" if peer_mode == "invalid-root" or unresolved_peer
        else "ordinary-legacy-writer-ambiguous"
    )
    assert all(owner.refusal == refusal
               for owner in host.bindings.values())
    monkeypatch.setattr(repair_api, "run_action", lambda *a, **k: pytest.fail("executed"))
    modules = [module.qualified_id for module in host.report.modules]
    if peer_mode == "aliased-ancestor":
        with pytest.raises(RepairAdmissionError, match="linked or reparse"):
            repair_api.repair(project, home=home, modules=modules)
        with pytest.raises(RepairAdmissionError, match="linked or reparse"):
            repair_api.repair(
                project, home=home, modules=modules, apply=True, expected_plan_digest="0" * 64,
            )
        return
    plan = repair_api.repair(project, home=home, modules=modules)
    result = repair_api.repair(
        project, home=home, modules=modules, apply=True, expected_plan_digest=plan["planDigest"],
    )
    assert len(result["outcomes"]) == (
        2 if peer_mode.startswith("no-readiness") or unresolved_peer else 4
    )
    assert result["status"] == "incomplete"
    assert not any(row["attempted"] for row in result["outcomes"])
    assert not list(home.glob("*.ready"))


def test_installation_repair_disabled_unavailable_peer_does_not_poison_census(repair_fixture):
    home, project, _, _, _ = repair_fixture
    settings = project / ".github" / "copilot" / "settings.json"
    value = json.loads(settings.read_text())
    value["enabledPlugins"]["agent-absent@fixture"] = False
    write_json(settings, value)
    host = admit_host(project, home=home)
    assert not host.refused and len(host.bindings) == 1
    assert all(owner.refusal is None for owner in host.bindings.values())


def test_installation_repair_census_binding_disagreement_refuses_execution(
    repair_fixture, monkeypatch,
):
    from dataclasses import replace

    home, project, _, _, _ = repair_fixture
    original = admission_api._binding

    def changed(*args, **kwargs):
        return replace(original(*args, **kwargs), legacy_root=home / ".different")

    monkeypatch.setattr(admission_api, "_binding", changed)
    monkeypatch.setattr(repair_api, "run_action", lambda *a, **k: pytest.fail("executed"))
    host = admit_host(project, home=home)
    assert not host.bindings
    assert host.refused == {"agent-example@fixture": "enabled-owner-admission-refused"}
    plan = invoke(repair_fixture)
    result = invoke(repair_fixture, apply=True, digest=plan["planDigest"])
    assert result["status"] == "incomplete"
    assert not any(row["attempted"] for row in result["outcomes"])


def test_installation_repair_cycle_refuses_before_any_invocation(repair_fixture, monkeypatch):
    _, _, payload, _, _ = repair_fixture
    path = payload / "installer-readiness.json"
    declaration = json.loads(path.read_text())
    declaration["modules"][0]["dependsOn"] = ["agent-example/child"]
    write_json(path, declaration)
    monkeypatch.setattr(repair_api, "_invoke", lambda *args, **kwargs: pytest.fail("executed"))
    with pytest.raises(RepairAdmissionError, match="cycle"):
        invoke(repair_fixture)


def test_installation_repair_unsupported_platform_never_invokes(repair_fixture, monkeypatch):
    _, _, payload, _, _ = repair_fixture
    path = payload / "installer-readiness.json"
    declaration = json.loads(path.read_text())
    actual = repair_api.current_platform().value
    for module in declaration["modules"]:
        module["platforms"].remove(actual)
        module["installer"].pop(actual)
        module["readiness"]["invocations"].pop(actual)
    write_json(path, declaration)
    monkeypatch.setattr(repair_api, "_invoke", lambda *args, **kwargs: pytest.fail("executed"))
    plan = invoke(repair_fixture)
    assert plan["outcomes"][0]["observedBefore"]["state"] == "unsupported"
    result = invoke(repair_fixture, apply=True, digest=plan["planDigest"])
    assert result["status"] == "incomplete"
    assert not any(row["attempted"] for row in result["outcomes"])


@pytest.mark.parametrize("empty_satisfied", [True, False])
def test_installation_repair_optional_empty_prerequisite_respects_declared_semantics(
    repair_fixture, monkeypatch, empty_satisfied,
):
    from agent_machines.installation_repair_process import ActionResult

    _, _, payload, configure, _ = repair_fixture
    configure(classification="optional", dependencies=("agent-example/base",))
    path = payload / "installer-readiness.json"
    declaration = json.loads(path.read_text())
    declaration["modules"][0]["readiness"]["configurationEmpty"] = (
        "satisfied" if empty_satisfied else "unsatisfied"
    )
    write_json(path, declaration)

    def probe(module, owner, platform, *, readonly, timeout):
        if readonly:
            state = "configuration-empty" if module.module_id.endswith("/base") else "ready"
            return ActionResult("exited", 0, json.dumps({
                "schema": "copilot-extensions.module-readiness", "version": 1,
                "module": module.module_id, "state": state,
            }).encode())
        return ActionResult("exited", 2)

    monkeypatch.setattr(repair_api, "_invoke", probe)
    plan = invoke(repair_fixture)
    result = invoke(repair_fixture, apply=True, digest=plan["planDigest"])
    base, child = result["outcomes"]
    if empty_satisfied:
        assert result["status"] == "ready"
        assert not base["attempted"]
        assert not child["blockedBy"]
    else:
        assert result["status"] == "incomplete"
        assert not result["requiredSatisfied"]
        assert child["observedBefore"]["state"] == "ready"
        assert child["blockedBy"] == [base["module"]]


def test_installation_repair_active_cell_validates_receipts_and_refuses_ordinary_init(
    repair_fixture,
):
    import installation_context as context

    home, project, payload, _, identity = repair_fixture
    environment = ordinary_environment(os.environ, home=home, payload=payload)
    stamped = context.stamp_context(
        payload_root=payload, plugin_id="agent-example", copilot_home=home / ".copilot",
        project_root=project, environment=environment, durable_home=home / ".copilot-extensions",
        payload_version="1.0.0", payload_origin="explicit",
        expected_namespace_generation=0, expected_install_generation=0,
    )
    install_path = Path(stamped["installReceipt"])
    install = context.read_json(install_path)
    namespace = context.read_json(Path(install["namespaceReceipt"]))
    write_json(home / ".copilot-extensions" / "installation-mode.json", {
        "schema": "copilot-extensions.installation-mode", "version": 1,
        "installationMode": {"enabled": True},
    })
    write_json(install_path.parent / "installation-activation.json", {
        "schema": "copilot-extensions.installation-activation", "version": 1,
        "marketplaceId": identity, "pluginId": "agent-example",
        "mode": "namespaced", "state": "active",
        "environment": {
            "platform": "windows" if os.name == "nt" else "posix",
            "homeRealPath": str(home.resolve()),
            "wslDistro": None if os.name == "nt" else os.environ.get("WSL_DISTRO_NAME") or None,
        },
        "context": str(install_path), "namespaceGeneration": namespace["generation"],
        "installGeneration": install["generation"], "generation": 1,
        "legacy": {
            "disposition": "absent",
            "probe": {"declared": True, "result": "absent", "checkedAt": "2026-01-01T00:00:00Z"},
        },
        "createdAt": "2026-01-01T00:00:00Z", "updatedAt": "2026-01-01T00:00:00Z",
    })
    host = admit_host(project, home=home)
    owner = next(iter(host.bindings.values()))
    assert owner.mode == "core-active"
    assert owner.refusal is None
    assert owner.environment["COPILOT_EXTENSIONS_CONTEXT"] == str(install_path)
    plan = invoke(repair_fixture)
    result = invoke(repair_fixture, apply=True, digest=plan["planDigest"])
    assert result["outcomes"][0]["installer"]["reason"] == "active-core-action-not-declared"
    assert not any(row["attempted"] for row in result["outcomes"])
    assert not list(home.glob("*.ready"))
    for name in ("base", "child"):
        (home / f"agent-example-{name}.ready").write_text("ready")
    observed = invoke(repair_fixture)
    assert all(row["observedBefore"]["state"] == "ready" for row in observed["outcomes"])


@pytest.mark.parametrize("pipe", ["stdout", "stderr"])
def test_installation_repair_process_bounds_both_pipes(tmp_path, pipe):
    result = run_action(
        [sys.executable, "-c", f"import sys; sys.{pipe}.write('x' * 70000)"],
        cwd=tmp_path, environment=os.environ, timeout=5, readonly=True,
    )
    assert result.status == "output-limit"
    assert result.stdout == b""


def test_installation_repair_process_timeout(tmp_path):
    result = run_action(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        cwd=tmp_path, environment=os.environ, timeout=0.2, readonly=True,
    )
    assert result.status == "timeout"
    assert result.exit_code is not None


def test_installation_repair_process_cancellation_reaps_started_process(tmp_path, monkeypatch):
    from agent_machines import installation_repair_process as process_api

    started = []
    spawn = process_api.spawn_sync_in_kill_on_close_job
    sleep = process_api.time.sleep
    interrupted = False

    def record(*args, **kwargs):
        result = spawn(*args, **kwargs)
        started.append(result[0])
        return result

    def interrupt(_seconds):
        nonlocal interrupted
        if not interrupted:
            interrupted = True
            raise KeyboardInterrupt
        sleep(_seconds)

    monkeypatch.setattr(process_api, "spawn_sync_in_kill_on_close_job", record)
    monkeypatch.setattr(process_api.time, "sleep", interrupt)
    with pytest.raises(KeyboardInterrupt):
        run_action(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            cwd=tmp_path, environment=os.environ, timeout=5, readonly=True,
        )
    assert len(started) == 1
    assert started[0].poll() is not None


def test_installation_repair_process_reaps_inherited_pipe_descendant(tmp_path):
    marker = tmp_path / "descendant-pid"
    code = (
        "import subprocess, sys; from pathlib import Path; "
        "from agent_procutil import no_window_kwargs; "
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], "
        "stdout=sys.stdout, stderr=sys.stderr, **no_window_kwargs()); "
        "Path(sys.argv[1]).write_text(str(child.pid))"
    )
    result = run_action(
        [sys.executable, "-c", code, str(marker)],
        cwd=tmp_path, environment=os.environ, timeout=2, readonly=True,
    )
    assert marker.is_file()
    assert result.status == "timeout"
    pid = int(marker.read_text())
    deadline = time.monotonic() + 5
    while True:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel.OpenProcess.restype = wintypes.HANDLE
            kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            handle = kernel.OpenProcess(0x00100000, False, pid)
            if not handle:
                break
            try:
                alive = kernel.WaitForSingleObject(handle, 0) == 0x102
            finally:
                kernel.CloseHandle(handle)
        else:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            proc = Path("/proc") / str(pid) / "stat"
            alive = not proc.is_file() or proc.read_text().split(") ", 1)[1][0] != "Z"
        if not alive:
            break
        assert time.monotonic() < deadline, "owned descendant survived cleanup"
        time.sleep(0.02)


def test_installation_repair_installed_cli_native_first_touch(repair_fixture):
    home, project, _, _, identity = repair_fixture
    command = Path(sys.executable).parent / (
        "agent-machines.exe" if os.name == "nt" else "agent-machines"
    )
    assert command.is_file(), "this contract requires the installed console entry point"
    environment = ordinary_environment(os.environ, home=home, payload=project / ".ai")
    argv = [
        str(command), "installation-repair", "--project-root", str(project),
        "--module", identity + "::agent-example/child",
    ]
    plan = run_action(argv, cwd=project, environment=environment, timeout=15, readonly=True)
    assert plan.status == "exited" and plan.exit_code == 0
    preview = json.loads(plan.stdout)
    assert not list(home.glob("*.ready"))
    applied = run_action(
        [*argv, "--apply", "--expected-plan-digest", preview["planDigest"]],
        cwd=project, environment=environment, timeout=20, readonly=False,
    )
    assert applied.status == "exited" and applied.exit_code == 0
    result = json.loads(applied.stdout)
    assert result["status"] == "ready"
    assert list(home.glob("*.ready"))
