"""Vault's native installer adopts only a retained, complete serving runtime."""

from pathlib import Path
import os
import shutil
import subprocess

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def test_installer_adopts_before_activation_and_preserves_client_only_mode():
    source = (SCRIPTS / "install.ps1").read_text(encoding="utf-8")
    assert source.index("-Plugin 'agent-vault'") < source.index("# === install-contract:v4 self-stage")
    assert "-not ($Action -eq 'uninstall' -and $DryRun)" in source
    complete = source.split("function Invoke-VersionedMarkComplete {", 1)[1].split("\nfunction ", 1)[0]
    assert "-RetainHelpers" in complete
    install = source.split("function Install-Runtime {", 1)[1].split("\nfunction ", 1)[0]
    reused = install.split("if ($script:RuntimeConstructionReuse) {", 1)[1]
    assert reused.index("Invoke-VersionedMarkComplete") < reused.index("Invoke-RuntimeBrokerAdoption")
    assert reused.index("Invoke-RuntimeBrokerAdoption") < reused.index("Invoke-VersionedActivate")
    fresh = install.split("$prevVersion = ''", 1)[1]
    assert fresh.index("Invoke-VersionedMarkComplete") < fresh.index("Invoke-RuntimeBrokerAdoption")
    assert fresh.index("Invoke-RuntimeBrokerAdoption") < fresh.index("Invoke-VersionedActivate")
    remove = source.split("function Invoke-Uninstall {", 1)[1].split("\nfunction ", 1)[0]
    assert remove.index("--remove-broker") < remove.index("Unregister-ScheduledTask")
    assert remove.index("--remove-broker") < remove.index("Remove-InstallRuntimeContent")


@pytest.mark.parametrize("mode", ["client", "contained", "posix", "unretained", "ready"])
def test_broker_adoption_native_function_has_no_suppressed_side_effects(tmp_path, mode):
    shell = shutil.which("powershell.exe") if os.name == "nt" else shutil.which("pwsh")
    if not shell:
        pytest.skip("PowerShell unavailable")
    source = (SCRIPTS / "install.ps1").read_text(encoding="utf-8")
    function = "function Invoke-RuntimeBrokerAdoption {" + source.split(
        "function Invoke-RuntimeBrokerAdoption {", 1,
    )[1].split("# === end install-contract:v4 marker/toss helpers ===", 1)[0]
    harness = tmp_path / "adoption.ps1"
    harness.write_text(
        "param([string]$Mode)\n$ErrorActionPreference = 'Stop'\n"
        "$env:OS = if ($Mode -eq 'posix') { 'Unix' } else { 'Windows_NT' }\n"
        "$env:COPILOT_EXTENSIONS_TEST_CONTAINED = if ($Mode -eq 'contained') { '1' } else { '' }\n"
        "$NoService = $Mode -eq 'client'\n"
        "$VenvPython = 'candidate-python'\n$VenvDir = 'candidate-slot'\n$InstallDir = 'consumer'\n"
        "$script:Calls = 0\n"
        "$script:RuntimeConstructionSession = if ($Mode -eq 'ready') {\n"
        "  @{Complete=$true;Process=@{HasExited=$false}}\n} else { $null }\n"
        "function Invoke-Hidden {\n"
        "  param($FilePath,$ArgList,$ErrorOutput)\n"
        "  if ($FilePath -ne $VenvPython -or $ArgList[0] -ne '-I' -or\n"
        "      $ArgList[2] -ne 'update_driver.windows_broker') { throw 'wrong command' }\n"
        "  $script:Calls++; $global:LASTEXITCODE = 0; return '{\"adopted\":true}'\n}\n"
        + function
        + "\ntry { Invoke-RuntimeBrokerAdoption } catch {\n"
        "  if ($Mode -ne 'unretained' -or $_ -notmatch 'complete-slot retention') { throw }\n"
        "  if ($script:Calls -ne 0) { throw 'spawned without retention' }\n  exit 0\n}\n"
        "if ($Mode -eq 'unretained') { throw 'unretained runtime was accepted' }\n"
        "$expected = if ($Mode -eq 'ready') { 1 } else { 0 }\n"
        "if ($script:Calls -ne $expected) { throw 'unexpected adoption side effects' }\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(harness), mode],
        capture_output=True, text=True, timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stdout + result.stderr
