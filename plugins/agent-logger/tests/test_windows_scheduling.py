"""Windows scheduling policy and mocked native task-only entrypoint."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import venv

import pytest
import yaml

from agent_logger.scheduling import windows_scheduled_task

PLUGIN = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("policy", ("preserve", "enabled", "absent"))
def test_installation_policy_roundtrip(tmp_path: Path, policy: str) -> None:
    config = tmp_path / "config.yaml"
    data = {"windows_scheduled_task": policy, "sync": {"enabled": True}}
    config.write_text(yaml.safe_dump(data), encoding="utf-8")
    assert windows_scheduled_task(tmp_path) == policy
    assert yaml.safe_load(config.read_text()) == data


@pytest.mark.parametrize("document", ("", "{}", "sync: {enabled: true}"))
def test_missing_policy_preserves(tmp_path: Path, document: str) -> None:
    assert windows_scheduled_task(tmp_path) == "preserve"
    (tmp_path / "config.yaml").write_text(document, encoding="utf-8")
    assert windows_scheduled_task(tmp_path) == "preserve"


@pytest.mark.parametrize("document", (
    "[]", "false", "windows_scheduled_task: false", "windows_scheduled_task: null",
    "windows_scheduled_task: automatically", "windows_scheduled_task: [absent]", "[",
))
def test_invalid_policy_is_not_absence(tmp_path: Path, document: str) -> None:
    (tmp_path / "config.yaml").write_text(document, encoding="utf-8")
    with pytest.raises((ValueError, yaml.YAMLError)):
        windows_scheduled_task(tmp_path)


def _ps_run(tmp_path: Path, body: str) -> subprocess.CompletedProcess[str]:
    pwsh = shutil.which("pwsh")
    if not pwsh or os.name != "nt":
        pytest.skip("Windows PowerShell entrypoint fixture")
    script = tmp_path / "fixture.ps1"
    script.write_text("$ErrorActionPreference='Stop'\n" + body, encoding="utf-8")
    env = {**os.environ, "USERPROFILE": str(tmp_path), "HOME": str(tmp_path),
           "COPILOT_EXTENSIONS_TEST_CONTAINED": "1"}
    for key in tuple(env):
        if key.startswith(("COPILOT_PLUGIN_", "COPILOT_EXTENSIONS_CONTEXT",
                           "COPILOT_EXTENSIONS_COMMAND_", "AGENT_LOGGER_")):
            env.pop(key)
    return subprocess.run(
        [pwsh, "-NoProfile", "-NonInteractive", "-File", str(script)],
        env=env, capture_output=True, text=True, timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


@pytest.mark.parametrize("case", (
    "missing", "owned", "foreign", "disabled", "query-error", "remove-error",
    "unconfirmed", "ambiguous", "multiple-actions", "dry-run",
))
def test_native_task_retirement_preserves_runtime_and_state(tmp_path: Path, case: str) -> None:
    root = tmp_path / "installed"
    root.mkdir()
    durable = {"config.yaml": b"sync: {enabled: true}\n", "sessions.db": b"data",
               "current-version": b"installed-not-incoming", "owner.live.json": b"live"}
    for name, value in durable.items():
        (root / name).write_bytes(value)
    task_name = "Agent Logger Session Sync - " + hashlib.sha256(
        str(root).lower().encode(),
    ).hexdigest()[:12]
    launcher = root / "bin" / "session-sync-task.ps1"
    args = f'-I "{launcher.with_suffix(".py")}" "C:\\shell\\pwsh.exe" "{launcher}"'
    result = _ps_run(tmp_path, f"""
$global:removed=$false
$global:task=[pscustomobject]@{{
    TaskName='{task_name}'; TaskPath='\\'; State='Ready'
    Settings=[pscustomobject]@{{Enabled=$true}}
    Actions=@([pscustomobject]@{{Execute='C:\\python\\pythonw.exe'; Arguments='{args}'}})
}}
if ('{case}' -eq 'foreign') {{ $global:task.Actions[0].Arguments='foreign' }}
if ('{case}' -eq 'disabled') {{ $global:task.Settings.Enabled=$false }}
if ('{case}' -eq 'ambiguous') {{ $global:task.TaskPath='\\another\\' }}
if ('{case}' -eq 'multiple-actions') {{ $global:task.Actions += $global:task.Actions[0] }}
function Get-ScheduledTask {{
    if ('{case}' -eq 'query-error') {{ throw 'query refused' }}
    if ('{case}' -ne 'missing' -and (-not $global:removed -or '{case}' -eq 'unconfirmed')) {{
        $global:task
    }}
}}
function Unregister-ScheduledTask {{
    param($TaskName,$TaskPath,[switch]$Confirm)
    if ('{case}' -eq 'remove-error') {{ throw 'removal refused' }}
    $global:removed=$true
}}
function Stop-ScheduledTask {{ throw 'must not stop running sync' }}
function Disable-ScheduledTask {{ throw 'must not change independent enablement' }}
function Register-ScheduledTask {{ throw 'must not register' }}
function Start-Process {{ throw 'must not launch runtime' }}
try {{
    & '{PLUGIN / "scripts" / "install.ps1"}' task-retire -InstallDir '{root}' -DryRun:${str(case == "dry-run").lower()}
    if ('{case}' -in @('missing','owned')) {{
        & '{PLUGIN / "scripts" / "install.ps1"}' task-retire -InstallDir '{root}'
    }}
    if ('{case}' -notin @('missing','owned','dry-run')) {{ throw 'unexpected success' }}
}} catch {{
    if ('{case}' -in @('missing','owned','dry-run') -or $_ -match 'unexpected success') {{ throw }}
    Write-Output "EXPECTED: $_"
}}
if ($global:removed -ne ('{case}' -in @('owned','unconfirmed'))) {{ throw 'wrong registration effect' }}
""")
    assert result.returncode == 0, result.stdout + result.stderr
    expected_error = {
        "foreign": "foreign or unsupported", "disabled": "disabled",
        "query-error": "query refused", "remove-error": "removal refused",
        "unconfirmed": "removal not confirmed", "ambiguous": "Ambiguous",
        "multiple-actions": "foreign or unsupported",
    }.get(case)
    if expected_error:
        assert expected_error in result.stdout, result.stdout
    assert {name: (root / name).read_bytes() for name in durable} == durable
    assert sorted(p.name for p in root.iterdir()) == sorted(durable)


@pytest.mark.parametrize("existing", (False, True))
def test_default_and_repeat_do_not_touch_custom_or_missing_task(tmp_path: Path, existing: bool) -> None:
    result = _ps_run(tmp_path, f"""
$InstallDir='{tmp_path}'
$LinkPython='not-an-installed-runtime'
$DryRun=$false
$global:customTask=if (${str(existing).lower()}) {{ 'custom-disabled-registration' }} else {{ $null }}
function Write-Ok {{ param($Message) }}
function Get-ScheduledTask {{ throw 'preserve must not query or rewrite tasks' }}
function Register-SyncTask {{ throw 'preserve must not register' }}
. '{PLUGIN / "scripts" / "windows-scheduled-task.ps1"}'
Sync-LoggerScheduledTask
Sync-LoggerScheduledTask
if (${str(existing).lower()} -and $global:customTask -ne 'custom-disabled-registration') {{ throw 'custom task changed' }}
""")
    assert result.returncode == 0, result.stdout + result.stderr


def test_explicit_scheduling_reaches_supported_registration(tmp_path: Path) -> None:
    result = _ps_run(tmp_path, f"""
$InstallDir='{tmp_path}'
$DryRun=$false
function Write-Ok {{ param($Message) }}
function Get-ScheduledTask {{}}
. '{PLUGIN / "scripts" / "windows-scheduled-task.ps1"}'
function Get-WindowsScheduledTaskPolicy {{ 'enabled' }}
$script:calls=0
function Register-SyncTask {{ $script:calls++ }}
Sync-LoggerScheduledTask
if ($script:calls -ne 1) {{ throw 'explicit scheduling lost' }}
""")
    assert result.returncode == 0, result.stdout + result.stderr


def test_native_policy_reader_uses_real_selected_interpreter(tmp_path: Path) -> None:
    if os.name != "nt":
        pytest.skip("Windows runtime layout")
    runtime = tmp_path / "selected-runtime"
    venv.EnvBuilder(with_pip=False).create(runtime)
    site = runtime / "Lib" / "site-packages"
    (site / "fixture-source.pth").write_text(
        str(PLUGIN / "src") + "\n" + str(Path(yaml.__file__).parent.parent) + "\n",
        encoding="utf-8",
    )
    for policy in ("preserve", "enabled", "absent", "invalid"):
        config = tmp_path / "config.yaml"
        document = f"windows_scheduled_task: {policy}\nsync: {{enabled: true}}\n"
        config.write_text(document, encoding="utf-8")
        result = _ps_run(tmp_path, f"""
$InstallDir='{tmp_path}'
$LinkPython='{runtime / "Scripts" / "python.exe"}'
. '{PLUGIN / "scripts" / "windows-scheduled-task.ps1"}'
$policy=Get-WindowsScheduledTaskPolicy
if ($policy -ne '{policy}') {{ throw "Wrong installed policy: $policy" }}
""")
        assert (result.returncode == 0) == (policy != "invalid"), result.stdout + result.stderr
        if policy == "invalid":
            assert "Cannot read installation-local" in result.stderr
        assert config.read_text(encoding="utf-8") == document
