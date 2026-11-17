"""Native start must report the selected CLI's readiness, not task activation."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import venv

import agent_procutil
import pytest


INSTALLER = Path(__file__).resolve().parents[1] / "scripts" / "install.ps1"
pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows native start")


@pytest.mark.parametrize("shell_name", ["powershell.exe", "pwsh.exe"])
@pytest.mark.parametrize("task_state,exit_code", [
    ("Ready", 0), ("Ready", 23), ("Running", 0), ("Running", 23),
    ("missing", 23), ("Disabled", 0),
])
def test_native_start_preserves_readiness_and_disabled_policy(
    tmp_path: Path, shell_name: str, task_state: str, exit_code: int,
) -> None:
    shell = shutil.which(shell_name)
    if shell is None:
        pytest.skip(f"{shell_name} unavailable")
    root = tmp_path / "runtime"
    active = root / "versions" / "selected+base"
    venv.EnvBuilder(with_pip=False).create(active)
    python = active / "Scripts" / "python.exe"
    package = active / "Lib" / "site-packages" / "agent_vault"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    observed = tmp_path / "observed.json"
    (package / "__main__.py").write_text(
        "import json, os, pathlib, sys\n"
        f"pathlib.Path({str(observed)!r}).write_text(json.dumps({{"
        "'arguments': sys.argv[1:], 'runtime': sys.prefix, 'cwd': os.getcwd()"
        "}), encoding='utf-8')\n"
        f"raise SystemExit({exit_code})\n",
        encoding="utf-8",
    )
    resolver = root / "bin" / "resolve-runtime.ps1"
    resolver.parent.mkdir()
    resolver.write_text(
        "param($TraceMode)\n"
        "if ($TraceMode -cne 'read-only') { throw 'unexpected resolver mutation' }\n"
        f"$AgentRtPy = '{str(python).replace(chr(39), chr(39) * 2)}'\n",
        encoding="utf-8",
    )
    project = tmp_path / "selected project"
    project.mkdir()
    policy = root / "runtime-profile.json"
    policy.write_bytes(b"unbuilt desired profile must not select execution")
    before = {path: path.read_bytes() for path in (resolver, policy)}
    driver = tmp_path / "start.ps1"
    driver.write_text(r"""
param($Source, $Root, $Project, $TaskState)
$ErrorActionPreference = 'Stop'
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($Source,[ref]$tokens,[ref]$errors)
if ($errors.Count) { throw 'installer source has parse errors' }
foreach($name in @('Get-ActiveVaultPython','Invoke-Start')) {
    $node=$ast.FindAll({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst]},$true) |
        Where-Object Name -eq $name | Select-Object -First 1
    if (-not $node) { throw "missing native function $name" }
    Invoke-Expression $node.Extent.Text
}
function Get-ScheduledTask {
    if ($script:TaskState -ne 'missing') { [pscustomobject]@{State=$script:TaskState} }
}
function Start-ScheduledTask { throw 'task activation is not service readiness' }
function Write-Fail($message) { throw $message }
function Write-Ok($message) { Write-Output $message }
$InstallDir=$Root
$LinkPython=Join-Path $Root 'versions\unbuilt\Scripts\python.exe'
$TaskName='fixture'
$env:COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT=$Project
$env:AGENT_RT_ROOT='retained-root'
[void](Get-ActiveVaultPython)
if ($env:AGENT_RT_ROOT -cne 'retained-root') { throw 'resolver changed caller context' }
Invoke-Start
""", encoding="utf-8")
    environment = {
        key: value for key, value in os.environ.items()
        if key.upper() not in {"PYTHONPATH", "PYTHONHOME"}
    }
    result = subprocess.run(
        [shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(driver),
         str(INSTALLER), str(root), str(project), task_state],
        capture_output=True, text=True, timeout=20, env=environment,
        **agent_procutil.no_window_kwargs(),
    )
    if task_state == "Disabled":
        assert result.returncode != 0
        assert "scheduled task is disabled" in result.stderr
        assert not observed.exists()
    else:
        assert result.returncode == exit_code, result.stdout + result.stderr
        assert json.loads(observed.read_text(encoding="utf-8")) == {
            "arguments": ["start"], "runtime": str(active), "cwd": str(project),
        }
    assert {path: path.read_bytes() for path in before} == before
