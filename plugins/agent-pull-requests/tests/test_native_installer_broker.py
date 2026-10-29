"""Legacy native broker adoption without installation or host task mutations."""

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
PWSH = shutil.which("pwsh") or shutil.which("powershell")
pytestmark = pytest.mark.guard


def _function(name):
    return f"function {name} {{" + SOURCE.split(f"function {name} {{", 1)[1].split(
        "\n}", 1
    )[0] + "\n}"


def _uninstall():
    return SOURCE.rsplit("\n    if ($Action -eq 'uninstall') {", 1)[1].split(
        "\n        exit 0\n    }", 1,
    )[0]


def _probe(body):
    with tempfile.TemporaryDirectory(prefix="pr-native-probe-") as directory:
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
def test_pull_requests_native_installer_parses_without_execution():
    path = str(SCRIPTS / "install.ps1").replace("'", "''")
    _probe(f"""
$tokens=$null
$errors=$null
[void][Management.Automation.Language.Parser]::ParseFile('{path}',[ref]$tokens,[ref]$errors)
if ($errors.Count) {{ throw ($errors | Out-String) }}
""")


def test_pull_requests_driver_recipe_is_closed_and_shipped():
    path = SCRIPTS / "installer_driver.py"
    assert path.is_file(), "canonical installer-engine adopter sync is required"
    spec = importlib.util.spec_from_file_location("_pull_requests_broker_driver", path)
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)
    assert adapter.RECIPES["agent-pull-requests"] == frozenset({"Force"})
    args = SimpleNamespace(
        plugin="agent-pull-requests", action="update", root=PLUGIN, options=["Force"],
    )
    assert adapter.native_arguments(args) == [
        "-Action", "update", "-InstallDir", str(PLUGIN.resolve()), "-Force",
    ]
    for option in ("Context", "RuntimeVersion", "SnapshotId", "Command"):
        args.options = [option]
        with pytest.raises(ValueError, match="Unsupported"):
            adapter.native_arguments(args)


def test_legacy_driver_admission_does_not_absorb_cell_or_read_only_actions():
    assert SOURCE.index("if ($cellRequested)") < SOURCE.index("-Plugin 'agent-pull-requests'")
    assert SOURCE.index("-Plugin 'agent-pull-requests'") < SOURCE.index(
        "# === install-contract:v4 self-stage"
    )
    admission = SOURCE.split("$driverPayload =", 1)[0]
    assert "$Action -in @('install', 'update', 'provision', 'uninstall')" in admission
    assert "COPILOT_EXTENSIONS_TEST_CONTAINED -ne '1'" in admission
    assert "$env:OS -eq 'Windows_NT'" in admission
    assert "if ($Force) { $driverOptions += 'Force' }" in SOURCE


def test_pull_requests_adopts_before_selection_and_removes_before_deletion():
    complete = _function("Invoke-VersionedMarkComplete")
    assert "-RetainHelpers" in complete
    activate = _function("Invoke-VersionedActivate")
    assert activate.index("Invoke-VersionedMarkComplete") < activate.index(
        "Invoke-RuntimeBrokerAdoption"
    ) < activate.index("activate $SrcVersion")
    uninstall = _uninstall()
    assert uninstall.index("Invoke-StatePreservingUninstall -AdmissionOnly") < uninstall.index("--remove-broker")
    assert uninstall.index("--remove-broker") < uninstall.index("Invoke-StatePreservingUninstall\n")
    assert "Owned runtime broker removal failed; runtimes preserved" in uninstall
    assert "Stop-RuntimeConstruction" in SOURCE


def test_pull_requests_declares_local_execution_driver_closure():
    project = tomllib.loads((PLUGIN / "pyproject.toml").read_text(encoding="utf-8"))
    assert "agent-update-driver" in project["project"]["dependencies"]
    assert project["tool"]["uv"]["sources"]["agent-update-driver"]["path"] == (
        "../../libs/update-driver"
    )
    assert "'--reinstall-package', 'agent-update-driver'" in SOURCE
    assert "'--refresh-package', 'agent-update-driver'" in SOURCE


@pytest.mark.skipif(PWSH is None, reason="PowerShell unavailable")
@pytest.mark.parametrize("platform,contained", [("Linux", ""), ("Windows_NT", "1")])
def test_pull_requests_suppressed_adoption_never_invokes_adapter(platform, contained):
    _probe(_function("Invoke-RuntimeBrokerAdoption") + f"""
$env:OS='{platform}'
$env:COPILOT_EXTENSIONS_TEST_CONTAINED='{contained}'
function Invoke-Hidden {{ throw 'unexpected broker call' }}
Invoke-RuntimeBrokerAdoption
""")


@pytest.mark.skipif(PWSH is None, reason="PowerShell unavailable")
@pytest.mark.parametrize("complete,exited", [
    ("$false", "$false"), ("$true", "$true"),
])
def test_pull_requests_adoption_refuses_unretained_runtime(complete, exited):
    _probe(_function("Invoke-RuntimeBrokerAdoption") + f"""
$env:OS='Windows_NT'
$env:COPILOT_EXTENSIONS_TEST_CONTAINED=''
$script:RuntimeConstructionSession=@{{Complete={complete};Process=@{{HasExited={exited}}}}}
function Invoke-Hidden {{ throw 'unexpected broker call' }}
try {{ Invoke-RuntimeBrokerAdoption; throw 'missing refusal' }} catch {{
    $expected='Broker adoption requires attached complete-slot retention'
    if ($_.Exception.Message -ne $expected) {{ throw }}
}}
""")


@pytest.mark.skipif(PWSH is None, reason="PowerShell unavailable")
@pytest.mark.parametrize("exit_code,receipt,expected", [
    (9, '{"adopted":true}', "Runtime broker adoption failed:"),
    (0, '{"adopted":false}', "Runtime broker adoption returned no receipt"),
])
def test_pull_requests_adoption_checks_exit_and_receipt(exit_code, receipt, expected):
    _probe(_function("Invoke-RuntimeBrokerAdoption") + f"""
$env:OS='Windows_NT'
$env:COPILOT_EXTENSIONS_TEST_CONTAINED=''
$InstallDir='C:\\fixture\\pull-requests'
$VenvDir='C:\\fixture\\pull-requests\\versions\\1.2.3'
$VenvPython='C:\\fixture\\pull-requests\\versions\\1.2.3\\Scripts\\python.exe'
$script:RuntimeConstructionSession=@{{Complete=$true;Process=@{{HasExited=$false}}}}
function Invoke-Hidden {{
    param($FilePath,$ArgList,[ref]$ErrorOutput)
    $global:LASTEXITCODE={exit_code}
    return '{receipt}'
}}
try {{ Invoke-RuntimeBrokerAdoption; throw 'missing refusal' }} catch {{
    if (-not $_.Exception.Message.StartsWith('{expected}')) {{ throw }}
}}
""")


@pytest.mark.skipif(PWSH is None, reason="PowerShell unavailable")
def test_pull_requests_adoption_failure_blocks_selection():
    activate = _function("Invoke-VersionedActivate").replace(
        "$PSScriptRoot", "'C:\\fixture\\scripts'"
    )
    output = _probe(activate + """
$VersionedRuntime=$true
$VenvPython='Invoke-FakePython'
$InstallDir='C:\\fixture\\pull-requests'
$SrcVersion='1.2.3'
$script:Events=[Collections.Generic.List[string]]::new()
function Test-SourceRuntimeUnchanged {}
function Test-Path { return $true }
function Invoke-FakePython { $global:LASTEXITCODE=0 }
function Invoke-Hidden { $global:LASTEXITCODE=0 }
function Invoke-VersionedMarkComplete { $script:Events.Add('complete'); return $true }
function Invoke-RuntimeBrokerAdoption { $script:Events.Add('adopt'); throw 'fixture refusal' }
try { Invoke-VersionedActivate | Out-Null; throw 'missing refusal' } catch {
    if ($_.Exception.Message -ne 'fixture refusal') { throw }
}
$script:Events | ConvertTo-Json -Compress
""")
    assert json.loads(output) == ["complete", "adopt"]


@pytest.mark.skipif(PWSH is None, reason="PowerShell unavailable")
def test_pull_requests_uninstall_removal_failure_preserves_launchers_and_runtime():
    body = _uninstall().replace(
        ". (Join-Path $PSScriptRoot 'uninstall-runtime.ps1')",
        "function Invoke-StatePreservingUninstall { param([switch]$AdmissionOnly); "
        "if (-not $AdmissionOnly) { throw 'unexpected runtime removal' } }",
    )
    _probe("function Invoke-FixtureUninstall {" + body + "\n}\n" + """
$env:OS='Windows_NT'
$env:COPILOT_EXTENSIONS_TEST_CONTAINED=''
$driverPython='Invoke-Refuse'
$driverArguments=@()
function Invoke-Refuse { $global:LASTEXITCODE=19 }
function Remove-Item { throw 'unexpected file removal' }
function Remove-InstallRuntimeContent { throw 'unexpected runtime removal' }
try { Invoke-FixtureUninstall; throw 'missing refusal' } catch {
    $expected='Owned runtime broker removal failed; runtimes preserved'
    if ($_.Exception.Message -ne $expected) { throw }
}
""")


@pytest.mark.skipif(PWSH is None, reason="PowerShell unavailable")
def test_pull_requests_reused_slot_executes_completion_and_adoption_before_publication():
    activate = _function("Invoke-VersionedActivate").replace(
        "$PSScriptRoot", "'C:\\fixture\\scripts'"
    )
    reusable = "    [void](Invoke-VersionedSlotClean)" + SOURCE.split(
        "    [void](Invoke-VersionedSlotClean)", 1
    )[1].split("    $sourceFields = Get-SourceManifestFields", 1)[0]
    functions = "\n".join((
        activate, _function("Invoke-VersionedMarkComplete"),
        _function("Invoke-RuntimeBrokerAdoption"),
    ))
    output = _probe(functions + """
$env:OS='Windows_NT'
$env:COPILOT_EXTENSIONS_TEST_CONTAINED=''
$VersionedRuntime=$true
$Force=$false
$SrcVersion='1.2.3'
$InstallDir='C:\\fixture\\pull-requests'
$VenvDir='C:\\fixture\\pull-requests\\versions\\1.2.3'
$VenvPython='Invoke-FakePython'
$LinkPython=$VenvPython
$LocalBin='fixture-bin'
$SourceRuntimeIdentity=$null
$payloadHash='fixture-hash'
$script:Events=[Collections.Generic.List[string]]::new()
function Invoke-VersionedSlotClean {
    $script:RuntimeConstructionReuse=$true
    $script:RuntimeConstructionSession=@{Complete=$false;Process=@{HasExited=$false}}
    $script:Events.Add('reuse')
}
function Test-VersionedSlotComplete { return $true }
function Test-SourceRuntimeUnchanged {}
function Get-PayloadHash { return 'fixture-hash' }
function Test-Path { return $true }
function Write-Step {}
function Write-Ok {}
function Write-Skip {}
function New-SignedVenv { throw 'unexpected rebuild' }
function Invoke-UvPipInstallResilient { throw 'unexpected package install' }
function Invoke-FakePython {
    $global:LASTEXITCODE=0
    if ($args -contains 'activate') { $script:Events.Add('select') }
    if ($args -contains 'current') { return 'previous' }
}
function Complete-RuntimeConstruction {
    param($PayloadHash,[switch]$RetainHelpers)
    if (-not $RetainHelpers) { throw 'missing construction retention' }
    $script:RuntimeConstructionSession.Complete=$true
    $script:Events.Add('complete')
}
function Invoke-Hidden {
    param($FilePath,$ArgList,[ref]$ErrorOutput)
    if ($ArgList[2] -eq '-m' -and $ArgList[3] -eq 'agent_pull_requests') {
        $global:LASTEXITCODE=0
        return ''
    }
    if ($ArgList[2] -ne 'update_driver.windows_broker') { throw 'unexpected adapter' }
    $global:LASTEXITCODE=0
    $script:Events.Add('adopt')
    return '{"adopted":true}'
}
function Write-SimpleBinstub { $script:Events.Add('publish') }
""" + reusable + "\n$script:Events | ConvertTo-Json -Compress")
    assert json.loads(output) == ["reuse", "complete", "adopt", "select", "publish"]
