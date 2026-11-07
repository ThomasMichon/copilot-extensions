"""MCP bootstrap selection and propagation under native PowerShell hosts."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
SOURCE = (SCRIPTS / "init.ps1").read_text(encoding="utf-8")
HOSTS = ["powershell.exe", "pwsh"] if os.name == "nt" else ["pwsh"]
pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows bootstrap candidate selection")


def _ps(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _run(host: str, script: str, tmp_path: Path) -> object:
    executable = shutil.which(host)
    if executable is None:
        pytest.skip(f"{host} unavailable")
    harness = tmp_path / "selection.ps1"
    harness.write_text(
        "Set-StrictMode -Version 2.0\n$ErrorActionPreference='Stop'\n" + script,
        encoding="utf-8",
    )
    result = subprocess.run(
        [executable, "-NoProfile", "-NonInteractive", "-File", str(harness)],
        capture_output=True, text=True, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize(
    "scenario",
    ["path", "launcher", "launcher-selector", "known-root", "unsupported", "missing", "admitted", "slot"],
)
def test_bootstrap_qualifies_each_candidate(tmp_path: Path, host: str, scenario: str) -> None:
    old = tmp_path / "old" / "python.exe"
    supported = tmp_path / "Programs" / "Python" / "Python313" / "python.exe"
    alias = tmp_path / "WindowsApps" / "python.exe"
    for path in (old, supported, alias):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    launcher = tmp_path / "py.exe"
    launcher.touch()
    slot = tmp_path / "slot"
    slot_python = slot / "Scripts" / "python.exe"
    slot_python.parent.mkdir(parents=True)
    slot_python.touch()
    commands = {
        "path": {"python3": [old, alias, supported]},
        "launcher": {"py": [launcher]},
        "launcher-selector": {"py": [launcher]},
        "known-root": {},
        "unsupported": {"python": [alias, old]},
        "missing": {},
        "admitted": {"python3": [old]},
        "slot": {"python": [supported]},
    }[scenario]
    command_map = "\n".join(
        f"$commands[{_ps(name)}]=@({','.join(_ps(p) for p in paths)})"
        for name, paths in commands.items()
    )
    function = re.search(r"(?ms)^function Get-BootstrapPython \{.*?^\}", SOURCE)
    assert function is not None
    # Execute the production probe in real Python; only candidate version/identity
    # facts are synthetic, so a missing version predicate fails this control.
    script = f"""
. {_ps(SCRIPTS / 'versioned_runtime_native.ps1')}
Rename-Item Function:\\Invoke-Hidden Invoke-RealHidden
$commands=@{{}}
{command_map}
$calls=New-Object 'System.Collections.Generic.List[string]'
function Get-Command {{
    param($Name, [switch]$All, $CommandType, $ErrorAction)
    foreach ($path in @($commands[$Name])) {{
        if ($path) {{
            [pscustomobject]@{{Source=$path;CommandType='Application'}}
            if (-not $All) {{ return }}
        }}
    }}
}}
function Invoke-Hidden {{
    param($FilePath, $ArgList, [ref]$ErrorOutput)
    $calls.Add([string]$FilePath)
    if ($FilePath -eq {_ps(launcher)}) {{
        $global:LASTEXITCODE=0
        if ($ArgList[0] -eq '-0p') {{
            if ({_ps(scenario)} -eq 'launcher-selector') {{ $global:LASTEXITCODE=1; return }}
            return " -V:3.9 * {_ps(old)[1:-1]}`n -V:3.13 {_ps(supported)[1:-1]}"
        }}
        if ($ArgList[0] -eq '-3.12') {{ return {_ps(supported)} }}
        return {_ps(old)}
    }}
    $version=if ($FilePath -in @({_ps(old)}, {_ps(slot_python)})) {{ '(3,9)' }} else {{ '(3,13)' }}
    if ({_ps(scenario)} -eq 'unsupported') {{ $version='(3,10)' }}
    $prefix='import sys; sys.version_info=' + $version + '; sys.executable=' +
        (ConvertTo-Json -Compress ([string]$FilePath)) + '; sys._base_executable=sys.executable; '
    $code=$ArgList[[array]::IndexOf($ArgList, '-c')+1]
    $mode=if ($ArgList[-1] -eq 'base') {{ 'base' }} else {{ 'executable' }}
    $captured=''
    Invoke-RealHidden -FilePath {_ps(sys.executable)} -ArgList @('-I','-S','-B','-c',($prefix+$code),$mode) `
        -ErrorOutput ([ref]$captured)
}}
$env:ProgramFiles={_ps(tmp_path / 'absent-program-files')}
${{env:ProgramFiles(x86)}}=$env:ProgramFiles
$env:LOCALAPPDATA={_ps(tmp_path if scenario == 'known-root' else tmp_path / 'absent-local')}
$VenvDir={_ps(slot)}
$LinkDir=$VenvDir
{function.group()}
"""
    if scenario == "admitted":
        script += f"$script:McpBootstrapPython={_ps(supported)}\n"
    script += (
        "$selected=Get-BootstrapPython" + ("" if scenario == "slot" else " -ExternalOnly") + "\n"
        "@{selected=$selected;calls=@($calls.ToArray())} | ConvertTo-Json -Compress\n"
    )
    details = _run(host, script, tmp_path)
    expected = None if scenario in {"unsupported", "missing"} else str(supported)
    assert details["selected"] == expected
    assert str(alias) not in details["calls"]
    if scenario == "admitted":
        assert details["calls"][0] == str(supported)
    if scenario in {"path", "unsupported"}:
        assert str(old) in details["calls"]


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize("uv", ["available", "fails", "missing"])
def test_venv_fallback_retains_selected_python(tmp_path: Path, host: str, uv: str) -> None:
    start = SOURCE.index("# -- 2. Create venv")
    start = SOURCE.index("if ($Force -or -not (Test-Path $VenvPython)) {", start)
    end = SOURCE.index("# -- 3. Install the package", start)
    selected = tmp_path / "supported" / "python.exe"
    venv = tmp_path / "candidate"
    venv_python = venv / "Scripts" / "python.exe"
    script = f"""
$env:OS='Windows_NT'
$pythonCmd={_ps(selected)}
$VenvDir={_ps(venv)}
$VenvPython={_ps(venv_python)}
$Force=$false
$script:RuntimeConstructionReuse=$false
$calls=New-Object 'System.Collections.Generic.List[object]'
function Invoke-VersionedSlotClean {{}}
function Write-Step {{}}
function Write-Ok {{}}
function Write-Skip {{}}
function Write-Fail {{ param($Message); throw $Message }}
function Get-Command {{
    param($Name, $ErrorAction)
    if ($Name -eq 'uv' -and {_ps(uv)} -ne 'missing') {{ return 'uv' }}
}}
function Invoke-Hidden {{
    param($FilePath, $ArgList)
    $calls.Add(@{{file=$FilePath;args=@($ArgList)}})
    $global:LASTEXITCODE=0
    if ($FilePath -eq 'uv' -and {_ps(uv)} -eq 'fails') {{ $global:LASTEXITCODE=1; return }}
    [void][IO.Directory]::CreateDirectory((Split-Path -Parent $VenvPython))
    [IO.File]::WriteAllText($VenvPython, '')
}}
{SOURCE[start:end]}
ConvertTo-Json -InputObject @($calls.ToArray()) -Depth 4 -Compress
"""
    calls = _run(host, script, tmp_path)
    if uv != "missing":
        arguments = calls[0]["args"]
        assert arguments[arguments.index("--python") + 1] == str(selected)
    if uv != "available":
        assert calls[-1]["file"] == str(selected)
        assert calls[-1]["args"] == ["-m", "venv", str(venv)]
