"""Contract checks for the Windows dtssh host watchdog."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from textwrap import dedent

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
LAUNCHER = (
    PLUGIN
    / "transports"
    / "dtssh"
    / "scripts"
    / "dtssh-host-launcher.ps1"
)
PWSH = shutil.which("pwsh")


@pytest.mark.guard
def test_released_dtssh_leaks_are_classified_before_reaping() -> None:
    text = LAUNCHER.read_text(encoding="utf-8")

    assert "[int]$IdleSessionWarnThreshold = 8" in text
    assert "[int]$IdleSessionReapThreshold = 16" in text
    assert "function Get-DedicatedSshdSessionPressure" in text
    assert "$children.ContainsKey($current)" in text
    assert "$seen.Add($current)" in text
    assert "$proc.CreationDate -lt $parentProc.CreationDate" in text
    assert "$forwardingPids.Contains($current)" in text
    assert "$sessionPressure.ActiveRoots -gt 0" in text
    assert "SESSION REAP deferred:" in text
    assert "SESSION REAP:" in text
    assert "[int]$PreAuthReapThreshold = 128" in text
    assert "SATURATION REAP:" in text


@pytest.mark.skipif(PWSH is None, reason="PowerShell is unavailable")
@pytest.mark.guard
def test_session_pressure_classifies_idle_command_and_forwarding_roots() -> None:
    text = LAUNCHER.read_text(encoding="utf-8")
    start = text.index("function Get-DedicatedSshdSessionPressure")
    end = text.index("function Start-HostProc", start)
    function_text = text[start:end]
    script = dedent(
        """
        function Write-Log { param($Message, $Level) }
        function Get-NetTCPConnection {
          param($LocalPort, $State, $ErrorAction)
          if ($LocalPort) {
            return [pscustomobject]@{ OwningProcess = 100 }
          }
          return @(
            [pscustomobject]@{ LocalPort = 50000; OwningProcess = 401 }
          )
        }
        function Get-CimInstance {
          param($ClassName, $ErrorAction)
          $t = [datetime]'2026-01-01T00:00:00Z'
          return @(
            [pscustomobject]@{
              ProcessId=100; ParentProcessId=1
              Name='sshd.exe'; CreationDate=$t
            },
            [pscustomobject]@{
              ProcessId=200; ParentProcessId=100
              Name='sshd-session.exe'; CreationDate=$t.AddSeconds(1)
            },
            [pscustomobject]@{
              ProcessId=201; ParentProcessId=200
              Name='sshd-session.exe'; CreationDate=$t.AddSeconds(2)
            },
            [pscustomobject]@{
              ProcessId=300; ParentProcessId=100
              Name='sshd-session.exe'; CreationDate=$t.AddSeconds(3)
            },
            [pscustomobject]@{
              ProcessId=301; ParentProcessId=300
              Name='sshd-session.exe'; CreationDate=$t.AddSeconds(4)
            },
            [pscustomobject]@{
              ProcessId=302; ParentProcessId=301
              Name='pwsh.exe'; CreationDate=$t.AddSeconds(5)
            },
            [pscustomobject]@{
              ProcessId=400; ParentProcessId=100
              Name='sshd-session.exe'; CreationDate=$t.AddSeconds(6)
            },
            [pscustomobject]@{
              ProcessId=401; ParentProcessId=400
              Name='sshd-session.exe'; CreationDate=$t.AddSeconds(7)
            },
            [pscustomobject]@{
              ProcessId=999; ParentProcessId=201
              Name='unrelated.exe'; CreationDate=$t.AddSeconds(-1)
            }
          )
        }
        """
    )
    script += (
        function_text
        + "\nGet-DedicatedSshdSessionPressure 2222 | ConvertTo-Json -Compress\n"
    )
    assert PWSH is not None
    result = subprocess.run(  # noqa: S603 - resolved local PowerShell, fixed script
        [PWSH, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "TotalRoots": 3,
        "IdleRoots": 1,
        "ActiveRoots": 2,
    }
