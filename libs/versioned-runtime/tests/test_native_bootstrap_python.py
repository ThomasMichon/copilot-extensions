"""Native ingress discovers prerequisites before any installer driver action."""

import base64
import json
import os
from pathlib import Path
import sys

import pytest

from test_runtime_source_native import _quote as _ascii_quote, _run

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows native ingress")


def _quote(value):
    if not str(value).isascii():
        return "(ConvertFrom-Json " + _ascii_quote(json.dumps(str(value))) + ")"
    return _ascii_quote(value)


@pytest.mark.parametrize("host", ["powershell.exe", "pwsh"])
def test_native_path_admission_does_not_probe_unused_launcher(tmp_path, host):
    script = f"""
function Get-Command {{
    param($Name,[switch]$All,$CommandType,$ErrorAction)
    if ($Name -eq 'py') {{ throw 'discovery continued after supported PATH admission' }}
    if ($Name -eq 'python3') {{
        [pscustomobject]@{{Source={_quote(sys._base_executable)};CommandType='Application'}}
    }}
}}
$before=$env:PATH
$selected=Resolve-NativeBootstrapPython -MinimumMinor 10
if (-not $selected) {{ throw 'physical PATH reader was not retained' }}
if ($before -cne $env:PATH) {{ throw 'discovery changed PATH' }}
$selected | ConvertTo-Json -Compress
"""
    result = _run(host, script, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert Path(json.loads(result.stdout)).samefile(sys._base_executable)


@pytest.mark.parametrize("host", ["powershell.exe", "pwsh"])
def test_native_discovery_suppresses_manager_install_only_in_child(tmp_path, host):
    script = f"""
$env:PYTHON_MANAGER_AUTOMATIC_INSTALL='true'
$code='import json,os; print(json.dumps(os.environ["PYTHON_MANAGER_AUTOMATIC_INSTALL"]))'
$output=Invoke-Hidden -FilePath {_quote(sys._base_executable)} -ArgList @(
    '-I','-S','-B','-c',$code
) -Environment @{{PYTHON_MANAGER_AUTOMATIC_INSTALL='false'}}
if ($LASTEXITCODE -ne 0) {{ throw 'native discovery child failed' }}
if ($env:PYTHON_MANAGER_AUTOMATIC_INSTALL -cne 'true') {{ throw 'discovery changed parent policy' }}
$output
"""
    result = _run(host, script, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == "false"


@pytest.mark.parametrize("host", ["powershell.exe", "pwsh"])
@pytest.mark.parametrize(
    "scenario", ["path", "launcher-unc", "launcher-selector", "launcher-selector-unicode", "known-root", "unsupported", "missing", "removal"],
)
def test_native_ingress_qualifies_before_driver(tmp_path, host, scenario):
    root = tmp_path / "runtime"
    old = tmp_path / "old" / "python.exe"
    supported = tmp_path / "Programs" / "Python" / "Python313" / "python.exe"
    if scenario == "launcher-selector-unicode":
        supported = supported.parent / chr(0x4F8B) / "python.exe"
    alias = tmp_path / "WindowsApps" / "python.exe"
    launcher = tmp_path / "py.exe"
    inside = root / "python.exe"
    for path in (old, supported, alias, launcher, inside):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    unc = r"\\example.invalid\Python313\python.exe"
    commands = {
        "path": {"python3": [old, alias, supported]},
        "launcher-unc": {"py": [launcher]},
        "launcher-selector": {"py": [launcher]},
        "launcher-selector-unicode": {"py": [launcher]},
        "known-root": {},
        "unsupported": {"python": [old, alias]},
        "missing": {},
        "removal": {"python": [inside]},
    }[scenario]
    command_map = "\n".join(
        f"$commands[{_quote(name)}]=@({','.join(_quote(p) for p in paths)})"
        for name, paths in commands.items()
    )
    script = f"""
Rename-Item Function:\\Invoke-Hidden Invoke-RealHidden
$commands=@{{}}
{command_map}
$calls=New-Object 'System.Collections.Generic.List[string]'
$driverCalls=0
function Get-Command {{
    param($Name,[switch]$All,$CommandType,$ErrorAction)
    foreach ($path in @($commands[$Name])) {{
        if ($path) {{
            [pscustomobject]@{{Source=$path;CommandType='Application'}}
            if (-not $All) {{ return }}
        }}
    }}
}}
function Invoke-Hidden {{
    param($FilePath,$ArgList,[ref]$ErrorOutput,[switch]$StreamOutput)
    $calls.Add([string]$FilePath)
    if ($StreamOutput) {{
        $script:driverCalls++
        if ($FilePath -ne {_quote(supported)}) {{ throw 'driver used the wrong physical base' }}
        if ($ArgList -notcontains '--check-native') {{ throw 'participant admission was lost' }}
        if ($ArgList[[array]::IndexOf($ArgList, '--root')+1] -ne {_quote(root)}) {{
            throw 'discovery changed the requested installation root'
        }}
        $global:LASTEXITCODE=0
        return
    }}
    if ($FilePath -eq {_quote(launcher)}) {{
        $global:LASTEXITCODE=0
        if ($ArgList[0] -eq '-0p') {{
            if ({_quote(scenario)} -like 'launcher-selector*') {{ $global:LASTEXITCODE=1; return }}
            return " -V:3.9 * {_quote(old)[1:-1]}`n -V:3.13 {_quote(unc)[1:-1]}"
        }}
        $base=if ($ArgList[0] -eq '-3.12') {{ {_quote(supported)} }} else {{ {_quote(old)} }}
        $prefix='import sys; sys.stdout.reconfigure(encoding="cp1252",errors="strict"); sys.executable=' +
            (ConvertTo-Json -Compress ([string]$base)) + '; '
        $code=$ArgList[[array]::IndexOf($ArgList, '-c')+1]
        $captured=''
        return Invoke-RealHidden -FilePath {_quote(sys.executable)} -ArgList @(
            '-I','-S','-B','-c',($prefix+$code)
        ) -ErrorOutput ([ref]$captured)
    }}
    $version=if ($FilePath -eq {_quote(old)}) {{ '(3,9)' }} else {{ '(3,13)' }}
    $base=if ($FilePath -eq {_quote(inside)}) {{ {_quote(inside)} }} else {{ {_quote(supported)} }}
    # Candidate/version facts are controlled; the production probe and physical
    # base/removal assertions run in real Python against this local fixture.
    $prefix='import sys; sys.version_info=' + $version + '; sys._base_executable=' +
        (ConvertTo-Json -Compress ([string]$base)) + '; '
    $code=$ArgList[[array]::IndexOf($ArgList, '-c')+1]
    $captured=''
    Invoke-RealHidden -FilePath {_quote(sys.executable)} -ArgList @(
        '-I','-S','-B','-c',($prefix+$code),$ArgList[-2],$ArgList[-1]
    ) -ErrorOutput ([ref]$captured)
}}
$env:ProgramFiles={_quote(tmp_path / 'absent-program-files')}
${{env:ProgramFiles(x86)}}=$env:ProgramFiles
$env:LOCALAPPDATA={_quote(tmp_path if scenario == 'known-root' else tmp_path / 'absent-local')}
$env:COPILOT_EXTENSIONS_NATIVE_INSTALLER='fixture-plugin'
$before=$env:PATH
$refused=$false
$selected=$null
try {{
    $entry=Enter-NativeInstallerDriver -Payload {_quote(tmp_path)} -Root {_quote(root)} `
        -Plugin 'fixture-plugin' -Action {'uninstall' if scenario == 'removal' else 'install'}
    $selected=$entry.Python
}} catch {{
    if ($_.Exception.Message -notlike 'Native install and uninstall require*') {{ throw }}
    $refused=$true
}}
if ($selected) {{ $selected=[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($selected)) }}
@{{selected=$selected;refused=$refused;driverCalls=$driverCalls;calls=@($calls.ToArray());
    pathUnchanged=($env:PATH -ceq $before)}} | ConvertTo-Json -Compress
"""
    result = _run(host, script, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    details = json.loads(result.stdout)
    if details["selected"]:
        details["selected"] = base64.b64decode(details["selected"]).decode("utf-8")
    refused = scenario in {"unsupported", "missing", "removal"}
    assert details["refused"] is refused
    assert details["driverCalls"] == (0 if refused else 1)
    assert details["selected"] == (None if refused else str(supported))
    assert str(alias) not in details["calls"]
    assert details["pathUnchanged"] is True
    if scenario == "launcher-unc":
        assert unc in details["calls"]


@pytest.mark.parametrize("host", ["powershell.exe", "pwsh"])
def test_native_ingress_runs_driver_with_real_path_interpreter(tmp_path, host):
    payload = tmp_path / "payload"
    scripts = payload / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "installer_driver.py").write_text(
        "import json,sys\nprint(json.dumps({'argv':sys.argv[1:],'base':sys._base_executable}))\n",
        encoding="utf-8",
    )
    root = tmp_path / "runtime"
    script = f"""
$env:PATH={_quote(Path(sys.executable).parent)}
$env:COPILOT_EXTENSIONS_NATIVE_INSTALLER='fixture-plugin'
$entry=Enter-NativeInstallerDriver -Payload {_quote(payload)} -Root {_quote(root)} `
    -Plugin 'fixture-plugin' -Action 'install' -Options @('two words', 'a"b')
@{{python=$entry.Python;participant=$entry.Participant;exitCode=$entry.ExitCode;
    runtimeCreated=(Test-Path -LiteralPath {_quote(root)})}} | ConvertTo-Json -Compress
"""
    result = _run(host, script, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    driver, entry = map(json.loads, result.stdout.splitlines())
    assert entry["exitCode"] == 0
    assert entry["participant"] is True
    assert entry["runtimeCreated"] is False
    assert Path(entry["python"]).samefile(sys._base_executable)
    assert Path(driver["base"]).samefile(sys._base_executable)
    assert driver["argv"] == [
        "--payload", str(payload), "--root", str(root),
        "--shell", driver["argv"][5], "--plugin", "fixture-plugin",
        "--action", "install", "--option", "two words", "--option", 'a"b',
        "--check-native",
    ]
    assert Path(driver["argv"][5]).is_file()
