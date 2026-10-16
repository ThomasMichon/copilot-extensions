"""Invoke-Start's direct-start retries survive a failed ``service start --direct``.

``agent-bridge service start --direct`` writes to stderr and exits non-zero
when the daemon does not come up. The installer runs under
``$ErrorActionPreference = 'Stop'``, where Windows PowerShell 5.1 turns
redirected native stderr into a terminating error -- which would abort
``Invoke-Start`` on the first failed attempt, before its drain and the
remaining retries. ``Invoke-Start`` is extracted verbatim from
``scripts/install.ps1`` and run with its collaborators replaced by recording
functions and a fake interpreter that always fails.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_INSTALL_PS1 = Path(__file__).resolve().parents[1] / "scripts" / "install.ps1"


def _invoke_start() -> str:
    text = _INSTALL_PS1.read_text(encoding="utf-8-sig")
    start = text.index("function Invoke-Start {")
    return text[start:text.index("\nfunction ", start + 1)]


def test_direct_start_runs_with_continue_and_restores_the_preference():
    body = _invoke_start()
    call = body.index("& $LinkPython -m agent_bridge service start --direct")
    relax = body.rindex("$ErrorActionPreference = 'Continue'", 0, call)
    restore = body.index("$ErrorActionPreference = $prevEAP", call)
    assert body.rindex("$prevEAP = $ErrorActionPreference", 0, relax) < relax < call
    assert "finally" in body[call:restore]


def _hosts() -> list[str]:
    hosts = []
    if sys.platform == "win32":
        hosts += [h for h in (shutil.which("powershell"),) if h]
    return hosts


@pytest.mark.skipif(not _hosts(), reason="Windows PowerShell 5.1 is not available")
@pytest.mark.parametrize("host", _hosts())
def test_failed_direct_starts_are_drained_and_retried(tmp_path, host):
    fake_python = tmp_path / "python.cmd"
    fake_python.write_text("@echo daemon did not become healthy 1>&2\r\n@exit /b 1\r\n")
    script = tmp_path / "harness.ps1"
    script.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        f". '{_INSTALL_PS1.with_name('installer-watchdog.ps1')}'\n"
        f"$LinkPython = '{fake_python}'\n"
        "$TaskName = 'agent-bridge-test-task-that-does-not-exist'\n"
        "function Test-ActiveIsForward { $false }\n"
        "function Get-RunningProcess { $null }\n"
        "function Test-HealthOnce { $false }\n"
        "function Write-UpdateMarker { }\n"
        "function Clear-UpdateMarker { }\n"
        "function Get-ScheduledTask { $null }\n"
        "function Start-Sleep { }\n"
        "function Stop-DaemonProcesses { Write-Host 'DRAIN' }\n"
        "function Write-Step { param($m) Write-Host \"STEP $m\" }\n"
        "function Write-Warn { param($m) Write-Host \"WARN $m\" }\n"
        "function Write-Fail { param($m) Write-Host \"FAIL $m\" }\n"
        "function Write-Ok { param($m) Write-Host \"OK $m\" }\n"
        "function Write-Skip { param($m) Write-Host \"SKIP $m\" }\n"
        + _invoke_start()
        + "\nInvoke-Start\n",
        encoding="utf-8-sig",
    )
    proc = subprocess.run(
        [host, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(script)],
        capture_output=True, text=True, timeout=120, check=False,
    )
    out = proc.stdout.splitlines()
    starts = [line for line in out if line.startswith("STEP Starting agent-bridge")]
    assert len(starts) == 3, proc.stdout + proc.stderr
    assert out.count("DRAIN") == 3
    assert any(line.startswith("FAIL agent-bridge failed to start") for line in out)
    assert proc.returncode == 1
