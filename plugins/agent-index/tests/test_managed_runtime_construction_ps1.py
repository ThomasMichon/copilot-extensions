"""Actual Index construction keeps managed selection separate from helper Python."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest
from agent_procutil import no_window_kwargs

from test_install_engine_adoption import function, stage_index_installer
from test_managed_bootstrap_reader_ps1 import quote, reader_environment, run, shell
from test_managed_bootstrap_reader_sh import prepared_helper as prepared_helper


@pytest.mark.parametrize("host", ["pwsh", "powershell.exe"])
@pytest.mark.parametrize("scenario", [
    "raw-cli", "packaged-server", "engine", "partial", "unprepared",
    "uv-failure", "missing-bound-uv", "signed", "explicit-wheel",
])
def test_native_construction_retains_managed_selection_and_slot_custody(
    tmp_path: Path, host: str, scenario: str,
    prepared_helper: tuple[Path, Path, Path], request: pytest.FixtureRequest,
) -> None:
    executable = shell(host)
    uv, helper, managed_root = prepared_helper
    probe = subprocess.run(
        [str(uv), "--no-config", "python", "find", "--no-project", "--system",
         "--managed-python", "--no-python-downloads", "3.10"],
        env={**os.environ, "UV_PYTHON_INSTALL_DIR": str(managed_root)},
        capture_output=True, text=True, timeout=15, **no_window_kwargs(),
    )
    if probe.returncode:
        pytest.skip("This acquisition-free control requires prepared managed Python 3.10")
    materialized = scenario != "raw-cli"
    payload = stage_index_installer(
        tmp_path / "repository/plugins/agent-index", materialized=materialized,
    )
    support = payload / "scripts" if materialized else payload.parents[1] / "libs/installer-engine"
    env = reader_environment(tmp_path, prepared_helper, scenario)
    if scenario == "signed":
        base = Path(probe.stdout.strip()).resolve()
        installation = base.parent if os.name == "nt" else base.parent.parent
        private = tmp_path / "managed-\u00e9"
        shutil.copytree(installation, private / installation.name, symlinks=True)
        env["UV_PYTHON_INSTALL_DIR"] = str(private)
    env.update(
        UV_WORKING_DIR=str(tmp_path / "missing-working-directory"),
        UV_PROJECT=str(tmp_path / "missing-project"),
    )
    (tmp_path / ".python-version").write_text("3.9\n", encoding="utf-8")
    (tmp_path / "uv.toml").write_text("invalid project configuration\n", encoding="utf-8")
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    python = candidate / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    receipt = candidate / ".construction-receipt.json"
    receipt.write_text('{"owner":"retained-construction"}\n', encoding="utf-8")
    if scenario == "partial":
        (candidate / "pyvenv.cfg").write_text("partial candidate\n", encoding="utf-8")
    harness = tmp_path / "construct.ps1"
    harness.write_text(
        f"""
$ErrorActionPreference='Stop'
Set-StrictMode -Version 2
[Console]::OutputEncoding=[Text.Encoding]::GetEncoding(437)
. {quote(support / 'installer-engine.ps1')}
. {quote(payload / 'scripts/versioned_runtime_native.ps1')}
{function('Test-IndexVenv', 'ps1')}
{function('New-IndexVenv', 'ps1')}
function Write-Step {{ param($Message) }}
function Write-Warn {{ param($Message) [Console]::Error.WriteLine($Message) }}
$env:OS='Windows_NT'
$scenario={quote(scenario)}
$WheelProfile=if ($scenario -eq 'explicit-wheel') {{ 'admitted-fixture-profile' }} else {{ $null }}
$script:UvCommand=if ($scenario -eq 'missing-bound-uv') {{
    {quote(tmp_path / 'missing-bound-uv')}
}} else {{ {quote(uv)} }}
$Dir={quote(candidate)}
$Python={quote(python)}
$VenvDir=if ($scenario -eq 'raw-cli') {{ $Dir }} else {{ {quote(tmp_path / 'primary')} }}
$helper={quote(helper)}
$calls=New-Object 'System.Collections.Generic.List[string]'
$baseCalls=0
Rename-Item Function:\\Invoke-Hidden Invoke-RealHidden
function Invoke-Hidden {{
    param($FilePath,$ArgList,[hashtable]$Environment,[ref]$ErrorOutput,[Text.Encoding]$CaptureEncoding)
    if ($FilePath -eq {quote(uv)} -or $FilePath -eq $helper -or '-m' -in $ArgList) {{
        if ($Environment.UV_NO_CONFIG -cne '1' -or
            $null -ne $Environment.UV_CONFIG_FILE -or $null -ne $Environment.UV_PROJECT -or
            $null -ne $Environment.UV_WORKING_DIR -or $null -ne $Environment.UV_PYTHON -or
            $null -ne $Environment.UV_NO_MANAGED_PYTHON -or
            $null -ne $Environment.PYTHONHOME -or $null -ne $Environment.PYTHONPATH) {{
            throw 'construction inherited interpreter or project selectors'
        }}
    }}
    if ($FilePath -eq {quote(uv)}) {{
        if ($null -eq $CaptureEncoding -or $CaptureEncoding.CodePage -ne 65001) {{
            throw 'UV path output requires explicit UTF-8 capture'
        }}
        $calls.Add(($ArgList -join ' '))
        if ($scenario -eq 'uv-failure') {{ $global:LASTEXITCODE=19; return 'fixture UV failure' }}
    }} elseif ('-m' -in $ArgList) {{
        if ($FilePath -eq $helper -and -not $WheelProfile) {{ throw 'helper fallback used' }}
        $script:baseCalls++
        if (($ArgList -join '|') -ne "-I|-B|-m|venv|--copies|$Dir") {{
            throw 'selected base construction lost isolation'
        }}
    }}
    return Invoke-RealHidden @PSBoundParameters
}}
function Get-AuthenticodeSignature {{
    param($Path)
    [pscustomobject]@{{Status=$(if ($scenario -eq 'signed') {{'Valid'}} else {{'Unknown'}})}}
}}
function Get-SignedBasePython {{ throw 'ambient signed Python discovery' }}
function Get-Command {{
    [CmdletBinding()]param($Name,$CommandType)
    if ($Name -in @('python','python3','py')) {{ throw 'ambient Python discovery' }}
    return Microsoft.PowerShell.Core\\Get-Command @PSBoundParameters
}}
$before=$env:PATH
$cwd=(Get-Location).Path
$success=$scenario -notin @('unprepared','uv-failure','missing-bound-uv')
$samples=@()
$outcomes=@()
$versions=@()
for ($cycle=0; $cycle -lt $(if ($success) {{2}} else {{1}}); $cycle++) {{
    $clock=[Diagnostics.Stopwatch]::StartNew()
    $outcomes+=New-IndexVenv -Dir $Dir -Python $Python -PythonCmd $helper `
        -PreferSignedPython ($scenario -ne 'engine')
    $clock.Stop()
    $samples+=$clock.Elapsed.TotalSeconds
    if ($success -and $outcomes[-1]) {{
        $version=& $Python -I -B -c 'import sys; print(*sys.version_info[:2], sep=chr(46))'
        if ($LASTEXITCODE -ne 0) {{ throw 'constructed interpreter failed' }}
        $versions+="$version".Trim()
    }}
}}
@{{outcomes=$outcomes;versions=$versions;samples_seconds=$samples;calls=@($calls.ToArray());
    base_calls=$baseCalls;unchanged=($env:PATH -ceq $before -and (Get-Location).Path -ceq $cwd -and
    $env:UV_PYTHON -ceq 'unrelated' -and $env:UV_NO_MANAGED_PYTHON -ceq '1' -and
    $env:UV_CONFIG_FILE -ceq 'missing-relative.toml' -and $env:UV_NO_CONFIG -ceq 'false' -and
    $env:UV_WORKING_DIR -ceq {quote(env['UV_WORKING_DIR'])} -and
    $env:UV_PROJECT -ceq {quote(env['UV_PROJECT'])})}} | ConvertTo-Json -Compress
""",
        encoding="utf-8",
    )
    result = run(executable, harness, env)
    assert result.returncode == 0, result.stdout + result.stderr
    details = json.loads(result.stdout)
    successful = scenario not in {"unprepared", "uv-failure", "missing-bound-uv"}
    assert details["unchanged"], details
    assert details["outcomes"] == [successful] * (2 if successful else 1), details
    assert details["versions"] == (
        ["3.11" if scenario == "explicit-wheel" else "3.10"] * 2 if successful else []
    ), details
    assert receipt.read_text(encoding="utf-8") == '{"owner":"retained-construction"}\n'
    find = "python find --managed-python --no-project --system --no-python-downloads 3.10"
    create = f"venv {candidate} --python 3.10 --managed-python --no-project --allow-existing"
    expected_calls = (
        [] if scenario in {"missing-bound-uv", "explicit-wheel"} else
        [find, find] if scenario == "signed" else
        [create] if scenario == "engine" else
        [find, create, find] if successful else [find, create]
    )
    assert details["calls"] == expected_calls, details
    assert details["base_calls"] == int(scenario in {"signed", "explicit-wheel"}), details
    if not successful:
        assert not python.exists()
    request.node.user_properties.append(("index_windows_construction_performance", json.dumps({
        "host": host, "scenario": scenario, "layout": "packaged" if materialized else "raw",
        "status": "measured", "completed": len(details["samples_seconds"]),
        "samples_seconds": details["samples_seconds"], "uv_calls": len(expected_calls),
        "network": "disabled", "runtime_python": "3.10", "helper_python": "3.11.18",
        "signature_policy": "controlled", "cache": "fresh/retained private UV cache",
        "unavailable": ["full package install latency", "child CPU", "tail qualification"],
    })))
