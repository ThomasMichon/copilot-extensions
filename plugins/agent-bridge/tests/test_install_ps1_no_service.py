"""Closed-wheel construction must leave service lifetime with its caller."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from test_install_ps1_forwarded_route import _extract_function

INSTALLER = Path(__file__).resolve().parents[1] / "scripts" / "install.ps1"
PWSH = shutil.which("pwsh")
pytestmark = pytest.mark.skipif(PWSH is None, reason="pwsh is not available")


def _run(
    tmp_path: Path, source: str | None, *arguments: str,
) -> subprocess.CompletedProcess[str]:
    script = INSTALLER if source is None else tmp_path / "runtime-only.ps1"
    if source is not None:
        script.write_text(source, encoding="utf-8")
    assert PWSH is not None
    return subprocess.run(
        [PWSH, "-NoProfile", "-NonInteractive", "-File", str(script), *arguments],
        capture_output=True, text=True, timeout=30,
        env={**os.environ, "COPILOT_EXTENSIONS_HUMAN_UTF8": "0"},
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False,
    )


def test_full_native_entry_refuses_incomplete_runtime_only_input(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    result = _run(tmp_path, None, "-Action", "update", "-InstallDir", str(root), "-NoService")
    assert result.returncode != 0
    assert "NoService requires" in result.stderr
    assert not root.exists()


@pytest.mark.parametrize(
    ("arguments", "complete"),
    [
        (("-Action", "update", "-NoService"), False),
        (("-Action", "provision", "-NoService"), True),
        (("-Action", "status", "-NoService"), True),
        (("-Action", "update", "-NoService", "-WheelProfile", "profile"), False),
        (("-Action", "update", "-NoService", "-RequireGracefulCutover"), True),
        (("-Action", "install", "-NoService", "-NonInteractive"), True),
        (("-Action", "install", "-NoService", "-ForceTaskRepair"), True),
        (("-Action", "install", "-NoService", "-Purge"), True),
        (("-Action", "install", "-NoService", "-DryRun"), True),
    ],
)
def test_runtime_only_refuses_incomplete_or_conflicting_input_before_effects(
    tmp_path: Path, arguments: tuple[str, ...], complete: bool,
) -> None:
    header = INSTALLER.read_text(encoding="utf-8").split("$NativeWheelSelection = $null")[0]
    if complete:
        arguments += (
            "-WheelProfile", "profile", "-WheelDirectory", "wheels",
            "-WheelPython", "python", "-WheelUv", "uv",
        )
    result = _run(tmp_path, header + "\nthrow 'EFFECTS REACHED'\n", *arguments)
    assert result.returncode != 0
    assert "EFFECTS REACHED" not in result.stderr
    assert "NoService requires" in result.stderr


@pytest.mark.parametrize("action", ["install", "update"])
def test_runtime_only_complete_input_and_native_forwarding(tmp_path: Path, action: str) -> None:
    source = INSTALLER.read_text(encoding="utf-8")
    header = source.split("$NativeWheelSelection = $null")[0]
    forwarding = source.split("if ($env:OS -eq 'Windows_NT' -and", 1)[1]
    forwarding = "if ($env:OS -eq 'Windows_NT' -and" + forwarding.split(
        "# === install-contract:test-persistent-environment", 1,
    )[0]
    result = _run(
        tmp_path,
        header + r"""
$env:OS = 'Windows_NT'
$env:COPILOT_EXTENSIONS_TEST_CONTAINED = '0'
function Resolve-Path { return @{ Path = 'payload' } }
function Enter-NativeInstallerDriver {
    param($Payload, $Root, $Plugin, $Action, $Options, $WheelProfile,
          $WheelDirectory, $WheelPython, $WheelUv)
    [Console]::WriteLine(($Options | ConvertTo-Json -Compress))
    return @{ Participant = $false; ExitCode = 0 }
}
""" + forwarding,
        "-Action", action, "-InstallDir", str(tmp_path / "runtime"), "-NoService",
        "-ZeroDowntime", "-WheelProfile", "profile", "-WheelDirectory", "wheels",
        "-WheelPython", "python", "-WheelUv", "uv",
    )
    assert result.returncode == 0, result.stderr
    assert set(json.loads(result.stdout)) == {"NoService", "ZeroDowntime"}


def _lifecycle_source(
    action: str, match: str, *, no_service: bool = True, fail_build: bool = False,
) -> str:
    current = {
        "same": "$true",
        "after-lock": "($script:checks -gt 1)",
        "changed": "$false",
    }[match]
    stubs = {
        "Ensure-Uv": "return $true",
        "Initialize-BridgeBootstrapPython": "",
        "Enter-InstallLock": "return $true",
        "Exit-InstallLock": "$events.Add('unlock')",
        "Get-PayloadHash": "return 'hash'",
        "Test-PayloadUnchangedSinceAdmission": "return $true",
        "Ensure-RuntimeSlotRetention": "$events.Add('retain')",
        "Get-SlotAdmission": "return 'construct'",
        "Invoke-VersionedSlotClean": "$events.Add('clean')",
        "Invoke-InstallSlotBuild": "$events.Add('build')",
        "Invoke-UpdateSlotBuild": (
            "$events.Add('build'); throw 'candidate construction failed'"
            if fail_build else "$events.Add('build')"
        ),
        "Test-RuntimeHealthy": "return $true",
        "Invoke-VersionedMarkComplete": "$events.Add('complete'); return $true",
        "Invoke-VersionedActivate": "$events.Add('activate'); return $true",
        "Write-Binstubs": "$events.Add('bindings')",
        "Write-DeployManifest": "$events.Add('manifest')",
        "Get-VersionedCurrent": "return '1'",
        "Remove-VenvBackup": "",
        "Get-CopilotPersistentEnvironmentVariable": "return ''",
        "Get-ActiveEndpoint": "return @{ Bind = '127.0.0.1'; Port = 1 }",
        "Test-Path": "return ($args[0] -eq 'link')",
        "New-Item": "",
    }
    service_functions = (
        "Invoke-MigrationCheck", "Ensure-ScheduledTask", "Invoke-Start",
        "Invoke-ServingRuntimeReconcile", "Test-ActiveIsForward", "Write-UpdateMarker",
        "Clear-UpdateMarker", "Get-ActiveSignature", "Get-RunningProcess",
        "Invoke-UpdateStart", "Invoke-UpdateDrainStop", "Invoke-VersionedGc",
    )
    for name in service_functions:
        stubs[name] = (
            f"throw 'unexpected service effect: {name}'" if no_service
            else f"$events.Add('{name}')"
        )
    return (
        "Set-StrictMode -Version 2.0\n$ErrorActionPreference = 'Stop'\n"
        f"$NoService = ${str(no_service).lower()}\n"
        "$RequireGracefulCutover = $false\n$VersionedRuntime = $true\n"
        "$SrcVersion = '2'\n$InstallDir = 'runtime'\n$LocalBin = 'bin'\n"
        "$LinkPython = 'link'\n$VenvPython = 'absent-python'\n"
        "$publishGlobalBinstubs = $false\n$script:RuntimeConstructionReuse = $false\n"
        "$events = [Collections.Generic.List[string]]::new()\n$script:checks = 0\n"
        "function Write-Step {}\nfunction Write-Ok {}\nfunction Write-Skip {}\n"
        "function Write-Warn {}\nfunction Write-Fail { throw $args[0] }\n"
        + "\n".join(f"function {name} {{ {body} }}" for name, body in stubs.items())
        + "\nfunction Test-SlotContentCurrent { $script:checks++; return " + current + " }\n"
        + _extract_function(f"Invoke-{action}")
        + f"\ntry {{ Invoke-{action} }} finally {{\n"
        + "ConvertTo-Json -InputObject @($events) -Compress\n}\n"
    )


@pytest.mark.parametrize(
    ("action", "match"),
    [("Install", "same"), ("Install", "after-lock"), ("Install", "changed"),
     ("Update", "same"), ("Update", "changed")],
)
def test_runtime_only_retains_construction_without_service_effects(
    tmp_path: Path, action: str, match: str,
) -> None:
    result = _run(tmp_path, _lifecycle_source(action, match))
    assert result.returncode == 0, result.stderr
    events = json.loads(result.stdout.strip().splitlines()[-1])
    assert "manifest" in events
    if match == "changed":
        assert [event for event in events if event in {
            "build", "complete", "activate", "bindings", "manifest",
        }] == ["build", "complete", "activate", "bindings", "manifest"]
    else:
        assert "unlock" in events
        assert "retain" in events
        assert "build" not in events


def test_ordinary_same_content_update_still_reconciles_serving_runtime(tmp_path: Path) -> None:
    result = _run(tmp_path, _lifecycle_source("Update", "same", no_service=False))
    assert result.returncode == 0, result.stderr
    events = json.loads(result.stdout.strip().splitlines()[-1])
    assert events.index("Ensure-ScheduledTask") < events.index("Invoke-ServingRuntimeReconcile")
    assert events.index("Invoke-ServingRuntimeReconcile") < events.index("manifest")


def test_failed_runtime_only_update_leaves_recovery_and_markers_with_caller(tmp_path: Path) -> None:
    result = _run(tmp_path, _lifecycle_source("Update", "changed", fail_build=True))
    assert result.returncode != 0
    assert "candidate construction failed" in result.stderr
    assert "unexpected service effect" not in result.stderr
    events = json.loads(result.stdout.strip().splitlines()[-1])
    assert "build" in events
    assert not set(events) & {"complete", "activate", "bindings", "manifest"}
