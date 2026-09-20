from __future__ import annotations

from pathlib import Path

import pytest

from tools.install_contract_guard import (
    PERSISTENT_ENV_END,
    PERSISTENT_ENV_START,
    is_ignored_scan_path,
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


@pytest.mark.parametrize(
    "path",
    [
        Path("plugins/agent-worktrees/.venv/Scripts/Activate.ps1"),
        Path(".git/hooks/pre-push.ps1"),
        Path("worktree-manager/.test-venvs/foo/Scripts/Activate.ps1"),
        Path("libs/foo/.venv-tools/bin/x.ps1"),
        Path("web/node_modules/.bin/thing.ps1"),
    ],
)
def test_ignores_generated_and_vendored_scan_paths(path):
    assert is_ignored_scan_path(path)


@pytest.mark.parametrize(
    "path",
    [
        Path("plugins/agent-worktrees/scripts/install.ps1"),
        Path("libs/installation-context/tests/powershell-test-host.ps1"),
        Path("worktree-manager/bin/launch-session.ps1"),
    ],
)
def test_allows_tracked_source_scan_paths(path):
    assert not is_ignored_scan_path(path)
