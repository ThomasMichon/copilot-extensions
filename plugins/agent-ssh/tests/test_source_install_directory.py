"""Selected source, not the caller's project, supplies installer configuration."""
from __future__ import annotations

from pathlib import Path
import re
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.portfolio_tier("T3")
@pytest.mark.parametrize("host", ["powershell", "pwsh"])
@pytest.mark.parametrize("failure", [False, True])
def test_powershell_package_install_restores_caller_directory(
    tmp_path: Path, host: str, failure: bool,
) -> None:
    executable = shutil.which(host)
    if executable is None:
        pytest.skip(f"{host} unavailable")
    source = tmp_path / "source"
    caller = tmp_path / "unrelated-project"
    source.mkdir()
    caller.mkdir()
    text = (ROOT / "scripts/install.ps1").read_text(encoding="utf-8")
    match = re.search(r"(?ms)^function Install-AgentSshPackage \{.*?^\}", text)
    assert match
    runner = tmp_path / "directory.ps1"
    runner.write_text(
        r"""
param($Source, $Failure)
$ErrorActionPreference = 'Stop'
$beforeProvider = (Get-Location).ProviderPath
$beforeNative = [IO.Directory]::GetCurrentDirectory()
$script:calls = 0
function Resolve-VenueCopilot { return '' }
function Write-Fail($Message) {}
function Invoke-UvPipInstallResilient {
    param($UvCommand, $Arguments, $PayloadDirToScrub)
    if ((Get-Location).ProviderPath -ne $Source -or
        [IO.Directory]::GetCurrentDirectory() -ne $Source) {
        throw 'dependency installation inherited the caller directory'
    }
    $script:calls++
    return [pscustomobject]@{ ExitCode = [int]$Failure; Output = '' }
}
""" + match.group() + r"""
$result = Install-AgentSshPackage -Python 'fixture-python' -Source $Source -UvCommand 'fixture-uv'
if ($result -ne ($Failure -eq '0')) { throw 'wrong package installation outcome' }
if ($script:calls -ne $(if ($Failure -eq '0') { 2 } else { 1 })) { throw 'wrong invocation count' }
if ((Get-Location).ProviderPath -ne $beforeProvider -or
    [IO.Directory]::GetCurrentDirectory() -ne $beforeNative) {
    throw 'package installation changed the caller directory'
}
Write-Output 'directory-restored'
""", encoding="utf-8",
    )
    result = subprocess.run(
        [executable, "-NoProfile", "-File", str(runner), str(source), "37" if failure else "0"],
        cwd=caller, capture_output=True, text=True, timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "directory-restored" in result.stdout
