"""Inert primary-CLI adoption checks: never run installers or native registration."""

from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

from agent_index.engine import daemon

PLUGIN = Path(__file__).resolve().parents[1]
INSTALL = (PLUGIN / "scripts" / "install.ps1").read_text(encoding="utf-8")


def function(name: str) -> str:
    body = INSTALL.split(f"function {name} {{", 1)[1]
    return re.split(r"\nfunction \w", body, maxsplit=1)[0]


def test_native_driver_admission_precedes_staging_and_keeps_explicit_actions():
    admission = INSTALL.split(". (Join-Path $PSScriptRoot 'versioned_runtime_native.ps1')", 1)[1]
    admission = admission.split("# Preserve the invoking repository", 1)[0]
    assert "$Action -in @('install', 'update', 'provision', 'uninstall')" in admission
    assert "'engine'" not in admission
    assert "'engine-update'" not in admission
    assert "'start'" not in admission
    assert "'ensure'" not in admission
    assert "$env:OS -eq 'Windows_NT'" in admission
    assert "$env:COPILOT_EXTENSIONS_TEST_CONTAINED -ne '1'" in admission
    assert "-not ($Action -eq 'uninstall' -and $DryRun)" in admission
    staging = INSTALL.index("# === install-contract:v4 self-stage")
    assert INSTALL.index("Enter-NativeInstallerDriver") < staging
    assert "-Plugin 'agent-index' -Action $Action -Options $driverOptions" in admission
    assert "$driverEntry.Participant" in admission
    for option in ("NoService", "Purge", "Force", "ZeroDowntime", "AllowTaskElevation", "DryRun"):
        assert f"'{option}'" in admission


def test_completion_retains_helpers_without_retrofitting_completed_slots():
    completion = function("Invoke-VersionedMarkComplete")
    assert "$script:RuntimeConstructionReuse" in completion
    assert "'.runtime-bootstrap'" in completion
    assert "select a distinct fresh runtime generation" in completion
    assert completion.index("throw 'Completed primary") < completion.index(
        "Complete-RuntimeConstruction"
    )
    assert "Complete-RuntimeConstruction -PayloadHash $actualHash -RetainHelpers" in completion
    assert "Copy-Item" not in completion


def test_adoption_binds_only_retained_primary_slot_and_checks_receipt():
    adoption = function("Invoke-RuntimeBrokerAdoption")
    assert "$NoService -or $env:OS -ne 'Windows_NT'" in adoption
    assert "$env:COPILOT_EXTENSIONS_TEST_CONTAINED -eq '1'" in adoption
    assert "$script:RuntimeConstructionSession.Complete" in adoption
    assert "$script:RuntimeConstructionSession.Process.HasExited" in adoption
    assert '$script:RuntimeConstructionSession.Key -cne "$InstallDir/$SrcVersion"' in adoption
    assert "-FilePath $VenvPython" in adoption
    assert "'--runtime-root', $InstallDir, '--slot', $VenvDir" in adoption
    assert "'update_driver.windows_broker'" in adoption
    assert "$receipt.adopted -ne $true" in adoption
    assert "$LASTEXITCODE -ne 0" in adoption
    assert "EngineHome" not in adoption


def test_adoption_precedes_primary_selection_and_all_action_callbacks():
    install = function("Install-Runtime")
    assert install.index("Invoke-VersionedMarkComplete") < install.index(
        "Invoke-RuntimeBrokerAdoption"
    )
    assert install.index("Invoke-RuntimeBrokerAdoption") < install.index("Publish-IndexRuntime")
    assert "Invoke-VersionedActivate" in function("Publish-IndexRuntime")
    actions = INSTALL.split("switch ($Action) {", 1)[1]
    for action in ("install", "update", "provision"):
        body = actions.split(f"'{action}' {{", 1)[1].split("\n    }", 1)[0]
        assert body.index("Install-Runtime") < body.index("Invoke-ServiceCutover")


def test_uninstall_removes_primary_broker_before_deleting_any_runtime():
    remove = function("Invoke-Uninstall")
    assert remove.index("return") < remove.index("Invoke-Stop")
    assert remove.index("Invoke-Stop") < remove.index("--remove-broker")
    assert remove.index("--remove-broker") < remove.index("Unregister-ScheduledTask")
    assert remove.index("--remove-broker") < remove.index("Remove-Item $InstallDir")
    assert "throw 'Owned primary broker removal failed; runtimes preserved'" in remove


def test_engine_management_and_explicit_engine_lifecycle_remain_separate():
    ensure = function("Ensure-EngineRunning")
    assert "$managePy = Get-ActiveSlotPython" in ensure
    assert "& $managePy -I -X utf8 -m agent_index engine start --strict" in ensure
    assert "BrokerAdoption" not in ensure
    engine = function("Install-Engine")
    assert "-RetainHelpers" not in engine
    assert "BrokerAdoption" not in engine
    assert "Complete-RuntimeConstruction -PayloadHash $payloadHash" in engine
    restart = function("Restart-EngineDaemon")
    assert "$managePy = Get-ActiveSlotPython" in restart
    assert "& $managePy -I -X utf8 -m agent_index engine start --strict" in restart
    assert "& $EngineVenvPython" not in restart
    assert "BrokerAdoption" not in restart


def test_spawn_has_no_installation_or_adoption_side_effect(monkeypatch, tmp_path):
    calls = []
    expected = object()

    def launch(argv, **kwargs):
        calls.append((argv, kwargs))
        return expected

    monkeypatch.setattr(daemon, "spawn_daemon", launch)
    monkeypatch.setattr(daemon, "windowless_python_env", lambda _python: {})
    home = tmp_path / "engine"
    result = daemon._spawn(
        ["engine-python", "-m", "agent_index_engine.app", "--port", "8421"],
        python="engine-python", home=home, contained_fallback=False,
    )
    assert result is expected
    assert len(calls) == 1
    argv, kwargs = calls[0]
    assert argv[0] == "engine-python"
    assert argv[-2:] == ["--port", "8421"]
    assert kwargs["cwd"] == home.resolve()
    assert kwargs["contained_fallback"] is False
    tree = ast.parse(inspect.getsource(daemon._spawn))
    names = {
        node.func.id for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert not any("install" in name.lower() or "adopt" in name.lower() for name in names)


def test_driver_closure_is_optional_and_local_without_engine_dependency_change():
    project = tomllib.loads((PLUGIN / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"]["requires-python"] == ">=3.10"
    assert project["tool"]["uv"]["exclude-newer"] == "7 days"
    extras = project["project"]["optional-dependencies"]
    assert extras["native-installer"] == ["agent-update-driver"]
    assert "agent-update-driver" not in project["project"]["dependencies"]
    sources = project["tool"]["uv"]["sources"]
    for distribution in ("agent-update-driver", "agent-single-instance-lease"):
        root = (PLUGIN / sources[distribution]["path"]).resolve()
        assert (root / "pyproject.toml").is_file()
    install = function("Install-Runtime")
    assert "$pkgSpec = \"$PluginDir[native-installer]\"" in install
    assert install.index("$driverPackages") < install.index(
        "$pkgSpec = \"$PluginDir[native-installer]\""
    )
    snapshot = function("Materialize-IndexSnapshot")
    assert "'agent-update-driver' = 'update-driver'" in snapshot
    assert "'agent-single-instance-lease' = 'single-instance-lease'" in snapshot
