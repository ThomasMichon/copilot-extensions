from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


PLUGIN = Path(__file__).resolve().parents[1]
INSTALL_SH = PLUGIN / "scripts" / "install.sh"
INSTALL_PS1 = PLUGIN / "scripts" / "install.ps1"


def _bash() -> str | None:
    candidate = shutil.which("bash")
    if candidate is None:
        return None
    if os.name == "nt" and "WindowsApps" in candidate:
        return None
    try:
        probe = subprocess.run(
            [candidate, "-c", "exit 7"],
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if os.name == "nt":
        cygpath = subprocess.run(
            [candidate, "-c", "command -v cygpath >/dev/null 2>&1"],
            timeout=30,
        )
        if cygpath.returncode != 0:
            return None
    return candidate if probe.returncode == 7 else None


def _bash_resolver_source() -> str:
    source = INSTALL_SH.read_text(encoding="utf-8")
    body = source.split("resolve_executable_command_path() {", 1)[1].split(
        "\n}\n\ndeploy_copilot_plugin()",
        1,
    )[0]
    return "resolve_executable_command_path() {" + body + "\n}\n"


def _bash_path(bash: str, path: Path) -> str:
    if os.name != "nt":
        return str(path)
    result = subprocess.run(
        [bash, "-c", 'cygpath -u -- "$1"', "_", str(path)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@pytest.mark.skipif(_bash() is None, reason="a conformant Bash is unavailable")
def test_bash_resolver_ignores_aliases_and_functions_for_path_command(
    tmp_path: Path,
) -> None:
    bash = _bash()
    assert bash is not None
    executable = tmp_path / "copilot"
    executable.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    resolver = _bash_resolver_source()
    bash_dir = _bash_path(bash, tmp_path)
    bash_executable = _bash_path(bash, executable)

    for shadow in (
        "alias copilot='printf alias'",
        "copilot() { printf function; }",
    ):
        script = (
            "set -euo pipefail\n"
            "shopt -s expand_aliases\n"
            f"{resolver}\n"
            f"{shadow}\n"
            f"PATH={shlex_quote(bash_dir)}:$PATH\n"
            "resolve_executable_command_path copilot\n"
        )
        result = subprocess.run(
            [bash, "-c", script],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip().casefold() == bash_executable.casefold()


@pytest.mark.skipif(_bash() is None, reason="a conformant Bash is unavailable")
def test_bash_resolver_rejects_function_without_path_command() -> None:
    bash = _bash()
    assert bash is not None
    script = (
        "set -euo pipefail\n"
        f"{_bash_resolver_source()}\n"
        "copilot_only_function() { :; }\n"
        "if resolve_executable_command_path copilot_only_function; then exit 9; fi\n"
    )
    result = subprocess.run(
        [bash, "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def shlex_quote(value: str) -> str:
    """Quote one test path for Bash without depending on a shell process."""
    return "'" + value.replace("'", "'\"'\"'") + "'"


def _powershell() -> str | None:
    return (
        (shutil.which("powershell.exe") if os.name == "nt" else None)
        or shutil.which("pwsh")
        or shutil.which("powershell")
    )


@pytest.mark.skipif(_powershell() is None, reason="PowerShell is unavailable")
def test_powershell_resolver_handles_command_types_and_rejects_function(
    tmp_path: Path,
) -> None:
    powershell = _powershell()
    assert powershell is not None
    application = tmp_path / "copilot.exe"
    batch = tmp_path / "copilot.cmd"
    external_script = tmp_path / "copilot.ps1"
    application.touch()
    batch.write_text(
        '@echo off\r\necho ["%1","%2","%3"]\r\n',
        encoding="ascii",
    )
    external_script.write_text(
        "@($args) | ConvertTo-Json -Compress\n",
        encoding="utf-8",
    )

    script = r"""
$tokens = $null
$errors = $null
$source = Get-Content -LiteralPath $env:INSTALLER -Raw
$ast = [System.Management.Automation.Language.Parser]::ParseInput(
    $source, [ref]$tokens, [ref]$errors
)
foreach ($name in @(
    'Resolve-WinGetPackageExecutable',
    'Get-ApplicationPath',
    'Get-CurrentPowerShellPath',
    'Resolve-CopilotCommand'
)) {
    $functionAst = $ast.Find({
        param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
            $node.Name -eq $name
    }, $true)
    Invoke-Expression $functionAst.Extent.Text
}

function Get-Command {
    [CmdletBinding()]
    param(
        [Parameter(Position=0)][string]$Name,
        [System.Management.Automation.CommandTypes]$CommandType,
        [switch]$All
    )
    $commands = switch ($script:Case) {
        'application' {
            if ($Name -eq 'copilot') {
                [pscustomobject]@{ CommandType = 'Application'; Source = $env:APP }
            }
        }
        'batch' {
            if ($Name -eq 'copilot') {
                [pscustomobject]@{ CommandType = 'Application'; Source = $env:BATCH }
            }
        }
        'external' {
            if ($Name -eq 'copilot') {
                [pscustomobject]@{
                    CommandType = 'ExternalScript'
                    Source = $env:SCRIPT
                    Path = $env:SCRIPT
                }
            }
        }
        'alias-application' {
            if ($Name -eq 'copilot') {
                [pscustomobject]@{ CommandType = 'Alias'; Definition = 'copilot-real' }
            } elseif ($Name -eq 'copilot-real') {
                [pscustomobject]@{ CommandType = 'Application'; Source = $env:APP }
            }
        }
        'alias-external' {
            if ($Name -eq 'copilot') {
                [pscustomobject]@{ CommandType = 'Alias'; Definition = 'copilot-real' }
            } elseif ($Name -eq 'copilot-real') {
                [pscustomobject]@{
                    CommandType = 'ExternalScript'
                    Source = $env:SCRIPT
                    Path = $env:SCRIPT
                }
            }
        }
        'function-and-application' {
            if ($Name -eq 'copilot') {
                [pscustomobject]@{ CommandType = 'Function'; Source = '' }
                [pscustomobject]@{ CommandType = 'Application'; Source = $env:APP }
            }
        }
        'function-only' {
            if ($Name -eq 'copilot') {
                [pscustomobject]@{ CommandType = 'Function'; Source = '' }
            }
        }
    }
    if ($CommandType) {
        return @($commands | Where-Object {
            [string]$_.CommandType -eq [string]$CommandType
        })
    }
    return @($commands)
}

$results = @()
foreach ($case in @(
    'application',
    'batch',
    'external',
    'alias-application',
    'alias-external',
    'function-and-application'
)) {
    $script:Case = $case
    $results += [pscustomobject]@{
        Case = $case
        Command = @(Resolve-CopilotCommand)
    }
}
$script:Case = 'function-only'
try {
    Resolve-CopilotCommand | Out-Null
    $functionError = $null
} catch {
    $functionError = $_.Exception.Message
}
[pscustomobject]@{
    Results = $results
    FunctionError = $functionError
} | ConvertTo-Json -Depth 8 -Compress
"""
    result = subprocess.run(
        [powershell, "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={
            **os.environ,
            "INSTALLER": str(INSTALL_PS1),
            "APP": str(application),
            "BATCH": str(batch),
            "SCRIPT": str(external_script),
        },
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    commands = {item["Case"]: item["Command"] for item in payload["Results"]}
    assert commands["application"] == [str(application)]
    assert commands["alias-application"] == [str(application)]
    assert commands["function-and-application"] == [str(application)]
    for case in ("external", "alias-external"):
        assert commands[case][-3:] == ["-NoProfile", "-File", str(external_script)]
        assert Path(commands[case][0]).is_file()
    assert commands["batch"] == [str(batch)]
    assert "unsupported PowerShell command type(s): Function" in payload[
        "FunctionError"
    ]

    forwarded = ["plugin", "install", "example@marketplace"]
    external_result = subprocess.run(
        [*commands["external"], *forwarded],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert external_result.returncode == 0, external_result.stderr
    assert json.loads(external_result.stdout) == forwarded

    if os.name == "nt":
        batch_result = subprocess.run(
            [*commands["batch"], *forwarded],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
        assert batch_result.returncode == 0, batch_result.stderr
        assert json.loads(batch_result.stdout) == forwarded
