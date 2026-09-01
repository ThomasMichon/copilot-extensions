"""Behavioral regression tests for deterministic Windows PSMux PATH repair."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_HELPER = _PLUGIN_ROOT / "scripts" / "psmux-path.ps1"
_INSTALLER = _PLUGIN_ROOT / "scripts" / "install.ps1"
_LAUNCHER = _PLUGIN_ROOT / "bin" / "launch-session.ps1"
_PWSH = shutil.which("pwsh") or shutil.which("powershell")


@pytest.mark.skipif(_PWSH is None, reason="PowerShell is not available")
def test_selects_exact_version_and_removes_all_stale_package_paths(tmp_path):
    package_root = tmp_path / "Microsoft" / "WinGet" / "Packages"
    old_dir = package_root / "marlocarlo.psmux_old"
    desired_dir = package_root / "marlocarlo.psmux_current"
    old_dir.mkdir(parents=True)
    desired_dir.mkdir()
    (old_dir / "psmux.exe").touch()
    (desired_dir / "psmux.exe").touch()
    keep = tmp_path / "keep"
    keep.mkdir()

    script = tmp_path / "probe.ps1"
    script.write_text(
        """
param($Helper, $Root, $Old, $Desired, $Keep)
. $Helper
$probe = {
    param($Path)
    if ($Path -like '*_old*') { return '3.3.3' }
    return '3.3.8'
}
$selected = Find-AwPsmuxPackageBinary -PackageRoot $Root `
    -DesiredVersion '3.3.8' -VersionProbe $probe
$repair = Repair-AwPsmuxPath -SelectedDirectory $selected.Directory `
    -UserPath "$Old;$Keep;$Desired" `
    -ProcessPath "$Keep;$Old;$Desired" -PackageRoot $Root
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
            str(keep),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    result = json.loads(proc.stdout)
    assert Path(result["selected"]) == desired_dir / "psmux.exe"
    for repaired in (result["userPath"], result["processPath"]):
        parts = repaired.split(";")
        assert Path(parts[0]) == desired_dir
        assert str(old_dir) not in parts
        assert parts.count(str(desired_dir)) == 1
        assert str(keep) in parts


@pytest.mark.skipif(_PWSH is None, reason="PowerShell is not available")
def test_failed_session_probe_is_unknown_and_pin_parser_uses_last_column(tmp_path):
    script = tmp_path / "probe.ps1"
    script.write_text(
        """
param($Helper)
. $Helper
$failed = Get-AwPsmuxSessionState -Path 'unused.exe' -SessionProbe {
    [pscustomobject]@{ ReturnCode = 1; Output = @() }
}
$pins = @'
Name  Id                        Version Source Pin type Pinned version
other prefix.marlocarlo.psmux  3.3.5  winget Gating   9.9.9
psmux marlocarlo.psmux         3.3.5  winget Gating   3.3.7
'@
[pscustomobject]@{
    known = $failed.Known
    count = $failed.Sessions.Count
    pin = Get-AwWingetPinVersion -Output $pins -PackageId 'marlocarlo.psmux'
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
    assert result == {"known": False, "count": 0, "pin": "3.3.7"}


def test_installer_and_launcher_use_exact_version_helper():
    helper = _HELPER.read_text(encoding="utf-8")
    installer = _INSTALLER.read_text(encoding="utf-8")
    launcher = _LAUNCHER.read_text(encoding="utf-8")
    assert "marlocarlo.psmux_*" in helper
    assert "Get-AwPsmuxBinaryVersion" in helper
    assert "Find-AwPsmuxPackageBinary" in installer
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
    assert "Find-AwPsmuxPackageBinary" in launcher
    assert "-DesiredVersion '3.3.8'" in launcher
    assert "$desiredVersion = '3.3.8'" in installer


def test_path_repair_does_not_mutate_live_sessions():
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
    assert "& winget install" not in unknown_branch
    assert "not replacing it" in unknown_branch
    assert "& winget install" not in live_branch
    assert "not replacing it now" in live_branch
    assert "--uninstall-previous --force" in ensure
