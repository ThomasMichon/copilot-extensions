"""Contained native policy consumers: no real scheduler, daemon or install."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from xml.sax.saxutils import escape

import pytest

from test_install_engine_adoption import environment

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
SOURCE = (SCRIPTS / "install.ps1").read_text(encoding="utf-8-sig")
pytestmark = [
    pytest.mark.portfolio_tier("T3"),
    pytest.mark.skipif(os.name != "nt", reason="native Windows policy consumer"),
]


def function(name: str) -> str:
    match = re.search(rf"(?ms)^function {re.escape(name)} \{{.*?^\}}$", SOURCE)
    assert match, name
    return match.group()


def task(root: Path, name="agent-index", *, enabled=True, arguments=None, command="conhost.exe"):
    launcher = root / ("service.ps1" if name == "agent-index" else r"engine\engine.ps1")
    arguments = arguments or (
        '--headless powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden '
        f'-File "{launcher}"'
    )
    return (
        '<Task xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task" version="1.2">'
        f"<RegistrationInfo><URI>\\{name}</URI></RegistrationInfo>"
        "<Principals><Principal><UserId>FIXTURE-IDENTITY</UserId>"
        "<LogonType>InteractiveToken</LogonType></Principal></Principals>"
        f"<Settings><Enabled>{str(enabled).lower()}</Enabled></Settings>"
        f"<Actions><Exec><Command>{escape(command)}</Command><Arguments>{escape(arguments)}"
        "</Arguments></Exec></Actions></Task>"
    )


def run_policy(tmp_path, host, *, tasks=None, policy=None, body="", action=None, failure=""):
    shell = shutil.which(host)
    if not shell:
        pytest.skip(f"{host} unavailable")
    root = tmp_path / "install"
    root.mkdir(exist_ok=True)
    (root / "service.ps1").write_text("throw 'must not execute launcher'")
    (root / "engine").mkdir(exist_ok=True)
    (root / "engine" / "engine.ps1").write_text("throw 'must not execute engine launcher'")
    if policy is not None:
        (root / "installation.json").write_text(json.dumps({
            "version": 1, "installation": {"root": str(root), "local": policy},
        }))
    fixture = tmp_path / "tasks.json"
    fixture.write_text(json.dumps(tasks or {}))
    for name in ("source-runtime.ps1", "installer-instance-reconcile.ps1"):
        (tmp_path / name).write_text("")
    helpers = "\n".join(function(name) for name in (
        "New-IndexMutex", "Enter-IndexStampLock", "Test-TaskElevationOptIn",
        "Invoke-RegisterTasks", "Ensure-Running",
    ))
    script = f"""
$ErrorActionPreference='Stop'
Set-StrictMode -Version 2
$InstallDir='{root}'; $EngineHome=Join-Path $InstallDir 'engine'
$TaskName='agent-index'; $EngineTaskName='agent-index-engine'
$Launcher=Join-Path $InstallDir 'service.ps1'; $EngineLauncher=Join-Path $EngineHome 'engine.ps1'
$NoService=$false; $DryRun=$false; $AllowTaskElevation=$false; $SrcVersion='fixture'
$ScheduledTaskNames=$null; $script:removed=@(); $script:events=@()
$script:tasks=@{{}}
$parsed=Get-Content -LiteralPath '{fixture}' -Raw | ConvertFrom-Json
foreach ($p in $parsed.PSObject.Properties) {{
 $script:tasks[$p.Name]=$p.Value.Replace('FIXTURE-IDENTITY', [Security.Principal.WindowsIdentity]::GetCurrent().User.Value)
}}
function Write-Ok {{ param($Msg) }}
function Write-Skip {{ param($Msg) }}
function Write-Warn {{ param($Msg) }}
function Write-Step {{ param($Msg) }}
function Test-Elevated {{ $false }}
function Invoke-ScopedElevation {{ throw 'forbidden elevation' }}
function Start-ScheduledTask {{ throw 'forbidden task start' }}
function Stop-ScheduledTask {{ throw 'forbidden task stop' }}
function Start-Process {{ throw 'forbidden process start' }}
function Stop-Process {{ throw 'forbidden process stop' }}
function New-ItemProperty {{ throw 'forbidden registry write' }}
function Remove-Item {{ throw 'forbidden state deletion' }}
function Get-BootstrapPython {{ throw 'forbidden runtime selection' }}
function Initialize-SourceRuntime {{ }}
function Assert-SourceRuntime {{ }}
function Install-Runtime {{ $script:events += 'build' }}
function Invoke-DowngradeGuard {{ }}
function Invoke-InstallerInstanceReconcile {{ }}
function Get-ActivationRole {{ 'host' }}
function Get-RequiredRuntimeRole {{ 'host' }}
function Invoke-ServiceCutover {{ if (Test-IndexStartAllowed $TaskName) {{ $script:events += 'service' }} }}
function Ensure-ServiceRunning {{ if (Test-IndexStartAllowed $TaskName) {{ $script:events += 'service' }} }}
function Ensure-EngineRunning {{ param([switch]$RequireReady); if (Test-IndexStartAllowed $EngineTaskName) {{ $script:events += 'engine' }} }}
function New-TaskSpec {{ param($LauncherPath); @{{}} }}
function Register-UserModeTask {{ param($TaskName,$Spec); $script:events += "schedule:$TaskName"; $true }}
function Invoke-Hidden {{
 param($FilePath,$ArgList,$ErrorOutput)
 if ($FilePath -ne 'schtasks.exe') {{ throw 'foreign native operation' }}
 $global:LASTEXITCODE=0
 if ('{failure}' -eq 'query') {{ $global:LASTEXITCODE=5; return '' }}
 if ($ArgList[0] -eq '/Delete') {{
  if ('{failure}' -eq 'remove') {{ $global:LASTEXITCODE=5; return '' }}
  $name=$ArgList[2].TrimStart('\\')
  $script:removed += $name
  if ('{failure}' -ne 'unconfirmed') {{ $script:tasks.Remove($name) }}
  return ''
 }}
 if ($ArgList -contains '/XML') {{ return $script:tasks[$ArgList[2].TrimStart('\\')] }}
 return (($script:tasks.Keys | ForEach-Object {{ '"\\'+$_+'","N/A","Ready"' }}) -join "`n")
}}
. '{SCRIPTS / "scheduling-policy.ps1"}'
{helpers}
"""
    if action:
        script += f"$Action='{action}'\n"
        # Execute the real production admission and dispatch, with only native
        # effects/builds replaced above. This catches accidental action shadowing.
        script += SOURCE.split("$transactionMutex = $null\ntry {\n", 1)[1]
        script = script.replace(
            f"$Action='{action}'\n", f"$Action='{action}'\n$transactionMutex=$null\ntry {{\n", 1,
        )
    else:
        script += "\nInitialize-IndexSchedulingPolicy\n" + body + "\n"
    # Early return must still expose the observations to this caller.
    harness = tmp_path / "harness.ps1"
    harness.write_text(
        "try {\n& {\n" + script + "\n}\n} finally {\n"
        " @{removed=$script:removed;events=$script:events} | ConvertTo-Json -Compress\n}\n",
        encoding="utf-8-sig",
    )
    result = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(harness)],
        env=environment(tmp_path), cwd=tmp_path, text=True, capture_output=True, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    return result


HOSTS = pytest.mark.parametrize("host", ["powershell.exe", "pwsh"])


@HOSTS
@pytest.mark.parametrize("action", ["install", "update", "provision"])
def test_default_and_repeated_install_keep_tasks_absent_and_converge_capabilities(tmp_path, host, action):
    for _ in range(2):
        result = run_policy(tmp_path, host, action=action)
        assert result.returncode == 0, result.stdout + result.stderr
        assert json.loads(result.stdout.splitlines()[-1]) == {
            "removed": [], "events": ["build", "service", "engine"],
        }


@HOSTS
def test_preserve_leaves_custom_tasks_and_disabled_holds_untouched(tmp_path, host):
    root = tmp_path / "install"
    tasks = {"agent-index": task(root, enabled=False, arguments='foreign custom args'),
             "agent-index-engine": task(root, "agent-index-engine", arguments="custom deployment")}
    result = run_policy(tmp_path, host, tasks=tasks, action="update")
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout.splitlines()[-1]) == {"removed": [], "events": ["build", "engine"]}


@HOSTS
@pytest.mark.parametrize("action", ["task-retire", "update"])
def test_explicit_owned_retirement_is_only_task_removal_and_preserves_readiness(tmp_path, host, action):
    root = tmp_path / "install"
    result = run_policy(tmp_path, host, action=action, policy={"windows_scheduled_task": "absent"},
                        tasks={name: task(root, name) for name in ("agent-index", "agent-index-engine")})
    assert result.returncode == 0, result.stdout + result.stderr
    observed = json.loads(result.stdout.splitlines()[-1])
    assert observed["removed"] == ["agent-index", "agent-index-engine"]
    assert observed["events"] == ([] if action == "task-retire" else ["build", "service", "engine"])


@HOSTS
@pytest.mark.parametrize("failure", ["query", "remove", "unconfirmed"])
def test_task_observation_and_removal_errors_refuse_without_lifecycle(tmp_path, host, failure):
    root = tmp_path / "install"
    result = run_policy(tmp_path, host, action="task-retire", failure=failure,
                        policy={"windows_scheduled_task": "absent"}, tasks={"agent-index": task(root)})
    assert result.returncode != 0
    assert json.loads(result.stdout.splitlines()[-1])["events"] == []


@HOSTS
@pytest.mark.parametrize("arguments", [
    '--headless powershell.exe -File "C:\\foreign.ps1" -File "{owned}"',
    '--headless powershell.exe -Command "& \\"{owned}\\""',
    '--headless powershell.exe -File "{owned}" extra',
    '--headless powershell.exe -File "{owned}" -NoProfile',
    '--headless powershell.exe -File "{owned}',
])
def test_foreign_or_ambiguous_shell_operands_never_authorize_retirement(tmp_path, host, arguments):
    root = tmp_path / "install"
    result = run_policy(
        tmp_path, host, action="task-retire", policy={"windows_scheduled_task": "absent"},
        tasks={"agent-index": task(root, arguments=arguments.format(owned=root / "service.ps1"))},
    )
    assert result.returncode != 0
    assert json.loads(result.stdout.splitlines()[-1]) == {"removed": [], "events": []}


@HOSTS
@pytest.mark.parametrize("hold", [False, True])
def test_retiring_disabled_task_requires_durable_hold_and_never_bypasses_it(tmp_path, host, hold):
    root = tmp_path / "install"
    local = {"windows_scheduled_task": "absent"}
    if hold:
        local["start_service"] = False
    result = run_policy(tmp_path, host, action="update", policy=local,
                        tasks={"agent-index": task(root, enabled=False)})
    assert (result.returncode == 0) is hold, result.stdout + result.stderr
    observed = json.loads(result.stdout.splitlines()[-1])
    assert observed == ({"removed": ["agent-index"], "events": ["build", "engine"]}
                        if hold else {"removed": [], "events": []})


@HOSTS
def test_explicit_schedule_only_does_not_activate_daemons_or_reenable_disabled_task(tmp_path, host):
    root = tmp_path / "install"
    result = run_policy(tmp_path, host, action="register-tasks",
                        tasks={"agent-index": task(root, enabled=False)})
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout.splitlines()[-1]) == {
        "removed": [], "events": ["schedule:agent-index-engine"],
    }


@HOSTS
def test_direct_consumers_observe_disabled_hold_before_runtime_selection(tmp_path, host):
    result = run_policy(
        tmp_path, host, tasks={"agent-index": task(tmp_path / "install", enabled=False),
                              "agent-index-engine": task(tmp_path / "install", "agent-index-engine", enabled=False)},
        body="\n".join(function(name) for name in (
            "Ensure-ServiceRunning", "Ensure-EngineRunning", "Invoke-ServiceCutover", "Restart-EngineDaemon",
        )) + "\nEnsure-ServiceRunning\nEnsure-EngineRunning\nInvoke-ServiceCutover\nRestart-EngineDaemon",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout.splitlines()[-1]) == {"removed": [], "events": []}


@HOSTS
@pytest.mark.parametrize("enabled", [False, True])
def test_real_registration_owner_preserves_disabled_state_and_uses_engine_operand(tmp_path, host, enabled):
    root = tmp_path / "install"
    body = "\n".join(function(name) for name in (
        "Register-UserModeTask", "Test-TaskCurrent", "Test-AccessDenied",
    )) + """
function Get-ScheduledTask {
 param($TaskName,$TaskPath,$ErrorAction)
 [pscustomobject]@{ State='Ready'; Triggers=@() }
}
function Set-ScheduledTask {
 param($TaskName,$TaskPath,$Action,$Trigger,$Settings,$Principal,$ErrorAction)
 if ($TaskPath -ne '\\') { throw 'foreign task folder' }
 $script:events += "update:$TaskName"
}
function Register-ScheduledTask { throw 'must not recreate observed task' }
$spec=@{ Action='action';Trigger='trigger';Settings='settings';Principal='principal' }
if (-not (Register-UserModeTask -TaskName $EngineTaskName -Spec $spec)) { throw 'failed registration' }
"""
    result = run_policy(
        tmp_path, host, tasks={"agent-index-engine": task(root, "agent-index-engine", enabled=enabled)},
        policy={"windows_scheduled_task": "enabled", "start_service": False},
        body=body,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout.splitlines()[-1]) == {
        "removed": [], "events": ["update:agent-index-engine"] if enabled else [],
    }


@HOSTS
@pytest.mark.parametrize("edit", ["folder", "principal", "multi-action"])
def test_retirement_rejects_foreign_identity_before_any_effect(tmp_path, host, edit):
    root = tmp_path / "install"
    xml = task(root)
    if edit == "folder":
        xml = xml.replace("<URI>\\agent-index</URI>", "<URI>\\foreign\\agent-index</URI>")
    elif edit == "principal":
        xml = xml.replace("FIXTURE-IDENTITY", "S-1-5-18")
    else:
        xml = xml.replace("</Actions>", "<Exec><Command>foreign</Command></Exec></Actions>")
    result = run_policy(tmp_path, host, action="task-retire",
                        policy={"windows_scheduled_task": "absent"}, tasks={"agent-index": xml})
    assert result.returncode != 0
    assert json.loads(result.stdout.splitlines()[-1]) == {"removed": [], "events": []}


@HOSTS
@pytest.mark.parametrize("wrapper", ["conhost", "powershell"])
def test_foreign_absolute_system_lookalikes_are_not_supported_wrappers(tmp_path, host, wrapper):
    root = tmp_path / "install"
    drive = "Z:" if not os.environ["SystemRoot"].upper().startswith("Z:") else "Y:"
    command = drive + (r"\Windows\System32\conhost.exe" if wrapper == "conhost"
                       else r"\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")
    arguments = None if wrapper == "conhost" else f'-File "{root / "service.ps1"}"'
    result = run_policy(
        tmp_path, host, action="task-retire", policy={"windows_scheduled_task": "absent"},
        tasks={"agent-index": task(root, command=command, arguments=arguments)},
    )
    assert result.returncode != 0
    assert json.loads(result.stdout.splitlines()[-1]) == {"removed": [], "events": []}


@HOSTS
def test_caller_relative_selectors_are_frozen_before_staging_or_driver_entry(tmp_path, host):
    shell = shutil.which(host)
    if not shell:
        pytest.skip(f"{host} unavailable")
    source = SOURCE.split("# Capture caller-relative selectors", 1)[1].split("\n", 1)[1].split(
        "if ($env:OS -eq 'Windows_NT'", 1,
    )[0]
    script = tmp_path / "selectors.ps1"
    original = tmp_path / "original"
    original.mkdir()
    script.write_text(
        "$ErrorActionPreference='Stop'\n"
        f"Set-Location -LiteralPath '{original}'\n"
        f"[IO.Directory]::SetCurrentDirectory('{tmp_path}')\n"
        "$Action='task-retire';$InstallDir='runtime';$InstallationConfig='policy.json'\n"
        "$ScheduledTaskNames=$null\n"
        "$env:AGENT_INDEX_CONFIG='config.yaml';$env:AGENT_INDEX_REPO='repo'\n"
        "$env:AGENT_INDEX_CONFIG_ROOT='configs';$env:AGENT_INDEX_ENGINE_HOME='engine'\n"
        + source
        + "\n@($InstallDir,$env:AGENT_INDEX_CONFIG,$env:AGENT_INDEX_REPO,"
        "$env:AGENT_INDEX_CONFIG_ROOT,$env:AGENT_INDEX_ENGINE_HOME,"
        "$env:AGENT_INDEX_INSTALLATION_CONFIG) | ConvertTo-Json -Compress",
        encoding="utf-8-sig",
    )
    result = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-File", str(script)],
        env=environment(tmp_path), cwd=tmp_path, text=True, capture_output=True, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == [
        str(original / name) for name in ("runtime", "config.yaml", "repo", "configs", "engine", "policy.json")
    ]


@HOSTS
def test_taskless_update_recovers_independently_configured_unhealthy_machine_host(tmp_path, host):
    body = function("Invoke-WithEnvFile") + "\n" + function("Invoke-ServiceCutover") + r"""
$EnvFile=Join-Path $InstallDir 'service.env'
$LinkPython='fixture-python';$Force=$false
function Get-ActivationRole { 'unconfigured' }
function Get-MachineRole { 'host' }
function Test-IndexPublicationFresh { param($Version,$AllowDowngrade); $true }
function Test-ServiceHealthy { param($ExpectedVersion); return [bool]$ExpectedVersion }
function Write-ServiceFiles { }
function Test-Path {
 param($Path,$LiteralPath)
 if ($Path -eq 'fixture-python') { return $true }
 return (Microsoft.PowerShell.Management\Test-Path -LiteralPath $LiteralPath)
}
function fixture-python {
 if (($args -join ' ') -ne '-I -X utf8 -m agent_index deploy') { throw 'foreign runtime action' }
 $script:events += 'incoming-service'
 $global:LASTEXITCODE=0
}
Invoke-ServiceCutover
"""
    result = run_policy(tmp_path, host, body=body, policy={"windows_scheduled_task": "absent"})
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout.splitlines()[-1]) == {"removed": [], "events": ["incoming-service"]}
