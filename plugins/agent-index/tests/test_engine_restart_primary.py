"""Inert extracted restart function: no installer, engine, task or broker runs."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from agent_procutil import no_window_kwargs, python_cache_args

PLUGIN = Path(__file__).resolve().parents[1]
PWSH = shutil.which("pwsh") or shutil.which("powershell")


def _function(name: str) -> str:
    source = (PLUGIN / "scripts" / "install.ps1").read_text(encoding="utf-8")
    tail = source.split(f"function {name} {{", 1)[1]
    return f"function {name} {{" + re.split(r"\nfunction \w", tail, maxsplit=1)[0]


def _restart_body() -> str:
    return _function("Restart-EngineDaemon")


@pytest.mark.skipif(PWSH is None, reason="PowerShell is unavailable")
@pytest.mark.parametrize(
    "mode,expected",
    [
        ("ready", "OK:Engine daemon restarted"),
        ("missing-primary", "REFUSED:Primary index runtime not installed"),
        ("stop-failed", "REFUSED:Primary index runtime failed to stop"),
        ("start-failed", "REFUSED:Primary index runtime failed to restart"),
        ("no-service", "SKIP:Engine daemon restart skipped"),
    ],
)
def test_restart_uses_primary_manager_without_first_touch_install(
    tmp_path: Path, mode: str, expected: str,
) -> None:
    script = r"""
$ErrorActionPreference = 'Stop'
$EngineVenvPython = 'engine-python'
$EngineTaskName = 'inert-engine-task'
$NoService = $Mode -eq 'no-service'
function Get-ScheduledTask { return $null }
function Get-ActiveSlotPython { return 'primary-python' }
function Test-Path {
    param($Path)
    return $Path -eq 'engine-python' -or
        ($Path -eq 'primary-python' -and $Mode -ne 'missing-primary')
}
function engine-python { throw 'Engine-owned management must never be invoked' }
function primary-python {
    $global:LASTEXITCODE = 0
    $module = [Array]::IndexOf($args, '-m')
    $verb = $args[$module + 3]
    Write-Output ('CALL:' + (ConvertTo-Json -InputObject @($args) -Compress))
    if (($Mode -eq 'stop-failed' -and $verb -eq 'stop') -or
        ($Mode -eq 'start-failed' -and $verb -eq 'start')) { $global:LASTEXITCODE = 7 }
}
function Start-Sleep { }
function Write-Ok { param($Msg) Write-Output "OK:$Msg" }
function Write-Skip { param($Msg) Write-Output "SKIP:$Msg" }
"""
    root = tmp_path / "owned index"
    quoted_root = str(root).replace("'", "''")
    script = (
        f"$Mode = '{mode}'\n$InstallDir = '{quoted_root}'\n"
        + script + "\n" + _function("Get-IndexPythonCacheArgs") + "\n" + _restart_body()
    )
    script += "\ntry { Restart-EngineDaemon } catch { Write-Output \"REFUSED:$_\" }\n"
    result = subprocess.run(  # noqa: S603
        [PWSH, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=30, check=False,
        **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    assert expected in result.stdout
    assert "Engine-owned management" not in result.stdout
    if mode in ("missing-primary", "no-service"):
        assert "CALL:" not in result.stdout
    else:
        calls = [json.loads(line.removeprefix("CALL:"))
                 for line in result.stdout.splitlines() if line.startswith("CALL:")]
        stop = calls[0]
        module = stop.index("-m")
        assert stop[module:] == ["-m", "agent_index", "engine", "stop"]
        assert stop[:module] == ["-I", *python_cache_args(root), "-X", "utf8"]
        if mode == "stop-failed":
            assert len(calls) == 1
        else:
            start = calls[1]
            module = start.index("-m")
            assert start[:module] == ["-I", *python_cache_args(root), "-X", "utf8"]
            assert start[module:] == ["-m", "agent_index", "engine", "start", "--strict"]
    if mode != "ready":
        assert "OK:" not in result.stdout


def test_restart_never_constructs_or_adopts_and_keeps_engine_selection_separate():
    restart = _restart_body()
    assert restart.index("Primary index runtime not installed") < restart.index("engine stop")
    for forbidden in ("Install-Runtime", "Install-Engine", "BrokerAdoption", "spawn_daemon"):
        assert forbidden not in restart
    daemon = (PLUGIN / "src" / "agent_index" / "engine" / "daemon.py").read_text(
        encoding="utf-8",
    )
    command = daemon.split("def engine_command(", 1)[1].split("\ndef start(", 1)[0]
    assert "py = engine_venv_python(home)" in command
    assert "windowless_python(str(py))" in command
