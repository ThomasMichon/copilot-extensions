"""Behavioral regression tests for deterministic Windows PSMux PATH repair."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from agent_procutil import no_window_kwargs

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_HELPER = _PLUGIN_ROOT / "scripts" / "psmux-path.ps1"
_INSTALLER = _PLUGIN_ROOT / "scripts" / "install.ps1"
_PWSH = shutil.which("pwsh") or shutil.which("powershell")


@pytest.mark.skipif(_PWSH is None, reason="PowerShell is not available")
def test_selects_newest_compatible_version_and_removes_stale_paths(tmp_path: Path) -> None:
    assert _PWSH is not None
    package_root = tmp_path / "Microsoft" / "WinGet" / "Packages"
    old_dir = package_root / "marlocarlo.psmux_old"
    desired_dir = package_root / "marlocarlo.psmux_current"
    newer_dir = package_root / "marlocarlo.psmux_newer"
    old_dir.mkdir(parents=True)
    desired_dir.mkdir()
    newer_dir.mkdir()
    (old_dir / "psmux.exe").touch()
    (desired_dir / "psmux.exe").touch()
    (newer_dir / "psmux.exe").touch()
    keep = tmp_path / "keep"
    keep.mkdir()

    script = tmp_path / "probe.ps1"
    script.write_text(
        """
param($Helper, $Root, $Old, $Desired, $Newer, $Keep)
. $Helper
$probe = {
    param($Path)
    if ($Path -like '*_old*') { return '3.3.5' }
    if ($Path -like '*_newer*') { return '3.4.1' }
    return '3.4.0'
}
$selected = Find-AwCompatiblePsmuxPackageBinary -PackageRoot $Root `
    -VersionProbe $probe
$repair = Repair-AwPsmuxPath -SelectedDirectory $selected.Directory `
    -UserPath "$Old;$Keep;$Desired;$Newer" `
    -ProcessPath "$Keep;$Old;$Desired;$Newer" -PackageRoot $Root
[pscustomobject]@{
    selected = $selected.Path
    userPath = $repair.UserPath
    processPath = $repair.ProcessPath
} | ConvertTo-Json -Compress
""",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [
            _PWSH,
            "-NoLogo",
            "-NoProfile",
            "-File",
            str(script),
            str(_HELPER),
            str(package_root),
            str(old_dir),
            str(desired_dir),
            str(newer_dir),
            str(keep),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    result = json.loads(proc.stdout)
    assert Path(result["selected"]) == newer_dir / "psmux.exe"
    for repaired in (result["userPath"], result["processPath"]):
        parts = repaired.split(";")
        assert Path(parts[0]) == newer_dir
        assert str(old_dir) not in parts
        assert str(desired_dir) not in parts
        assert parts.count(str(newer_dir)) == 1
        assert str(keep) in parts


@pytest.mark.skipif(_PWSH is None, reason="PowerShell is not available")
def test_failed_session_probe_is_unknown(tmp_path: Path) -> None:
    assert _PWSH is not None
    script = tmp_path / "probe.ps1"
    script.write_text(
        """
param($Helper)
. $Helper
$failed = Get-AwPsmuxSessionState -Path 'unused.exe' -SessionProbe {
    [pscustomobject]@{ ReturnCode = 1; Output = @() }
}
[pscustomobject]@{
    known = $failed.Known
    count = $failed.Sessions.Count
} | ConvertTo-Json -Compress
""",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [_PWSH, "-NoLogo", "-NoProfile", "-File", str(script), str(_HELPER)],
        capture_output=True,
        text=True,
        check=True,
    )
    result = json.loads(proc.stdout)
    assert result == {"known": False, "count": 0}


def test_installer_uses_compatible_version_helper() -> None:
    helper = _HELPER.read_text(encoding="utf-8")
    installer = _INSTALLER.read_text(encoding="utf-8")
    assert "marlocarlo.psmux_*" in helper
    assert "Get-AwPsmuxBinaryVersion" in helper
    assert "Find-AwCompatiblePsmuxPackageBinary" in installer
    assert "Repair-AwPsmuxPath" in installer
    assert "NoProfile/SSH-style verification" in installer
    assert (
        "Get-Command psmux -CommandType Application -ErrorAction SilentlyContinue |"
        in installer
    )
    assert (
        "Get-Command psmux -CommandType Application -ErrorAction Stop | "
        "Select-Object -First 1"
    ) in installer
    assert "Test-AwPsmuxVersionCompatible" in helper
    assert "[string]$MinimumVersion = '3.4.0'" in helper
    assert "[string[]]$BlockedVersions = @('3.3.6')" in helper
    assert "$installVersion = '3.4.0'" in installer
    assert "winget pin add" not in installer


@pytest.mark.skipif(_PWSH is None, reason="PowerShell is not available")
@pytest.mark.parametrize("version,returncode", [("3.4.0.0", 0), ("3.4.0.1", 1)])
def test_file_version_representation_matches_the_installer_banner_check(
    tmp_path: Path, version: str, returncode: int,
) -> None:
    assert _PWSH is not None
    installer = _INSTALLER.read_text(encoding="utf-8")
    start = installer.index("        $verify = ")
    verify = installer[start:installer.index("        $encodedVerify = ", start)]
    provider = tmp_path / "provider.ps1"
    provider.write_text("'psmux 3.4.0'\n", encoding="utf-8")
    script = tmp_path / "verify.ps1"
    script.write_text(
        "param($Helper,$Provider,$Version)\n. $Helper\n"
        "$script:Provider=$Provider\n"
        "$env:AW_PSMUX_EXPECTED_VERSION=Get-AwPsmuxBinaryVersion "
        "-Path 'unused.exe' -VersionProbe { $Version }\n"
        "function Get-Command { [pscustomobject]@{Source=$script:Provider} }\n"
        + verify
        + "& ([scriptblock]::Create($verify))\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [_PWSH, "-NoLogo", "-NoProfile", "-File", str(script), str(_HELPER),
         str(provider), version],
        capture_output=True, text=True, timeout=15, check=False,
        **no_window_kwargs(),
    )
    assert proc.returncode == returncode, proc.stdout + proc.stderr


@pytest.mark.skipif(_PWSH is None, reason="PowerShell is not available")
def test_compatibility_requires_340_for_new_provider_selection(tmp_path: Path) -> None:
    assert _PWSH is not None
    script = tmp_path / "probe.ps1"
    script.write_text(
        """
param($Helper)
. $Helper
$versions = @(
    '3.3.4', '3.3.5', '3.3.6', '3.3.6.0', '3.3.6.1',
    '3.3.7', '3.3.7.0', '3.3.8', '3.4.0', '3.4.0.0', '3.4.1'
) | ForEach-Object {
    [pscustomobject]@{
        version = $_
        compatible = Test-AwPsmuxVersionCompatible -Version $_
    }
}
[pscustomobject]@{
    versions = $versions
    nullCompatible = Test-AwPsmuxVersionCompatible -Version $null
    invalidCompatible = Test-AwPsmuxVersionCompatible -Version 'unknown'
} | ConvertTo-Json -Compress
""",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [_PWSH, "-NoLogo", "-NoProfile", "-File", str(script), str(_HELPER)],
        capture_output=True,
        text=True,
        check=True,
    )
    result = json.loads(proc.stdout)
    versions = {
        item["version"]: item["compatible"] for item in result["versions"]
    }
    assert versions == {
        "3.3.4": False,
        "3.3.5": False,
        "3.3.6": False,
        "3.3.6.0": False,
        "3.3.6.1": False,
        "3.3.7": False,
        "3.3.7.0": False,
        "3.3.8": False,
        "3.4.0": True,
        "3.4.0.0": True,
        "3.4.1": True,
    }
    assert result["nullCompatible"] is False
    assert result["invalidCompatible"] is False


@pytest.mark.skipif(_PWSH is None, reason="PowerShell is not available")
def test_passive_legacy_inspection_never_executes_the_provider(tmp_path: Path) -> None:
    assert _PWSH is not None
    marker = tmp_path / "must-not-run"
    provider = tmp_path / "legacy.ps1"
    provider.write_text(
        f"[IO.File]::WriteAllText('{marker.as_posix()}', 'executed')\n'unsafe session'\n",
        encoding="utf-8",
    )
    script = tmp_path / "probe.ps1"
    script.write_text(
        """
param($Helper, $Provider)
. $Helper
$state = Get-AwPsmuxSessionState -Path $Provider
function Get-AwPsmuxSessionState {
    param($Path)
    [pscustomobject]@{ Known = $false; Sessions = @() }
}
$version = Get-AwPsmuxBinaryVersion -Path $Provider
@{ count = $state.Sessions.Count; version = $version } | ConvertTo-Json -Compress
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        [_PWSH, "-NoProfile", "-File", str(script), str(_HELPER), str(provider)],
        capture_output=True, text=True, check=True,
    )
    assert json.loads(result.stdout) == {"count": 0, "version": None}
    assert not marker.exists()


@pytest.mark.skipif(_PWSH is None, reason="PowerShell is not available")
@pytest.mark.parametrize("state", ["missing", "missing-live", "missing-unknown", "cold", "live", "unknown"])
def test_installer_upgrades_only_without_retained_provider_activity(tmp_path: Path, state: str) -> None:
    assert _PWSH is not None
    helper = tmp_path / "psmux-path.ps1"
    helper.write_text(
        _HELPER.read_text(encoding="utf-8") + """
function Get-AwPsmuxBinaryVersion { param($Path) return '3.3.5' }
function Get-AwPsmuxSessionState {
    param($Path)
    $sessions = @()
    if ($script:Mode -like '*live') { $sessions = @('retained-session') }
    [pscustomobject]@{ Known = ($script:Mode -notlike '*unknown'); Sessions = $sessions }
}
function Find-AwCompatiblePsmuxPackageBinary {
    param($PackageRoot)
    [pscustomobject]@{ Version = '3.4.0' }
}
""",
        encoding="utf-8",
    )
    source = _INSTALLER.read_text(encoding="utf-8")
    start = source.index("function Ensure-Psmux {")
    end = source.index("# Helper: check if a WSL", start)
    script = tmp_path / "probe.ps1"
    script.write_text(
        """
param($Mode)
$ErrorActionPreference = 'Stop'
$script:Mode = $Mode
$script:Calls = [Collections.Generic.List[object]]::new()
$script:PathRepair = $false
$env:LOCALAPPDATA = $PSScriptRoot
function Get-Command {
    param($Name, $ErrorAction)
    if ($Name -eq 'psmux' -and $script:Mode -like 'missing*') { return $null }
    [pscustomobject]@{ Source = 'fixture-provider.exe' }
}
function Resolve-AwPsmuxBin { param($Cmd) 'fixture-provider.exe' }
function Ensure-PsmuxSshSafe { $script:PathRepair = $true }
function Write-ServiceWarn { param($Message) }
function Write-ServiceOk { param($Message) }
function Write-ServiceChanged { param($Message) }
function winget {
    $script:Calls.Add(@($args))
    $global:LASTEXITCODE = 0
}
""" + source[start:end] + """
Ensure-Psmux
@{ calls = @($script:Calls.ToArray()); pathRepair = $script:PathRepair } |
    ConvertTo-Json -Depth 4 -Compress
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        [_PWSH, "-NoProfile", "-File", str(script), state],
        capture_output=True, text=True, check=True,
    )
    observed = json.loads(result.stdout.splitlines()[-1])
    if state in {"live", "unknown", "missing-live", "missing-unknown"}:
        assert observed == {"calls": [], "pathRepair": False}
    else:
        command, = observed["calls"]
        assert command[command.index("--version") + 1] == "3.4.0"
        assert ("--uninstall-previous" in command) is (state == "cold")
        assert observed["pathRepair"] is True


def test_path_repair_does_not_mutate_live_sessions() -> None:
    installer = _INSTALLER.read_text(encoding="utf-8")
    start = installer.index("function Ensure-PsmuxSshSafe")
    end = installer.index("function Resolve-AwPsmuxBin", start)
    path_repair = installer[start:end]
    assert "kill-session" not in path_repair
    assert "& winget install" not in path_repair
    assert "& winget uninstall" not in path_repair

    ensure_start = installer.index("function Ensure-Psmux {")
    ensure_end = installer.index("# Helper: check if a WSL", ensure_start)
    ensure = installer[ensure_start:ensure_end]
    unknown_branch = ensure[
        ensure.index("if (-not $sessionState.Known)"):
        ensure.index("} elseif", ensure.index("if (-not $sessionState.Known)"))
    ]
    live_branch = ensure[
        ensure.index("elseif ($sessionState.Sessions.Count -gt 0)"):
        ensure.index("} else {", ensure.index("elseif ($sessionState.Sessions.Count -gt 0)"))
    ]
    assert "compatibility cannot be validated because the helper is missing" in ensure
    assert "compatibility cannot be validated because the helper is unavailable" in ensure
    assert "$psmuxVer = Get-AwPsmuxBinaryVersion -Path $muxBin" in ensure
    assert "& winget install" not in unknown_branch
    assert "not replacing it" in unknown_branch
    assert "& winget install" not in live_branch
    assert "not replacing it now" in live_branch
    assert "--uninstall-previous --force" in ensure
