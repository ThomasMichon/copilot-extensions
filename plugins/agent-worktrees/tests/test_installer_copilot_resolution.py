from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from native_shell_support import bash_path as _bash_path
from native_shell_support import run_headless

PLUGIN = Path(__file__).resolve().parents[1]
INSTALL_SH = PLUGIN / "scripts" / "install.sh"
INSTALL_PS1 = PLUGIN / "scripts" / "install.ps1"


def _bash_resolver_source() -> str:
    source = INSTALL_SH.read_text(encoding="utf-8")
    body = source.split("resolve_executable_command_path() {", 1)[1].split(
        "\n}\n\ndeploy_copilot_plugin()",
        1,
    )[0]
    return "resolve_executable_command_path() {" + body + "\n}\n"


@pytest.mark.portfolio_tier("T3")
@pytest.mark.effect("process")
@pytest.mark.effect("filesystem")
def test_bash_resolver_ignores_aliases_and_functions_for_path_command(
    tmp_path: Path, native_bash: str,
) -> None:
    bash = native_bash
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
        result = run_headless(
            [bash, "-c", script],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip().casefold() == bash_executable.casefold()


@pytest.mark.portfolio_tier("T3")
@pytest.mark.effect("process")
@pytest.mark.effect("filesystem")
def test_bash_resolver_rejects_function_without_path_command(native_bash: str) -> None:
    bash = native_bash
    script = (
        "set -euo pipefail\n"
        f"{_bash_resolver_source()}\n"
        "copilot_only_function() { :; }\n"
        "if resolve_executable_command_path copilot_only_function; then exit 9; fi\n"
    )
    result = run_headless(
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


@pytest.mark.portfolio_tier("T3")
@pytest.mark.effect("process")
@pytest.mark.effect("filesystem")
@pytest.mark.parametrize(
    ("payload", "origin", "expected_updates"),
    [
        ("stage/agent-worktrees", ".copilot/installed-plugins/example/agent-worktrees", 0),
        (".copilot/installed-plugins/example/agent-worktrees", "", 0),
        ("stage/agent-worktrees", "source/agent-worktrees", 1),
        (".copilot/installed-plugins-backup/agent-worktrees", "", 1),
    ],
)
def test_bash_payload_refresh_uses_original_source(
    tmp_path: Path,
    native_bash: str,
    payload: str,
    origin: str,
    expected_updates: int,
) -> None:
    executable = tmp_path / "copilot"
    calls = tmp_path / "calls"
    executable.write_text(
        '#!/usr/bin/env bash\n'
        'printf "%s\\n" "$*" >> "$CALLS"\n'
        'case "$*" in\n'
        '  "plugin marketplace list") printf "copilot-extensions\\n" ;;\n'
        '  "plugin list") printf "agent-worktrees@copilot-extensions\\n" ;;\n'
        '  "plugin update agent-worktrees@copilot-extensions") ;;\n'
        '  *) exit 9 ;;\n'
        'esac\n',
        encoding="utf-8",
    )
    executable.chmod(0o755)
    source = INSTALL_SH.read_text(encoding="utf-8")
    functions = []
    for name in ("_source_kind", "deploy_copilot_plugin"):
        body = source.split(f"{name}() {{", 1)[1].split("\n}\n", 1)[0]
        functions.append(f"{name}() {{" + body + "\n}\n")
    script = (
        "set -euo pipefail\n"
        + f"PATH={shlex_quote(_bash_path(native_bash, tmp_path))}:$PATH\n"
        + _bash_resolver_source()
        + "\n".join(functions)
        + "\nok() { :; }; changed() { :; }; warn() { echo \"$*\" >&2; exit 8; }\n"
        + "err() { echo \"$*\" >&2; exit 8; }\n"
        + "deploy_copilot_plugin\n"
    )
    result = run_headless(
        [native_bash, "-c", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={
            **os.environ,
            "HOME": _bash_path(native_bash, tmp_path),
            "PLUGIN_DIR": _bash_path(native_bash, tmp_path / payload),
            "COPILOT_PLUGIN_STAGED_FROM": _bash_path(native_bash, tmp_path / origin) if origin else "",
            "CALLS": _bash_path(native_bash, calls),
        },
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    invocations = calls.read_text(encoding="utf-8").splitlines()
    assert invocations.count("plugin update agent-worktrees@copilot-extensions") == expected_updates


@pytest.mark.portfolio_tier("T3")
@pytest.mark.effect("process")
@pytest.mark.effect("filesystem")
def test_powershell_resolver_handles_command_types_and_rejects_function(
    tmp_path: Path, native_powershell: str,
) -> None:
    powershell = native_powershell
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
    if (-not $functionAst) { throw "Missing installer function: $name" }
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
    result = run_headless(
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
    external_result = run_headless(
        [*commands["external"], *forwarded],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert external_result.returncode == 0, external_result.stderr
    assert json.loads(external_result.stdout) == forwarded

    if os.name == "nt":
        batch_result = run_headless(
            [*commands["batch"], *forwarded],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
        assert batch_result.returncode == 0, batch_result.stderr
        assert json.loads(batch_result.stdout) == forwarded


@pytest.mark.portfolio_tier("T3")
@pytest.mark.effect("process")
@pytest.mark.effect("filesystem")
@pytest.mark.parametrize(
    ("payload", "origin", "expected_updates"),
    [
        ("stage/agent-worktrees", ".copilot/installed-plugins/example/agent-worktrees", 0),
        (".copilot/installed-plugins/example/agent-worktrees", "", 0),
        ("stage/agent-worktrees", "source/agent-worktrees", 1),
        (".copilot/installed-plugins-backup/agent-worktrees", "", 1),
    ],
)
def test_powershell_payload_refresh_uses_original_source(
    tmp_path: Path,
    native_powershell: str,
    payload: str,
    origin: str,
    expected_updates: int,
) -> None:
    script = r"""
$ErrorActionPreference = 'Stop'
$tokens = $null
$errors = $null
$source = Get-Content -LiteralPath $env:INSTALLER -Raw
$ast = [System.Management.Automation.Language.Parser]::ParseInput(
    $source, [ref]$tokens, [ref]$errors
)
if ($errors.Count) { throw "Invalid installer: $errors" }
foreach ($name in @('Get-SourceKind', 'Invoke-NativeCapture', 'Deploy-CopilotPlugin')) {
    $functionAst = $ast.Find({
        param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
            $node.Name -eq $name
    }, $true)
    if (-not $functionAst) { throw "Missing installer function: $name" }
    Invoke-Expression $functionAst.Extent.Text
}

function Resolve-CopilotCommand { 'Invoke-FixtureCopilot' }
function Write-ServiceOk { param($Message) }
function Write-ServiceChanged { param($Message) }
function Write-ServiceWarn { param($Message); throw $Message }
function Write-ServiceErr { param($Message); throw $Message }
function Invoke-FixtureCopilot {
    $global:LASTEXITCODE = 0
    switch ($args -join '|') {
        'plugin|marketplace|list' { 'copilot-extensions' }
        'plugin|list' { 'agent-worktrees@copilot-extensions' }
        'plugin|update|agent-worktrees@copilot-extensions' { $script:Updates++ }
        default { throw "Unexpected Copilot invocation: $args" }
    }
}
$script:Updates = 0
$PluginDir = [pscustomobject]@{Path = $env:PAYLOAD}
Deploy-CopilotPlugin
[pscustomobject]@{Updates = $script:Updates} | ConvertTo-Json -Compress
"""
    result = run_headless(
        [native_powershell, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={
            **os.environ,
            "USERPROFILE": str(tmp_path),
            "INSTALLER": str(INSTALL_PS1),
            "PAYLOAD": str(tmp_path / payload),
            "COPILOT_PLUGIN_STAGED_FROM": str(tmp_path / origin) if origin else "",
        },
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["Updates"] == expected_updates


@pytest.mark.portfolio_tier("T3")
@pytest.mark.effect("process")
@pytest.mark.effect("filesystem")
@pytest.mark.parametrize(
    ("failure", "warning", "call_count"),
    [
        ("", None, 5),
        ("plugin marketplace list", "Failed to inspect marketplaces", 1),
        ("plugin list", "Failed to inspect plugins", 2),
        ("plugin update agent-worktrees@copilot-extensions", "Plugin update failed", 5),
        ("plugin uninstall agent-worktrees", "Failed to remove stale _direct", 5),
    ],
)
def test_powershell_plugin_deployment_uses_native_exit_not_stderr(
    tmp_path: Path, native_powershell: str, failure: str, warning: str | None,
    call_count: int,
) -> None:
    executable = tmp_path / "copilot_fixture.py"
    calls = tmp_path / "calls.jsonl"
    executable.write_text(
        "import json,os,pathlib,sys\n"
        "command=' '.join(sys.argv[1:])\n"
        "with pathlib.Path(os.environ['CALLS']).open('a') as log:\n"
        "    log.write(json.dumps(command)+'\\n')\n"
        "print('Package extraction took 10ms', file=sys.stderr, flush=True)\n"
        "if command == os.environ['FAILURE']: sys.exit(9)\n"
        "if command == 'plugin marketplace list': print('copilot-extensions')\n"
        "if command == 'plugin list':\n"
        "    print('agent-worktrees@copilot-extensions\\nagent-worktrees')\n",
        encoding="utf-8",
    )
    script = r"""
$ErrorActionPreference = 'Stop'
$tokens = $null
$errors = $null
$source = Get-Content -LiteralPath $env:INSTALLER -Raw
$ast = [System.Management.Automation.Language.Parser]::ParseInput(
    $source, [ref]$tokens, [ref]$errors
)
if ($errors.Count) { throw "Invalid installer: $errors" }
foreach ($name in @('Get-SourceKind', 'Invoke-NativeCapture', 'Deploy-CopilotPlugin')) {
    $functionAst = $ast.Find({
        param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
            $node.Name -eq $name
    }, $true)
    if (-not $functionAst) { throw "Missing installer function: $name" }
    Invoke-Expression $functionAst.Extent.Text
}
function Resolve-CopilotCommand { $env:FIXTURE_PYTHON; $env:FIXTURE_COPILOT }
function Write-ServiceOk { param($Message) }
function Write-ServiceChanged { param($Message); $script:Changes += $Message }
function Write-ServiceWarn { param($Message); $script:Warnings += $Message }
function Write-ServiceErr { param($Message); throw $Message }
$script:Warnings = @()
$script:Changes = @()
$PluginDir = [pscustomobject]@{Path = $env:PAYLOAD}
Deploy-CopilotPlugin
if ($ErrorActionPreference -ne 'Stop') { throw 'Native capture leaked its error policy' }
[pscustomobject]@{Warnings = @($script:Warnings); Changes = @($script:Changes)} |
    ConvertTo-Json -Compress
"""
    result = run_headless(
        [native_powershell, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
        env={
            **os.environ, "INSTALLER": str(INSTALL_PS1), "USERPROFILE": str(tmp_path),
            "PAYLOAD": str(tmp_path / "source"), "COPILOT_PLUGIN_STAGED_FROM": "",
            "FIXTURE_PYTHON": sys.executable, "FIXTURE_COPILOT": str(executable),
            "CALLS": str(calls), "FAILURE": failure,
        },
    )
    assert result.returncode == 0, result.stderr
    outcome = json.loads(result.stdout)
    assert len(calls.read_text(encoding="utf-8").splitlines()) == call_count
    if warning is None:
        assert outcome["Warnings"] == []
        assert outcome["Changes"] == ["Removed stale _direct plugin install"]
    else:
        assert len(outcome["Warnings"]) == 1
        assert warning in outcome["Warnings"][0]
        assert "Removed stale _direct plugin install" not in outcome["Changes"] or (
            failure.startswith("plugin update ")
        )
