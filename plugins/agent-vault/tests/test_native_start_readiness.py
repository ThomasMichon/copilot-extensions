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
@pytest.mark.parametrize("task_state,exit_code,installation_id", [
    ("Ready", 0, "fixture-installation"), ("Ready", 23, "fixture-installation"),
    ("Running", 0, "fixture-installation"), ("Running", 23, "fixture-installation"),
    ("missing", 23, "fixture-installation"), ("Disabled", 0, "fixture-installation"),
    ("Disabled", 23, "fixture-installation"),
    ("Ready", 0, ""), ("missing", 23, ""),
])
def test_native_start_preserves_readiness_and_disabled_policy(
    tmp_path: Path, shell_name: str, task_state: str, exit_code: int, installation_id: str,
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
    restored = tmp_path / "restored.json"
    selected_environment = {
        "AGENT_RT_ROOT": str(root),
        "AGENT_VAULT_HOME": str(root),
        "AGENT_VAULT_RUN_DIR": str(root / "run"),
        "AGENT_VAULT_CORE_RUN_DIR": str(root / "core"),
        "AGENT_VAULT_CACHE_DIR": str(root / "cache"),
        "AGENT_VAULT_SOCKET": str(root / "run" / "agent-vault.sock"),
        "AGENT_VAULT_PIPE": r"\\.\pipe\agent-vault-fixture",
        "AGENT_VAULT_PID": str(root / "run" / "agent-vault-service.pid"),
        "AGENT_VAULT_LOG": str(root / "logs" / "agent-vault-service.log"),
        "AGENT_VAULT_CONFIG": str(tmp_path / "selected project" / "native-default.json"),
        "AGENT_VAULT_TASK_NAME": "fixture",
        "AGENT_VAULT_INSTALLATION_ID": installation_id or None,
        "AGENT_VAULT_PORT": "0" if installation_id else None,
    }
    (package / "configuration.py").write_text(
        "import os\nfrom pathlib import Path\n"
        "def service_configuration_snapshot(project):\n"
        f"    assert os.environ['AGENT_RT_ROOT'] == {str(root)!r}\n"
        f"    assert os.environ['AGENT_VAULT_HOME'] == {str(root)!r}\n"
        "    return {'root':project, 'config':str(Path(project)/'native-default.json'),"
        " 'native_default_owner':'1'}\n",
        encoding="utf-8",
    )
    (package / "service.py").write_text(
        "import json, os, pathlib, sys\n"
        f"pathlib.Path({str(observed)!r}).write_text(json.dumps({{"
        "'arguments': sys.argv[1:], 'runtime': sys.prefix, 'cwd': os.getcwd(), "
        f"'environment': {{key: os.environ.get(key) or None for key in {list(selected_environment)!r}}}"
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
param($Source, $Root, $Project, $TaskState, $Restored, $InstallationId)
$ErrorActionPreference = 'Stop'
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($Source,[ref]$tokens,[ref]$errors)
if ($errors.Count) { throw 'installer source has parse errors' }
foreach($name in @('Get-VaultServiceConfiguration','Get-ActiveVaultPython','Invoke-Start')) {
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
$RunDir=Join-Path $Root 'run'
$SocketPath=Join-Path $RunDir 'agent-vault.sock'
$PipePath='\\.\pipe\agent-vault-fixture'
$PidFile=Join-Path $RunDir 'agent-vault-service.pid'
$LogFile=Join-Path $Root 'logs\agent-vault-service.log'
$environmentNames=@(
    'HOME','RUN_DIR','CORE_RUN_DIR','CACHE_DIR','SOCKET','PIPE',
    'PID','LOG','TASK_NAME','INSTALLATION_ID','PORT','CONFIG'
) | ForEach-Object { "AGENT_VAULT_$_" }
foreach ($name in $environmentNames) {
    [Environment]::SetEnvironmentVariable($name, "retained-$name", 'Process')
}
[Environment]::SetEnvironmentVariable('AGENT_VAULT_CACHE_DIR', $null, 'Process')
$before=@{}
foreach ($name in $environmentNames) {
    $before[$name]=[Environment]::GetEnvironmentVariable($name, 'Process')
}
$env:COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT=$Project
$env:AGENT_RT_ROOT='retained-root'
[void](Get-ActiveVaultPython)
if ($env:AGENT_RT_ROOT -cne 'retained-root') { throw 'resolver changed caller context' }
try { Invoke-Start } finally {
    if ($env:AGENT_RT_ROOT -cne 'retained-root') { throw 'start changed caller runtime root' }
    $after=@{}
    foreach ($name in $environmentNames) {
        $after[$name]=[Environment]::GetEnvironmentVariable($name, 'Process')
    }
    @{before=$before; after=$after} | ConvertTo-Json -Depth 3 |
        Set-Content -LiteralPath $Restored -Encoding UTF8
}
""", encoding="utf-8")
    environment = {
        key: value for key, value in os.environ.items()
        if key.upper() not in {"PYTHONPATH", "PYTHONHOME"}
    }
    result = subprocess.run(
        [shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(driver),
         str(INSTALLER), str(root), str(project), task_state, str(restored), installation_id],
        capture_output=True, text=True, timeout=20, env=environment,
        **agent_procutil.no_window_kwargs(),
    )
    assert result.returncode == exit_code, result.stdout + result.stderr
    assert json.loads(observed.read_text(encoding="utf-8")) == {
        "arguments": [
            "--ensure-selected", "--configuration-root", str(project), "--native-default-owner",
        ],
        "runtime": str(active), "cwd": str(project), "environment": selected_environment,
    }
    restoration = json.loads(restored.read_text(encoding="utf-8-sig"))
    assert restoration["after"] == restoration["before"]
    assert {path: path.read_bytes() for path in before} == before
