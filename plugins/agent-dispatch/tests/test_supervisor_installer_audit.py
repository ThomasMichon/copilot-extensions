from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]


def _shell_function(name: str) -> str:
    text = (PLUGIN / "scripts/install.sh").read_text()
    start = text.index(f"{name}() {{")
    return text[start:text.index("\n}\n", start) + 2]


@pytest.mark.skipif(os.name != "posix", reason="POSIX installer contract")
def test_shell_actions_audit_exact_results_preserve_disable_and_profile_scope(tmp_path):
    runtime = tmp_path / "runtime"
    units = tmp_path / "units"
    state = tmp_path / "state"
    bindir = tmp_path / "bin"
    for directory in (runtime / "supervisors", units, state, bindir):
        directory.mkdir(parents=True)
    unit = "agent-dispatch-supervisor.service"
    profile = "agent-dispatch-supervisor-example.service"
    env_file = runtime / "supervisor.env"
    env_file.write_text("AGENT_DISPATCH_SUPERVISE_MODE=serve\n")
    profile_env = runtime / "supervisors/example.env"
    profile_env.write_text("AGENT_DISPATCH_SUPERVISE_LABELS=example\n")
    (units / profile).write_text("fixture profile unit\n")
    fake_systemctl = bindir / "systemctl"
    fake_systemctl.write_text(
        '#!/bin/bash\nset -eu\nshift\nop="$1"; shift\nunit="${1:-}"\n'
        'echo "$op $unit" >> "$STATE/calls"\n'
        'case "$op" in\n'
        'is-enabled) if [[ -f "$STATE/runtime-mask" ]]; then echo masked-runtime; exit 1; elif [[ -f "$STATE/$unit.enabled" ]]; then echo enabled; else echo disabled; exit 1; fi;;\n'
        'is-active) if [[ -f "$STATE/$unit.active" ]]; then echo active; else echo inactive; exit 3; fi;;\n'
        'enable) touch "$STATE/$unit.enabled";;\n'
        'start|restart) [[ ! -f "$STATE/fail" ]] || exit 7; touch "$STATE/$unit.active";;\n'
        'disable) rm -f "$STATE/$unit.enabled";;\n'
        'stop) rm -f "$STATE/$unit.active";;\n'
        'daemon-reload) :;;\n*) exit 99;;\nesac\n'
    )
    fake_systemctl.chmod(0o700)
    functions = "\n".join(_shell_function(name) for name in (
        "_supervisor_audit", "_supervisor_action", "_supervisor_mode",
        "_supervisor_labels_configured", "_supervisor_env_for_unit",
        "_install_supervisor_unit", "_stop_supervisor_unit_if_active",
    ))
    values = {
        "INSTALL_DIR": runtime, "UNIT_DIR": units, "SUPERVISOR_PROFILE_DIR": runtime / "supervisors",
        "SUPERVISOR_ENV_FILE": env_file, "SUPERVISOR_UNIT": unit,
        "SUPERVISOR_UNIT_BASE": "agent-dispatch-supervisor", "SYSTEMD_UNIT": "agent-dispatch.service",
        "SUPERVISOR_LAUNCHER": runtime / "launcher.sh", "SUPERVISOR_PATH": "/fixture/bin",
        "VENV_PYTHON": sys.executable, "PKG_SRC_DIR": PLUGIN / "src/agent_dispatch",
        "ACTION": "update", "SRC_VERSION": "1.2.3", "SUPERVISOR_AUDIT_CORRELATION": "123-456",
    }
    script = "\n".join([
        "set -euo pipefail", '_warn() { echo "$*" >&2; }; _ok() { :; }; _skip() { :; }; _step() { :; }',
        functions, *(f"{key}={shlex.quote(str(value))}" for key, value in values.items()),
        f'_install_supervisor_unit "{unit}" "{env_file}" primary',
        f'touch "{state}/runtime-mask"; echo stale > "{units}/{unit}"',
        f'_install_supervisor_unit "{unit}" "{env_file}" primary',
        f'grep -q -- "--systemd-unit" "{units}/{unit}"; rm "{state}/runtime-mask"',
        # An enabled refresh retains opt-in and records the actual command result.
        f'touch "{state}/fail"',
        f'_supervisor_action restart "{unit}" "{env_file}" runtime-refresh',
        f'rm "{state}/fail"; rm "{state}/{unit}.enabled"',
        f'_install_supervisor_unit "{unit}" "{env_file}" primary',
        f'touch "{state}/{profile}.active"',
        f'_stop_supervisor_unit_if_active "{profile}" profile',
        f'printf "AGENT_DISPATCH_SUPERVISE_MODE=\\n" > "{env_file}"',
        f'_install_supervisor_unit "{unit}" "{env_file}" primary',
        f'mv "{runtime}/lifecycle.log" "{runtime}/evidence.jsonl"',
        f'mkdir "{runtime}/lifecycle.log"; touch "{state}/{profile}.active"',
        f'_stop_supervisor_unit_if_active "{profile}" profile',
    ])
    result = subprocess.run(
        ["/bin/bash"], input=script, text=True, capture_output=True, timeout=20,
        env=dict(os.environ, STATE=str(state), PATH=f"{bindir}:/usr/bin:/bin"),
    )
    assert result.returncode == 0, result.stderr
    assert "restart failed" in result.stderr
    assert "audit unavailable" in result.stderr
    events = [json.loads(line) for line in (runtime / "evidence.jsonl").read_text().splitlines()]
    assert [(e["action"], e["outcome"]) for e in events] == [
        ("supervisor-enable", "begin"), ("supervisor-enable", "ok"),
        ("supervisor-restart", "begin"), ("supervisor-restart", "ok"),
        ("supervisor-selection", "skipped"),
        ("supervisor-restart", "begin"), ("supervisor-restart", "fail"),
        ("supervisor-selection", "skipped"),
        ("supervisor-stop", "begin"), ("supervisor-stop", "ok"),
        ("supervisor-stop", "begin"), ("supervisor-stop", "ok"),
        ("supervisor-disable", "begin"), ("supervisor-disable", "ok"),
    ]
    assert events[4]["detail"]["native_enabled"] == "masked-runtime"
    assert events[6]["detail"]["exit_code"] == 7
    assert events[7]["detail"]["reason"] == "prior-native-disabled"
    assert events[7]["detail"]["configured_enabled"] is True
    assert events[7]["detail"]["native_enablement_provenance"] == "unknown"
    assert events[8]["detail"]["profile"] == "example"
    assert events[10]["detail"]["reason"] == "installer-inert"
    assert events[10]["detail"]["configured_enabled"] is False
    assert not (state / f"{unit}.enabled").exists()
    assert not (state / f"{profile}.active").exists()


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell is unavailable")
def test_powershell_adapter_records_failures_and_preserves_native_disable(tmp_path):
    script_text = (PLUGIN / "scripts/install.ps1").read_text()
    start = script_text.index("function Install-SupervisorTaskInstance")
    function = script_text[start:script_text.index("\nfunction ", start + 1)]
    env_file = tmp_path / "supervisor.env"
    env_file.write_text("AGENT_DISPATCH_SUPERVISE_MODE=serve\n")
    helper = PLUGIN / "scripts/supervisor-lifecycle.ps1"
    script = "\n".join([
        "$ErrorActionPreference = 'Stop'",
        f"$PluginDir='{PLUGIN}'; $InstallDir='{tmp_path}'; $VenvPython='{sys.executable}'; $LinkPython=$VenvPython",
        "$SrcVersion='1.2.3'; $Action='update'; $SupervisorTaskName='agent-dispatch-supervisor'",
        f"$SupervisorProfileDir='{tmp_path}'",
        'function Write-Warn { param($msg) [Console]::Error.WriteLine($msg) }',
        'function Write-Skip { param($msg) }',
        'function Get-CimInstance { param($ClassName,$Filter) [pscustomobject]@{ParentProcessId=100} }',
        "function Get-ScheduledTask { param($TaskName) [pscustomobject]@{State='Disabled'} }",
        "function Get-SupervisorMode { param($EnvFile) 'serve' }",
        "function Stop-ScheduledTask { param($TaskName) throw 'fixture stop refused' }",
        f". '{helper}'", function,
        "$script:SupervisorAuditCorrelation='123-456'",
        "function Invoke-ShadowedAudit { $action=[pscustomobject]@{Execute='fixture'}; Invoke-SupervisorAudit -Event selection -Reason shadow-probe -Name $SupervisorTaskName -Outcome skipped }",
        "Invoke-ShadowedAudit",
        f"Install-SupervisorTaskInstance -Name $SupervisorTaskName -EnvFile '{env_file}' -Launcher fixture -DisplayName fixture",
        "$result=Invoke-SupervisorTaskAction -Operation stop -Name $SupervisorTaskName -Reason lifecycle-stop",
    ])
    script_path = tmp_path / "fixture.ps1"
    script_path.write_text(script)
    result = subprocess.run(["pwsh", "-NoProfile", "-File", str(script_path)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert "fixture stop refused" in result.stderr
    events = [json.loads(line) for line in (tmp_path / "lifecycle.log").read_text().splitlines()]
    assert [(e["action"], e["outcome"]) for e in events] == [
        ("supervisor-selection", "skipped"),
        ("supervisor-selection", "skipped"), ("supervisor-stop", "begin"), ("supervisor-stop", "fail"),
    ]
    assert events[0]["detail"]["origin"] == "installer.ps1"
    assert events[0]["detail"]["parent_pid"] == 100
    assert events[0]["detail"]["configured_enabled"] is True
    assert all(e["detail"]["verb"] == "update" for e in events)


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell is unavailable")
@pytest.mark.parametrize("master_state", ["Disabled", "Absent", "Ready", "OptOut"])
def test_disabled_windows_master_restores_only_enabled_profiles(tmp_path, master_state):
    text = (PLUGIN / "scripts/install.ps1").read_text()

    def function(name):
        start = text.index(f"function {name} {{")
        return text[start:text.index("\nfunction ", start + 1)]

    script = "\n".join([
        "$ErrorActionPreference='Stop'; $NoSupervisor=$false; $NoService=$false; $env:OS='Windows_NT'",
        "$SupervisorTaskName='agent-dispatch-supervisor'; $InstallDir='fixture'; $SupervisorProfileDir='fixture'",
        "$script:Started=@(); $script:Stopped=@(); $script:Retired=0",
        f"$script:MasterState='{master_state}'",
        "if($script:MasterState -eq 'OptOut'){$NoSupervisor=$true}",
        "$script:AuditReasons=@(); function Invoke-SupervisorAudit { param($Event,$Reason,$Name,$EnvFile,$Outcome) $script:AuditReasons+=$Reason }",
        "function Remove-AllSupervisorTasks { }",
        "function Write-Skip { param($message) }",
        "function Write-Warn { param($message) }; function Write-Step { param($message) }; function Write-Ok { param($message) }",
        "function Retire-SupervisorProcesses { $script:Retired++; return 0 }",
        "function Register-ScheduledTask { throw 'fixture must not register' }",
        "function Test-Path { param($Path) return $true }",
        "function Write-SupervisorDefaultEnv { return 'supervisor.env' }",
        "function Write-SupervisorLauncher { return 'launcher.ps1' }",
        "function Install-SupervisorTaskInstance { param($Name,$EnvFile,$Launcher,$DisplayName) }",
        "function Get-SupervisorMode { param($EnvFile) return 'serve' }",
        "function Get-ScheduledTaskDisabled { return [pscustomobject]@{TaskName='agent-dispatch-supervisor-disabled';State='Disabled'} }",
        "function Get-SupervisorProfileFiles { return @([pscustomobject]@{BaseName='enabled';FullName='enabled.env'}, [pscustomobject]@{BaseName='disabled';FullName='disabled.env'}) }",
        'function Get-SupervisorTaskName { param($ProfileName) return "$SupervisorTaskName-$ProfileName" }',
        "function Get-ItemProperty { param($Path,$Name) return $null }",
        "function Get-ScheduledTask { param($TaskName) if($TaskName -eq 'agent-dispatch-supervisor-disabled'){return Get-ScheduledTaskDisabled}; if($TaskName -eq 'agent-dispatch-supervisor-enabled'){return [pscustomobject]@{TaskName=$TaskName;State='Ready'}}; if($TaskName -eq 'agent-dispatch-supervisor-*'){return @([pscustomobject]@{TaskName='agent-dispatch-supervisor-enabled';State='Running'},(Get-ScheduledTaskDisabled))}; if($script:MasterState -eq 'Absent'){return $null}; return [pscustomobject]@{TaskName=$TaskName;State=$script:MasterState} }",
        "function Remove-ProfileSupervisorTasks { throw 'must preserve profiles' }",
        "function Invoke-SupervisorTaskAction { param($Operation,$Name,$Reason) if($Operation -eq 'start'){$script:Started+=$Name}; if($Operation -eq 'stop'){$script:Stopped+=$Name} }",
        *(function(name) for name in ("Install-SupervisorTask", "Invoke-SupervisorStart", "Invoke-SupervisorsStart", "Invoke-SupervisorsStop")),
        "Install-SupervisorTask",
        "@{started=$script:Started;stopped=$script:Stopped;retired=$script:Retired;reasons=$script:AuditReasons}|ConvertTo-Json -Compress",
    ])
    fixture = tmp_path / "preserved-profiles.ps1"
    fixture.write_text(script)
    result = subprocess.run(["pwsh", "-NoProfile", "-File", str(fixture)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["retired"] == 1
    assert data["reasons"][-1] == "sequence-complete"
    if master_state == "OptOut":
        assert data["started"] == []
        return
    assert data["started"] == ["agent-dispatch-supervisor-enabled"]
    assert "agent-dispatch-supervisor-enabled" in data["stopped"]
    assert "agent-dispatch-supervisor-disabled" in data["stopped"]


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell is unavailable")
def test_audit_scheduler_probe_failure_does_not_suppress_native_action(tmp_path):
    helper = PLUGIN / "scripts/supervisor-lifecycle.ps1"
    script = "\n".join([
        "$ErrorActionPreference='Stop'",
        f"$PluginDir='{PLUGIN}'; $InstallDir='{tmp_path}'; $VenvPython='{sys.executable}'; $LinkPython=$VenvPython",
        "$SrcVersion='1.2.3'; $Action='stop'; $SupervisorTaskName='agent-dispatch-supervisor'; $script:Stopped=$false",
        "function Write-Warn { param($msg) [Console]::Error.WriteLine($msg) }",
        "function Get-CimInstance { param($ClassName,$Filter) [pscustomobject]@{ParentProcessId=100} }",
        "function Get-ScheduledTask { param($TaskName) throw 'fixture scheduler probe unavailable' }",
        "function Stop-ScheduledTask { param($TaskName) $script:Stopped=$true }",
        f". '{helper}'",
        "Invoke-SupervisorTaskAction -Operation stop -Name $SupervisorTaskName -Reason lifecycle-stop",
        "if(-not $script:Stopped){throw 'native action was suppressed'}",
    ])
    fixture = tmp_path / "probe-failure.ps1"
    fixture.write_text(script)
    result = subprocess.run(["pwsh", "-NoProfile", "-File", str(fixture)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert "audit probe unavailable" in result.stderr


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell is unavailable")
@pytest.mark.parametrize("state", ["Running", "Ready"])
def test_native_start_audit_requires_running_receipt(tmp_path, state):
    helper = PLUGIN / "scripts/supervisor-lifecycle.ps1"
    script = "\n".join([
        "$ErrorActionPreference='Stop'",
        f"$PluginDir='{PLUGIN}'; $InstallDir='{tmp_path}'; $VenvPython='{sys.executable}'; $LinkPython=$VenvPython",
        "$SrcVersion='1.2.3'; $Action='update'; $SupervisorTaskName='agent-dispatch-supervisor'",
        "function Write-Warn { param($msg) [Console]::Error.WriteLine($msg) }",
        "function Get-CimInstance { param($ClassName,$Filter) [pscustomobject]@{ParentProcessId=100} }",
        f"function Get-ScheduledTask {{ param($TaskName) [pscustomobject]@{{State='{state}'}} }}",
        "function Start-ScheduledTask { param($TaskName) }",
        f". '{helper}'",
        "Invoke-SupervisorTaskAction -Operation start -Name $SupervisorTaskName -Reason configured-opt-in",
        f"if($script:SupervisorActionSucceeded -ne ${'true' if state == 'Running' else 'false'}){{throw 'incorrect action result'}}",
    ])
    fixture = tmp_path / "start-result.ps1"
    fixture.write_text(script)
    result = subprocess.run(["pwsh", "-NoProfile", "-File", str(fixture)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    events = [json.loads(line) for line in (tmp_path / "lifecycle.log").read_text().splitlines()]
    assert events[-1]["outcome"] == ("ok" if state == "Running" else "fail")
    assert ("activation is unconfirmed" in result.stderr) == (state != "Running")


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell is unavailable")
def test_retirement_audit_warning_does_not_corrupt_result_or_suppress_diagnostic(tmp_path):
    helper = PLUGIN / "scripts/supervisor-lifecycle.ps1"
    script = "\n".join([
        "$ErrorActionPreference='Stop'",
        "function Write-Warn { param($msg) [Console]::Error.WriteLine($msg) }",
        f". '{helper}'",
        '$raw=\'supervisor lifecycle audit unavailable\' + "`n" + \'{"ok":true,"retired":[123],"errors":[]}\'',
        "$result=Convert-SupervisorRetirementResult -Output $raw",
        "$result|ConvertTo-Json -Compress",
    ])
    fixture = tmp_path / "retirement-result.ps1"
    fixture.write_text(script)
    result = subprocess.run(["pwsh", "-NoProfile", "-File", str(fixture)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"ok": True, "retired": [123], "errors": []}
    assert "audit unavailable" in result.stderr


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell is unavailable")
def test_blocked_removal_propagates_reconciliation_failure(tmp_path):
    text = (PLUGIN / "scripts/install.ps1").read_text()
    start = text.index("function Remove-SupervisorTask {")
    function = text[start:text.index("\nfunction ", start + 1)]
    script = "\n".join([
        "$ErrorActionPreference='Stop'; $Action='uninstall'; $script:SupervisorReconcileFailed=$false",
        "function Get-ScheduledTask {param($TaskName) return [pscustomobject]@{State='Ready'}}",
        "function Invoke-SupervisorTaskAction {param($Operation,$Name,$Reason)}",
        "function Unregister-ScheduledTask {param($TaskName,$Confirm)}",
        "function Invoke-SupervisorAudit {param($Event,$Reason,$Name,$Outcome)}",
        function, "$result=Remove-SupervisorTask -Name fixture",
        "if($result -ne 'blocked' -or -not $script:SupervisorReconcileFailed){throw 'incorrect removal outcome'}",
    ])
    fixture = tmp_path / "blocked-removal.ps1"
    fixture.write_text(script)
    result = subprocess.run(["pwsh", "-NoProfile", "-File", str(fixture)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
