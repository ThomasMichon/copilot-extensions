"""Payload-local invocation and explicit management-boundary tests."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]


def _installer_function(name: str) -> str:
    text = (PLUGIN / "scripts" / "install.ps1").read_text(encoding="utf-8-sig")
    start = text.index(f"function {name} ")
    end = text.find("\nfunction ", start + 1)
    return text[start:] if end == -1 else text[start:end]


@pytest.mark.skipif(os.name != "nt", reason="isolated Windows installer policy")
@pytest.mark.parametrize(
    "policy,task,action,expected",
    [
        ("preserve", "missing", "install", "unchanged"),
        ("preserve", "custom", "update", "unchanged"),
        ("preserve", "disabled", "update", "unchanged"),
        ("enabled", "missing", "install", "registered"),
        ("enabled", "owned", "update", "unchanged"),
        ("enabled", "disabled", "update", "unchanged"),
        ("enabled", "missing", "schedule", "registered"),
        ("enabled", "missing", "inactive-schedule", "refused"),
        ("absent", "missing", "install", "unchanged"),
        ("absent", "owned", "schedule", "removed"),
        ("absent", "direct", "schedule", "removed"),
        ("absent", "absolute-shell", "schedule", "removed"),
        ("absent", "foreign-first", "schedule", "refused"),
        ("enabled", "foreign-first", "schedule", "refused"),
        ("absent", "owned-forwarded", "schedule", "refused"),
        ("enabled", "owned-forwarded", "schedule", "refused"),
        ("absent", "foreign-wrapper", "schedule", "refused"),
        ("absent", "foreign-executor", "schedule", "refused"),
        ("absent", "command", "schedule", "refused"),
        ("absent", "unquoted-space", "schedule", "refused"),
        ("absent", "custom", "schedule", "refused"),
        ("absent", "suffix", "schedule", "refused"),
        ("absent", "ambiguous", "schedule", "refused"),
        ("absent", "disabled", "schedule", "refused"),
        ("absent", "remove-fails", "schedule", "refused"),
    ],
)
def test_windows_scheduling_is_separate_from_service_and_state(tmp_path, policy, task, action, expected):
    root = tmp_path / "vault space"
    root.mkdir()
    (root / "bin").mkdir()
    interpreter = root / "python.ps1"
    interpreter.write_text(
        "$global:LASTEXITCODE=0; '{\"root\":\"fixture\",\"config\":\"fixture\"}'\n",
    )
    (root / "bin" / "resolve-runtime.ps1").write_text(f"$AgentRtPy='{interpreter}'\n")
    state = root / "state"
    state.write_text("live-daemon-and-state")
    (tmp_path / "runtime-activation.ps1").write_text(
        "function Get-VaultRuntimeActivation { param($Project) "
        "[pscustomobject]@{state='"
        + ("configuration-empty" if action == "inactive-schedule" else "enabled")
        + "'} }\n",
    )
    action = "schedule" if action == "inactive-schedule" else action
    def quote(value):
        return "'" + str(value).replace("'", "''") + "'"
    script = tmp_path / "scheduling.ps1"
    script.write_text(
        "$ErrorActionPreference='Stop'; Set-StrictMode -Version 2\n"
        f"$InstallDir={quote(root)}; $TaskName='fixture-only'; $Action={quote(action)}; $script:kind={quote(task)}\n"
        "$TaskLauncher=Join-Path $InstallDir 'service.ps1'; $RunDir=Join-Path $InstallDir 'run'\n"
        "$SocketPath='fixture'; $PipePath='fixture'; $PidFile='fixture'; $LogFile='fixture'; $installationId=''\n"
        f"$NoService=$false; $LinkPython={quote(interpreter)}\n"
        "$script:registered=0; $script:removed=0; $script:starts=0; $script:ensured=0\n"
        "function Write-Ok { param($m) }; function Write-Skip { param($m) }\n"
        "function Write-Fail { param($m) throw $m }; function Write-Warn { param($m) throw $m }\n"
        f"function Get-WindowsScheduledTaskPolicy {{ {quote(policy)} }}\n"
        "function Stop-ScheduledTask { throw 'service stop forbidden' }\n"
        "function Invoke-Stop { throw 'daemon stop forbidden' }\n"
        "function Start-ScheduledTask { param($TaskName,$ErrorAction) $script:starts++ }\n"
        "function powershell.exe { $script:ensured++; $global:LASTEXITCODE=0 }\n"
        "function New-ScheduledTaskAction { param($Execute,$Argument,$WorkingDirectory) "
        "[pscustomobject]@{Execute=$Execute; Arguments=$Argument; WorkingDirectory=$WorkingDirectory} }\n"
        "function New-ScheduledTaskTrigger { param([switch]$AtLogOn,$User) [pscustomobject]@{Delay=''} }\n"
        "function New-ScheduledTaskSettingsSet { param([switch]$AllowStartIfOnBatteries,[switch]$DontStopIfGoingOnBatteries,"
        "[switch]$StartWhenAvailable,$ExecutionTimeLimit,$MultipleInstances,$RestartCount,$RestartInterval) @{} }\n"
        "function Register-ScheduledTask { param($TaskName,$Action,$Trigger,$Settings,$Description) "
        "$script:registered++; $script:task=[pscustomobject]@{Actions=@($Action); State='Running'} }\n"
        "function Set-ScheduledTask { throw 'existing registration must not be rewritten' }\n"
        "function Unregister-ScheduledTask { param($TaskName,$Confirm,$ErrorAction)\n"
        " if ($script:kind -eq 'remove-fails') { throw 'access denied' }; $script:removed++; $script:task=$null }\n"
        "function Get-ScheduledTask { param($TaskName,$ErrorAction) $script:task }\n"
        "$script:task=$null\n"
        "if ($script:kind -ne 'missing') {\n"
        " $launcher=$TaskLauncher; if ($script:kind -eq 'custom') { $launcher='C:\\foreign\\service.ps1' }\n"
        " if ($script:kind -eq 'suffix') { $launcher += '.foreign' }\n"
        " $actions=@([pscustomobject]@{Execute='conhost.exe'; Arguments=\"--headless powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `\"$launcher`\"\"; WorkingDirectory=$InstallDir})\n"
        " if ($script:kind -eq 'direct') { $actions[0].Execute='C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe'; $actions[0].Arguments=\"-NoProfile -File `\"$launcher`\"\" }\n"
        " if ($script:kind -eq 'absolute-shell') { $actions[0].Arguments=\"--headless `\"C:\\Program Files\\PowerShell\\7\\pwsh.exe`\" -WindowStyle Hidden -NoProfile -ExecutionPolicy Bypass -File `\"$launcher`\"\" }\n"
        " if ($script:kind -eq 'foreign-first') { $actions[0].Arguments=\"--headless powershell.exe -File C:\\foreign.ps1 -File `\"$launcher`\"\" }\n"
        " if ($script:kind -eq 'owned-forwarded') { $actions[0].Arguments=\"--headless powershell.exe -File C:\\foreign.ps1 `\"$launcher`\"\" }\n"
        " if ($script:kind -eq 'foreign-wrapper') { $actions[0].Arguments=\"--headless C:\\foreign.exe -File `\"$launcher`\"\" }\n"
        " if ($script:kind -eq 'foreign-executor') { $actions[0].Execute='cmd.exe' }\n"
        " if ($script:kind -eq 'command') { $actions[0].Arguments=\"--headless powershell.exe -Command C:\\foreign.ps1 -File `\"$launcher`\"\" }\n"
        " if ($script:kind -eq 'unquoted-space') { $actions[0].Arguments=\"--headless powershell.exe -File $launcher\" }\n"
        " if ($script:kind -eq 'ambiguous') { $actions += $actions[0] }\n"
        " $script:task=[pscustomobject]@{Actions=$actions; State=$(if ($script:kind -eq 'disabled') {'Disabled'} else {'Running'})}\n"
        "}\n"
        + _installer_function("Assert-OwnedVaultTask")
        + _installer_function("Get-SchedulingPolicyTask")
        + _installer_function("Register-AgentVaultTask")
        + "\n$result='unchanged'; try { Register-AgentVaultTask; Register-AgentVaultTask } "
        "catch { $result='refused'; [Console]::Error.WriteLine($_) }\n"
        "if ($script:registered) { $result='registered' }; if ($script:removed) { $result='removed' }\n"
        "[Console]::WriteLine((@{result=$result; removed=$script:removed; registered=$script:registered;"
        " starts=$script:starts; ensured=$script:ensured} | ConvertTo-Json -Compress))\n",
        encoding="utf-8-sig",
    )
    result = subprocess.run(
        [shutil.which("powershell"), "-NoProfile", "-NonInteractive", "-File", str(script)],
        capture_output=True, text=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    actual = json.loads(result.stdout)
    assert actual["result"] == expected, result.stdout + result.stderr
    assert actual["removed"] == (1 if expected == "removed" else 0)
    assert actual["registered"] == (1 if expected == "registered" else 0)
    normal_service = action != "schedule" and expected != "refused" and task != "disabled"
    assert (actual["starts"], actual["ensured"]) == (
        2 if normal_service and policy == "enabled" else 0,
        2 if normal_service else 0,
    )
    assert state.read_text() == "live-daemon-and-state"


@pytest.mark.skipif(os.name != "nt", reason="isolated Windows task-query outcomes")
@pytest.mark.parametrize(
    "policy,query,succeeds",
    [
        ("absent", "missing", True),
        ("absent", "denied", False),
        ("absent", "cim-failure", False),
        ("absent", "other-not-found", False),
        ("absent", "module-unavailable", False),
        ("preserve", "module-unavailable", True),
        ("preserve", "missing", True),
    ],
)
def test_windows_scheduling_distinguishes_missing_from_unknown(tmp_path, policy, query, succeeds):
    script = tmp_path / "query.ps1"
    script.write_text(
        "$ErrorActionPreference='Stop'; $TaskName='fixture-only'; $script:reportedAbsent=$false\n"
        "$Action='schedule'; $NoService=$false\n"
        f"$query='{query}'\n"
        f"function Get-WindowsScheduledTaskPolicy {{ '{policy}' }}\n"
        "function Get-Command { if ($query -ne 'module-unavailable') { 'fixture-command' } }\n"
        "function Get-ScheduledTask { param($TaskName,$ErrorAction)\n"
        " if ($ErrorAction -ne 'Stop') { throw 'query must be terminating' }\n"
        " $id=if ($query -eq 'missing') {'CmdletizationQuery_NotFound_TaskName'} else {$query}\n"
        " throw [Management.Automation.ErrorRecord]::new([Exception]::new($query),"
        "$id,[Management.Automation.ErrorCategory]::ObjectNotFound,$TaskName)\n"
        "}\n"
        "function Write-Ok { param($m) $script:reportedAbsent=$true }\n"
        "function Write-Skip { param($m) }\n"
        "function Unregister-ScheduledTask { throw 'unexpected removal' }\n"
        + _installer_function("Get-SchedulingPolicyTask")
        + _installer_function("Register-AgentVaultTask")
        + "\n$succeeded=$false; try { Register-AgentVaultTask; $succeeded=$true } catch {}\n"
        "[Console]::WriteLine((@{success=$succeeded; absent=$script:reportedAbsent} | ConvertTo-Json -Compress))\n",
        encoding="utf-8-sig",
    )
    result = subprocess.run(
        [shutil.which("powershell"), "-NoProfile", "-NonInteractive", "-File", str(script)],
        capture_output=True, text=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    actual = json.loads(result.stdout)
    assert actual["success"] is succeeds
    assert actual["absent"] is (succeeds and policy == "absent")


@pytest.mark.skipif(os.name != "nt", reason="faithful Windows task-only variable setup and dispatch")
@pytest.mark.parametrize("retained_runtime", [True, False])
@pytest.mark.parametrize("config_state", ["absent", "malformed", "invalid-policy"])
def test_schedule_absent_with_unbuilt_incoming_payload(tmp_path, retained_runtime, config_state):
    root = tmp_path / "vault"
    root.mkdir()
    payload = tmp_path / "incoming"
    payload.mkdir()
    (payload / "pyproject.toml").write_text('[project]\nversion = "9.0.0"\n')
    (root / "current-version").write_text("1.0.0")
    if retained_runtime:
        interpreter = root / "versions" / "1.0.0" / "Scripts" / "python.exe"
        interpreter.parent.mkdir(parents=True)
        interpreter.write_text("retained interpreter must not be executed")
    state = root / "state"
    state.write_text("protected daemon state")
    home = tmp_path / "home"
    home.mkdir()
    config = tmp_path / "native-config.json"
    configuration = {"enabled": False, "windows_scheduled_task": (
        "invalid" if config_state == "invalid-policy" else "absent"
    )}
    original_config = "{" if config_state == "malformed" else json.dumps(configuration)
    config.write_text(original_config)
    source = (PLUGIN / "scripts" / "install.ps1").read_text(encoding="utf-8-sig")
    preamble_start = source.index("Set-StrictMode -Version 2.0")
    preamble_end = source.index("if ($Action -in @('install', 'update', 'stamp', 'provision', 'start'))")
    stage_start = source.index("                Set-Location -LiteralPath $env:USERPROFILE")
    stage_end = source.index("                $__selfStageRoot", stage_start)
    setup_start = source.index("# === install-contract:v3 versioned-venv (")
    setup_end = source.index("# === end install-contract:v3 versioned-venv ===", setup_start)
    dispatch_start = source.index("    switch ($Action) {", source.index("$needsInstallLock"))
    dispatch_end = source.index("\n} finally {", dispatch_start)

    def quote(value):
        return "'" + str(value).replace("'", "''") + "'"

    script = tmp_path / "dispatch.ps1"
    dispatch = source[dispatch_start:dispatch_end]
    script.write_text(
        "$ErrorActionPreference='Stop'; Set-StrictMode -Version 2\n"
        f"$InstallDir={quote(root)}; $PluginDir={quote(payload)}\n"
        f"$env:USERPROFILE={quote(home)}; Set-Location -LiteralPath {quote(tmp_path)}\n"
        f"[IO.Directory]::SetCurrentDirectory({quote(tmp_path)})\n"
        f"$env:AGENT_VAULT_CONFIG={quote(config.name)}\n"
        "$env:COPILOT_EXTENSIONS_HUMAN_UTF8=''; $Action='schedule'\n"
        "$env:COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT=''\n"
        + source[preamble_start:preamble_end]
        + source[stage_start:stage_end]
        + "$VenvDir=Join-Path $InstallDir '.venv'; $VenvPython=Join-Path $VenvDir 'Scripts\\python.exe'\n"
        "$Action='schedule'; $NoService=$false; $TaskName='fixture-only'; $script:removed=0\n"
        "$TaskLauncher=Join-Path $InstallDir 'service.ps1'\n"
        "$script:task=[pscustomobject]@{State='Running'; Actions=@([pscustomobject]@{"
        "Execute='conhost.exe'; Arguments=\"--headless powershell.exe -File `\"$TaskLauncher`\"\"})}\n"
        "function Get-ScheduledTask { param($TaskName,$ErrorAction) $script:task }\n"
        "function Unregister-ScheduledTask { param($TaskName,$Confirm,$ErrorAction) "
        "$script:removed++; $script:task=$null; "
        "[IO.File]::AppendAllText((Join-Path $InstallDir 'retired'),\"removed`n\") }\n"
        "function Start-ScheduledTask { throw 'service start forbidden' }\n"
        "function Stop-ScheduledTask { throw 'service stop forbidden' }\n"
        "function Invoke-Start { throw 'direct start forbidden' }\n"
        "function Invoke-Stop { throw 'daemon stop forbidden' }\n"
        "function Get-VaultRuntimeActivation { throw 'activation must not be consulted for retirement' }\n"
        "function Write-Ok { param($m) }; function Write-Skip { param($m) }\n"
        "function Write-Warn { param($m) throw $m }\n"
        + source[setup_start:setup_end]
        + "\nif (Test-Path -LiteralPath $LinkPython) { throw 'incoming slot must remain unbuilt' }\n"
        + _installer_function("Get-WindowsScheduledTaskPolicy")
        + _installer_function("Get-SchedulingPolicyTask")
        + _installer_function("Assert-OwnedVaultTask")
        + _installer_function("Register-AgentVaultTask")
        + "\n" + dispatch + "\n" + dispatch
        + "\n[Console]::WriteLine($script:removed)\n",
        encoding="utf-8-sig",
    )
    result = subprocess.run(
        [shutil.which("powershell"), "-NoProfile", "-NonInteractive", "-File", str(script)],
        capture_output=True, text=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if config_state == "absent":
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.strip() == "1"
    else:
        assert result.returncode != 0, result.stdout + result.stderr
        assert any(word in result.stderr.lower() for word in (
            "configuration", "json", "windows_scheduled_task",
        ))
    retired = root / "retired"
    assert (retired.read_text().splitlines() if retired.exists() else []) == (
        ["removed"] if config_state == "absent" else []
    )
    assert state.read_text() == "protected daemon state"
    assert (root / "current-version").read_text() == "1.0.0"
    assert config.read_text() == original_config
    assert not (root / "versions" / "9.0.0").exists()
    assert not (root / "service.ps1").exists()


@pytest.mark.skipif(os.name != "nt", reason="native config reader in PowerShell")
@pytest.mark.parametrize("policy", ["preserve", "enabled", "absent", "invalid"])
def test_windows_scheduling_reads_native_machine_config(tmp_path, policy):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({} if policy == "preserve" else {"windows_scheduled_task": policy}))
    def quote(value):
        return "'" + str(value).replace("'", "''") + "'"
    script = tmp_path / "reader.ps1"
    script.write_text(
        "$ErrorActionPreference='Stop'\n"
        f"$LinkPython={quote(sys.executable)}; $env:AGENT_VAULT_CONFIG={quote(path)}\n"
        + _installer_function("Get-WindowsScheduledTaskPolicy")
        + "\nGet-WindowsScheduledTaskPolicy\n",
        encoding="utf-8-sig",
    )
    result = subprocess.run(
        [shutil.which("powershell"), "-NoProfile", "-NonInteractive", "-File", str(script)],
        capture_output=True, text=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if policy == "invalid":
        assert result.returncode != 0
        assert "windows_scheduled_task must be" in result.stderr
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.strip() == policy


@pytest.mark.skipif(os.name != "nt", reason="isolated Windows installer startup")
@pytest.mark.parametrize("task", ["missing", "module-unavailable", "disabled"])
def test_installer_direct_start_retains_task_independent_activation(tmp_path, task):
    fake_python = tmp_path / "python.cmd"
    fake_python.write_text("@echo START %*\r\n@exit /b 0\r\n")
    def quote(value):
        return "'" + str(value).replace("'", "''") + "'"
    script = tmp_path / "start.ps1"
    query = "[pscustomobject]@{State='Disabled'}" if task == "disabled" else "$null"
    script.write_text(
        "$ErrorActionPreference='Stop'\n"
        f"$LinkPython={quote(fake_python)}; $TaskName='fixture-only'\n"
        f"$env:COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT={quote(tmp_path)}\n"
        f"function Get-ScheduledTask {{ {query} }}\n"
        + ("function Get-Command { $null }\n" if task == "module-unavailable" else "")
        + "function Start-ScheduledTask { throw 'scheduler startup forbidden' }\n"
        "function Write-Fail { param($m) throw $m }\n"
        + _installer_function("Invoke-Start") + "\nInvoke-Start\n",
        encoding="utf-8-sig",
    )
    result = subprocess.run(
        [shutil.which("powershell"), "-NoProfile", "-NonInteractive", "-File", str(script)],
        capture_output=True, text=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if task == "disabled":
        assert result.returncode != 0
        assert "refusing direct-start fallback" in result.stderr
        assert "START" not in result.stdout
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        assert "START -m agent_vault start" in result.stdout


def test_payload_manifest_describes_agent_vault_runtime() -> None:
    manifest = json.loads(
        (PLUGIN / "payload-invocation.json").read_text(encoding="utf-8")
    )
    assert manifest == {
        "schema": "copilot-extensions.payload-invocation",
        "version": 2,
        "command": "agent-vault",
        "module": "agent_vault",
        "legacyRuntimeRoot": ".agent-vault",
        "installationContext": "required",
        "noSelfProvisionEnv": "AGENT_VAULT_NO_SELFPROVISION",
        "purpose": "Fetch and manage machine-local vault credentials",
        "installer": "install",
        "installerLifecycle": True,
        "inProcessCatalog": True,
        "windowsCatalogShim": "cmd",
        "provisionMode": "direct",
        "payloadRootEnv": "AGENT_VAULT_PAYLOAD_ROOT",
        "payloadDispatcher": {
            "posix": "scripts/runtime-gate.sh",
            "windows": "scripts/runtime-gate.ps1",
        },
    }

    posix = (PLUGIN / "bin" / "agent-vault").read_text(encoding="utf-8")
    powershell = (PLUGIN / "bin" / "agent-vault.ps1").read_text(encoding="utf-8")
    assert "runtime-gate.sh" in posix
    assert "payload-dir" not in posix
    assert r"runtime-gate.ps1" in powershell
    assert "payload-dir" not in powershell


def test_runtime_gates_scope_namespaced_service_environment() -> None:
    for suffix in ("sh", "ps1"):
        gate = (PLUGIN / "scripts" / ("runtime-gate." + suffix)).read_text(encoding="utf-8")
        assert "runtime-environment." + suffix in gate
    posix = (PLUGIN / "scripts" / "runtime-environment.sh").read_text(encoding="utf-8")
    powershell = (PLUGIN / "scripts" / "runtime-environment.ps1").read_text(encoding="utf-8")

    for text in (posix, powershell):
        assert "AGENT_VAULT_INSTALLATION_ID" in text
        assert "AGENT_VAULT_RUN_DIR" in text
        assert "AGENT_VAULT_CORE_RUN_DIR" in text
        assert "AGENT_VAULT_CACHE_DIR" in text
        assert "AGENT_VAULT_SOCKET" in text
        assert "AGENT_VAULT_PIPE" in text
        assert "AGENT_VAULT_PID" in text
        assert "AGENT_VAULT_LOG" in text
        assert "AGENT_VAULT_PORT" in text
        assert "AGENT_VAULT_SYSTEMD_UNIT" in text
        assert "AGENT_VAULT_TASK_NAME" in text


@pytest.mark.parametrize("namespaced", [False, True])
@pytest.mark.parametrize("root_name", ["runtime", "runtime-\u0130\u03a3"])
def test_shared_runtime_environment_replaces_every_foreign_route(tmp_path, namespaced, root_name) -> None:
    from agent_vault import _peer_launch

    keys = (
        "AGENT_VAULT_HOME", "AGENT_VAULT_VALIDATED_CONTEXT", "COPILOT_EXTENSIONS_CONTEXT",
        "AGENT_VAULT_INSTALLATION_ID", "AGENT_VAULT_RUN_DIR", "AGENT_VAULT_CORE_RUN_DIR",
        "AGENT_VAULT_CACHE_DIR", "AGENT_VAULT_SOCKET", "AGENT_VAULT_PIPE",
        "AGENT_VAULT_PID", "AGENT_VAULT_LOG", "AGENT_VAULT_PORT",
        "AGENT_VAULT_SYSTEMD_UNIT", "AGENT_VAULT_TASK_NAME",
    )
    root = tmp_path / root_name
    context = str(tmp_path / "context.json") if namespaced else ""
    identity = "fixture-market/agent-vault" if namespaced else ""
    env = {**os.environ, **dict.fromkeys(keys, "foreign")}
    if os.name == "nt":
        shell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        helper = PLUGIN / "scripts" / "runtime-environment.ps1"
        quote = lambda value: "'" + str(value).replace("'", "''") + "'"
        listed = ",".join(quote(key) for key in keys)
        code = (
            "[Console]::OutputEncoding=New-Object Text.UTF8Encoding($false); "
            f". {quote(helper)}; $suffix=''; "
            + (f"$suffix=Get-RuntimeServiceSuffix -RuntimeRoot {quote(root)}; " if namespaced else "")
            + f"Set-RuntimeEnvironment -RuntimeRoot {quote(root)} "
            f"-Context {quote(context)} -InstallationId {quote(identity)} -ServiceSuffix $suffix; "
            f"$result=@{{}}; foreach ($key in @({listed})) {{ "
            "$result[$key]=[Environment]::GetEnvironmentVariable($key, 'Process') }; "
            "[Console]::Out.WriteLine(($result | ConvertTo-Json -Compress))"
        )
        command = [str(shell), "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", code]
    else:
        shell = shutil.which("bash")
        if shell is None:
            pytest.skip("Bash is unavailable")
        helper = PLUGIN / "scripts" / "runtime-environment.sh"
        code = "import os,json;print(json.dumps({key:os.environ.get(key) for key in " + repr(keys) + "}))"
        command = [
            shell, "-c", '. "$1"; suffix=""; if [ -n "$4" ]; then suffix="$(scoped_identity_suffix "$2")"; fi; '
            'apply_runtime_env "$2" "$3" "$4" "$suffix"; exec "$5" -I -c "$6"',
            "runtime-environment", str(helper), str(root), context, identity, sys.executable, code,
        ]
    result = subprocess.run(
        command, env=env, capture_output=True, text=True, encoding="utf-8",
        timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    actual = json.loads(result.stdout)
    assert actual["AGENT_VAULT_HOME"] == str(root)
    if namespaced:
        suffix = actual["AGENT_VAULT_PIPE"].removeprefix(r"\\.\pipe\agent-vault-")
        expected = {
            "AGENT_VAULT_VALIDATED_CONTEXT": context, "COPILOT_EXTENSIONS_CONTEXT": context,
            "AGENT_VAULT_INSTALLATION_ID": identity, "AGENT_VAULT_RUN_DIR": str(root / "run"),
            "AGENT_VAULT_CORE_RUN_DIR": str(root / "core"), "AGENT_VAULT_CACHE_DIR": str(root / "cache"),
            "AGENT_VAULT_SOCKET": str(root / "run" / "agent-vault.sock"),
            "AGENT_VAULT_PIPE": rf"\\.\pipe\agent-vault-{suffix}",
            "AGENT_VAULT_PID": str(root / "run" / "agent-vault-service.pid"),
            "AGENT_VAULT_LOG": str(root / "logs" / "agent-vault-service.log"),
            "AGENT_VAULT_PORT": "0", "AGENT_VAULT_SYSTEMD_UNIT": f"agent-vault-{suffix}.service",
            "AGENT_VAULT_TASK_NAME": f"AgentVault-{suffix}",
        }
        assert {key: actual[key] for key in expected} == expected
        if os.name == "nt" or sys.platform.startswith("linux"):
            rebound = _peer_launch.peer_environment({
                "pluginId": "agent-vault", "pluginRoot": str(root),
                "marketplaceId": "fixture-market", "installReceipt": context,
                "payloadRoot": str(PLUGIN),
            }, env)
            assert {key: rebound[key] for key in keys} == actual
    else:
        assert not actual["AGENT_VAULT_VALIDATED_CONTEXT"]
        assert all(actual[key] is None for key in keys[2:])
    assert not root.exists()


def test_installers_scope_service_identity_by_install_root() -> None:
    install_sh = (PLUGIN / "scripts" / "install.sh").read_text(encoding="utf-8")
    install_ps1 = (PLUGIN / "scripts" / "install.ps1").read_text(encoding="utf-8")

    assert "SERVICE_SUFFIX" in install_sh
    assert "agent-vault-${SERVICE_SUFFIX}.service" in install_sh
    assert "AGENT_VAULT_INSTALLATION_ID" in install_sh
    assert "AGENT_VAULT_PORT=0" in install_sh
    assert "TaskLauncher" in install_ps1
    assert "AgentVault-$serviceSuffix" in install_ps1
    assert "AGENT_VAULT_INSTALLATION_ID" in install_ps1
    assert "AGENT_VAULT_PORT = '0'" in install_ps1


def test_scheduled_task_launcher_resolves_slot_at_every_run() -> None:
    """#1836: the Windows Scheduled Task launcher must resolve the active
    versioned slot itself at process start (the same resolve-runtime.ps1 chain
    the binstub uses), never a concrete versions/<v> interpreter path baked in
    at registration time -- so an activated version cutover takes effect on
    the daemon's next restart without needing a task re-registration."""
    install_ps1 = (PLUGIN / "scripts" / "install.ps1").read_text(encoding="utf-8")
    idx = install_ps1.index("function Register-AgentVaultTask")
    body = install_ps1[idx : install_ps1.index("\nfunction ", idx + 1)]

    # The launcher body written to $TaskLauncher must dot-source the resolver
    # and resolve $_py fresh every time it runs, not at registration time.
    launcher_start = body.index("[System.IO.File]::WriteAllText($TaskLauncher")
    launcher_body = body[launcher_start : body.index('"@, [Text.UTF8Encoding]::new($true))', launcher_start)]
    assert "resolve-runtime.ps1" in launcher_body
    assert "$_py = $null" in launcher_body.replace("`$", "$")
    assert '& `$_py -X "pycache_prefix=`$_pythonCache"' in launcher_body
    assert "`$_serviceArguments = @('--foreground', '--persistent')" in launcher_body
    assert "-m agent_vault.service @_serviceArguments @args" in launcher_body

    # Task-only setup may resolve the installed runtime for configuration reads.
    # The generated launcher's primary selection must still be resolved afresh.
    primary_selection = launcher_body[:launcher_body.index("if (-not (`$_py")]
    assert ". `$_resolver; `$_py = `$AgentRtPy" in primary_selection
    assert "$LinkPython" not in primary_selection
    assert "taskPy" not in primary_selection


def test_scheduled_task_registration_skips_reregister_when_already_correct() -> None:
    """#1836: re-registering the Scheduled Task (Set-ScheduledTask) is reserved
    for a genuine Action drift, not every install/update -- the launcher path
    and working directory are stable, so a match means already-correct."""
    install_ps1 = (PLUGIN / "scripts" / "install.ps1").read_text(encoding="utf-8")
    idx = install_ps1.index("function Register-AgentVaultTask")
    body = install_ps1[idx : install_ps1.index("\nfunction ", idx + 1)]
    assert "$matchesDesired = $existingAction -and" in body
    assert "if ($matchesDesired) {" in body
    matches_idx = body.index("if ($matchesDesired) {")
    match_block = body[matches_idx : body.index("} else {", matches_idx)]
    assert "Set-ScheduledTask" not in match_block
    assert "Register-ScheduledTask" not in match_block


def test_start_does_not_require_a_registered_scheduled_task() -> None:
    """#1836: `install.ps1 start` must converge on the same user-mode ensure
    path the CLI's own `agent-vault start` uses (ensure_service/start_service
    in cli.py) when no Scheduled Task is registered -- a client-only
    (-NoService) host, or one where the ScheduledTasks module is unavailable,
    must still be able to start the daemon directly rather than failing."""
    install_ps1 = (PLUGIN / "scripts" / "install.ps1").read_text(encoding="utf-8")
    idx = install_ps1.index("function Invoke-Start")
    body = install_ps1[idx : install_ps1.index("\nfunction ", idx + 1)]
    assert "No AgentVault task installed" not in body
    assert "& $LinkPython -m agent_vault start" in body


def test_session_catalog_producer_is_not_registered_as_a_hook() -> None:
    hooks = json.loads((PLUGIN / "hooks.json").read_text(encoding="utf-8"))
    session_hooks = hooks["hooks"]["sessionStart"]
    assert len(session_hooks) == 2
    assert "bootstrap-check" in session_hooks[0]["bash"]
    assert "bootstrap-check" in session_hooks[0]["powershell"]
    assert "write-session-guidance" in session_hooks[1]["bash"]
    assert "write-session-guidance" in session_hooks[1]["powershell"]
    assert "emit-command-catalog" not in str(session_hooks)

    powershell_catalog = (
        PLUGIN / "scripts" / "emit-command-catalog.ps1"
    ).read_text(encoding="utf-8")
    assert r"bin\agent-vault.cmd" in powershell_catalog
    assert "shell = 'cmd'" in powershell_catalog


def test_out_of_session_boundaries_remain_explicit() -> None:
    skill = (PLUGIN / "skills" / "agent-vault" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    assert (
        "'!agent-vault git-credential' "
        "# marketplace-isolation: allow git-credential-management"
    ) in skill
    setup = (
        PLUGIN / "skills" / "agent-vault-setup" / "SKILL.md"
    ).read_text(encoding="utf-8")
    assert "scripts\\install.ps1 -Action start" in setup
    assert "scripts/install.sh start" in setup


@pytest.mark.skipif(os.name == "nt", reason="POSIX payload command test")
def test_posix_payload_command_ignores_shadow_path_and_preserves_stdin(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    runtime = home / ".agent-vault"
    python = runtime / "versions" / "test" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text(
        '#!/bin/sh\nprintf "%s|" "$*"\ncat\n',
        encoding="utf-8",
    )
    python.chmod(0o755)
    (python.parents[1] / ".install-complete.json").write_text(
        (
            '{"version": "test", '
            '"completed_at": "2026-08-27T00:00:00Z", "pid": 1}'
        ),
        encoding="utf-8",
    )
    (runtime / "current-version").write_text("test\n", encoding="utf-8")

    shadow_bin = tmp_path / "shadow-bin"
    shadow_bin.mkdir()
    shadow_marker = tmp_path / "shadow-called"
    shadow = shadow_bin / "agent-vault"
    shadow.write_text(
        f'#!/bin/sh\nprintf called > "{shadow_marker}"\nexit 99\n',
        encoding="utf-8",
    )
    shadow.chmod(0o755)

    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "COPILOT_PLUGIN_ROOT": str(PLUGIN),
            "PATH": f"{shadow_bin}{os.pathsep}{env['PATH']}",
        }
    )
    result = subprocess.run(
        [str(PLUGIN / "bin" / "agent-vault"), "seal", "example"],
        input="secret",
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout == "-m agent_vault seal example|secret"
    assert not shadow_marker.exists()


@pytest.mark.skipif(os.name != "nt", reason="native Windows CMD test")
def test_windows_catalog_cmd_preserves_native_stdin(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    cmd = bin_dir / "agent-vault.cmd"
    shutil.copy2(PLUGIN / "bin" / "agent-vault.cmd", cmd)
    (bin_dir / "agent-vault.ps1").write_text(
        "$body = [Console]::In.ReadToEnd()\n"
        "[Console]::Out.Write(($args -join '|') + '::' + $body)\n",
        encoding="utf-8",
    )
    comspec = os.environ.get("COMSPEC", "cmd.exe")
    env = {
        **os.environ,
        "PATH": str(Path(comspec).parent),
    }
    result = subprocess.run(
        [comspec, "/d", "/s", "/c", str(cmd), "probe", "two words"],
        input="secret",
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout == "probe|two words::secret"
