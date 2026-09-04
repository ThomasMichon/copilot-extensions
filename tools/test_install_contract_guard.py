from __future__ import annotations

import pytest

from tools.install_contract_guard import (
    PERSISTENT_ENV_END,
    PERSISTENT_ENV_START,
    persistent_environment_violations,
)


@pytest.mark.parametrize(
    "source",
    [
        """
[Environment]::SetEnvironmentVariable(
    'Path',
    $value,
    'User'
)
""",
        """
[System.Environment]::GetEnvironmentVariable(
    'Path',
    [EnvironmentVariableTarget]::Machine
)
""",
        """
$target = [EnvironmentVariableTarget]::User
[Environment]::SetEnvironmentVariable('Path', $value, $target)
""",
        r"Set-ItemProperty -Path 'HKCU:\Environment' -Name Path -Value $value",
        (
            r"$key = 'HKLM:\SYSTEM\CurrentControlSet\Control\Session "
            r"Manager\Environment'"
        ),
        """
$key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey(
    'Environment',
    $true
)
""",
    ],
)
def test_rejects_persistent_environment_bypasses(source):
    assert persistent_environment_violations(source)


@pytest.mark.parametrize(
    "source",
    [
        "[Environment]::GetEnvironmentVariable('Path')",
        "[Environment]::GetEnvironmentVariable('Path', 'Process')",
        (
            "[Environment]::SetEnvironmentVariable("
            "'Path', $value, [EnvironmentVariableTarget]::Process)"
        ),
        (
            "Write-Host \"Example: "
            "[Environment]::SetEnvironmentVariable('Path', 'x', 'User')\""
        ),
        "# [Environment]::SetEnvironmentVariable('Path', 'x', 'Machine')",
    ],
)
def test_allows_process_environment_access(source):
    assert persistent_environment_violations(source) == []


def test_allows_persistent_access_only_inside_canonical_adapter():
    source = f"""
{PERSISTENT_ENV_START}
function Set-CopilotPersistentEnvironmentVariable {{
    [Environment]::SetEnvironmentVariable($Name, $Value, $Target)
}}
{PERSISTENT_ENV_END}
Set-CopilotPersistentEnvironmentVariable -Name Path -Value $value -Target User
"""

    assert persistent_environment_violations(source) == []
