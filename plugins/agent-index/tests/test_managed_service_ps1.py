"""Generated native launchers admit UV before any application interpreter."""
from __future__ import annotations

from collections.abc import Callable
import json
import os
from pathlib import Path
import subprocess

import pytest
from agent_procutil import no_window_kwargs

from test_install_engine_adoption import function, stage_index_installer
from test_managed_bootstrap_reader_ps1 import quote, reader_environment, run, shell
from test_managed_bootstrap_reader_sh import prepared_helper as prepared_helper


@pytest.mark.skipif(os.name != "nt", reason="Native pre-interpreter capture owner")
@pytest.mark.parametrize("host", ["pwsh", "powershell.exe"])
@pytest.mark.parametrize("scenario", [
    "raw-service", "packaged-engine", "legacy-engine", "last-known-good",
    "incomplete", "missing-uv", "missing-capture",
])
def test_generated_service_prepares_only_through_its_installation_owner(
    tmp_path: Path, host: str, scenario: str,
    prepared_helper: tuple[Path, Path, Path],
    record_testsuite_property: Callable[[str, object], None],
) -> None:
    executable = shell(host)
    uv, helper, _store = prepared_helper
    payload = stage_index_installer(
        tmp_path / "repository/plugins/agent-index", materialized=scenario == "packaged-engine",
    )
    support = (
        payload / "scripts" if scenario == "packaged-engine"
        else payload.parents[1] / "libs/installer-engine"
    )
    root = tmp_path / "owned index's \u00e9"
    engine = scenario in {"packaged-engine", "legacy-engine"}
    root.mkdir()
    slot = root / (".venv" if scenario == "legacy-engine" else "versions/1.0.0")
    built = subprocess.run(
        [str(helper), "-I", "-B", "-m", "venv", "--copies", "--without-pip", str(slot)],
        capture_output=True, text=True, timeout=20, **no_window_kwargs(),
    )
    assert built.returncode == 0, built.stdout + built.stderr
    python = slot / "Scripts/python.exe"
    package = slot / "Lib/site-packages/agent_index"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "__main__.py").write_text(
        "import json, os, sys\n"
        "print(json.dumps({'args': sys.argv[1:], 'root': sys.prefix,"
        " 'base': sys.base_prefix, 'disabled': sys.dont_write_bytecode,"
        " 'prefix': sys.pycache_prefix, 'cwd': os.getcwd(),"
        " 'environment': os.environ.get('INDEX_TEST_CONTEXT'),"
        " 'engine_home': os.environ.get('AGENT_INDEX_ENGINE_HOME'),"
        " 'caller_root': os.environ.get('AGENT_RT_ROOT')}))\n"
        "sys.exit(23)\n", encoding="utf-8",
    )
    if scenario != "legacy-engine":
        if scenario != "incomplete":
            (slot / ".install-complete.json").write_text(json.dumps({
                "version": "1.0.0", "completed_at": "fixture", "pid": 0,
            }), encoding="utf-8")
        (root / "current-version").write_text(
            "incomplete-newer" if scenario == "last-known-good" else "1.0.0",
            encoding="utf-8",
        )
        if scenario == "last-known-good":
            (root / "last-known-good").write_text("1.0.0", encoding="utf-8")
    env_file = root / "service.env"
    env_file.write_text("INDEX_TEST_CONTEXT=retained\n", encoding="utf-8")
    before = {path.relative_to(root): path.read_bytes()
              for path in root.rglob("*") if path.is_file()}
    environment = reader_environment(tmp_path, prepared_helper, "prepared")
    environment["AGENT_RT_ROOT"] = "retained-caller"
    commands = tmp_path / "empty-path"
    commands.mkdir()
    environment["PATH"] = str(commands)
    harness = tmp_path / "service.ps1"
    launcher = root / ("engine.ps1" if engine else "service.ps1")
    functions = "\n".join(function(name, "ps1") for name in (
        "Publish-FileAtomically", "Publish-IndexServiceSupport",
        "Write-EngineFiles" if engine else "Write-ServiceFiles",
    )).replace("$PSScriptRoot", quote(payload / "scripts"))
    harness.write_text(
        f"""
$ErrorActionPreference='Stop'
Set-StrictMode -Version 2
. {quote(support / 'installer-engine.ps1')}
{functions}
$InstallDir={quote(root)}
$EnvFile={quote(env_file)}
$Launcher={quote(launcher)}
$EngineHome=$InstallDir
$EngineEnvFile=$EnvFile
$EngineLauncher=$Launcher
$EngineVenvPython={quote(python)}
$WheelProfile=$null
$utf8NoBom=[Text.UTF8Encoding]::new($false)
function Write-Ok {{ }}
function Write-Skip {{ }}
function Ensure-Uv {{
    param($InstallRoot,$AcquireIfMissing)
    if ($AcquireIfMissing) {{ throw 'Service publication tried acquisition' }}
    return {quote(uv)}
}}
{'Write-EngineFiles' if engine else 'Write-ServiceFiles'}
if ({quote(scenario)} -eq 'missing-uv') {{
    $text=[IO.File]::ReadAllText($Launcher).Replace(
        {quote(str(uv).replace("'", "''"))},
        {quote(str(tmp_path / 'missing-uv').replace("'", "''"))})
    [IO.File]::WriteAllText($Launcher,$text,[Text.UTF8Encoding]::new($true))
}}
if ({quote(scenario)} -eq 'missing-capture') {{
    Move-Item -LiteralPath (Join-Path $InstallDir 'bin/bootstrap-capture.exe') `
        -Destination (Join-Path $InstallDir 'capture-unavailable')
}}
$samples=@()
$outcomes=@()
for ($cycle=0; $cycle -lt 2; $cycle++) {{
    $clock=[Diagnostics.Stopwatch]::StartNew()
    & (Get-Process -Id $PID).Path -NoProfile -NonInteractive -File $Launcher
    $outcomes+=$LASTEXITCODE
    $samples+=$clock.Elapsed.TotalSeconds
}}
@{{outcomes=$outcomes;samples_seconds=$samples}} | ConvertTo-Json -Compress
""",
        encoding="utf-8-sig",
    )
    result = run(executable, harness, environment)
    assert result.returncode == 0, result.stdout + result.stderr
    lines = result.stdout.splitlines()
    details = json.loads(lines[-1])
    success = scenario not in {"incomplete", "missing-uv", "missing-capture"}
    assert details["outcomes"] == [23 if success else 1] * 2, result.stdout + result.stderr
    records = [json.loads(line) for line in lines[:-1] if line.startswith("{")]
    assert len(records) == (2 if success else 0), result.stdout + result.stderr
    for record in records:
        assert record["args"] == (["engine", "run"] if engine else ["start"])
        assert Path(record["root"]) == slot
        assert record["disabled"] is True and record["prefix"] is None
        assert Path(record["cwd"]) == tmp_path
        assert record["environment"] == "retained"
        assert record["caller_root"] == "retained-caller"
        if engine:
            assert Path(record["engine_home"]) == root
    assert all((root / name).read_bytes() == content for name, content in before.items())
    assert not list(slot.rglob("*.pyc"))
    assert not list((tmp_path / "unprepared-python").iterdir())
    record_testsuite_property("index_windows_service_ingress", json.dumps({
        "host": host, "scenario": scenario, "status": "measured",
        "completed": 2, "samples_seconds": details["samples_seconds"],
        "network": "disabled", "tail_qualification": "insufficient samples",
    }))
