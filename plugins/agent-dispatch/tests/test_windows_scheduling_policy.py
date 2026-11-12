"""Fixture-only scheduler adapters; never query or mutate host registrations."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
HELPER = PLUGIN / "src" / "agent_dispatch" / "windows-scheduling.ps1"
INSTALLER = PLUGIN / "scripts" / "install.ps1"
SHELL = shutil.which("pwsh") or shutil.which("powershell")
pytestmark = pytest.mark.skipif(SHELL is None, reason="PowerShell unavailable")


def quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def run(tmp_path, body):
    script = tmp_path / "fixture.ps1"
    script.write_text(
        "$ErrorActionPreference='Stop'\nSet-StrictMode -Version 2\n"
        + f". {quote(HELPER)}\n" + body.replace("$script:", "$global:"),
        encoding="utf-8",
    )
    return subprocess.run(
        [SHELL, "-NoProfile", "-NonInteractive", "-File", str(script)],
        capture_output=True, text=True, timeout=20,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        cwd=tmp_path,
    )


def policy(root, mode="absent", **local):
    path = root / "installation.json"
    path.write_text(json.dumps({
        "version": 1, "installation": {
            "root": str(root), "local": {"windows_scheduled_task": mode, **local},
        },
    }), encoding="utf-8")
    return path


@pytest.mark.parametrize("source_failure", [False, True])
def test_combined_source_start_preserves_classic_fallback_but_not_action_failures(tmp_path, source_failure):
    failure = """
function Invoke-DispatchSelectedSource {param($Python) $global:LASTEXITCODE=9}
""" if source_failure else ""
    result = run(tmp_path, f"""
$InstallDir={quote(tmp_path)}
$script:Started=0
function Test-DispatchStartIntent {{param($Kind,$Name) return $true}}
function Get-SupervisorMode {{param($EnvFile) return 'serve'}}
function Get-DispatchSelectedPython {{return {quote(sys.executable)}}}
function Assert-DispatchActions {{param($Actions)}}
function Start-DispatchDetachedLogonLauncher {{
 param($Launcher,$LauncherArguments,[switch]$Ensure,$Python)
 if(-not $Ensure){{throw 'classic ensure was lost'}}
 $script:Started++
}}
{failure}
$refused=$false
try {{
 Invoke-DispatchSupervisorEnsure -Name fixture -EnvFile fixture.env -Launcher fixture.ps1
}} catch {{
 if(-not $_.Exception.Message.Contains('Selected Source application readiness is unconfirmed')){{throw}}
 $refused=$true
}}
if($refused -ne ${str(source_failure).lower()}){{throw 'Source failure was misclassified'}}
if($script:Started -ne {0 if source_failure else 1}){{throw 'wrong classic fallback count'}}
exit 0
""")
    assert result.returncode == 0, result.stdout + result.stderr


def adapters(root, *, state="Ready", folder="\\", arguments=None, refusal=""):
    args = arguments or (
        '--headless powershell.exe -NoProfile -ExecutionPolicy Bypass '
        f'-WindowStyle Hidden -File "{root / "serve-service.ps1"}"'
    )
    return f"""
$Root={quote(root)}
$script:Tasks=@([pscustomobject]@{{
 TaskName='agent-dispatch'; TaskPath={quote(folder)}; State={quote(state)};
 Actions=@([pscustomobject]@{{Execute='conhost.exe'; Arguments={quote(args)}; WorkingDirectory=$Root}})
}})
$script:Removed=0
function Get-ScheduledTask {{ param($ErrorAction)
 if ({quote(refusal)} -eq 'query') {{ throw 'fixture query refused' }}
 $script:Tasks
}}
function Unregister-ScheduledTask {{ param($TaskName,$TaskPath,$Confirm,$ErrorAction)
 if ({quote(refusal)} -eq 'remove') {{ throw 'fixture removal refused' }}
 if ({quote(refusal)} -ne 'unconfirmed') {{ $script:Tasks=@($script:Tasks|Where-Object {{$_.TaskName -ne $TaskName}}) }}
 $script:Removed++
}}
function Start-ScheduledTask {{ throw 'task-only must never start' }}
function Stop-ScheduledTask {{ throw 'task-only must never stop' }}
function Invoke-DispatchCoordinatorEnsure {{ throw 'task-only must never ensure' }}
function Start-DispatchDetachedLogonLauncher {{ throw 'task-only must never directly start' }}
"""


def test_default_missing_and_existing_custom_tasks_are_preserved(tmp_path):
    result = run(tmp_path, adapters(tmp_path, arguments="custom operator action") + """
$policy=Read-DispatchInstallationPolicy $Root
if($policy.windows_scheduled_task -ne 'preserve'){throw 'wrong default'}
if($script:Removed -ne 0){throw 'custom task changed'}
$script:Tasks=@()
1..2|ForEach-Object {
 $policy=Read-DispatchInstallationPolicy $Root
 if($policy.windows_scheduled_task -ne 'preserve'){throw 'wrong repeated default'}
}
if($script:Removed -ne 0 -or $script:Tasks.Count){throw 'missing task recreated'}
""")
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(os.name != "nt", reason="native Source policy consumer")
@pytest.mark.parametrize("state", ["Ready", "Disabled"])
@pytest.mark.parametrize("include_primary", [False, True])
def test_enabled_source_reconcile_preserves_existing_custom_tasks(tmp_path, monkeypatch, state, include_primary):
    from agent_dispatch import installation_policy
    from agent_dispatch.install_paths import installation_suffix
    from agent_procutil import owned_process

    policy(tmp_path, "enabled")
    suffix = "-" + installation_suffix(tmp_path)
    tasks = [
        {"TaskName": "agent-dispatch" + suffix, "TaskPath": "\\Operator\\", "State": "Ready",
         "Actions": [{"Execute": "operator.exe", "Arguments": "custom", "WorkingDirectory": str(tmp_path)}]},
        {"TaskName": "agent-dispatch-supervisor" + suffix, "TaskPath": "\\Operator\\", "State": state,
         "Actions": [{"Execute": "operator.exe", "Arguments": "custom", "WorkingDirectory": str(tmp_path)}]},
    ]
    prefix = f"$global:Tasks=ConvertFrom-Json {quote(json.dumps(tasks))}; "
    prefix += """
function Get-ScheduledTask {param($TaskName,$ErrorAction) $global:Tasks}
function Register-ScheduledTask {throw 'existing custom task must not be rewritten'}
function Unregister-ScheduledTask {throw 'existing custom task must not be retired'}
function Enable-ScheduledTask {throw 'disabled custom task must not be enabled'}
function Start-ScheduledTask {throw 'policy reconciliation must not start a task'}
function Stop-ScheduledTask {throw 'policy reconciliation must not stop a task'}
"""
    original = owned_process.run_caller_owned

    def scheduler_boundary(command, **options):
        assert command[-2] == "-Command"
        return original([*command[:-1], prefix + command[-1]], **options)

    monkeypatch.setattr(owned_process, "run_caller_owned", scheduler_boundary)
    result = installation_policy.windows_policy(
        tmp_path, reconcile=True, observe_supervisor=not include_primary, include_primary=include_primary,
    )
    assert result["start_coordinator"] is True
    assert result["start_supervisor"] is (include_primary or state != "Disabled")
    if include_primary:
        assert result["primary_start_supervisor"] is (state != "Disabled")
    assert result["windows_scheduled_task"] == "enabled"


def test_task_only_retirement_preserves_files_and_never_starts_or_stops(tmp_path):
    config = policy(tmp_path)
    state = tmp_path / "tasks.db"
    state.write_bytes(b"fixture queue state")
    result = run(tmp_path, adapters(tmp_path) + f"""
$policy=Read-DispatchInstallationPolicy $Root -Config {quote(config)}
Remove-DispatchScheduledTaskOnly $Root 'agent-dispatch' 'agent-dispatch-supervisor' @('agent-dispatch') $policy
Remove-DispatchScheduledTaskOnly $Root 'agent-dispatch' 'agent-dispatch-supervisor' @('agent-dispatch') $policy
if($script:Removed -ne 1){{throw 'not idempotent'}}
""")
    assert result.returncode == 0, result.stderr
    assert state.read_bytes() == b"fixture queue state"
    assert config.is_file()


@pytest.mark.parametrize("kind", ["foreign-folder", "foreign-action", "ambiguous", "query", "remove", "unconfirmed", "disabled"])
def test_explicit_absence_errors_do_not_become_success(tmp_path, kind):
    config = policy(tmp_path)
    body = adapters(
        tmp_path, folder="\\foreign\\" if kind == "foreign-folder" else "\\",
        arguments="foreign command" if kind == "foreign-action" else None,
        state="Disabled" if kind == "disabled" else "Ready", refusal=kind,
    )
    if kind == "ambiguous":
        body += "\n$script:Tasks+= $script:Tasks[0]\n"
    result = run(tmp_path, body + f"""
$policy=Read-DispatchInstallationPolicy $Root -Config {quote(config)}
Remove-DispatchScheduledTaskOnly $Root 'agent-dispatch' 'agent-dispatch-supervisor' @('agent-dispatch') $policy
""")
    assert result.returncode != 0


def test_disabled_retirement_needs_durable_disabled_start_intent(tmp_path):
    policy(tmp_path, start_coordinator=False)
    result = run(tmp_path, adapters(tmp_path, state="Disabled") + """
$policy=Read-DispatchInstallationPolicy $Root
Remove-DispatchScheduledTaskOnly $Root 'agent-dispatch' 'agent-dispatch-supervisor' @('agent-dispatch') $policy
if($script:Removed -ne 1 -or $policy.start_coordinator){throw 'disabled start intent lost'}
""")
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("mode", ["preserve", "enabled", "absent"])
def test_installation_bound_native_configuration(tmp_path, mode):
    config = policy(tmp_path, mode)
    result = run(tmp_path, f"""
$policy=Read-DispatchInstallationPolicy {quote(tmp_path)} -Config {quote(config)}
if($policy.windows_scheduled_task -ne {quote(mode)}){{throw 'policy lost'}}
""")
    assert result.returncode == 0, result.stderr
    other = tmp_path / "another"
    result = run(tmp_path, f"Read-DispatchInstallationPolicy {quote(other)} -Config {quote(config)}")
    assert result.returncode != 0


def test_missing_module_default_and_explicit_absence_are_distinct(tmp_path):
    result = run(tmp_path, """
function Get-Command { param($Name,$ErrorAction) return $null }
$policy=Read-DispatchInstallationPolicy $PSScriptRoot
if($policy.windows_scheduled_task -ne 'preserve'){throw 'wrong default'}
if(Get-DispatchNativeTask fixture){throw 'unexpected task'}
""")
    assert result.returncode == 0, result.stderr
    result = run(tmp_path, """
function Get-Command { param($Name,$ErrorAction) return $null }
Get-DispatchNativeTask fixture -Required
""")
    assert result.returncode != 0
    (tmp_path / "serve-service.ps1").write_text("# prior compatibility owner\n")
    result = run(tmp_path, """
function Get-Command { param($Name,$ErrorAction) return $null }
Get-DispatchNativeTask fixture -Root $PSScriptRoot
""")
    assert result.returncode != 0
    assert "cannot be observed" in result.stderr


def test_structured_native_query_results_distinguish_missing_from_refusal(tmp_path):
    result = run(tmp_path, """
$global:QueryKind='missing'
function Get-ScheduledTask {
 [CmdletBinding()]param([string]$TaskName)
 $category=if($global:QueryKind -eq 'missing'){
  [Management.Automation.ErrorCategory]::ObjectNotFound
 }else{[Management.Automation.ErrorCategory]::PermissionDenied}
 $record=[Management.Automation.ErrorRecord]::new(
  [Exception]::new('fixture native query result'),
  'CmdletizationQuery_NotFound_TaskName',$category,$TaskName)
 $PSCmdlet.ThrowTerminatingError($record)
}
if(Get-DispatchNativeTask fixture){throw 'structured absence failed'}
$global:QueryKind='refused'; $refused=$false
try{Get-DispatchNativeTask fixture}catch{$refused=$true}
if(-not $refused){throw 'query refusal became absence'}
""")
    assert result.returncode == 0, result.stderr


def test_actual_task_only_entry_exits_before_runtime_and_daemon_lifecycle(tmp_path):
    policy(tmp_path)
    suffix = "-" + hashlib.sha256(str(tmp_path).lower().encode()).hexdigest()[:12]
    body = adapters(tmp_path)
    body += f"""
$script:Tasks[0].TaskName='agent-dispatch{suffix}'
function New-Item {{ throw 'task-only must not construct runtime' }}
function Start-Process {{ throw 'task-only must not spawn' }}
function Stop-Process {{ throw 'task-only must not terminate' }}
& {quote(INSTALLER)} -Action task-retire -InstallDir {quote(tmp_path)} `
 -InstallationConfig '.\\installation.json' -ScheduledTaskNames 'agent-dispatch{suffix}'
if($script:Removed -ne 1){{throw 'task-only entry did not retire the fixture'}}
exit $LASTEXITCODE
"""
    result = run(tmp_path, body)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("kind", ["fresh-repeat", "custom", "absent", "disabled", "explicit-existing"])
def test_real_install_callbacks_keep_scheduling_separate_from_readiness(tmp_path, kind):
    (tmp_path / "supervisor.env").write_text("AGENT_DISPATCH_SUPERVISE_LABELS=fixture\n")
    (tmp_path / "supervisors").mkdir()
    if kind == "absent":
        policy(tmp_path, "absent")
    if kind == "explicit-existing":
        policy(tmp_path, "enabled")
    body = adapters(tmp_path, state="Disabled" if kind == "disabled" else "Ready")
    body += f"\n. {quote(HELPER)}\n"
    if kind == "fresh-repeat":
        body += "\n$script:Tasks=@()\n"
    if kind == "custom":
        body += "\n$script:Tasks[0].Actions[0].Arguments='operator custom action'\n"
    if kind == "explicit-existing":
        body += """
$script:Tasks[0].Actions[0].Arguments='operator custom coordinator'
$script:Tasks += [pscustomobject]@{
 TaskName='agent-dispatch-supervisor'; TaskPath='\\'; State='Ready'
 Actions=@([pscustomobject]@{Execute='operator';Arguments='custom supervisor';WorkingDirectory=$Root})
}
"""
    body += """
$InstallDir=$Root; $TaskName='agent-dispatch'; $SupervisorTaskName='agent-dispatch-supervisor'
$SupervisorProfileDir=Join-Path $Root 'supervisors'
$LinkDir=Join-Path $Root 'versions\\fixture'; $LinkPython='Invoke-FixturePython'
$utf8NoBom=[Text.UTF8Encoding]::new($false)
$DefaultPort=9847
$script:GlobalActivationLockTimeoutSeconds=5
$NoService=$false; $NoSupervisor=$false; $Interactive=$false
$script:CoordinatorEnsures=0; $script:SupervisorEnsures=0; $script:TaskStarts=0
function Assert-DispatchActions { param($Actions) }
function Invoke-FixturePython {
 if($args -contains 'deploy'){$global:CoordinatorEnsures++}
 $global:LASTEXITCODE=0
}
function Get-DispatchSelectedPython { 'Invoke-FixturePython' }
function Invoke-DispatchSelectedSource { param($Python) $global:LASTEXITCODE=78 }
function Start-DispatchDetachedLogonLauncher {
 param($Launcher,$LauncherArguments,[switch]$Ensure,$Python)
 if(-not $Ensure){throw 'readiness must use the existing ensure adapter'}
 $global:SupervisorEnsures++
}
function Start-ScheduledTask {$global:TaskStarts++; throw 'no scheduled startup is allowed'}
function Register-ScheduledTask {throw 'existing custom registration must not be overwritten'}
function Stop-ScheduledTask {throw 'no scheduled stop is allowed'}
function Invoke-SupervisorsStop {throw 'no global supervisor stop is allowed'}
function Retire-SupervisorProcesses {throw 'no process retirement is allowed'}
function New-ItemProperty {throw 'no fallback registration is allowed'}
function Write-Ok {param($Message)}
function Write-Skip {param($Message)}
function Write-Step {param($Message)}
function Write-Warn {param($Message)}
function Invoke-SupervisorAudit {param($Event,$Reason,$Name,$EnvFile,$Outcome)}
"""
    functions = (
        "Enter-PluginSnapshotLock", "Publish-FileAtomically", "Publish-ServiceLauncher",
        "Install-CoordinatorTask", "Install-SupervisorTask", "Write-SupervisorDefaultEnv",
        "Write-SupervisorLauncher", "Get-SupervisorMode", "Test-SupervisorLabelsConfigured",
        "Get-SupervisorProfileFiles", "Test-SupervisorProfileName", "Get-SupervisorTaskName",
        "Install-SupervisorTaskInstance",
    )
    body += f"""
$tokens=$null; $errors=$null
$tree=[Management.Automation.Language.Parser]::ParseFile({quote(INSTALLER)},[ref]$tokens,[ref]$errors)
if($errors.Count){{throw 'source parse failed'}}
$requested=@({','.join(quote(name) for name in functions)})
foreach($requestedName in $requested){{
 $definitions=@($tree.FindAll({{
  param($node)
  $node -is [Management.Automation.Language.FunctionDefinitionAst]
 }},$true)|Where-Object {{$_.Name -eq $requestedName}})
 if($definitions.Count -ne 1){{throw 'function boundary is ambiguous'}}
 . ([scriptblock]::Create($definitions[0].Extent.Text))
}}
"""
    body += """
Install-CoordinatorTask
Install-SupervisorTask
Install-CoordinatorTask
@{coordinator=$global:CoordinatorEnsures;supervisor=$global:SupervisorEnsures;
 removed=$global:Removed;task_starts=$global:TaskStarts;remaining=$global:Tasks.Count}|ConvertTo-Json -Compress
"""
    result = run(tmp_path, body)
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert observed == {
        "coordinator": 0 if kind == "disabled" else 2,
        "supervisor": 1,
        "removed": 1 if kind == "absent" else 0,
        "task_starts": 0,
        "remaining": 2 if kind == "explicit-existing" else (1 if kind in {"custom", "disabled"} else 0),
    }
