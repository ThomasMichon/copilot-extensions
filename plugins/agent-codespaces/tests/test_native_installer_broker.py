"""Native publication refuses failed broker adoption before any callbacks."""

from __future__ import annotations

import json
import pytest

from .test_installer_engine_adoption import PLUGIN, PWSH, _env, _function, _prelude, _run

pytestmark = [
    pytest.mark.installer_only,
    pytest.mark.skipif(PWSH is None, reason="native PowerShell is unavailable"),
]


@pytest.mark.parametrize("action", ["Install", "Update"])
def test_native_adoption_failure_preserves_selection_and_blocks_callbacks(tmp_path, action):
    root = tmp_path / "runtime"
    slot = root / "versions" / "1.2.3"
    slot.mkdir(parents=True)
    selector = root / "current-version"
    selector.write_text("previous", encoding="utf-8")
    python = slot / "python.exe"
    python.touch()
    script = (
        _prelude("ps1")
        + _function("ps1", "Invoke-VersionedActivate")
        + _function("ps1", f"Invoke-{action}")
        + f"""
$InstallDir='{root}'
$VenvDir='{slot}'
$VenvPython='{python}'
$LinkPython=$VenvPython
$LocalBin='{tmp_path / "bin"}'
$VersionedRuntime=$true
$SrcVersion='1.2.3'
$script:Events=[Collections.Generic.List[string]]::new()
function Write-Step {{ param($Message) }}
function Deploy-Venv {{ return $true }}
function Deploy-Package {{ return $true }}
function Test-CodespacesRuntime {{ return $true }}
function Invoke-VersionedMarkComplete {{ $script:Events.Add('complete'); return $true }}
function Invoke-RuntimeBrokerAdoption {{ $script:Events.Add('adopt'); throw 'fixture adoption refusal' }}
function Deploy-VersionedRuntimeHelper {{ throw 'UNEXPECTED_HELPER_PUBLICATION' }}
function Deploy-SelfProvisioningBinstub {{ throw 'UNEXPECTED_BINSTUB_PUBLICATION' }}
function Write-CodespacesDeployManifest {{ throw 'UNEXPECTED_MANIFEST_PUBLICATION' }}
function Sync-ConnectionOwnerService {{ throw 'UNEXPECTED_CONNECTION_OWNER' }}
$refused=$false
try {{ Invoke-{action} }} catch {{
    if ($_.Exception.Message -ne 'fixture adoption refusal') {{ throw }}
    $refused=$true
}}
if (-not $refused) {{ throw 'Missing adoption refusal' }}
$script:Events | ConvertTo-Json -Compress
"""
    )
    result = _run("ps1", script, tmp_path, _env(tmp_path))
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout.strip().splitlines()[-1]) == ["complete", "adopt"]
    assert selector.read_text(encoding="utf-8") == "previous"


def test_native_adoption_requires_live_complete_slot_retention(tmp_path):
    script = _prelude("ps1") + _function("ps1", "Invoke-RuntimeBrokerAdoption") + """
$env:OS='Windows_NT'
$env:COPILOT_EXTENSIONS_TEST_CONTAINED=''
$script:RuntimeConstructionSession=@{Complete=$false;Process=@{HasExited=$false}}
Invoke-RuntimeBrokerAdoption
"""
    result = _run("ps1", script, tmp_path, _env(tmp_path))
    assert result.returncode != 0
    assert "attached complete-slot retention" in result.stdout + result.stderr


def test_native_uninstall_failure_keeps_connection_owner_and_runtime(tmp_path):
    root = tmp_path / "runtime"
    root.mkdir()
    marker = root / "preserved"
    marker.write_text("unchanged", encoding="utf-8")
    remover = tmp_path / "refuse.ps1"
    remover.write_text("exit 19\n", encoding="utf-8")
    (tmp_path / "uninstall-runtime.ps1").write_text(
        "function Invoke-StatePreservingUninstall { param([switch]$Preview,[switch]$AdmissionOnly) }\n",
        encoding="utf-8",
    )
    script = _prelude("ps1") + _function("ps1", "Invoke-Uninstall") + f"""
$env:OS='Windows_NT'
$env:COPILOT_EXTENSIONS_TEST_CONTAINED=''
$DryRun=$false
$ServiceName='fixture'
$InstallDir='{root}'
$driverPython='{PWSH}'
$driverArguments=@('-NoProfile','-File','{remover}')
function Write-ServiceHeader {{ param($Message) }}
function Unregister-ConnectionOwnerService {{ throw 'UNEXPECTED_OWNER_REMOVAL' }}
function Stop-ManagedSshConnections {{ throw 'UNEXPECTED_SSH_STOP' }}
$refused=$false
try {{ Invoke-Uninstall }} catch {{
    if ($_.Exception.Message -ne 'Owned runtime broker removal failed; runtimes preserved') {{ throw }}
    $refused=$true
}}
if (-not $refused) {{ throw 'Missing removal refusal' }}
"""
    result = _run("ps1", script, tmp_path, _env(tmp_path))
    assert result.returncode == 0, result.stdout + result.stderr
    assert marker.read_text(encoding="utf-8") == "unchanged"


def test_codespaces_declares_local_driver_in_both_native_package_recipes():
    import tomllib

    project = tomllib.loads((PLUGIN / "pyproject.toml").read_text(encoding="utf-8"))
    assert "agent-update-driver" in project["project"]["dependencies"]
    source = project["tool"]["uv"]["sources"]["agent-update-driver"]
    assert (PLUGIN / source["path"]).resolve().samefile(PLUGIN.parents[1] / "libs/update-driver")
    for extension, token in (("ps1", "'agent-update-driver' = $UpdateDriverDir"),
                             ("sh", "agent-update-driver agent-codespaces")):
        assert token in (PLUGIN / "scripts" / f"install.{extension}").read_text(encoding="utf-8")
