"""Installation-local scheduling and a mocked native task-only entrypoint."""

from __future__ import annotations

import os
import json
from pathlib import Path
import shutil
import subprocess
import venv

import pytest
import yaml

from agent_codespaces.scheduling import windows_scheduled_task

PLUGIN = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("policy", ("preserve", "enabled", "absent"))
def test_installation_policy_roundtrip(tmp_path: Path, policy: str) -> None:
    config = tmp_path / "config.yaml"
    data = {"windows_scheduled_task": policy, "connection_owner": {"enabled": True}}
    config.write_text(yaml.safe_dump(data), encoding="utf-8")
    assert windows_scheduled_task(tmp_path) == policy
    assert yaml.safe_load(config.read_text()) == data


@pytest.mark.parametrize("document", ("", "{}", "connection_owner: {enabled: true}"))
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
                           "COPILOT_EXTENSIONS_COMMAND_", "AGENT_CODESPACES_")):
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
def test_native_task_retirement_preserves_owner_and_state(tmp_path: Path, case: str) -> None:
    root = tmp_path / "installed"
    root.mkdir()
    durable = {"config.yaml": b"connection_owner: {enabled: true}\n", "leases.db": b"data",
               "current-version": b"installed-not-incoming", "connection-owner.live.json": b"live"}
    for name, value in durable.items():
        (root / name).write_bytes(value)
    stub = tmp_path / ".local" / "bin" / "agent-codespaces.ps1"
    args = f'--headless "C:\\shell\\pwsh.exe" -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{stub}" owner'
    result = _ps_run(tmp_path, f"""
$global:removed=$false
$global:task=[pscustomobject]@{{
    TaskName='agent-codespaces-owner'; TaskPath='\\'; State='Ready'
    Settings=[pscustomobject]@{{Enabled=$true}}
    Actions=@([pscustomobject]@{{Execute='conhost.exe'; Arguments='{args}'; WorkingDirectory='{root}'}})
}}
if ('{case}' -eq 'foreign') {{ $global:task.Actions[0].WorkingDirectory='another-installation' }}
if ('{case}' -eq 'disabled') {{ $global:task.State='Disabled' }}
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
function Stop-ScheduledTask {{ throw 'must not stop owner' }}
function Disable-ScheduledTask {{ throw 'must not change independent enablement' }}
function Register-ScheduledTask {{ throw 'must not register' }}
function Start-Process {{ throw 'must not launch runtime' }}
function Get-ConnectionOwnerConfig {{ throw 'task-retire must not query capability activation' }}
function Sync-ConnectionOwnerService {{ throw 'task-retire must not reconcile a service' }}
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


def _sync_definition() -> str:
    return _installer_definition("Sync-ConnectionOwnerService")


def _installer_definition(name: str) -> str:
    source = (PLUGIN / "scripts" / "install.ps1").read_text(encoding="utf-8")
    return f"function {name}" + source.split(
        f"function {name}", 1,
    )[1].split("\n}\n", 1)[0] + "\n}\n"


@pytest.mark.parametrize("case", ("missing", "custom", "disabled", "no-module", "query-error"))
def test_preserve_never_registers_and_honors_disabled_or_unknown_hold(tmp_path: Path, case: str) -> None:
    result = _ps_run(tmp_path, f"""
$InstallDir='{tmp_path}'
$LocalBin='{tmp_path}'
$LinkPython='not-an-installed-runtime'
$OwnerTaskName='agent-codespaces-owner'
function Write-ServiceOk {{ param($Message) }}
function Write-ServiceWarn {{ param($Message) }}
function Write-ServiceChanged {{ param($Message) }}
. '{PLUGIN / "scripts" / "windows-scheduled-task.ps1"}'
$script:ensures=0
function Get-ConnectionOwnerConfig {{ @{{Known=$true; Enabled=$true; Interval=15}} }}
function Get-ScheduledTask {{
    if ('{case}' -eq 'query-error') {{ throw 'query refused' }}
    if ('{case}' -in @('custom','disabled')) {{
        [pscustomobject]@{{ TaskName=$OwnerTaskName; TaskPath='\\';
            State='Ready'; Settings=[pscustomobject]@{{Enabled=('{case}' -ne 'disabled')}} }}
    }}
}}
function Get-Command {{
    param($Name)
    if ($Name -ne 'Get-ScheduledTask' -or '{case}' -ne 'no-module') {{
        Microsoft.PowerShell.Core\\Get-Command $Name
    }}
}}
function not-an-installed-runtime {{ $script:ensures++; $global:LASTEXITCODE=0 }}
function Register-ScheduledTask {{ throw 'preserve must not register' }}
function New-ScheduledTaskAction {{ throw 'preserve must not construct task action' }}
{_sync_definition()}
try {{
    Sync-ConnectionOwnerService
    Sync-ConnectionOwnerService
    if ('{case}' -eq 'query-error') {{ throw 'unexpected success' }}
}} catch {{
    if ('{case}' -ne 'query-error' -or $_ -match 'unexpected success') {{ throw }}
}}
$expected=if ('{case}' -in @('missing','custom')) {{ 2 }} else {{ 0 }}
if ($script:ensures -ne $expected) {{ throw "wrong direct-start count $script:ensures" }}
""")
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("lifecycle", ("Install", "Update"))
@pytest.mark.parametrize("activation", ("enabled", "disabled", "unknown"))
def test_absent_policy_retains_normal_selected_owner_convergence(
    tmp_path: Path, lifecycle: str, activation: str,
) -> None:
    if os.name != "nt":
        pytest.skip("Windows runtime layout")
    runtime = tmp_path / "selected-runtime"
    venv.EnvBuilder(with_pip=False).create(runtime)
    site = runtime / "Lib" / "site-packages"
    repo = PLUGIN.parents[1]
    sources = [
        PLUGIN / "src", Path(yaml.__file__).parent.parent,
        *[path / "src" if (path / "src").is_dir() else path
          for path in (repo / "libs").iterdir() if path.is_dir()],
    ]
    (site / "fixture-source.pth").write_text(
        "\n".join(map(str, sources)) + "\n", encoding="utf-8",
    )
    witness = tmp_path / "ensure.json"
    (site / "sitecustomize.py").write_text(
        "import sys\nsys.dont_write_bytecode = True\n"
        "import json\nfrom pathlib import Path\n"
        "from agent_codespaces import connection_owner\n"
        "def fixture_ensure(config):\n"
        f"    Path({str(witness)!r}).write_text(json.dumps({{"
        "'executable': sys.executable, 'config_is_none': config is None, "
        "'isolated': sys.flags.isolated}), encoding='utf-8')\n"
        "    return True\n"
        "connection_owner.ensure_owner_running = fixture_ensure\n",
        encoding="utf-8",
    )
    selected = runtime / "Scripts" / "python.exe"
    result = _ps_run(tmp_path, f"""
$InstallDir='{tmp_path}'
$LocalBin='{tmp_path}'
$LinkPython='{selected}'
$ServiceName='fixture service'
$DryRun=$false
$OwnerTaskName='agent-codespaces-owner'
$script:capabilityQueries=0
$script:messages=[Collections.Generic.List[string]]::new()
function Write-Step {{ param($Message) }}
function Write-ServiceOk {{ param($Message) $script:messages.Add($Message) }}
function Write-ServiceWarn {{ param($Message) }}
function Select-InstallRuntimeGeneration {{}}
function Deploy-Venv {{ $true }}
function Deploy-Package {{ $true }}
function Invoke-VersionedActivate {{ $true }}
function Deploy-VersionedRuntimeHelper {{}}
function Deploy-SelfProvisioningBinstub {{}}
function Write-CodespacesDeployManifest {{}}
function Test-CodespacesRuntime {{ param($Python) $true }}
function fixture-migrate {{ $global:LASTEXITCODE=0 }}
$VenvPython='fixture-migrate'
. '{PLUGIN / "scripts" / "windows-scheduled-task.ps1"}'
function Get-WindowsScheduledTaskPolicy {{ 'absent' }}
function Get-ScheduledTask {{}}
function Get-ConnectionOwnerConfig {{
    $script:capabilityQueries++
    @{{Known=('{activation}' -ne 'unknown'); Enabled=('{activation}' -eq 'enabled'); Interval=15}}
}}
function Register-ScheduledTask {{ throw 'absence must not register' }}
function Start-ScheduledTask {{ throw 'must not start through Task Scheduler' }}
function Stop-ScheduledTask {{ throw 'must not stop an Owner' }}
function Disable-ScheduledTask {{ throw 'must not change independent holds' }}
function Unregister-ConnectionOwnerService {{ throw 'must not stop or unregister a service' }}
{_sync_definition()}
{_installer_definition("Invoke-" + lifecycle)}
try {{
    Invoke-{lifecycle}
    if ('{activation}' -eq 'unknown') {{ throw 'unexpected readiness success' }}
}} catch {{
    if ('{activation}' -ne 'unknown' -or $_ -notmatch 'selected Owner readiness not confirmed') {{ throw }}
}}
if ($script:capabilityQueries -ne 1) {{ throw 'normal lifecycle skipped capability activation query' }}
if ('{activation}' -eq 'unknown' -and @($script:messages | Where-Object {{ $_ -match '^fixture service (installed|updated)$' }}).Count) {{
    throw 'unknown capability falsely reported complete readiness'
}}
""")
    assert result.returncode == 0, result.stdout + result.stderr
    assert witness.exists() == (activation == "enabled"), result.stdout + result.stderr
    if witness.exists():
        assert json.loads(witness.read_text(encoding="utf-8")) == {
            "executable": str(selected), "config_is_none": True, "isolated": 1,
        }


@pytest.mark.parametrize("failure", ("disabled", "query-error", "missing-module"))
def test_absent_retirement_refusal_prevents_direct_owner_start(tmp_path: Path, failure: str) -> None:
    stub = tmp_path / "agent-codespaces.ps1"
    args = f'--headless "C:\\shell\\pwsh.exe" -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{stub}" owner'
    result = _ps_run(tmp_path, f"""
$InstallDir='{tmp_path}'
$LocalBin='{tmp_path}'
$OwnerTaskName='agent-codespaces-owner'
$DryRun=$false
. '{PLUGIN / "scripts" / "windows-scheduled-task.ps1"}'
function Get-WindowsScheduledTaskPolicy {{ 'absent' }}
function Get-ScheduledTask {{
    if ('{failure}' -eq 'query-error') {{ throw 'query refused' }}
    if ('{failure}' -eq 'missing-module') {{ throw [Management.Automation.CommandNotFoundException]::new('ScheduledTasks module unavailable') }}
    [pscustomobject]@{{TaskName=$OwnerTaskName; TaskPath='\\'; State='Disabled'
        Settings=[pscustomobject]@{{Enabled=$false}}
        Actions=@([pscustomobject]@{{Execute='conhost.exe'; Arguments='{args}'; WorkingDirectory=$InstallDir}})}}
}}
function Get-ConnectionOwnerConfig {{ throw 'unexpected capability query after retirement refusal' }}
function Unregister-ScheduledTask {{ throw 'unexpected removal of a held task' }}
function Start-ScheduledTask {{ throw 'unexpected task start' }}
function Stop-ScheduledTask {{ throw 'unexpected task stop' }}
{_sync_definition()}
try {{
    Sync-ConnectionOwnerService
    throw 'unexpected successful absence'
}} catch {{
    $expected=switch ('{failure}') {{
        'disabled' {{ 'task is disabled' }}
        'query-error' {{ 'query refused' }}
        'missing-module' {{ 'ScheduledTasks module unavailable' }}
    }}
    if ($_ -notmatch $expected) {{ throw }}
}}
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
        document = f"windows_scheduled_task: {policy}\nconnection_owner: {{enabled: true}}\n"
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
