"""Installer freshness compares package versions, not SDK slot identities."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN / "scripts"
ENGINE = PLUGIN.parents[1] / "libs" / "installer-engine"
PROFILE_A = ".profile." + "a" * 16
PROFILE_B = ".profile." + "b" * 16

pytestmark = pytest.mark.portfolio_tier("T3")


def _ps(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _function(name: str) -> str:
    text = (SCRIPTS / "install.ps1").read_text(encoding="utf-8")
    match = re.search(rf"(?ms)^function {re.escape(name)} \{{.*?^\}}", text)
    assert match is not None
    return match.group() + "\n"


def _run(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command, cwd=cwd, capture_output=True, text=True, timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


@pytest.mark.parametrize(
    ("action", "current", "desired", "skip_install", "skip_stamp", "failure"),
    [
        ("update", "", "1.2.3-dev4" + PROFILE_A, False, False, None),
        (
            "update", "", "1.2.3-dev4" + PROFILE_A + ".repair." + "c" * 32,
            False, False, None,
        ),
        (
            "update", "1.2.4-dev1" + PROFILE_A, "1.2.3-dev4",
            True, False, None,
        ),
        (
            "update", "1.2.3-dev4" + PROFILE_B, "1.2.3-dev4" + PROFILE_A,
            False, False, None,
        ),
        (
            "stamp", "1.2.4-dev1" + PROFILE_A, "1.2.3-dev4",
            False, True, None,
        ),
        (
            "update", "", "1.2.3-dev4.profile.bad",
            False, False, "Invalid agent-logger payload version",
        ),
    ],
)
def test_powershell_lock_accepts_sdk_profile_identities(
    tmp_path: Path, action: str, current: str, desired: str,
    skip_install: bool, skip_stamp: bool, failure: str | None,
) -> None:
    shell = shutil.which("pwsh")
    if shell is None:
        pytest.skip("PowerShell unavailable")
    root = tmp_path / "runtime"
    root.mkdir()
    if current:
        slot = root / "versions" / current
        slot.mkdir(parents=True)
        (slot / ".install-complete.json").write_text("{}", encoding="utf-8")
        (root / "current-version").write_text(current, encoding="utf-8")
        (root / "deploy-manifest.json").write_text(
            json.dumps({"source": {"version": current}}), encoding="utf-8",
        )
    script = (
        "$ErrorActionPreference = 'Stop'\n"
        f". {_ps(ENGINE / 'installer-engine.ps1')}\n"
        f". {_ps(SCRIPTS / 'versioned_runtime_native.ps1')}\n"
        f"$ScriptDir = {_ps(SCRIPTS)}\n"
        f"$InstallDir = {_ps(root)}\n"
        f"$SrcVersion = {_ps(desired)}\n"
        f"$Action = {_ps(action)}\n"
        "$WheelProfile = ''\n"
        f"$script:publicationPython = {_ps(sys.executable)}\n"
        "$script:SourceRuntimeIdentity = $null\n"
        "$script:InstallLockStream = $null\n"
        "$script:SkipPackageInstall = $false\n"
        "$script:SkipStamp = $false\n"
        "function Assert-SourceRuntime {}\n"
        + _function("Get-BootstrapPython")
        + _function("ConvertTo-AgentLoggerVersionKey")
        + _function("Enter-LoggerInstallLock")
        + """
try {
    Enter-LoggerInstallLock
    @{ skip_install = $script:SkipPackageInstall; skip_stamp = $script:SkipStamp } |
        ConvertTo-Json -Compress
} finally {
    if ($script:InstallLockStream) { $script:InstallLockStream.Dispose() }
}
"""
    )
    path = tmp_path / "lock.ps1"
    path.write_text(script, encoding="utf-8")
    result = _run([shell, "-NoProfile", "-NonInteractive", "-File", str(path)], tmp_path)
    if failure is not None:
        assert result.returncode != 0
        assert failure in result.stdout + result.stderr
        return
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {
        "skip_install": skip_install, "skip_stamp": skip_stamp,
    }


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        ("1.2.4-dev1" + PROFILE_A, "1.2.3-dev9" + PROFILE_B, 1),
        ("1.2.3-dev4" + PROFILE_A, "1.2.3-dev4" + PROFILE_B, 0),
        ("1.2.3.dev4" + PROFILE_A, "1.2.3-dev4", 0),
    ],
)
def test_posix_comparison_projects_sdk_profile_identities(
    tmp_path: Path, left: str, right: str, expected: int,
) -> None:
    if os.name == "nt":
        git = shutil.which("git")
        candidate = Path(git).parent.parent / "bin" / "bash.exe" if git else None
        shell = str(candidate) if candidate is not None and candidate.is_file() else None
    else:
        shell = shutil.which("bash")
    if shell is None:
        pytest.skip("Git Bash or native Bash unavailable")
    text = (SCRIPTS / "install.sh").read_text(encoding="utf-8")
    match = re.search(r"(?ms)^        __version_cmp\(\) \{.*?^        \}", text)
    assert match is not None
    script = (
        "set -euo pipefail\n"
        f"SCRIPT_DIR={shlex.quote(str(SCRIPTS))}\n"
        f"__lock_py={shlex.quote(sys.executable)}\n"
        + match.group()
        + f"\n__version_cmp {shlex.quote(left)} {shlex.quote(right)}\n"
    )
    path = tmp_path / "compare.sh"
    path.write_text(script, encoding="utf-8")
    result = _run([shell, str(path)], tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == str(expected)
