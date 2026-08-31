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

    assert "[int]$PressureCheckSec    = 5" in text
    assert "[int]$PreAuthWarnThreshold = 8" in text
    assert "[int]$PreAuthReapThreshold = 9" in text
    assert "[int]$IdleSessionWarnThreshold = 4" in text
    assert "[int]$IdleSessionReapThreshold = 8" in text
    assert "function Get-DedicatedSshdSessionPressure" in text
    assert "$children.ContainsKey($current)" in text
    assert "$seen.Add($current)" in text
    assert "$root.CommandLine -match '(?:^|\\s)-z(?:\\s|$)'" in text
    assert "$proc.CreationDate -lt $parentProc.CreationDate" in text
    assert "$forwardingPids.Contains($current)" in text
    assert "$sessionPressure.ActiveRoots -gt 0" in text
    assert "SESSION REAP deferred:" in text
    assert "SESSION REAP:" in text
    assert "$PreAuthWarnThreshold -gt 0" in text
    assert (
        "$PreAuthWarnThreshold -gt 0 -and "
        "$preAuthConns -ge $PreAuthWarnThreshold"
        in text
    )
    assert "$shouldClassifySessions = (" in text
    assert "$estConns -ge $PreAuthWarnThreshold" in text
    assert "function Get-EstimatedPreAuthConnCount" in text
    assert "$preAuthConns -ge $PreAuthReapThreshold" in text
    assert "if (-not $sessionPressure)" in text
    assert "$forceHealthCheck = (" in text
    assert "$estConns -lt 0 -or" in text
    assert "$shouldClassifySessions -and -not $sessionPressure" in text
    assert "forcing banner health check" in text
    assert "$scheduledHealthCheck = (Get-Date) -ge $nextHealthCheckAt" in text
    assert "$forceHealthCheck -or $scheduledHealthCheck" in text
    assert "$scheduledHealthCheck -and $tunnelId" in text
    assert "$sessionPressure.ActiveRoots -gt 0" in text
    assert "PRESSURE REAP deferred:" in text
    assert "PRESSURE REAP:" in text
    assert "$nextHealthCheckAt = [datetime]::MinValue" in text
    assert "Start-Sleep -Seconds $GracePeriodSec" not in text
    assert (
        "$nextHealthCheckAt = (Get-Date).AddSeconds([Math]::Max(1, $GracePeriodSec))"
        in text
    )
    assert "[Math]::Max(1, $PressureCheckSec)" in text


@pytest.mark.skipif(PWSH is None, reason="PowerShell is unavailable")
@pytest.mark.guard
def test_pre_auth_pressure_excludes_authenticated_session_roots() -> None:
    text = LAUNCHER.read_text(encoding="utf-8")
    start = text.index("function Get-EstimatedPreAuthConnCount")
    end = text.index("function Start-HostProc", start)
    function_text = text[start:end]
    script = (
        function_text
        + "\n"
        + dedent(
            """
            $pressure = [pscustomobject]@{ AuthenticatedRoots = 11 }
            [pscustomobject]@{
              Classified = Get-EstimatedPreAuthConnCount 13 $pressure
              Unclassified = Get-EstimatedPreAuthConnCount 13 $null
              Bounded = Get-EstimatedPreAuthConnCount 3 ([pscustomobject]@{ AuthenticatedRoots = 5 })
            } | ConvertTo-Json -Compress
            """
        )
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
        "Classified": 2,
        "Unclassified": 13,
        "Bounded": 0,
    }


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
              CommandLine='sshd-session.exe -R'
            },
            [pscustomobject]@{
              ProcessId=201; ParentProcessId=200
              Name='sshd-session.exe'; CreationDate=$t.AddSeconds(2)
              CommandLine='sshd-session.exe -z'
            },
            [pscustomobject]@{
              ProcessId=300; ParentProcessId=100
              Name='sshd-session.exe'; CreationDate=$t.AddSeconds(3)
              CommandLine='sshd-session.exe -R'
            },
            [pscustomobject]@{
              ProcessId=301; ParentProcessId=300
              Name='sshd-session.exe'; CreationDate=$t.AddSeconds(4)
              CommandLine='sshd-session.exe -z'
            },
            [pscustomobject]@{
              ProcessId=302; ParentProcessId=301
              Name='pwsh.exe'; CreationDate=$t.AddSeconds(5)
            },
            [pscustomobject]@{
              ProcessId=400; ParentProcessId=100
              Name='sshd-session.exe'; CreationDate=$t.AddSeconds(6)
              CommandLine='sshd-session.exe -R'
            },
            [pscustomobject]@{
              ProcessId=401; ParentProcessId=400
              Name='sshd-session.exe'; CreationDate=$t.AddSeconds(7)
              CommandLine='sshd-session.exe -z'
            },
            [pscustomobject]@{
              ProcessId=500; ParentProcessId=100
              Name='sshd-session.exe'; CreationDate=$t.AddSeconds(8)
              CommandLine='sshd-session.exe -R'
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
        "AuthenticatedRoots": 3,
        "PreAuthRoots": 1,
        "IdleRoots": 1,
        "ActiveRoots": 2,
    }
