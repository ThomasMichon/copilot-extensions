"""Prepared-only Index readers and actual pre-runtime cell admission."""
from __future__ import annotations

from collections.abc import Callable
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
from agent_procutil import no_window_kwargs

from test_install_engine_adoption import environment, function, stage_index_installer
from test_managed_bootstrap_reader_sh import prepared_helper as prepared_helper


def quote(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def shell(host: str) -> str:
    executable = shutil.which(host)
    if not executable:
        pytest.skip(f"{host} unavailable")
    return executable


def run(
    executable: str, script: Path, env: dict[str, str], *args: str,
    encoding: str | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [executable, "-NoProfile", "-NonInteractive", "-File", str(script), *args],
        cwd=script.parent, env=env, capture_output=True, text=True, encoding=encoding, timeout=20,
        **no_window_kwargs(),
    )


def reader_environment(
    tmp_path: Path, prepared: tuple[Path, Path, Path], scenario: str,
) -> dict[str, str]:
    env = environment(tmp_path)
    empty = tmp_path / "unprepared-python"
    empty.mkdir()
    env.update(
        UV_PYTHON_INSTALL_DIR=str(empty if scenario == "unprepared" else prepared[2]),
        UV_CACHE_DIR=str(tmp_path / "uv-cache"), UV_OFFLINE="1",
        UV_PYTHON="unrelated", UV_NO_MANAGED_PYTHON="1",
        UV_CONFIG_FILE="missing-relative.toml", UV_NO_CONFIG="false",
        VIRTUAL_ENV=str(tmp_path / "incumbent"), CONDA_PREFIX=str(tmp_path / "incumbent"),
        PYTHONHOME=str(tmp_path / "invalid-home"), PYTHONPATH=str(tmp_path / "invalid-path"),
        PYTHON_MANAGER_AUTOMATIC_INSTALL="true",
    )
    return env


@pytest.mark.parametrize("host", ["pwsh", "powershell.exe"])
@pytest.mark.parametrize("layout", ["raw", "packaged"])
@pytest.mark.parametrize("scenario", [
    "prepared", "unprepared", "missing-uv", "missing-provider", "missing-request",
])
def test_bootstrap_readers_inspect_bound_managed_python(
    tmp_path: Path, host: str, layout: str, scenario: str,
    prepared_helper: tuple[Path, Path, Path],
    record_testsuite_property: Callable[[str, object], None],
) -> None:
    executable = shell(host)
    payload = stage_index_installer(
        tmp_path / "repository/plugins/agent-index", materialized=layout == "packaged",
    )
    support = payload / "scripts" if layout == "packaged" else payload.parents[1] / "libs/installer-engine"
    if scenario in {"missing-provider", "missing-request"}:
        name = "bootstrap-uv.ps1" if scenario == "missing-provider" else "installer-instance-python-version.txt"
        (support / name).rename(support / (name + ".unavailable"))
    env = reader_environment(tmp_path, prepared_helper, scenario)
    harness = tmp_path / "reader.ps1"
    harness.write_text(
        f"""
$ErrorActionPreference='Stop'
Set-StrictMode -Version 2
. {quote(support / 'installer-engine.ps1')}
. {quote(payload / 'scripts/versioned_runtime_native.ps1')}
Rename-Item Function:\\Invoke-Hidden Invoke-RealHidden
$calls=New-Object 'System.Collections.Generic.List[string]'
function Invoke-Hidden {{
    param($FilePath,$ArgList,[hashtable]$Environment,[ref]$ErrorOutput,[Text.Encoding]$CaptureEncoding)
    if ($FilePath -eq {quote(prepared_helper[0])}) {{ $calls.Add(($ArgList -join ' ')) }}
    return Invoke-RealHidden @PSBoundParameters
}}
{function('Get-BootstrapPython', 'ps1')}
$WheelProfile=$null
$Action='inspect'
$InstallDir={quote(tmp_path / 'runtime')}
$VenvDir={quote(tmp_path / 'incumbent')}
$LinkDir=$VenvDir
$script:IndexBootstrapPython={quote(sys.executable)}
function Get-Command {{
    param($Name,$CommandType,$ErrorAction)
    if ($Name -ne 'uv') {{ throw "ambient Python discovery: $Name" }}
    if ({quote(scenario)} -ne 'missing-uv') {{
        [pscustomobject]@{{Source={quote(prepared_helper[0])};CommandType='Application'}}
    }}
}}
$before=$env:PATH
$samples=@()
$selected=@()
$failures=@()
foreach ($external in @($false,$true)) {{
    $clock=[Diagnostics.Stopwatch]::StartNew()
    $value=$null
    try {{ $value=Get-BootstrapPython -ExternalOnly:$external }}
    catch {{ $failures+=$_.Exception.Message }}
    $clock.Stop()
    $samples+=($clock.Elapsed.TotalSeconds)
    $selected+=$value
}}
@{{selected=$selected;failures=$failures;calls=@($calls.ToArray());samples_seconds=$samples;
    unchanged=($env:PATH -ceq $before -and $env:UV_PYTHON -ceq 'unrelated' -and
        $env:UV_CONFIG_FILE -ceq 'missing-relative.toml' -and $env:UV_NO_CONFIG -ceq 'false' -and
        $env:UV_NO_MANAGED_PYTHON -ceq '1' -and $env:VIRTUAL_ENV -ceq $VenvDir -and
        $env:PYTHON_MANAGER_AUTOMATIC_INSTALL -ceq 'true')}} | ConvertTo-Json -Compress
""",
        encoding="utf-8",
    )
    result = run(executable, harness, env)
    assert result.returncode == 0, result.stdout + result.stderr
    details = json.loads(result.stdout)
    assert details["unchanged"], details
    assert len(details["failures"]) == (2 if scenario in {"missing-provider", "missing-request"} else 0), details
    assert all("ambient Python discovery" not in failure for failure in details["failures"])
    expected: list[str | None] = (
        [str(prepared_helper[1])] * 2 if scenario == "prepared" else [None, None]
    )
    assert details["selected"] == expected, details
    expected_calls = [
        "--version",
        "python find --no-project --system --managed-python --no-python-downloads cpython@3.11.18",
    ] * 2 if scenario in {"prepared", "unprepared"} else []
    assert details["calls"] == expected_calls, details
    assert not list((tmp_path / "unprepared-python").iterdir())
    assert not (tmp_path / "runtime").exists()
    if scenario == "prepared":
        record_testsuite_property("index_windows_bootstrap_performance", json.dumps({
            "host": host, "layout": layout, "status": "measured", "completed": 2,
            "samples_seconds": details["samples_seconds"], "network": "disabled",
            "helper_python": "3.11.18", "uv_calls_per_reader": len(details["calls"]) // 2,
            "cache": "fresh private UV cache then retained cache; preinstalled interpreter",
            "unavailable": ["full install latency", "child CPU", "desktop observation"],
        }))


@pytest.mark.parametrize("host", ["pwsh", "powershell.exe"])
def test_explicit_closed_wheel_python_keeps_its_admission_owner(tmp_path: Path, host: str) -> None:
    executable = shell(host)
    harness = tmp_path / "explicit.ps1"
    harness.write_text(
        "$ErrorActionPreference='Stop'\n"
        + function("Get-BootstrapPython", "ps1")
        + f"""
$WheelProfile='fixture-profile'
$WheelPython={quote(sys.executable)}
function Resolve-NativeBootstrapPython {{ throw 'explicit profile rediscovered Python' }}
if ((Get-BootstrapPython) -cne $WheelPython -or
    (Get-BootstrapPython -ExternalOnly) -cne $WheelPython) {{ throw 'explicit binding lost' }}
""",
        encoding="utf-8",
    )
    result = run(executable, harness, environment(tmp_path))
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("host", ["pwsh", "powershell.exe"])
@pytest.mark.parametrize("layout", ["raw", "packaged"])
@pytest.mark.parametrize("scenario", ["prepared", "unprepared"])
@pytest.mark.parametrize("entry", ["cell", "driver"])
def test_actual_entry_selects_before_runtime(
    tmp_path: Path, host: str, layout: str, scenario: str, entry: str,
    prepared_helper: tuple[Path, Path, Path],
) -> None:
    executable = shell(host)
    payload = stage_index_installer(
        tmp_path / "repository/plugins/agent-index", materialized=layout == "packaged",
    )
    (payload / "pyproject.toml").write_text('[project]\nversion = "1.0.0"\n', encoding="utf-8")
    child = "cell-runtime.py" if entry == "cell" else "installer_driver.py"
    (payload / "scripts" / child).write_text(
        "import json,os,sys\n"
        "print(json.dumps({'python':sys.executable,'args':sys.argv[1:],'cwd':os.getcwd(),"
        "'isolated':sys.flags.isolated,'bytecode':sys.dont_write_bytecode,"
        "'pythonpath':os.environ.get('PYTHONPATH'),'pythonhome':os.environ.get('PYTHONHOME'),"
        "'expected_shell':os.environ['FIXTURE_NATIVE_SHELL']}))\n"
        "raise SystemExit(23)\n",
        encoding="utf-8",
    )
    env = reader_environment(tmp_path, prepared_helper, scenario)
    if entry == "driver":
        env["OS"] = "Windows_NT"
        env["COPILOT_EXTENSIONS_TEST_CONTAINED"] = "0"
    harness = tmp_path / "cell.ps1"
    harness.write_text(
        f"""
$ErrorActionPreference='Stop'
$env:FIXTURE_NATIVE_SHELL=(Get-Process -Id $PID).Path
function Get-Command {{
    param($Name,$CommandType,$ErrorAction)
    if ($Name -ne 'uv') {{ throw "ambient Python discovery: $Name" }}
    [pscustomobject]@{{Source={quote(prepared_helper[0])};CommandType='Application'}}
}}
& {quote(payload / 'scripts/install.ps1')} @args
exit $LASTEXITCODE
""",
        encoding="utf-8",
    )
    context = tmp_path / "context with spaces.json"
    arguments = (
        ["-Action", "cell-recover", "-Context", str(context),
         "-ExpectedMarketplaceId", "example--1234"]
        if entry == "cell" else ["-Action", "install", "-NoService"]
    )
    result = run(executable, harness, env, *arguments)
    assert not list((tmp_path / "unprepared-python").iterdir())
    assert not (Path(env["USERPROFILE"]) / ".agent-index").exists()
    if scenario == "unprepared":
        assert result.returncode == 1, result.stdout + result.stderr
        assert "requires prepared uv-managed Python" in result.stdout + result.stderr
        return
    assert result.returncode == 23, result.stdout + result.stderr
    details = json.loads(result.stdout)
    assert Path(details["python"]).samefile(prepared_helper[1])
    if entry == "cell":
        assert details["args"] == [
            "cell-recover", "--context", str(context), "--expected-marketplace-id", "example--1234",
        ]
        assert Path(details["cwd"]).samefile(env["USERPROFILE"])
        assert details["pythonpath"] is None and details["pythonhome"] is None
    else:
        assert details["args"] == [
            "--payload", str(payload), "--root", str(Path(env["USERPROFILE"]) / ".agent-index"),
            "--shell", details["expected_shell"], "--plugin", "agent-index",
            "--action", "install", "--option", "NoService",
        ]
        assert Path(details["expected_shell"]).is_file()
    assert details["isolated"] == 1 and details["bytecode"]
