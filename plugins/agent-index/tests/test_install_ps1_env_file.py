"""``Invoke-WithEnvFile``: the user-mode ensure path honors the launcher env files.

Starting a daemon now through ``Ensure-ServiceRunning`` / ``Ensure-EngineRunning``
(instead of running its logon launcher) must apply the same ``service.env`` /
``engine.env`` settings the launcher applies, and must not leak them into the
rest of the installer run. The function is extracted verbatim from
``scripts/install.ps1`` and executed under PowerShell.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
PWSH = shutil.which("pwsh")

pytestmark = pytest.mark.skipif(PWSH is None, reason="PowerShell 7 (pwsh) is not available")


def _function(name: str) -> str:
    text = (PLUGIN / "scripts" / "install.ps1").read_text(encoding="utf-8-sig")
    start = text.index(f"function {name} {{")
    end = text.index("\nfunction ", start + 1)
    return text[start:end]


def _run(script: str) -> list[str]:
    proc = subprocess.run(
        [PWSH, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=120, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.splitlines()


def test_env_file_applies_during_the_body_and_is_restored(tmp_path):
    env_file = tmp_path / "engine.env"
    env_file.write_text(
        "# comment\n\nAIX_TEST_PORT = 9555 \nAIX_TEST_URL=http://h/?a=b\nnot-a-pair\n",
        encoding="utf-8",
    )
    script = _function("Invoke-WithEnvFile") + f"""
$env:AIX_TEST_PORT = 'before'
Remove-Item Env:AIX_TEST_URL -ErrorAction SilentlyContinue
Remove-Item Env:AIX_TEST_HOME -ErrorAction SilentlyContinue
Invoke-WithEnvFile -Path '{env_file}' -Extra @{{ AIX_TEST_HOME = 'C:\\home' }} -Body {{
    Write-Output "in:$env:AIX_TEST_PORT|$env:AIX_TEST_URL|$env:AIX_TEST_HOME"
    return
}}
Write-Output "out:$env:AIX_TEST_PORT|$([bool]$env:AIX_TEST_URL)|$([bool]$env:AIX_TEST_HOME)"
"""
    assert _run(script) == [
        "in:9555|http://h/?a=b|C:\\home",
        "out:before|False|False",
    ]


def test_env_is_restored_when_the_body_throws(tmp_path):
    env_file = tmp_path / "service.env"
    env_file.write_text("AIX_TEST_HOST=10.0.0.1\n", encoding="utf-8")
    script = _function("Invoke-WithEnvFile") + f"""
$env:AIX_TEST_HOST = 'orig'
try {{ Invoke-WithEnvFile -Path '{env_file}' -Body {{ throw 'boom' }} }} catch {{ }}
Write-Output "out:$env:AIX_TEST_HOST"
"""
    assert _run(script) == ["out:orig"]


def test_missing_env_file_runs_the_body_unchanged(tmp_path):
    script = _function("Invoke-WithEnvFile") + f"""
Invoke-WithEnvFile -Path '{tmp_path / "absent.env"}' -Body {{ Write-Output 'ran' }}
"""
    assert _run(script) == ["ran"]


def test_ensure_paths_run_their_python_inside_the_env_file():
    engine = _function("Ensure-EngineRunning")
    service = _function("Ensure-ServiceRunning")
    assert "Invoke-WithEnvFile -Path $EngineEnvFile" in engine
    assert "AGENT_INDEX_ENGINE_HOME = $EngineHome" in engine
    assert engine.index("Invoke-WithEnvFile") < engine.index("Test-EnginePort")
    assert engine.index("Invoke-WithEnvFile") < engine.index("-m agent_index engine start")
    assert "Invoke-WithEnvFile -Path $EnvFile" in service
    assert service.index("Invoke-WithEnvFile") < service.index("-m agent_index deploy")


def _windows_powershell() -> list[str]:
    host = shutil.which("powershell") if sys.platform == "win32" else None
    return [host] if host else []


@pytest.mark.skipif(not _windows_powershell(), reason="Windows PowerShell 5.1 is not available")
@pytest.mark.parametrize("exit_code,expected", [(0, "[OK]   Engine ensured"),
                                                 (3, "[WARN] Engine start exited 3")])
def test_engine_ensure_survives_stderr_and_reports_the_exit_under_5_1(
    tmp_path, exit_code, expected
):
    """Windows PowerShell 5.1 turns redirected native stderr into a terminating
    error under 'Stop'; the engine start logs warnings to stderr even when it
    succeeds, which must not abort the installer before the logon entry is
    registered."""
    fake = tmp_path / "python.cmd"
    fake.write_text(f"@echo engine warning on stderr 1>&2\r\n@exit /b {exit_code}\r\n")
    install = (PLUGIN / "scripts" / "install.ps1").read_text(encoding="utf-8-sig")
    helpers = "\n".join(
        line for line in install.splitlines()
        if line.startswith(("function Write-Ok ", "function Write-Skip ", "function Write-Warn "))
    )
    script = tmp_path / "harness.ps1"
    script.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        f"$NoService = $false\n$EngineVenvPython = '{fake}'\n"
        f"$EngineEnvFile = '{tmp_path / 'engine.env'}'\n$EngineHome = '{tmp_path}'\n"
        "function Write-EngineFiles { }\nfunction Test-EnginePort { $false }\n"
        + helpers + "\n" + _function("Invoke-WithEnvFile") + "\n"
        + _function("Ensure-EngineRunning") + "\nEnsure-EngineRunning\nWrite-Host 'AFTER'\n",
        encoding="utf-8-sig",
    )
    proc = subprocess.run(
        [_windows_powershell()[0], "-NoProfile", "-NonInteractive", "-ExecutionPolicy",
         "Bypass", "-File", str(script)],
        capture_output=True, text=True, timeout=120, check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert any(expected in line for line in proc.stdout.splitlines()), proc.stdout
    assert "AFTER" in proc.stdout
