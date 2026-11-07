"""Index/Dispatch selection at real PowerShell function and call boundaries."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import runpy
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOSTS = ["powershell.exe", "pwsh"]
pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows installer selection")


def _ps(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _source(plugin: str) -> str:
    return (ROOT / "plugins" / plugin / "scripts" / "install.ps1").read_text(encoding="utf-8")


def _function(source: str, name: str) -> str:
    match = re.search(rf"(?ms)^function {name} \{{.*?^\}}", source)
    assert match is not None
    return match.group()


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
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize("plugin", ["agent-index", "agent-dispatch"])
def test_complete_installer_parses(tmp_path: Path, host: str, plugin: str) -> None:
    script = f"""
$tokens=$null
$errors=$null
[void][Management.Automation.Language.Parser]::ParseFile(
    {_ps(ROOT / 'plugins' / plugin / 'scripts' / 'install.ps1')}, [ref]$tokens, [ref]$errors)
if ($errors.Count) {{ throw ($errors | Out-String) }}
ConvertTo-Json -InputObject $true -Compress
"""
    assert _run(host, script, tmp_path) is True


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize("plugin", ["agent-index", "agent-dispatch"])
@pytest.mark.parametrize(
    "scenario", ["path", "launcher", "launcher-unc", "launcher-selector", "known-root", "unsupported", "admitted", "slot"],
)
def test_bootstrap_candidates(tmp_path: Path, host: str, plugin: str, scenario: str) -> None:
    old = tmp_path / "old" / "python.exe"
    supported = tmp_path / "Programs" / "Python" / "Python313" / "python.exe"
    alias = tmp_path / "WindowsApps" / "python.exe"
    launcher = tmp_path / "py.exe"
    slot = tmp_path / "slot"
    slot_python = slot / "Scripts" / "python.exe"
    unc = r"\\example.invalid\Python313\python.exe"
    for path in (old, supported, alias, launcher, slot_python):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    commands = {
        "path": {"python3": [old, alias, supported]},
        "launcher": {"py": [launcher]},
        "launcher-unc": {"py": [launcher]},
        "launcher-selector": {"py": [launcher]},
        "known-root": {},
        "unsupported": {"python": [alias, old]},
        "admitted": {"python3": [old]},
        "slot": {},
    }[scenario]
    command_map = "\n".join(
        f"$commands[{_ps(name)}]=@({','.join(_ps(p) for p in paths)})"
        for name, paths in commands.items()
    )
    pin = "IndexBootstrapPython" if plugin == "agent-index" else "DispatchBootstrapPython"
    script = f"""
. {_ps(ROOT / 'plugins' / plugin / 'scripts' / 'versioned_runtime_native.ps1')}
Rename-Item Function:\\Invoke-Hidden Invoke-RealHidden
$commands=@{{}}
{command_map}
$calls=New-Object 'System.Collections.Generic.List[string]'
function Resolve-DispatchBasePython {{ return {_ps(old)} }}
function Test-Path {{
    [CmdletBinding()]param([Parameter(Position=0)]$Path,$LiteralPath,$PathType)
    if ($LiteralPath -eq {_ps(unc)}) {{ return $true }}
    Microsoft.PowerShell.Management\\Test-Path @PSBoundParameters
}}
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
            return " -V:3.9 * {_ps(old)[1:-1]}`n -V:3.13 {_ps(unc if scenario == 'launcher-unc' else supported)[1:-1]}"
        }}
        if ($ArgList[0] -eq '-3.12') {{ return {_ps(supported)} }}
        return {_ps(old)}
    }}
    $version=if ($FilePath -eq {_ps(old)}) {{
        if ({_ps(scenario)} -in @('path','launcher','launcher-selector')) {{ '(3,9)' }} else {{ '(3,10)' }}
    }} else {{ '(3,13)' }}
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
{_function(_source(plugin), 'Get-BootstrapPython')}
"""
    if scenario == "admitted":
        script += f"$script:{pin}={_ps(supported)}\n"
    script += (
        "$selected=Get-BootstrapPython" + ("" if scenario == "slot" else " -ExternalOnly") + "\n"
        "@{selected=$selected;calls=@($calls.ToArray())} | ConvertTo-Json -Compress\n"
    )
    details = _run(host, script, tmp_path)
    expected = None if scenario == "unsupported" else str(slot_python if scenario == "slot" else supported)
    if scenario == "launcher-unc":
        expected = unc
    assert details["selected"] == expected
    assert str(alias) not in details["calls"]
    if scenario == "admitted":
        assert details["calls"][0] == str(supported)


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize("plugin", ["agent-index", "agent-dispatch"])
def test_native_participant_retains_admission(tmp_path: Path, host: str, plugin: str) -> None:
    source = _source(plugin)
    start = source.index("    $driverPayload =")
    end = source.index("\n}", start)
    pin = "IndexBootstrapPython" if plugin == "agent-index" else "DispatchBootstrapPython"
    script = f"""
$PSScriptRoot={_ps(ROOT / 'plugins' / plugin / 'scripts')}
$InstallDir={_ps(tmp_path)}
$Action='install'
$PSBoundParameters=@{{}}
$RetainedRuntime=''
$RetainedRuntimeSha256=''
function Enter-NativeInstallerDriver {{
    param($Payload,$Root,$Plugin,$Action,$Options,$RetainedRuntime,$RetainedRuntimeSha256)
    return @{{Participant=$true;ExitCode=0;Python={_ps(sys.executable)};Arguments=@()}}
}}
{source[start:end]}
ConvertTo-Json -InputObject (Get-Variable {_ps(pin)} -Scope Script -ValueOnly) -Compress
"""
    assert _run(host, script, tmp_path) == sys.executable


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize("consumer", ["index-runtime", "index-engine", "dispatch", "dispatch-selected"])
def test_construction_selection_call(tmp_path: Path, host: str, consumer: str) -> None:
    plugin = "agent-index" if consumer.startswith("index") else "agent-dispatch"
    source = _source(plugin)
    parent = "Install-Engine" if consumer == "index-engine" else "Install-Runtime"
    function = _function(source, parent)
    start = function.index("    $pythonCmd =")
    end = function.index('\n    Write-Ok "Python:', start) if consumer != "index-engine" else function.index("\n    Write-Host", start)
    selected = consumer == "dispatch-selected"
    script = f"""
$selectedBasePython={_ps('explicit-signed-3.10') if selected else '$null'}
$calls=0
function Get-BootstrapPython {{
    param([switch]$ExternalOnly)
    if (-not $ExternalOnly) {{ throw 'construction requires the external base' }}
    $script:calls++
    return 'admitted-3.13'
}}
function Write-Fail {{ param($Message); throw $Message }}
function Write-Warn {{ param($Message); throw $Message }}
{function[start:end]}
@{{selected=$pythonCmd;calls=$calls}} | ConvertTo-Json -Compress
"""
    details = _run(host, script, tmp_path)
    assert details == {"selected": "explicit-signed-3.10" if selected else "admitted-3.13", "calls": 0 if selected else 1}


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize("uv", ["available", "fails", "missing", "signed"])
def test_index_venv_propagation(tmp_path: Path, host: str, uv: str) -> None:
    script = f"""
$env:OS='Windows_NT'
$env:SystemDrive=[IO.Path]::GetPathRoot({_ps(tmp_path)}).TrimEnd('\\')
$VenvDir={_ps(tmp_path / 'candidate')}
$script:UvCommand=$(if ({_ps(uv)} -eq 'missing') {{ $null }} else {{ 'uv' }})
$healthy=$false
$calls=New-Object 'System.Collections.Generic.List[object]'
function Write-Step {{}}
function Write-Warn {{}}
function Get-SignedBasePython {{ if ({_ps(uv)} -eq 'signed') {{ return 'signed-3.10' }} }}
function Test-IndexVenv {{ param($Dir,$Python); return $script:healthy }}
function Invoke-NativeCapture {{
    param($ScriptBlock)
    & $ScriptBlock
    return @{{ExitCode=0;Output=''}}
}}
function admitted-3.13 {{
    $calls.Add(@{{kind='python';base='admitted-3.13';args=@($args)}})
    $script:healthy=$true
}}
function signed-3.10 {{
    $calls.Add(@{{kind='python';base='signed-3.10';args=@($args)}})
    $script:healthy=$true
}}
function Invoke-UvVenvResilient {{
    param($UvCommand,$VenvDir,$Arguments)
    $calls.Add(@{{kind='uv';args=@($Arguments)}})
    $script:healthy={_ps(uv)} -eq 'available'
    return @{{ExitCode=$(if ($script:healthy) {{ 0 }} else {{ 1 }});Output=''}}
}}
{_function(_source('agent-index'), 'New-IndexVenv')}
$result=New-IndexVenv -Dir $VenvDir -Python (Join-Path $VenvDir 'Scripts\\python.exe') -PythonCmd 'admitted-3.13'
@{{result=$result;calls=@($calls.ToArray())}} | ConvertTo-Json -Depth 5 -Compress
"""
    details = _run(host, script, tmp_path)
    assert details["result"] is True
    if uv in {"available", "fails"}:
        args = details["calls"][0]["args"]
        assert args[args.index("--python") + 1] == "admitted-3.13"
    if uv == "signed":
        assert len(details["calls"]) == 1
        assert details["calls"][0]["base"] == "signed-3.10"
        assert details["calls"][0]["args"][:3] == ["-m", "venv", "--copies"]
    if uv in {"fails", "missing"}:
        assert details["calls"][-1]["base"] == "admitted-3.13"
        assert details["calls"][-1]["args"] == ["-m", "venv", str(tmp_path / "candidate")]


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize("uv", ["available", "fails", "missing", "signed"])
def test_dispatch_venv_propagation(tmp_path: Path, host: str, uv: str) -> None:
    source = _source("agent-dispatch")
    start = source.index("    # -- venv (SAC-trusted")
    end = source.index("    # The selected MCP extra", start)
    script = f"""
$env:OS='Windows_NT'
$VenvDir={_ps(tmp_path / 'candidate')}
$VenvPython=Join-Path $VenvDir 'Scripts\\python.exe'
$pythonCmd='admitted-3.13'
$selectedBasePython=$(if ({_ps(uv)} -eq 'signed') {{ 'signed-3.10' }} else {{ $null }})
$UvCommand=$(if ({_ps(uv)} -eq 'missing') {{ $null }} else {{ 'uv' }})
$healthy=$false
$global:LASTEXITCODE=0
$calls=New-Object 'System.Collections.Generic.List[object]'
function Get-Command {{ param($Name,$ErrorAction); return $null }}
function Write-Step {{}}
function Write-Warn {{}}
function Write-Ok {{}}
function Write-Skip {{}}
function Write-Fail {{ param($Message); throw $Message }}
function Invoke-VersionedSlotClean {{}}
function Assert-DispatchCandidateBase {{ param($Python,$BasePython) }}
function Test-DispatchVenv {{ param($Dir,$Python); return $script:healthy }}
function Invoke-Hidden {{
    param($FilePath,$ArgList,[ref]$ErrorOutput)
    $calls.Add(@{{kind='python';base=$FilePath;args=@($ArgList)}})
    $script:healthy=$true
}}
function admitted-3.13 {{
    $calls.Add(@{{kind='python';base='admitted-3.13';args=@($args)}})
    $script:healthy=$true
}}
function Invoke-UvVenvResilient {{
    param($UvCommand,$VenvDir,$Arguments)
    $calls.Add(@{{kind='uv';args=@($Arguments)}})
    $script:healthy={_ps(uv)} -eq 'available'
    return @{{ExitCode=$(if ($script:healthy) {{ 0 }} else {{ 1 }});Output=''}}
}}
{source[start:end]}
ConvertTo-Json -InputObject @($calls.ToArray()) -Depth 5 -Compress
"""
    calls = _run(host, script, tmp_path)
    if uv in {"available", "fails"}:
        args = calls[0]["args"]
        assert args[args.index("--python") + 1] == "admitted-3.13"
    if uv == "signed":
        assert len(calls) == 1
        assert calls[0]["base"] == "signed-3.10"
        assert calls[0]["args"] == ["-I", "-B", "-m", "venv", "--copies", str(tmp_path / "candidate")]
    if uv in {"fails", "missing"}:
        assert calls[-1]["base"] == "admitted-3.13"
        assert calls[-1]["args"] == ["-m", "venv", str(tmp_path / "candidate")]


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize("plugin", ["agent-index", "agent-dispatch"])
@pytest.mark.parametrize("parent", ["captured", "windowless"])
def test_real_launcher_source_contract(tmp_path: Path, host: str, plugin: str, parent: str) -> None:
    # Reuse the existing two-cycle source fixture and native window observer.
    native = runpy.run_path(str(ROOT / "plugins/agent-mcp/tests/test_source_bootstrap_ps1.py"))
    functions = native["FIXTURE"]
    original = functions["_payload"]

    def payload(directory: Path, owner: str) -> tuple[Path, str]:
        staged, version = original(directory, owner)
        if owner == "agent-dispatch":
            shutil.copy2(
                ROOT / "plugins" / owner / "scripts" / "base-python.ps1",
                staged / "scripts" / "base-python.ps1",
            )
            receipt = staged / "runtime-source.json"
            identity = json.loads(receipt.read_text(encoding="utf-8"))
            api = functions["IDENTITY"]
            identity["assembly_hash"] = api.assembly_hash(staged)
            version = api.runtime_version(
                functions["VERSION"], identity["source_commit"], identity["assembly_hash"],
            )
            identity["runtime_version"] = version
            receipt.write_text(json.dumps(identity), encoding="utf-8")
        return staged, version

    functions["_payload"] = payload
    check = native[
        "test_source_selection_works_with_only_real_py_launcher" if parent == "captured" else
        "test_source_selection_stays_windowless_from_pythonw_for_two_cycles"
    ]
    check(tmp_path, plugin, shutil.which(host))
