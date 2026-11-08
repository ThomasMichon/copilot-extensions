"""Contained native policy consumers: no real scheduler, daemon or install."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
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


def run_policy(tmp_path, host, *, tasks=None, policy=None, body="", action=None, failure="", root_name="install"):
    shell = shutil.which(host)
    if not shell:
        pytest.skip(f"{host} unavailable")
    root = tmp_path / root_name
    root_literal = str(root).replace("'", "''")
    root.mkdir(exist_ok=True)
    (root / "service.ps1").write_text("throw 'must not execute launcher'")
    (root / "engine").mkdir(exist_ok=True)
    (root / "engine" / "engine.ps1").write_text("throw 'must not execute engine launcher'")
    if policy is not None:
        (root / "installation.json").write_text(json.dumps({
            "version": 1, "installation": {"root": str(root), "local": policy},
        }, ensure_ascii=False), encoding="utf-8")
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
$InstallDir='{root_literal}'; $EngineHome=Join-Path $InstallDir 'engine'
$TaskName='agent-index'; $EngineTaskName='agent-index-engine'
$Launcher=Join-Path $InstallDir 'service.ps1'; $EngineLauncher=Join-Path $EngineHome 'engine.ps1'
$NoService=$false; $DryRun=$false; $AllowTaskElevation=$false; $SrcVersion='fixture'
$ScheduledTaskNames=$null; $script:removed=@(); $script:events=@()
$script:queryFailure=$false
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
 if ('{failure}' -eq 'query' -or $script:queryFailure) {{ $global:LASTEXITCODE=5; return '' }}
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
        script += body + "\n"
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


@HOSTS
@pytest.mark.parametrize("name,hold", [
    ("agent-index", "start_service"), ("agent-index-engine", "start_engine"),
])
def test_missing_task_refresh_honors_hold_changed_after_caller_admission(tmp_path, host, name, hold):
    body = "\n".join(function(value) for value in (
        "Register-UserModeTask", "Test-TaskCurrent", "Test-AccessDenied",
    )) + f"""
if (-not (Test-IndexStartAllowed -Name '{name}')) {{ throw 'caller was not admitted' }}
$configPath=Join-Path $InstallDir 'installation.json'
$document=Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
$document.installation.local.{hold}=$false
$document | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $configPath
function Register-ScheduledTask {{ throw 'forbidden creation after refreshed hold' }}
function Set-ScheduledTask {{ throw 'forbidden task update' }}
$spec=@{{ Action='action';Trigger='trigger';Settings='settings';Principal='principal' }}
if (-not (Register-UserModeTask -Name '{name}' -Spec $spec)) {{ throw 'failed registration' }}
if (Test-IndexStartAllowed -Name '{name}') {{ throw 'hold was not refreshed' }}
"""
    result = run_policy(tmp_path, host, body=body, policy={
        "windows_scheduled_task": "enabled", "start_service": True, "start_engine": True,
    })
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout.splitlines()[-1]) == {"removed": [], "events": []}


@HOSTS
@pytest.mark.parametrize("root_selector", ["C:\\Index\\", "C:\\Index & Search\\\\"])
def test_elevated_handoff_captures_exact_native_argv_without_elevation(tmp_path, host, root_selector):
    capture = tmp_path / "capture.py"
    output = tmp_path / "argv.json"
    capture.write_text(
        "import json, pathlib, sys\n"
        f"pathlib.Path({str(output)!r}).write_text(json.dumps(sys.argv[1:]))\n",
        encoding="utf-8",
    )
    python = os.fsdecode(sys._base_executable).replace("'", "''")
    config_selector = "C:\\Policy O'Brien\\installation.json"
    body = function("Invoke-ScopedElevation") + f"""
$InstallDir='{root_selector}'
$env:AGENT_INDEX_INSTALLATION_CONFIG='{config_selector.replace("'", "''")}'
function Start-Process {{
 param($FilePath,$Verb,$ArgumentList,[switch]$PassThru,[switch]$Wait,$ErrorAction)
 if ($FilePath -ne 'pwsh' -or $Verb -ne 'RunAs') {{ throw 'foreign handoff' }}
 $start=New-Object Diagnostics.ProcessStartInfo
 $start.FileName='{python}'
 $start.Arguments='-I -B "{capture}" '+$ArgumentList
 $start.UseShellExecute=$false;$start.CreateNoWindow=$true
 $start.RedirectStandardOutput=$true;$start.RedirectStandardError=$true
 $process=[Diagnostics.Process]::Start($start)
 $stdout=$process.StandardOutput.ReadToEndAsync()
 $stderr=$process.StandardError.ReadToEndAsync()
 if (-not $process.WaitForExit(10000)) {{ $process.Kill();throw 'capture timed out' }}
 if ($process.ExitCode -ne 0) {{ throw ($stdout.Result+$stderr.Result) }}
 [pscustomobject]@{{ExitCode=$process.ExitCode}}
 $process.Dispose()
}}
if (-not (Invoke-ScopedElevation -ElevAction 'register-tasks')) {{ throw 'handoff failed' }}
"""
    result = run_policy(tmp_path, host, body=body)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(output.read_text()) == [
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(tmp_path / "harness.ps1"),
        "register-tasks", "-AllowTaskElevation", "-InstallDir", root_selector,
        "-InstallationConfig", config_selector,
    ]
    assert json.loads(result.stdout.splitlines()[-1]) == {"removed": [], "events": []}


@HOSTS
@pytest.mark.parametrize("root_name", ["Index & Search", "Index's Search"])
@pytest.mark.parametrize("phase", ["create", "reuse", "retire"])
def test_quoted_literal_paths_are_owned_for_creation_reuse_and_retirement(tmp_path, host, root_name, phase):
    root = tmp_path / root_name
    if phase == "retire":
        result = run_policy(
            tmp_path, host, root_name=root_name, action="task-retire",
            policy={"windows_scheduled_task": "absent"}, tasks={"agent-index": task(root)},
        )
    else:
        template = task(root).replace("'", "''")
        body = "\n".join(function(value) for value in (
            "New-TaskSpec", "Register-UserModeTask", "Test-TaskCurrent", "Test-AccessDenied",
        )) + f"""
function New-ScheduledTaskAction {{
 param($Execute,$Argument)
 [pscustomobject]@{{Execute=$Execute;Arguments=$Argument}}
}}
function New-ScheduledTaskTrigger {{
 param([switch]$AtLogOn)
 [pscustomobject]@{{CimClass=[pscustomobject]@{{CimClassName='MSFT_TaskLogonTrigger'}}}}
}}
function New-ScheduledTaskSettingsSet {{
 param([switch]$AllowStartIfOnBatteries,[switch]$DontStopIfGoingOnBatteries,
       [switch]$StartWhenAvailable,$ExecutionTimeLimit,$RestartCount,$RestartInterval)
 [pscustomobject]@{{ExecutionTimeLimit=$ExecutionTimeLimit;StartWhenAvailable=$true}}
}}
function New-ScheduledTaskPrincipal {{
 param($UserId,$LogonType,$RunLevel)
 [pscustomobject]@{{LogonType=$LogonType;RunLevel=$RunLevel}}
}}
function Register-ScheduledTask {{
 param($TaskPath,$TaskName,$Action,$Trigger,$Settings,$Principal,$ErrorAction)
 if ($TaskPath -ne '\\') {{ throw 'foreign task folder' }}
 $xml=[xml]'{template}'
 $xml.Task.Principals.Principal.UserId=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
 $xml.Task.Actions.Exec.Command=$Action.Execute
 $xml.Task.Actions.Exec.Arguments=$Action.Arguments
 $manager=New-Object Xml.XmlNamespaceManager($xml.NameTable)
 $manager.AddNamespace('t','http://schemas.microsoft.com/windows/2004/02/mit/task')
 Assert-IndexTaskOwned -Name $TaskName -Observed @{{Xml=$xml;Namespace=$manager}}
 $script:events += "create:$TaskName"
}}
function Get-ScheduledTask {{
 param($TaskName,$TaskPath,$ErrorAction)
 $spec=New-TaskSpec -LauncherPath $Launcher
 $xml=[xml]$script:tasks[$TaskName]
 [pscustomobject]@{{
  State='Ready';Triggers=@($spec.Trigger);Settings=$spec.Settings;Principal=$spec.Principal
  Actions=@([pscustomobject]@{{Execute=$xml.Task.Actions.Exec.Command;Arguments=$xml.Task.Actions.Exec.Arguments}})
 }}
}}
function Set-ScheduledTask {{ throw 'current task must not be rewritten' }}
if (-not (Register-UserModeTask -Name $TaskName -Spec (New-TaskSpec -LauncherPath $Launcher))) {{
 throw 'failed registration'
}}
"""
        result = run_policy(
            tmp_path, host, root_name=root_name, body=body,
            tasks={"agent-index": task(root)} if phase == "reuse" else {},
        )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout.splitlines()[-1]) == {
        "removed": ["agent-index"] if phase == "retire" else [],
        "events": ["create:agent-index"] if phase == "create" else [],
    }


@HOSTS
@pytest.mark.parametrize("root_name", ["Index&Literal", "Index'Literal"])
def test_shell_special_characters_require_a_quoted_file_operand(tmp_path, host, root_name):
    root = tmp_path / root_name
    result = run_policy(
        tmp_path, host, root_name=root_name, action="task-retire",
        policy={"windows_scheduled_task": "absent"},
        tasks={"agent-index": task(root, arguments=f'--headless powershell.exe -File {root / "service.ps1"}')},
    )
    assert result.returncode != 0
    assert json.loads(result.stdout.splitlines()[-1]) == {"removed": [], "events": []}


def activation_owners() -> str:
    return "\n".join(function(name) for name in (
        "Invoke-WithEnvFile", "Ensure-ServiceRunning", "Ensure-EngineRunning",
        "Invoke-ServiceCutover", "Restart-EngineDaemon", "Get-IndexPythonCacheArgs",
    )) + r"""
$LinkPython='fixture-manager';$EngineVenvPython=$EngineLauncher
$EnvFile=Join-Path $InstallDir 'service.env';$EngineEnvFile=Join-Path $EngineHome 'engine.env'
$Force=$false
function Get-MachineRole { 'host' }
function Get-ActiveSlotPython { 'fixture-manager' }
function Test-IndexPublicationFresh { param($Version,$AllowDowngrade); $true }
function Test-ServiceHealthy { param($ExpectedVersion); return [bool]$ExpectedVersion }
function Test-EnginePort { $false }
function Write-ServiceFiles { }
function Write-EngineFiles { }
function Test-Path {
 param($Path,$LiteralPath,$PathType)
 $value=if($LiteralPath){$LiteralPath}else{$Path}
 if ($value -eq 'fixture-manager') { return $true }
 if ($PathType) { return (Microsoft.PowerShell.Management\Test-Path -LiteralPath $value -PathType $PathType) }
 return (Microsoft.PowerShell.Management\Test-Path -LiteralPath $value)
}
function fixture-manager {
 if (($args -join ' ') -eq '-I -X utf8 -m agent_index deploy') { $script:events += 'service' }
 elseif (($args -join ' ') -eq '-I -X utf8 -m agent_index engine start --strict') { $script:events += 'engine' }
 else { throw 'unexpected lifecycle mutation' }
 $global:LASTEXITCODE=0
}
"""


@HOSTS
@pytest.mark.parametrize("action,hold", [
    ("update", "service-durable"), ("update", "engine-durable"),
    ("update", "service-disabled"), ("update", "engine-disabled"), ("update", "query"),
    ("engine", "engine-durable"), ("engine", "engine-disabled"), ("engine", "query"),
    ("engine-update", "engine-durable"), ("engine-update", "engine-disabled"), ("engine-update", "query"),
])
def test_activation_refreshes_holds_changed_during_construction(tmp_path, host, action, hold):
    root = tmp_path / "install"
    name = "agent-index" if hold.startswith("service") else "agent-index-engine"
    flag = "start_service" if name == "agent-index" else "start_engine"
    mutation = (
        "$script:queryFailure=$true" if hold == "query" else
        f"$script:tasks['{name}']=$script:tasks['{name}'].Replace('<Enabled>true</Enabled>','<Enabled>false</Enabled>')"
        if hold.endswith("disabled") else
        f"""
$path=Join-Path $InstallDir 'installation.json'
$document=Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json
$document.installation.local.{flag}=$false
$document | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $path -Encoding UTF8
"""
    )
    body = activation_owners() + f"""
function Construct {{
 if (-not (Test-IndexStartAllowed -Name '{name}')) {{ throw 'construction was not admitted' }}
 $script:events += 'build'
 {mutation}
}}
function Install-Runtime {{ Construct }}
function Install-Engine {{ param([switch]$Upgrade); Construct; return $true }}
"""
    result = run_policy(
        tmp_path, host, action=action, body=body,
        policy={"windows_scheduled_task": "preserve", "start_service": True, "start_engine": True},
        tasks={task_name: task(root, task_name) for task_name in ("agent-index", "agent-index-engine")},
    )
    assert (result.returncode == 0) is (hold != "query"), result.stdout + result.stderr
    expected = ["build"]
    if action == "update" and hold != "query":
        expected += ["engine" if hold.startswith("service") else "service"]
    assert json.loads(result.stdout.splitlines()[-1]) == {"removed": [], "events": expected}


@HOSTS
@pytest.mark.parametrize("owner", ["service", "engine"])
@pytest.mark.parametrize("hold", ["durable", "query"])
def test_activation_rechecks_hold_after_launcher_preparation(tmp_path, host, owner, hold):
    flag = "start_service" if owner == "service" else "start_engine"
    mutation = "$script:queryFailure=$true" if hold == "query" else f"""
$path=Join-Path $InstallDir 'installation.json'
$document=Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json
$document.installation.local.{flag}=$false
$document | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $path -Encoding UTF8
"""
    writer = "Write-ServiceFiles" if owner == "service" else "Write-EngineFiles"
    call = "Ensure-ServiceRunning" if owner == "service" else "Ensure-EngineRunning -RequireReady"
    result = run_policy(
        tmp_path, host, policy={"start_service": True, "start_engine": True},
        body=activation_owners() + f"\nfunction {writer} {{ {mutation} }}\n{call}\n",
    )
    assert (result.returncode == 0) is (hold != "query"), result.stdout + result.stderr
    assert json.loads(result.stdout.splitlines()[-1]) == {"removed": [], "events": []}


@HOSTS
@pytest.mark.parametrize("healthy", [False, True])
def test_required_engine_readiness_uses_primary_http_health_not_tcp_shortcut(tmp_path, host, healthy):
    requests = []

    class Health(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            self.send_response(200 if healthy else 503)
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Health)
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    try:
        with socket.create_connection(server.server_address, timeout=3):
            pass
        root = tmp_path / "install"
        engine = root / "engine"
        engine.mkdir(parents=True)
        (engine / "engine.env").write_text(
            f"AGENT_INDEX_ENGINE_HOST=127.0.0.1\nAGENT_INDEX_ENGINE_PORT={server.server_port}\n", encoding="utf-8",
        )
        before = {path: path.read_bytes() for path in engine.rglob("*") if path.is_file()}
        primary = tmp_path / "primary.py"
        primary.write_text(
            "import os, pathlib, sys\n"
            "assert sys.argv[1:] == ['-I','-X','utf8','-m','agent_index','engine','start','--strict']\n"
            f"sys.path.insert(0,{str(SCRIPTS.parent / 'src')!r})\n"
            "from agent_index.engine import daemon\n"
            "try:\n"
            " print(daemon.start(pathlib.Path(os.environ['AGENT_INDEX_ENGINE_HOME']),strict=True))\n"
            "except FileNotFoundError as error:\n"
            " print(error)\n"
            " raise SystemExit(1)\n",
            encoding="utf-8",
        )
        body = activation_owners() + function("Test-EnginePort") + f"""
function fixture-manager {{ & '{sys.executable}' -I -B '{primary}' @args }}
if (-not (Test-EnginePort)) {{ throw 'fixture TCP port is not open' }}
Ensure-EngineRunning -RequireReady
"""
        # Establish the real endpoint before the production env-file overlay.
        body = f"$env:AGENT_INDEX_ENGINE_HOST='127.0.0.1';$env:AGENT_INDEX_ENGINE_PORT='{server.server_port}'\n" + body
        result = run_policy(tmp_path, host, body=body)
        assert (result.returncode == 0) is healthy, result.stdout + result.stderr
        assert requests == ["/health"]
        assert "already running" in result.stdout if healthy else "required durable engine is not serving" in result.stderr
        assert all(path.read_bytes() == contents for path, contents in before.items())
        assert not (engine / ".venv").exists() and not (engine / "engine.pid").exists()
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
        assert not worker.is_alive()


@HOSTS
@pytest.mark.parametrize("policy", [{"start_service": False}, {"unrecognized": False}])
def test_bomless_utf8_non_ascii_policy_preserves_root_and_refusal(tmp_path, host, policy):
    result = run_policy(tmp_path, host, root_name="José", policy=policy)
    raw = (tmp_path / "José" / "installation.json").read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf") and b"Jos\xc3\xa9" in raw
    valid = "unrecognized" not in policy
    assert (result.returncode == 0) is valid, result.stdout + result.stderr
    if valid:
        assert json.loads(result.stdout.splitlines()[-1]) == {"removed": [], "events": []}
    else:
        assert "Unknown Index installation-local policy" in result.stderr


@HOSTS
def test_service_held_callback_skips_deploy_and_post_attempt_health_reporting(tmp_path, host):
    body = activation_owners() + r"""
$script:healthChecks=0
function Write-ServiceFiles {
 $path=Join-Path $InstallDir 'installation.json'
 $document=Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json
 $document.installation.local.start_service=$false
 $document | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $path -Encoding UTF8
}
function Test-ServiceHealthy {
 param($ExpectedVersion)
 $script:healthChecks++
 if ($script:healthChecks -gt 1) { throw 'spurious post-attempt health check' }
 return $false
}
function Write-Skip {
 param($Msg)
 if ($Msg -ne 'Service start is held') { throw "unexpected skip: $Msg" }
 $script:events += 'held'
}
function Write-Warn { throw 'spurious readiness warning' }
function Write-Ok { throw 'spurious readiness success' }
Ensure-ServiceRunning
if ($script:healthChecks -ne 1) { throw 'missing initial health check' }
"""
    result = run_policy(tmp_path, host, policy={"start_service": True}, body=body)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout.splitlines()[-1]) == {"removed": [], "events": ["held"]}


@HOSTS
def test_engine_restart_preserves_exact_ownership_check_after_refresh(tmp_path, host):
    root = tmp_path / "install"
    body = activation_owners() + r"""
$script:originalPolicy=(Get-Item Function:\Initialize-IndexSchedulingPolicy).ScriptBlock
$script:refreshCount=0
function Initialize-IndexSchedulingPolicy {
 $script:refreshCount++
 if ($script:refreshCount -eq 2) {
  $script:tasks[$EngineTaskName]=$script:tasks[$EngineTaskName].Replace('engine.ps1','foreign.ps1')
 }
 & $script:originalPolicy
}
Restart-EngineDaemon
"""
    result = run_policy(
        tmp_path, host, body=body, tasks={"agent-index-engine": task(root, "agent-index-engine")},
    )
    assert result.returncode != 0
    assert "Foreign Index task installation" in result.stderr
    assert json.loads(result.stdout.splitlines()[-1]) == {"removed": [], "events": []}
