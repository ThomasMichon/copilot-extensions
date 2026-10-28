from __future__ import annotations

import ast
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
        'reset-failed) :;;\n'
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
        ("supervisor-reset-failed", "begin"), ("supervisor-reset-failed", "ok"),
        ("supervisor-restart", "begin"), ("supervisor-restart", "ok"),
        ("supervisor-selection", "skipped"),
        ("supervisor-restart", "begin"), ("supervisor-restart", "fail"),
        ("supervisor-selection", "skipped"),
        ("supervisor-stop", "begin"), ("supervisor-stop", "ok"),
        ("supervisor-stop", "begin"), ("supervisor-stop", "ok"),
        ("supervisor-disable", "begin"), ("supervisor-disable", "ok"),
    ]
    assert events[6]["detail"]["native_enabled"] == "masked-runtime"
    assert events[8]["detail"]["exit_code"] == 7
    assert events[9]["detail"]["reason"] == "prior-native-disabled"
    assert events[9]["detail"]["configured_enabled"] is True
    assert events[9]["detail"]["native_enablement_provenance"] == "unknown"
    assert events[10]["detail"]["profile"] == "example"
    assert events[12]["detail"]["reason"] == "installer-inert"
    assert events[12]["detail"]["configured_enabled"] is False
    calls = [
        call for call in (state / "calls").read_text().splitlines()
        if call.split()[0] in {"enable", "reset-failed", "restart"}
    ]
    reset = calls.index(f"reset-failed {unit}")
    assert calls[reset - 1 : reset + 2] == [
        f"enable {unit}", f"reset-failed {unit}", f"restart {unit}",
    ]
    assert calls.count(f"reset-failed {unit}") == 1
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
        f"$SupervisorTaskName='agent-dispatch-supervisor'; $InstallDir='{tmp_path}'; $SupervisorProfileDir='fixture'",
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
        *(function(name) for name in ("Assert-DispatchActions", "Install-SupervisorTask", "Invoke-SupervisorStart", "Invoke-SupervisorsStart", "Invoke-SupervisorsStop")),
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
        "$diagnostic=[System.Management.Automation.ErrorRecord]::new([Exception]::new('supervisor lifecycle audit unavailable'),'fixture',[System.Management.Automation.ErrorCategory]::NotSpecified,$null)",
        '$raw=@($diagnostic, \'{"ok":true,"selected":[123],"retired":[123],"errors":[]}\')',
        "$result=Convert-SupervisorRetirementResult -Output $raw",
        "$result|ConvertTo-Json -Compress",
    ])
    fixture = tmp_path / "retirement-result.ps1"
    fixture.write_text(script)
    result = subprocess.run(["pwsh", "-NoProfile", "-File", str(fixture)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"ok": True, "selected": [123], "retired": [123], "errors": []}
    assert "audit unavailable" in result.stderr


@pytest.mark.parametrize("host", ["pwsh", "powershell"])
def test_retirement_native_stream_framing(tmp_path, host):
    if not shutil.which(host):
        pytest.skip(f"{host} unavailable")
    modes = [
        "compact", "pretty", "failed", "truncated", "malformed", "multiple",
        "diagnostic-stdout", "wrong-type", "foreign-retired", "array-root", "empty",
    ]
    producer = tmp_path / "producer.py"
    producer.write_text(
        "import json, sys\n"
        "mode = sys.argv[1]\n"
        "payload = dict(ok=mode != 'failed', selected=[123], retired=[123], "
        "errors=['nested {brace} and \"quote\"'] if mode == 'failed' else [])\n"
        "if mode == 'pretty': payload['detail'] = {'nested': [{'text': 'braces }{ and \"quote\"'}, {}]}\n"
        "if mode == 'wrong-type': payload['ok'] = 'true'\n"
        "if mode == 'foreign-retired': payload['retired'] = [456]\n"
        "text = json.dumps(payload, indent=2 if mode == 'pretty' else None)\n"
        "if mode == 'truncated': text = text[:-1]\n"
        "if mode == 'malformed': text = '{not json}'\n"
        "if mode == 'multiple': text += '\\n' + text\n"
        "if mode == 'diagnostic-stdout': text = 'unexpected stdout\\n' + text\n"
        "if mode == 'array-root': text = '[' + text + ']'\n"
        "if mode == 'empty': text = ''\n"
        "print(mode + ': before {diagnostic}', file=sys.stderr, flush=True)\n"
        "print(text, flush=True)\n"
        "print(mode + ': after diagnostic', file=sys.stderr, flush=True)\n"
        "sys.exit(1 if mode == 'failed' else 0)\n"
    )
    helper = PLUGIN / "scripts/supervisor-lifecycle.ps1"
    fixture = tmp_path / "framing.ps1"
    fixture.write_text("\n".join([
        "$ErrorActionPreference='Continue'",
        "function Write-Warn { param($msg) [Console]::Error.WriteLine($msg) }",
        f". '{helper}'",
        "$outcomes=@(",
        "foreach($mode in @(" + ",".join(f"'{mode}'" for mode in modes) + ")) {",
        f"  $raw=@(& '{sys.executable}' '{producer}' $mode 2>&1)",
        "  $childExit=$LASTEXITCODE",
        "  try {",
        "    $result=Convert-SupervisorRetirementResult -Output $raw",
        "    @{mode=$mode; accepted=$true; result=$result; exit=$childExit}",
        "  } catch {",
        "    @{mode=$mode; accepted=$false; error=$_.Exception.Message; exit=$childExit}",
        "  }",
        "}",
        ")",
        "ConvertTo-Json -InputObject $outcomes -Depth 10 -Compress",
    ]))
    result = subprocess.run(
        [host, "-NoProfile", "-File", str(fixture)],
        capture_output=True, text=True, timeout=20,
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    outcomes = json.loads(result.stdout)
    assert [data["mode"] for data in outcomes] == modes
    for data in outcomes:
        mode = data["mode"]
        valid = mode in {"compact", "pretty", "failed"}
        assert data["accepted"] is valid, data
        assert data["exit"] == (1 if mode == "failed" else 0), data
        assert f"{mode}: before {{diagnostic}}" in result.stderr
        assert f"{mode}: after diagnostic" in result.stderr
        if valid:
            assert data["result"]["ok"] is (mode != "failed"), data
            assert data["result"]["retired"] == [123], data
        else:
            assert data["error"], data


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell is unavailable")
def test_real_retirement_producer_pretty_result_boundary(tmp_path):
    """Execute the real producer and emitter without cold-importing the CLI."""
    def function(filename, name):
        source = (PLUGIN / "src/agent_dispatch" / filename).read_text()
        node = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == name)
        return ast.get_source_segment(source, node)

    producer = tmp_path / "retirement-producer.py"
    producer.write_text(
        "from __future__ import annotations\n"
        "import json,os,sys\nfrom types import SimpleNamespace, ModuleType\n"
        "__package__='fixture'\n"
        "sys.modules['fixture']=ModuleType('fixture')\n"
        "supervisor_processes=ModuleType('fixture.supervisor_processes')\n"
        "supervisor_lifecycle=ModuleType('fixture.supervisor_lifecycle')\n"
        "sys.modules[supervisor_processes.__name__]=supervisor_processes\n"
        "sys.modules[supervisor_lifecycle.__name__]=supervisor_lifecycle\n"
        "supervisor_processes.retire_windows_supervisor_generations = "
        "lambda install_dir: SimpleNamespace(ok=False, selected=[123], retired=[], errors=['fixture {diagnostic}'])\n"
        "supervisor_lifecycle.Invocation=SimpleNamespace(create=lambda *a: SimpleNamespace())\n"
        "supervisor_lifecycle.audit = lambda *a, **kw: print('audit unavailable', file=sys.stderr)\n"
        + function("__main__.py", "_emit") + "\n"
        + function("coordinator_cli.py", "_cmd_retire_supervisors") + "\n"
        f"args=SimpleNamespace(install_dir={str(tmp_path)!r})\n"
        "sys.exit(_cmd_retire_supervisors(args))\n"
    )
    helper = PLUGIN / "scripts/supervisor-lifecycle.ps1"
    fixture = tmp_path / "actual-producer.ps1"
    fixture.write_text("\n".join([
        "$ErrorActionPreference='Continue'",
        "function Write-Warn { param($msg) [Console]::Error.WriteLine($msg) }",
        f". '{helper}'",
        f"$raw=@(& '{sys.executable}' '{producer}' 2>&1)",
        "$rc=$LASTEXITCODE",
        "$result=Convert-SupervisorRetirementResult -Output $raw",
        "if($rc -ne 1 -or $result.ok -ne $false){throw 'failed result lost'}",
        "$result|ConvertTo-Json -Depth 10 -Compress",
    ]))
    result = subprocess.run(
        ["pwsh", "-NoProfile", "-File", str(fixture)],
        capture_output=True, text=True, timeout=20,
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {
        "ok": False, "selected": [123], "retired": [], "errors": ["fixture {diagnostic}"],
    }
    assert "audit unavailable" in result.stderr


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="PowerShell is unavailable")
@pytest.mark.parametrize("mode", ["pretty", "partial-failure", "malformed"])
def test_retirement_installer_callsite_preserves_fallback_scope(tmp_path, mode):
    package = tmp_path / "agent_dispatch"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "__main__.py").write_text(
        "import json,sys\n"
        "print('audit diagnostic', file=sys.stderr)\n"
        f"mode={mode!r}\n"
        "print('{truncated' if mode == 'malformed' else json.dumps("
        "dict(ok=mode == 'pretty', selected=[123,456], retired=[123],"
        "errors=['fixture partial refusal'] if mode == 'partial-failure' else []), indent=2))\n"
        "sys.exit(0 if mode == 'pretty' else 1)\n"
    )
    source = (PLUGIN / "scripts/install.ps1").read_text()
    start = source.index("function Retire-SupervisorProcesses {")
    function = source[start:source.index("\nfunction ", start + 1)]
    helper = PLUGIN / "scripts/supervisor-lifecycle.ps1"
    fixture = tmp_path / "retirement-callsite.ps1"
    fixture.write_text("\n".join([
        "$ErrorActionPreference='Stop'; $env:OS='Windows_NT'; $script:Fallbacks=0",
        f"$InstallDir='{tmp_path}'; $VenvPython='{sys.executable}'; $LinkPython=$VenvPython",
        "function Write-Warn { param($msg) [Console]::Error.WriteLine($msg) }",
        f". '{helper}'",
        "function Invoke-SupervisorAudit { param($Event,$Reason,$Outcome,$ExitCode) }",
        "function Retire-SupervisorProcessesFallback { $script:Fallbacks++; return 42 }",
        function,
        "$count=Retire-SupervisorProcesses",
        "if($ErrorActionPreference -ne 'Stop'){throw 'error preference leaked'}",
        "@{count=$count;fallbacks=$script:Fallbacks}|ConvertTo-Json -Compress",
    ]))
    result = subprocess.run(
        ["pwsh", "-NoProfile", "-File", str(fixture)],
        env=dict(os.environ, PYTHONPATH=str(tmp_path)),
        capture_output=True, text=True, timeout=20,
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {
        "count": 42 if mode == "malformed" else 1,
        "fallbacks": 1 if mode == "malformed" else 0,
    }
    assert "audit diagnostic" in result.stderr
    assert ("result rejected:" in result.stderr) is (mode == "malformed")
    assert ("fixture partial refusal" in result.stderr) is (mode == "partial-failure")


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
