"""Native ingress inspects prepared managed Python before driver admission."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from test_runtime_source_native import LIBRARY, REPOSITORY, _quote, _run


def _scripts(area: Path) -> Path:
    scripts = area / "scripts"
    scripts.mkdir()
    shutil.copyfile(LIBRARY / "versioned_runtime_native.ps1", scripts / "versioned_runtime_native.ps1")
    for name in ("bootstrap-uv.ps1", "installer-instance-python-version.txt"):
        shutil.copyfile(REPOSITORY / "libs/installer-engine" / name, scripts / name)
    return scripts


@pytest.mark.parametrize("host", ["pwsh", "powershell.exe"])
@pytest.mark.parametrize(
    "scenario",
    ["prepared", "missing-uv", "missing-python", "failed-lookup",
     "removal-inside", "broker-remove-inside", "inspect-inside", "below-minimum", "alias-base"],
)
def test_native_bootstrap_is_prepared_managed_only(tmp_path: Path, host: str, scenario: str) -> None:
    scripts = _scripts(tmp_path)
    uv = tmp_path / ("uv.exe" if os.name == "nt" else "uv")
    uv.touch()
    root = tmp_path / "runtime"
    inside = scenario in {"removal-inside", "broker-remove-inside", "inspect-inside"}
    base = (root if inside else tmp_path / "base") / "python.exe"
    if scenario == "alias-base":
        base = tmp_path / "WindowsApps" / "python.exe"
    base.parent.mkdir(parents=True, exist_ok=True)
    base.touch()
    action = {
        "removal-inside": "uninstall", "broker-remove-inside": "broker-remove",
    }.get(scenario, "inspect")
    script = f"""
. {_quote(scripts / 'versioned_runtime_native.ps1')}
Rename-Item Function:\\Invoke-Hidden Invoke-RealHidden
$calls=New-Object 'System.Collections.Generic.List[string]'
function Get-Command {{
    param($Name,[switch]$All,$CommandType,$ErrorAction)
    if ($Name -ne 'uv') {{ throw "ambient Python discovery: $Name" }}
    if ({_quote(scenario)} -ne 'missing-uv') {{
        [pscustomobject]@{{Source={_quote(uv)};CommandType='Application'}}
    }}
}}
function Invoke-Hidden {{
    param($FilePath,$ArgList,[ref]$ErrorOutput,[hashtable]$Environment)
    if ($FilePath -eq {_quote(uv)}) {{
        $calls.Add(($ArgList -join ' '))
        if ($Environment['UV_PYTHON'] -ne $null -or
            $Environment['VIRTUAL_ENV'] -ne $null) {{ throw 'selection was not isolated' }}
        $global:LASTEXITCODE=0
        if ($ArgList[0] -eq '--version') {{ return 'uv 0.13.7' }}
        if ($ArgList[0] -ne 'python' -or $ArgList[1] -ne 'find' -or
            $ArgList -notcontains '--managed-python' -or
            $ArgList -notcontains '--no-python-downloads' -or
            $ArgList[-1] -ne 'cpython@3.11.18') {{ throw 'unexpected uv operation' }}
        if ({_quote(scenario)} -eq 'missing-python') {{
            $global:LASTEXITCODE=2
            $ErrorOutput.Value='error: No interpreter found for cpython@3.11.18'
            return ''
        }}
        if ({_quote(scenario)} -eq 'failed-lookup') {{
            $global:LASTEXITCODE=9
            $ErrorOutput.Value='lookup failed'
        }}
        return {_quote(base)}
    }}
    $calls.Add('probe')
    if ($FilePath -ne {_quote(base)}) {{ throw 'probe used unselected Python' }}
    if ($Environment['PYTHON_MANAGER_AUTOMATIC_INSTALL'] -cne 'false') {{
        throw 'probe did not suppress automatic installation'
    }}
    $code=$ArgList[[array]::IndexOf($ArgList,'-c')+1]
    $version=if ({_quote(scenario)} -eq 'below-minimum') {{ '(3,9)' }} else {{ '(3,11,18)' }}
    $prefix='import sys; sys.version_info=' + $version + '; sys._base_executable=' +
        (ConvertTo-Json -Compress ([string]{_quote(base)})) + '; '
    $captured=''
    $output=Invoke-RealHidden -FilePath {_quote(sys.executable)} -ArgList @(
        '-I','-S','-B','-c',($prefix+$code),$ArgList[-2],$ArgList[-1]
    ) -Environment $Environment -ErrorOutput ([ref]$captured)
    $ErrorOutput.Value=$captured
    return $output
}}
$env:UV_PYTHON='unrelated'
$env:VIRTUAL_ENV='unrelated'
$env:PYTHON_MANAGER_AUTOMATIC_INSTALL='true'
$before=$env:PATH
$selected=$null
$failure=$null
try {{
    $selected=Resolve-NativeBootstrapPython -Root {_quote(root)} -Action {_quote(action)}
}} catch {{ $failure=$_.Exception.Message }}
@{{selected=$selected;failure=$failure;calls=@($calls.ToArray());
    unchanged=($env:PATH -ceq $before -and $env:UV_PYTHON -ceq 'unrelated' -and
        $env:VIRTUAL_ENV -ceq 'unrelated' -and
        $env:PYTHON_MANAGER_AUTOMATIC_INSTALL -ceq 'true')}} | ConvertTo-Json -Compress
"""
    result = _run(host, script, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    details = json.loads(result.stdout)
    assert details["unchanged"]
    assert not (details["failure"] or "").startswith("ambient Python discovery:")
    accepted = scenario in {"prepared", "inspect-inside"}
    assert details["selected"] == (str(base) if accepted else None), details
    if scenario == "failed-lookup":
        assert details["failure"] and "lookup failed" in details["failure"]
        assert "probe" not in details["calls"]
    else:
        assert details["failure"] is None, details
    if scenario == "missing-uv":
        assert details["calls"] == []
    elif scenario not in {"missing-python", "failed-lookup"}:
        assert details["calls"][-1] == "probe"


@pytest.mark.parametrize("host", ["powershell.exe", "pwsh"])
def test_native_discovery_suppresses_manager_install_only_in_child(tmp_path: Path, host: str) -> None:
    script = f"""
$env:PYTHON_MANAGER_AUTOMATIC_INSTALL='true'
$code='import json,os; print(json.dumps(os.environ["PYTHON_MANAGER_AUTOMATIC_INSTALL"]))'
$output=Invoke-Hidden -FilePath {_quote(getattr(sys, '_base_executable', sys.executable))} -ArgList @(
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
def test_native_ingress_runs_driver_with_prepared_managed_interpreter(tmp_path: Path, host: str) -> None:
    uv = shutil.which("uv")
    if not uv:
        pytest.skip("prepared uv unavailable")
    found = subprocess.run(
        [uv, "python", "find", "--no-project", "--system", "--managed-python",
         "--no-python-downloads", "cpython@3.11.18"],
        capture_output=True, text=True, timeout=10,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
    )
    if found.returncode != 0:
        pytest.skip("managed CPython 3.11.18 not prepared")
    expected = Path(found.stdout.strip()).resolve()
    payload = tmp_path / "payload"
    payload.mkdir()
    scripts = _scripts(payload)
    (scripts / "installer_driver.py").write_text(
        "import json,sys\nprint(json.dumps({'argv':sys.argv[1:],'base':sys._base_executable}))\n",
        encoding="utf-8",
    )
    root = tmp_path / "runtime"
    script = f"""
. {_quote(scripts / 'versioned_runtime_native.ps1')}
function Get-Command {{
    param($Name,$CommandType,$ErrorAction)
    if ($Name -ne 'uv') {{ throw 'ambient Python discovery is forbidden' }}
    [pscustomobject]@{{Source={_quote(uv)};CommandType='Application'}}
}}
$env:COPILOT_EXTENSIONS_NATIVE_INSTALLER='fixture-plugin'
$before=$env:PATH
$entry=Enter-NativeInstallerDriver -Payload {_quote(payload)} -Root {_quote(root)} `
    -Plugin 'fixture-plugin' -Action 'install' -Options @('two words', 'a"b')
@{{python=$entry.Python;participant=$entry.Participant;exitCode=$entry.ExitCode;
    pathUnchanged=($env:PATH -ceq $before);
    runtimeCreated=(Test-Path -LiteralPath {_quote(root)})}} | ConvertTo-Json -Compress
"""
    result = _run(host, script, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    driver, entry = map(json.loads, result.stdout.splitlines())
    assert entry["exitCode"] == 0
    assert entry["participant"] is True
    assert entry["runtimeCreated"] is False
    assert entry["pathUnchanged"] is True
    assert Path(entry["python"]).samefile(expected)
    assert Path(driver["base"]).samefile(expected)
    assert driver["argv"] == [
        "--payload", str(payload), "--root", str(root),
        "--shell", driver["argv"][5], "--plugin", "fixture-plugin",
        "--action", "install", "--option", "two words", "--option", 'a"b',
        "--check-native",
    ]
    assert Path(driver["argv"][5]).is_file()
