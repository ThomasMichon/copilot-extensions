"""Native completion, publication and legacy-removal contracts."""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
import tomllib

PLUGIN = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN / "scripts"
SOURCE = (SCRIPTS / "install.ps1").read_text(encoding="utf-8")
OPTIONS = {
    "NoService", "NoSupervisor", "Interactive", "Purge", "Force",
    "RequireGracefulCutover", "PreserveSupervisors", "ZeroDowntime", "DryRun",
}
PWSH = shutil.which("pwsh") or shutil.which("powershell")
pytestmark = pytest.mark.guard


def _function(name):
    return f"function {name} {{" + SOURCE.split(f"function {name} {{", 1)[1].split(
        "\n}", 1
    )[0] + "\n}"


def _probe(body):
    with tempfile.TemporaryDirectory(prefix="dispatch-native-probe-") as directory:
        script = Path(directory) / "probe.ps1"
        script.write_text("$ErrorActionPreference='Stop'\n" + body + "\nexit 0\n", encoding="utf-8")
        result = subprocess.run(  # noqa: S603 - fixed interpreter, repository-owned inert mocks
            [PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(script)],
            capture_output=True, text=True, timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout.strip()


@pytest.mark.skipif(PWSH is None, reason="PowerShell unavailable")
def test_dispatch_native_installer_parses_without_execution():
    path = str(SCRIPTS / "install.ps1").replace("'", "''")
    _probe(f"""
$tokens=$null
$errors=$null
[void][Management.Automation.Language.Parser]::ParseFile('{path}',[ref]$tokens,[ref]$errors)
if ($errors.Count) {{ throw ($errors | Out-String) }}
""")


def test_dispatch_driver_recipe_is_closed_and_shipped():
    path = SCRIPTS / "installer_driver.py"
    assert path.is_file(), "canonical installer-engine adopter sync is required"
    spec = importlib.util.spec_from_file_location("_dispatch_broker_driver", path)
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)
    assert adapter.RECIPES["agent-dispatch"] == frozenset(OPTIONS)
    args = SimpleNamespace(
        plugin="agent-dispatch", action="update", root=PLUGIN, options=sorted(OPTIONS),
    )
    assert adapter.native_arguments(args) == [
        "-Action", "update", "-InstallDir", str(PLUGIN.resolve()),
        *("-" + name for name in sorted(OPTIONS)),
    ]
    args.options = ["RetainedRuntime"]
    with pytest.raises(ValueError, match="Unsupported"):
        adapter.native_arguments(args)


def test_dispatch_admission_precedes_staging_and_excludes_pure_actions():
    admission = SOURCE.split("$driverPayload =", 1)[0]
    assert "$Action -in @('install', 'update', 'provision', 'uninstall', 'restore')" in admission
    assert "-not ($Action -eq 'uninstall' -and $DryRun)" in admission
    assert "COPILOT_EXTENSIONS_TEST_CONTAINED -ne '1'" in admission
    assert "$env:OS -eq 'Windows_NT'" in admission
    assert SOURCE.index("-Plugin 'agent-dispatch'") < SOURCE.index(
        "# === install-contract:v4 self-stage"
    )
    for option in OPTIONS:
        assert f"'{option}'" in SOURCE.split("$driverOptions = @()", 1)[1].split(
            "$driverEntry =", 1
        )[0]


def test_dispatch_completion_precedes_selection_without_broker_adoption():
    assert "-RetainHelpers" in _function("Invoke-VersionedMarkComplete")
    install = _function("Install-Runtime")
    complete = install.index("if (-not (Invoke-VersionedMarkComplete))")
    assert complete < install.index("Invoke-SupervisorAudit -Event runtime-ready")
    assert complete < install.index("if (-not (Invoke-VersionedActivate))")
    assert "BrokerAdoption" not in SOURCE
    assert "Stop-RuntimeConstruction" in SOURCE
    uninstall = _function("Invoke-Uninstall")
    assert uninstall.index("--remove-broker") < uninstall.index("Invoke-SupervisorsStop")
    assert uninstall.index("--remove-broker") < uninstall.index("Remove-AllSupervisorTasks")
    assert "Owned runtime broker removal failed; runtimes preserved" in uninstall
    restore = _function("Install-RetainedRuntime")
    assert restore.index("Retained runtime failed its health gate") < restore.index("Invoke-VersionedActivate")
    assert "-RetainedRuntimeSha256 $RetainedRuntimeSha256" in SOURCE


def test_dispatch_snapshot_and_runtime_include_execution_driver():
    project = tomllib.loads((PLUGIN / "pyproject.toml").read_text(encoding="utf-8"))
    assert "agent-update-driver" in project["project"]["dependencies"]
    assert project["tool"]["uv"]["sources"]["agent-update-driver"]["path"] == (
        "../../libs/update-driver"
    )
    assert "'agent-update-driver' = 'update-driver'" in _function("Materialize-DispatchSnapshot")
    assert "Dir = 'update-driver'; Pkg = 'agent-update-driver'" in _function("Install-Runtime")


@pytest.mark.skipif(PWSH is None, reason="PowerShell unavailable")
def test_dispatch_uninstall_removal_failure_precedes_service_and_runtime_changes():
    tail = _function("Invoke-Uninstall").split(
        "$ownedProcesses = @(Get-DispatchUninstallProcesses)", 1,
    )[1]
    _probe("function Invoke-Uninstall {\n" + tail + """
$env:OS='Windows_NT'
$env:COPILOT_EXTENSIONS_TEST_CONTAINED=''
$DryRun=$false
$driverPython='Invoke-Refuse'
$driverArguments=@()
function Invoke-Refuse { $global:LASTEXITCODE=19 }
function Invoke-SupervisorsStop { throw 'unexpected supervisor stop' }
function Remove-AllSupervisorTasks { throw 'unexpected task removal' }
function Remove-Item { throw 'unexpected file removal' }
try { Invoke-Uninstall; throw 'missing refusal' } catch {
    $expected='Owned runtime broker removal failed; runtimes preserved'
    if ($_.Exception.Message -ne $expected) { throw }
}
""")


@pytest.mark.skipif(PWSH is None, reason="PowerShell unavailable")
def test_dispatch_retained_health_failure_precedes_selection_and_publication():
    _probe(_function("Install-RetainedRuntime") + """
$env:OS='Windows_NT'
$env:COPILOT_EXTENSIONS_TEST_CONTAINED=''
$NoService=$false
$InstallDir='C:\\fixture\\dispatch'
$VenvDir='C:\\fixture\\dispatch\\versions\\1.2.3'
$VenvPython='Invoke-FakePython'
function Invoke-FakePython { $global:LASTEXITCODE=9 }
function Invoke-RuntimeBrokerAdoption { throw 'unexpected broker adoption' }
function Enter-PluginSnapshotLock { throw 'unexpected selection/publication' }
try { Install-RetainedRuntime; throw 'missing refusal' } catch {
    if ($_.Exception.Message -cne 'Retained runtime failed its health gate; not activating') { throw }
}
""")


@pytest.mark.skipif(PWSH is None, reason="PowerShell unavailable")
@pytest.mark.parametrize("action", ["install", "update", "provision"])
def test_dispatch_reused_complete_slot_selects_without_adoption_before_real_action_callbacks(action):
    functions = "\n".join(_function(name) for name in (
        "Install-Runtime", "Invoke-VersionedMarkComplete",
        "Invoke-Install", "Invoke-Update",
    ))
    entry = "Invoke-Update" if action == "update" else "Invoke-Install"
    output = _probe(functions + """
$env:OS='Windows_NT'
$env:COPILOT_EXTENSIONS_TEST_CONTAINED=''
$env:PATH='fixture-bin'
$NoService=$false
$Force=$false
$RequireGracefulCutover=$false
$RetainedRuntime=''
$VersionedRuntime=$true
$SrcVersion='1.2.3'
$PluginDir='C:\\fixture\\payload'
$PkgSrcDir='C:\\fixture\\payload\\src\\agent_dispatch'
$InstallDir='C:\\fixture\\dispatch'
$VenvDir='C:\\fixture\\dispatch\\versions\\1.2.3'
$VenvPython='Invoke-FakePython'
$LinkPython=$VenvPython
$LocalBin='fixture-bin'
$script:SourceRuntimeIdentity=$null
$script:InstallerDependencyPolicy='fixture-policy'
$script:GlobalActivationLockTimeoutSeconds=1
$script:Events=[Collections.Generic.List[string]]::new()
function Assert-SourceRuntime {}
function Assert-DispatchActions {}
function Resolve-DispatchBasePython { return $null }
function Test-Path { return $true }
function Get-Content { return '' }
function Get-Command { return [pscustomobject]@{Source='Invoke-FakePython'} }
function Invoke-NativeCapture { return @{ExitCode=0;Output='Python fixture'} }
function Invoke-FakePython { $global:LASTEXITCODE=0 }
function Write-Step {}
function Write-Ok {}
function Write-Skip {}
function Write-Warn {}
function Invoke-SupervisorAudit {}
function Enter-PluginSnapshotLock {
    $lock=[pscustomobject]@{}
    $lock | Add-Member ScriptMethod ReleaseMutex {}
    $lock | Add-Member ScriptMethod Dispose {}
    return $lock
}
function New-PluginBuildSnapshot { return $PluginDir }
function Ensure-Uv { return 'fixture-uv' }
function Invoke-VersionedSlotClean {
    $script:RuntimeConstructionReuse=$true
    $script:RuntimeConstructionSession=@{Complete=$false;Process=@{HasExited=$false}}
    $script:Events.Add('reuse')
}
function Test-DispatchVenv { throw 'unexpected rebuild' }
function Invoke-UvPipInstallResilient { throw 'unexpected package install' }
function Get-VersionedCurrent { return 'previous' }
function Get-PayloadHash { return 'fixture-hash' }
function Complete-RuntimeConstruction {
    param($PayloadHash,[switch]$RetainHelpers)
    if (-not $RetainHelpers) { throw 'missing construction retention' }
    $script:RuntimeConstructionSession.Complete=$true
    $script:Events.Add('complete')
}
function Invoke-RuntimeBrokerAdoption { throw 'unexpected broker adoption' }
function Invoke-VersionedActivate { $script:Events.Add('select'); return $true }
function Test-ActivationSupersededNow { return $false }
function Deploy-SelfProvisioningBinstub { $script:Events.Add('publish') }
function Write-Manifest {}
function Register-PickerPivot {}
function Publish-DispatchBasePython {}
function Invoke-VersionedGc {}
function Write-UpdateMarker {}
function Clear-UpdateMarker {}
function Invoke-DowngradeGuard {}
function Test-CoordinatorHealthy { return $false }
function Install-CoordinatorTask { $script:Events.Add('coordinator') }
function Remove-CoordinatorFirewallRule {}
function Install-SupervisorTask { $script:Events.Add('supervisor') }
function Confirm-CoordinatorRunning { $script:Events.Add('confirm') }
""" + entry + "\n$script:Events | ConvertTo-Json -Compress")
    assert json.loads(output) == [
        "reuse", "complete", "select", "publish",
        "coordinator", "supervisor", "confirm",
    ]
