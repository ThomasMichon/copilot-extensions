from __future__ import annotations

from pathlib import Path

import pytest

from tools.install_contract_guard import (
    PERSISTENT_ENV_END,
    PERSISTENT_ENV_START,
    _lexical_views,
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


@pytest.mark.parametrize(
    ("text", "source", "calls"),
    [
        ("", "", ""),
        ("a\nb\r\n", "a\nb\r\n", "a\nb\r\n"),
        ("a#x\r\nb", "a   \nb", "a   \nb"),
        ("<#x\n#>a", "   \n  a", "   \n  a"),
        ("<#'<#\n", "     \n", "     \n"),
        ("#'\"<#`", "      ", "      "),
        ("'a#b'", "'a#b'", "     "),
        ('"a<#b"', '"a<#b"', "      "),
        ("'a''b'#x", "'a''b'  ", "        "),
        ('"a`"b"#x', '"a`"b"  ', "        "),
        ("'a\nb'", "'a\nb'", "  \n  "),
        ('"a\nb"', '"a\nb"', "  \n  "),
        ('"a`\nb"', '"a`\nb"', "      "),
        ("'a`'#x", "'a`'  ", "      "),
        ('"a`', '"a`', "    "),
        ("'a", "'a", "  "),
        ('"a`"\n#x', '"a`"\n#x', "    \n  "),
    ],
)
def test_lexical_views_preserve_masking_and_escape_positions(text, source, calls):
    assert _lexical_views(text) == (source, calls)


def test_violations_keep_category_order_line_numbers_and_arguments():
    text = """# [Environment]::GetEnvironmentVariable('hidden', 'User')
$key = 'HKCU:\\Environment'
[Microsoft.Win32.Registry]::CurrentUser.OpenSubKey('Environment')
[Environment]::SetEnvironmentVariable(
    'Path', $(Get-Value 'a,b'), <# ignored target #> 'User'
)
[Environment]::GetEnvironmentVariable('Path', 'Process')
[Environment]::GetEnvironmentVariable(
"""
    assert persistent_environment_violations(text) == [
        "line 4: direct .NET environment target \"'User'\"",
        "line 8: unterminated .NET environment call",
        "line 2: direct persistent environment registry path",
        "line 3: direct CurrentUser environment registry API",
    ]


def test_adapter_removal_keeps_following_line_numbers():
    text = f"""before
{PERSISTENT_ENV_START}
[Environment]::GetEnvironmentVariable('inside', 'User')
{PERSISTENT_ENV_END}
[Environment]::GetEnvironmentVariable('after', 'Machine')
"""
    assert persistent_environment_violations(text) == [
        "line 5: direct .NET environment target \"'Machine'\"",
    ]


def test_unterminated_adapter_does_not_hide_persistent_access():
    text = f"""{PERSISTENT_ENV_START}
[Environment]::GetEnvironmentVariable('Path', 'User')
"""
    assert persistent_environment_violations(text) == [
        "line 2: direct .NET environment target \"'User'\"",
    ]
