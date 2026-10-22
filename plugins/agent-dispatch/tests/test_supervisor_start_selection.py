"""Exercise the real Windows start selector with fixture-only native adapters."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


INSTALLER = Path(__file__).resolve().parents[1] / "scripts" / "install.ps1"
PWSH = shutil.which("pwsh")


@pytest.mark.skipif(PWSH is None, reason="PowerShell 7 is unavailable")
@pytest.mark.parametrize(
    "mode,labels,registered,task_state,expected",
    [
        ("serve", "", True, "Absent", "logon"),
        ("legacy", "", True, "Absent", "none"),
        ("legacy", "fixture", True, "Absent", "logon"),
        ("serve", "", False, "Absent", "none"),
        ("serve", "", True, "Disabled", "none"),
        ("legacy", "fixture", True, "Disabled", "none"),
        ("serve", "", True, "Ready", "task"),
    ],
)
def test_start_respects_mode_registration_and_native_disable(
    tmp_path: Path, mode: str, labels: str, registered: bool,
    task_state: str, expected: str,
) -> None:
    source = INSTALLER.read_text(encoding="utf-8-sig")

    def function(name: str) -> str:
        start = source.index(f"function {name} {{")
        return source[start:source.index("\nfunction ", start + 1)]

    (tmp_path / "supervisor.env").write_text(
        f"AGENT_DISPATCH_SUPERVISE_MODE={mode}\n"
        f"AGENT_DISPATCH_SUPERVISE_LABELS={labels}\n",
        encoding="utf-8",
    )
    (tmp_path / "supervise-service.ps1").write_text("# fixture only\n", encoding="utf-8")
    script = "\n".join(
        [
            "$ErrorActionPreference='Stop'",
            "$InstallDir=$PSScriptRoot; $script:Selected='none'",
            "$script:Audits=@(); $script:Skips=@()",
            f"$script:TaskState='{task_state}'; $script:Registered=${str(registered).lower()}",
            "function Get-ScheduledTask { param($TaskName) "
            "if($script:TaskState -ne 'Absent'){"
            "[pscustomobject]@{State=$script:TaskState}} }",
            "function Get-ItemProperty { param($Path,$Name) "
            "if($Path -ne 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Run'){"
            "throw 'unexpected registry scope'}; "
            "if($script:Registered){[pscustomobject]@{fixture='registered'}} }",
            "function Invoke-SupervisorTaskAction { param($Operation,$Name,$Reason) "
            "if($Operation -ne 'start'){throw 'unexpected native action'}; "
            "$script:Selected='task'; $script:SupervisorActionSucceeded=$true }",
            "function Install-SupervisorLogonAutostart { param($Name,$Launcher,$EnvFile) "
            "if(-not(Test-Path $Launcher)){throw 'missing fixture launcher'}; "
            "$script:Selected='logon' }",
            "function Write-Ok { param($Message) }",
            "function Write-Skip { param($Message) $script:Skips+=,$Message }",
            "function Invoke-SupervisorAudit { param($Event,$Reason,$Name,$EnvFile,$Outcome) "
            "$script:Audits+=,@{event=$Event;reason=$Reason;name=$Name;outcome=$Outcome} }",
            *(
                function(name)
                for name in (
                    "Test-SupervisorLabelsConfigured", "Get-SupervisorMode",
                    "Invoke-SupervisorStart",
                )
            ),
            "Invoke-SupervisorStart -Name fixture "
            "-EnvFile (Join-Path $InstallDir 'supervisor.env') -Label fixture",
            "@{selected=$script:Selected;audits=$script:Audits;skips=$script:Skips}"
            "|ConvertTo-Json -Depth 4 -Compress",
        ]
    )
    fixture = tmp_path / "start-selection.ps1"
    fixture.write_text(script, encoding="utf-8")
    result = subprocess.run(
        [PWSH, "-NoProfile", "-NonInteractive", "-File", str(fixture)],
        capture_output=True,
        text=True,
        timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert observed["selected"] == expected
    if task_state == "Disabled":
        assert observed["audits"] == [
            {
                "event": "selection", "reason": "prior-native-disabled",
                "name": "fixture", "outcome": "skipped",
            }
        ]
        assert observed["skips"] == [
            "fixture remains disabled; explicit native enablement is required"
        ]
    else:
        assert observed["audits"] == []
        assert observed["skips"] == []
