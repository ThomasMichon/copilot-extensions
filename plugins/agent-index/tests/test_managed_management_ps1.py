"""Prepared-only installer management through real UV and native selectors."""
from __future__ import annotations

from collections.abc import Callable
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
    "raw-current", "packaged-lkg", "incomplete", "missing-uv", "private-uv",
])
def test_management_admits_only_prepared_completed_runtime(
    tmp_path: Path, host: str, scenario: str,
    prepared_helper: tuple[Path, Path, Path],
    record_testsuite_property: Callable[[str, object], None],
) -> None:
    executable = shell(host)
    uv, helper, _store = prepared_helper
    packaged = scenario == "packaged-lkg"
    payload = stage_index_installer(
        tmp_path / "repository/plugins/agent-index", materialized=packaged,
    )
    support = payload / "scripts" if packaged else payload.parents[1] / "libs/installer-engine"
    root = tmp_path / "owned index's \u00e9"
    slot = root / "versions/1.0.0"
    built = subprocess.run(
        [str(helper), "-I", "-B", "-m", "venv", "--copies", "--without-pip", str(slot)],
        capture_output=True, text=True, timeout=20, **no_window_kwargs(),
    )
    assert built.returncode == 0, built.stdout + built.stderr
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    site = next(slot.glob("Lib/site-packages" if os.name == "nt" else "lib/python*/site-packages"))
    package = site / "agent_index"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "__main__.py").write_text(
        "import json,os,sys\n"
        "print(json.dumps({'args':sys.argv[1:],'root':sys.prefix,"
        "'disabled':sys.dont_write_bytecode,'isolated':sys.flags.isolated,"
        "'cwd':os.getcwd(),'context':os.environ.get('INDEX_TEST_CONTEXT')}))\n"
        "raise SystemExit(23)\n", encoding="utf-8",
    )
    if scenario != "incomplete":
        (slot / ".install-complete.json").write_text(json.dumps({
            "version": "1.0.0", "completed_at": "fixture", "pid": 0,
        }), encoding="utf-8")
    (root / "current-version").write_text(
        "9.0.0" if packaged else "1.0.0", encoding="utf-8",
    )
    prospective = root / "versions/9.0.0" / python.relative_to(slot)
    prospective.parent.mkdir(parents=True)
    prospective.write_bytes(b"uncompleted interpreter must not run")
    if packaged:
        (root / "last-known-good").write_text("1.0.0", encoding="utf-8")
    if scenario == "private-uv":
        private_uv = root / "tool/uv.exe"
        private_uv.parent.mkdir()
        shutil.copy2(uv, private_uv)
        uv = private_uv
    before = {path.relative_to(root): path.read_bytes()
              for path in root.rglob("*") if path.is_file()}
    environment = reader_environment(tmp_path, prepared_helper, "prepared")
    environment.update(AGENT_RT_ROOT="retained-caller", INDEX_TEST_CONTEXT="retained")
    commands = tmp_path / "empty-path"
    commands.mkdir()
    environment["PATH"] = str(commands)
    functions = "\n".join(function(name, "ps1") for name in (
        "Get-IndexManagementPython", "Get-ActiveSlotPython",
    )).replace("$PSScriptRoot", quote(payload / "scripts"))
    harness = tmp_path / "management.ps1"
    harness.write_text(f"""
$ErrorActionPreference='Stop'
Set-StrictMode -Version 2
[Console]::OutputEncoding=New-Object Text.UTF8Encoding($false)
. {quote(support / 'installer-engine.ps1')}
. {quote(payload / 'scripts/versioned_runtime_native.ps1')}
Rename-Item Function:\\Invoke-Hidden Invoke-RealHidden
$calls=New-Object 'System.Collections.Generic.List[string]'
function Invoke-Hidden {{
    param($FilePath,$ArgList,[hashtable]$Environment,[ref]$ErrorOutput,[Text.Encoding]$CaptureEncoding)
    if ($FilePath -eq {quote(uv)}) {{ $calls.Add(($ArgList -join ' ')) }}
    return Invoke-RealHidden @PSBoundParameters
}}
{functions}
$InstallDir={quote(root)}
$LinkPython={quote(prospective)}
$WheelProfile=$null
function Ensure-Uv {{ throw 'Ordinary management must not acquire, publish or alter PATH' }}
function Get-Command {{
    param($Name,$CommandType,$ErrorAction)
    if ($Name -ne 'uv') {{ throw "ambient Python discovery: $Name" }}
    if ({quote(scenario)} -notin @('missing-uv','private-uv')) {{
        [pscustomobject]@{{Source={quote(uv)}}}
    }}
}}
$before=$env:PATH
$outcomes=@();$samples=@();$failures=@()
for ($cycle=0;$cycle -lt 2;$cycle++) {{
    $clock=[Diagnostics.Stopwatch]::StartNew()
    try {{
        $binding=$null
        $selected=if ($cycle -eq 0) {{ Get-ActiveSlotPython }} else {{
            Get-ActiveSlotPython -SelectedUv ([ref]$binding)
        }}
        if ($selected) {{
            if ($cycle -eq 1 -and $binding -cne {quote(uv)}) {{
                throw 'Selected UV binding was not returned'
            }}
            & $selected -I -B -X utf8 -m agent_index engine stop
            $outcomes+=$LASTEXITCODE
        }} else {{ $outcomes+=-1 }}
    }} catch {{ $failures+=$_.Exception.Message }}
    $samples+=$clock.Elapsed.TotalSeconds
}}
@{{outcomes=$outcomes;failures=$failures;samples_seconds=$samples;calls=@($calls.ToArray());
    unchanged=($env:PATH -ceq $before -and $env:AGENT_RT_ROOT -ceq 'retained-caller' -and
        $env:UV_PYTHON -ceq 'unrelated' -and $env:PYTHONHOME -ceq {quote(environment['PYTHONHOME'])})}} |
    ConvertTo-Json -Compress
""", encoding="utf-8-sig")
    result = run(executable, harness, environment, encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr
    lines = result.stdout.splitlines()
    details = json.loads(lines[-1])
    assert details["unchanged"], details
    success = scenario not in {"incomplete", "missing-uv"}
    assert details["outcomes"] == (
        [23, 23] if success else [-1, -1] if scenario == "incomplete" else []
    ), details
    assert len(details["failures"]) == (2 if scenario == "missing-uv" else 0), details
    expected_calls = (
        [f"python find --no-project --managed-python --no-python-downloads {python}"] * 2
        if success else []
    )
    assert details["calls"] == expected_calls, {
        "expected_calls": expected_calls, "details": details,
    }
    records = [json.loads(line) for line in lines[:-1] if line.startswith("{")]
    assert len(records) == (2 if success else 0), result.stdout + result.stderr
    for record in records:
        assert record["args"] == ["engine", "stop"]
        assert Path(record["root"]) == slot
        assert record["disabled"] is True and record["isolated"] == 1
        assert Path(record["cwd"]) == tmp_path
        assert record["context"] == "retained"
    assert {path.relative_to(root): path.read_bytes()
            for path in root.rglob("*") if path.is_file()} == before
    assert not list((tmp_path / "unprepared-python").iterdir())
    record_testsuite_property("index_windows_management", json.dumps({
        "host": host, "scenario": scenario, "status": "measured",
        "completed": 2, "samples_seconds": details["samples_seconds"],
        "selector_calls": len(details["calls"]),
        "network": "disabled", "tail_qualification": "insufficient samples",
    }))


@pytest.mark.parametrize("host", ["pwsh", "powershell.exe"])
@pytest.mark.parametrize("provider", ["path", "private", "missing"])
def test_activation_role_requires_prepared_bootstrap_without_ambient_fallback(
    tmp_path: Path, host: str, provider: str,
    prepared_helper: tuple[Path, Path, Path],
) -> None:
    executable = shell(host)
    payload = stage_index_installer(tmp_path / "repository/plugins/agent-index", materialized=True)
    scripts = payload / "scripts"
    (scripts / "resolve-activation-role.py").write_text(
        "import sys\nassert sys.flags.isolated and sys.dont_write_bytecode\n"
        "assert sys.argv[1:] == ['--repo','inert-repository','--machine','inert-machine']\n"
        "print('host')\n", encoding="utf-8",
    )
    functions = "\n".join(function(name, "ps1") for name in (
        "Get-BootstrapPython", "Get-ActivationRole", "Ensure-Running",
    )).replace("$PSScriptRoot", quote(scripts))
    environment = reader_environment(tmp_path, prepared_helper, "prepared")
    environment.update(AGENT_INDEX_REPO="inert-repository", AGENT_INDEX_MACHINE="inert-machine")
    # The role selector normalizes an explicit repository path before invoking Python.
    repository = tmp_path / "inert-repository"
    environment["AGENT_INDEX_REPO"] = str(repository)
    role_script = scripts / "resolve-activation-role.py"
    role_script.write_text(role_script.read_text(encoding="utf-8").replace(
        "'inert-repository'", repr(str(repository)),
    ), encoding="utf-8")
    root = tmp_path / "runtime"
    if provider == "private":
        (root / "tool").mkdir(parents=True)
        shutil.copy2(prepared_helper[0], root / "tool/uv.exe")
    before = {path.relative_to(root): path.read_bytes()
              for path in root.rglob("*") if path.is_file()}
    harness = tmp_path / "role.ps1"
    harness.write_text(f"""
$ErrorActionPreference='Stop'
. {quote(scripts / 'installer-engine.ps1')}
. {quote(scripts / 'versioned_runtime_native.ps1')}
{functions}
$WheelProfile=$null;$Action='inspect';$InstallDir={quote(root)}
function Get-Command {{
    param($Name,$CommandType,$ErrorAction)
    if ($Name -ne 'uv') {{ throw "ambient Python discovery: $Name" }}
    if ({quote(provider)} -eq 'path') {{ [pscustomobject]@{{Source={quote(prepared_helper[0])}}} }}
}}
function Ensure-EngineRunning {{ Write-Output 'engine' }}
function Ensure-ServiceRunning {{ Write-Output 'service' }}
$before=$env:PATH
try {{ Get-ActivationRole; Ensure-Running }} catch {{ Write-Output "REFUSED:$_" }}
if ($env:PATH -cne $before) {{ throw 'Activation mutated caller PATH' }}
""", encoding="utf-8-sig")
    result = run(executable, harness, environment)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines() == (
        ["host", "engine", "service"] if provider != "missing"
        else ["REFUSED:Activation role requires the prepared managed bootstrap Python"]
    )
    assert {path.relative_to(root): path.read_bytes()
            for path in root.rglob("*") if path.is_file()} == before
    assert root.exists() is (provider == "private")
