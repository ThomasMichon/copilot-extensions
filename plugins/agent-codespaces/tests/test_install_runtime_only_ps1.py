"""Closed-wheel callers can retain ownership of service startup and task policy."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from agent_procutil._daemon_instance_identity import identity_state
from agent_procutil.owned_process import run_caller_owned

from . import test_installer_engine_adoption as installer
from .installer_test_support import installer_functional_timeout

PS5 = shutil.which("powershell.exe") if os.name == "nt" else None
pytestmark = [pytest.mark.timeout(installer_functional_timeout(60)), pytest.mark.installer_only]


@pytest.fixture(params=[
    pytest.param(
        installer.PWSH, id="pwsh",
        marks=pytest.mark.skipif(installer.PWSH is None, reason="pwsh unavailable"),
    ),
    pytest.param(
        PS5, id="powershell5",
        marks=pytest.mark.skipif(PS5 is None, reason="Windows PowerShell unavailable"),
    ),
])
def native_shell(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
    shell = request.param
    assert isinstance(shell, str)
    monkeypatch.setattr(installer, "PWSH", shell)
    return shell


def _environment(tmp_path: Path) -> dict[str, str]:
    environment = installer._env(tmp_path)
    root = str(tmp_path / "runtime")
    environment.update(
        AGENT_CODESPACES_HOME=root, AGENT_CODESPACES_INSTALL_DIR=root,
        COPILOT_EXTENSIONS_HUMAN_UTF8="0",
    )
    return environment


def _call(script: str, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    result: subprocess.CompletedProcess[str] = installer._run(
        "ps1", script, tmp_path, _environment(tmp_path),
    )
    return result


def test_item_timeout_outlives_the_native_subprocess_guard(
    tmp_path: Path, native_shell: str, request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    native_timeouts: list[int | float] = []

    def invoke(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        timeout = kwargs["timeout"]
        assert isinstance(timeout, (int, float))
        native_timeouts.append(timeout)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(installer, "run_caller_owned", invoke)
    assert _call("exit 0\n", tmp_path).returncode == 0
    assert len(native_timeouts) == 1
    marker = request.node.get_closest_marker("timeout")
    assert marker is not None
    item_timeout = marker.args[0]
    assert isinstance(item_timeout, (int, float))
    assert item_timeout > native_timeouts[0]


def _admission_entry(tmp_path: Path, continuation: str, *, lose_guard: bool = False) -> Path:
    source = (installer.PLUGIN / "scripts" / "install.ps1").read_text(encoding="utf-8")
    header = source.split("$NativeWheelSelection = $null", 1)[0]
    if lose_guard:
        guard = "if ($NoService -and ("
        assert header.count(guard) == 1
        header = header.replace(guard, "if ($false -and (", 1)
    entry = tmp_path / "admission.ps1"
    entry.write_text(header + "\n" + continuation + "\n", encoding="utf-8")
    return entry


@pytest.mark.parametrize("case", [
    "runtime-only", "default-start", "disabled-task", "disabled-config",
])
def test_owner_sync_keeps_caller_and_existing_holds(
    tmp_path: Path, native_shell: str, case: str,
) -> None:
    script = (
        installer._function("ps1", "Sync-ConnectionOwnerService")
        + f"\n$fixtureCase='{case}'\n"
        + f"$NoService=${str(case == 'runtime-only').lower()}\n"
        + r"""
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$script:starts=0
$script:policyReads=0
$script:configReads=0
$LocalBin=$PSScriptRoot
$InstallDir=$PSScriptRoot
$LinkPython='Invoke-FixturePython'
function Write-ServiceOk {}
function Write-ServiceWarn {}
function Get-ScheduledTask {}
function Get-WindowsScheduledTaskPolicy { $script:policyReads++; 'preserve' }
function Get-OwnerScheduledTask {
    if ($fixtureCase -eq 'disabled-task') {
        return [pscustomobject]@{State='Disabled';Settings=[pscustomobject]@{Enabled=$false}}
    }
    return $null
}
function Get-ConnectionOwnerConfig {
    $script:configReads++
    [pscustomobject]@{Known=$true;Enabled=($fixtureCase -ne 'disabled-config');Interval=15}
}
function Invoke-FixturePython { $script:starts++; $global:LASTEXITCODE=0 }
Sync-ConnectionOwnerService
$expectedStarts=if ($fixtureCase -eq 'default-start') {1} else {0}
if ($script:starts -ne $expectedStarts) { throw "Unexpected Owner starts: $script:starts" }
if ($NoService -and ($script:policyReads -or $script:configReads)) {
    throw 'Runtime-only construction queried service or task policy'
}
if ($fixtureCase -eq 'disabled-task' -and $script:configReads) {
    throw 'A disabled task did not retain its startup hold'
}
"""
    )
    result = _call(script, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("requested", [False, True])
def test_native_driver_forwards_runtime_only_without_changing_old_callers(
    tmp_path: Path, native_shell: str, requested: bool,
) -> None:
    entry = str(installer.PLUGIN / "scripts" / "install.ps1").replace("'", "''")
    script = f"$entry='{entry}'\n$global:fixtureRequestedHold=${str(requested).lower()}\n" + r"""
$ErrorActionPreference='Stop'
$tokens=$null
$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($entry,[ref]$tokens,[ref]$errors)
if ($errors.Count) { throw 'Installer did not parse' }
$driver=@($ast.EndBlock.Statements | Where-Object {
    $_ -is [Management.Automation.Language.IfStatementAst] -and
    $_.Extent.Text.Contains('Enter-NativeInstallerDriver')
})
if ($driver.Count -ne 1) { throw 'Native driver boundary is ambiguous' }
# Exercise the admitted forwarding body without relaxing outer test containment.
$forward=($driver[0].Clauses[0].Item2.Statements | ForEach-Object {$_.Extent.Text}) -join "`n"
$receiver=@'
function Enter-NativeInstallerDriver {
    param($Payload,$Root,$Plugin,$Action,[string[]]$Options,
          $WheelProfile,$WheelDirectory,$WheelPython,$WheelUv)
    $script:options=@($Options)
    $script:inputs=@($WheelProfile,$WheelDirectory,$WheelPython,$WheelUv)
    [pscustomobject]@{Participant=$true;ExitCode=0;Python='fixture-python';Arguments=@()}
}
'@
$check=@'
if (('NoService' -in $script:options) -ne $global:fixtureRequestedHold) {
    throw 'Runtime-only option was not preserved at the native driver boundary'
}
if (($script:inputs -join ',') -ne 'profile,wheels,python,uv') {
    throw 'Closed-wheel inputs changed at the native driver boundary'
}
'@
$invoke=Join-Path $PSScriptRoot 'receiver.ps1'
[IO.File]::WriteAllText($invoke,$ast.ParamBlock.Extent.Text+"`n"+$receiver+"`n"+$forward+"`n"+$check)
$arguments=@{Action='update';InstallDir=$PSScriptRoot;WheelProfile='profile';
             WheelDirectory='wheels';WheelPython='python';WheelUv='uv'}
if ($global:fixtureRequestedHold) { $arguments.NoService=$true }
& $invoke @arguments
"""
    result = _call(script, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("arguments", [
    "update -NoService",
    "status -NoService -WheelProfile profile -WheelDirectory wheels -WheelPython python -WheelUv uv",
    "update -NoService -DryRun -WheelProfile profile -WheelDirectory wheels -WheelPython python -WheelUv uv",
])
def test_runtime_only_rejects_incomplete_or_incompatible_requests_before_effects(
    tmp_path: Path, native_shell: str, arguments: str,
) -> None:
    entry = str(_admission_entry(
        tmp_path, "throw 'fixture: installation effects blocked'",
    )).replace("'", "''")
    root = tmp_path / "runtime"
    quoted_root = str(root).replace("'", "''")
    script = (
        "$ErrorActionPreference='Stop'\n"
        + f"& '{entry}' {arguments} -InstallDir '{quoted_root}'\n"
    )
    result = _call(script, tmp_path)
    assert result.returncode != 0
    assert "requires complete closed wheels" in result.stdout + result.stderr
    assert "fixture: installation effects blocked" not in result.stdout + result.stderr
    assert not root.exists()


def test_refusal_guard_regression_cannot_reach_installation(
    tmp_path: Path, native_shell: str,
) -> None:
    entry = str(_admission_entry(
        tmp_path, "throw 'fixture: installation effects blocked'", lose_guard=True,
    )).replace("'", "''")
    root = str(tmp_path / "runtime").replace("'", "''")
    result = _call(
        f"$ErrorActionPreference='Stop'\n& '{entry}' update -NoService -InstallDir '{root}'\n",
        tmp_path,
    )
    assert result.returncode != 0
    assert "fixture: installation effects blocked" in result.stdout + result.stderr
    assert not (tmp_path / "runtime").exists()


@pytest.mark.parametrize("action", ["install", "update"])
def test_declared_runtime_only_capability_reaches_the_native_parser(
    tmp_path: Path, native_shell: str, action: str,
) -> None:
    descriptor = json.loads(
        (installer.PLUGIN / "scripts" / "installer-contract.json").read_text(encoding="utf-8"),
    )
    assert descriptor["schema"] == "agent-codespaces.native-installer" and descriptor["version"] == 1
    capability = descriptor["capabilities"]["runtime-only-v1"]
    assert action in capability["actions"]
    entry = str(_admission_entry(tmp_path, "[bool]$NoService | ConvertTo-Json")).replace("'", "''")
    options = " ".join(f"{name} fixture" for name in capability["required_options"])
    result = _call(
        f"& '{entry}' {action} {capability['entrypoints']['install.ps1']} {options}\n",
        tmp_path,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout.strip()) is True
    assert not (tmp_path / "runtime").exists()


def _run_participant(
    tmp_path: Path, environment: dict[str, str], *, payload: Path, runtime: Path,
    shell: str, action: str, wheels: list[str], requested: bool, lose_hold: bool,
) -> subprocess.CompletedProcess[str]:
    worker = tmp_path / "participant.py"
    worker.write_text(r"""
import importlib.util
import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agent_procutil._daemon_instance_identity import birth_identity, created_process_identity

settings = json.loads(sys.argv[1])
work = Path(settings["work"])
scripts = Path(settings["scripts"])
sys.path.insert(0, str(scripts))
spec = importlib.util.spec_from_file_location("_codespaces_reentry_driver", scripts / "installer_driver.py")
assert spec is not None and spec.loader is not None
driver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(driver)
from update_driver import core as owner
witness = importlib.import_module("update_driver.participant")
import install_source as inputs

@contextmanager
def closed_inputs(*_args):
    yield object()

wheels = settings["wheels"]
args = SimpleNamespace(
    payload=Path(settings["payload"]), root=Path(settings["runtime"]),
    plugin="agent-codespaces", shell=Path(settings["shell"]),
    action=settings["action"], options=["NoService"] if settings["requested"] else [],
    check_native=False, remove_broker=False,
    wheel_profile=Path(wheels[0]), wheel_directory=Path(wheels[1]),
    wheel_python=Path(wheels[2]), wheel_uv=Path(wheels[3]),
)
if settings["lose_hold"]:
    native_arguments = driver.native_arguments
    driver.native_arguments = lambda value: [
        argument for argument in native_arguments(value) if argument != "-NoService"
    ]

native_popen = driver.subprocess.Popen
worker_identity = [os.getpid(), birth_identity(os.getpid())]
def native_child(*arguments, **kwargs):
    process = native_popen(*arguments, **kwargs)
    identity = created_process_identity(process)
    assert identity is not None
    (work / "participant-identities.json").write_text(
        json.dumps([worker_identity, identity]), encoding="utf-8",
    )
    return process

driver.subprocess = SimpleNamespace(
    Popen=native_child, CREATE_NO_WINDOW=getattr(driver.subprocess, "CREATE_NO_WINDOW", 0),
)
with (
    patch.object(inputs, "wheel_inputs", closed_inputs),
    patch.object(inputs, "wheel_selection", lambda *_args: {"fixture": True}),
    patch.object(owner, "default_root", lambda: work / "execution"),
):
    request = owner.enqueue(
        owner.RequestSpec("engine"),
        runner=owner.TrustedRunner((sys.executable, "-c", "pass"), str(work)),
    )
    def execute(transaction):
        with patch.dict(os.environ, transaction.participant_env()):
            return driver.execute(args)

    assert owner.drain(executor=execute) == 0
    status = owner.status(request.id)
    assert status.state == "completed" and isinstance(status.exit_code, int)
    assert witness.ENVIRONMENT_KEY not in os.environ
    raise SystemExit(status.exit_code)
""", encoding="utf-8")
    settings = {
        "work": str(tmp_path), "scripts": str(installer.PLUGIN / "scripts"),
        "payload": str(payload), "runtime": str(runtime), "shell": shell,
        "action": action, "wheels": wheels, "requested": requested, "lose_hold": lose_hold,
    }
    result: subprocess.CompletedProcess[str] = run_caller_owned(
        [sys.executable, "-I", "-B", str(worker), json.dumps(settings)],
        env=environment, cwd=tmp_path, timeout=installer_functional_timeout(45),
        max_stdout_bytes=1024 * 1024, max_stderr_bytes=1024 * 1024,
    )
    return result


def _reentry(
    tmp_path: Path, shell: str, boundary: str, action: str,
    *, requested: bool = True, lose_hold: bool = False, stall_native: bool = False,
) -> tuple[int, dict[str, object]]:
    if os.environ.get("COPILOT_EXTENSIONS_TEST_CONTAINED") != "1":
        pytest.skip("Runtime-only re-entry requires containment from tools/run-plugin-tests.py")
    environment = _environment(tmp_path)
    assert environment.get("COPILOT_EXTENSIONS_TEST_CONTAINED") == "1"
    home = Path(environment["USERPROFILE"])
    home.mkdir(parents=True, exist_ok=True)
    payload = (
        home / ".copilot" / "installed-plugins" / "fixture" / "agent-codespaces"
        if boundary == "self-stage" else tmp_path / "payload"
    )
    scripts = payload / "scripts"
    scripts.mkdir(parents=True)
    (payload / "plugin.json").write_text('{"name":"agent-codespaces"}', encoding="utf-8")
    source = (installer.PLUGIN / "scripts" / "install.ps1").read_text(encoding="utf-8")
    parameters = source.split("[CmdletBinding()]", 1)[1].split("Set-StrictMode", 1)[0]
    stage_marker = "# === install-contract:v4 self-stage"
    staging = stage_marker + source.split(stage_marker, 1)[1].split(
        "# === end install-contract:v4 self-stage ===", 1,
    )[0]
    if lose_hold and boundary == "self-stage":
        forwarding = "foreach ($__selfStageK in $PSBoundParameters.Keys) {"
        assert forwarding in staging
        staging = staging.replace(
            forwarding, forwarding + "\nif ($__selfStageK -eq 'NoService') { continue }",
        )
    functions = "\n".join(installer._function("ps1", name) for name in (
        "Invoke-Install", "Invoke-Update", "Sync-ConnectionOwnerService",
    ))
    entry = scripts / "install.ps1"
    entry.write_text(
        "[CmdletBinding()]" + parameters
        + "\n$ErrorActionPreference='Stop'\nSet-StrictMode -Version Latest\n"
        + installer._function("ps1", "Copy-InstallerStageDirectory")
        + staging + "\n" + functions
        + f"\n$fixtureAllowDefault=${str(not requested).lower()}\n"
        + f"$fixtureStall=${str(stall_native).lower()}\n" + r"""
$script:events=[Collections.Generic.List[string]]::new()
$ServiceName='fixture'
$LocalBin=Join-Path $InstallDir 'commands'
$VenvPython='Invoke-FixturePython'
$LinkPython=$VenvPython
function Write-Step {}
function Write-ServiceOk {}
function Write-ServiceWarn {}
function Write-ServiceErr { throw $args[0] }
function Select-InstallRuntimeGeneration { $script:events.Add('select') }
function Deploy-Venv {
    $script:events.Add('venv')
    if ($fixtureStall) { Start-Sleep -Seconds 600 }
    return $true
}
function Deploy-Package { $script:events.Add('package'); return $true }
function Invoke-VersionedActivate { $script:events.Add('activate'); return $true }
function Deploy-VersionedRuntimeHelper { $script:events.Add('helpers') }
function Deploy-SelfProvisioningBinstub { $script:events.Add('bindings') }
function Invoke-FixturePython {
    $script:events.Add($(if ($args -contains 'config-migrate') {'migrate'} else {'owner-start'}))
    $global:LASTEXITCODE=0
}
function Write-CodespacesDeployManifest { $script:events.Add('manifest') }
function Test-CodespacesRuntime { $script:events.Add('verify'); return $true }
function Get-WindowsScheduledTaskPolicy {
    $script:events.Add('service-policy')
    if ($fixtureAllowDefault) { return 'preserve' }
    throw 'Unexpected service/task effect'
}
function Get-ScheduledTask {}
function Get-OwnerScheduledTask { $script:events.Add('task-read'); return $null }
function Get-ConnectionOwnerConfig {
    if (-not $fixtureAllowDefault) { throw 'Unexpected Owner query' }
    $script:events.Add('config-read')
    return @{Known=$true;Enabled=$true;Interval=15}
}
try {
    if ($Action -eq 'install') { Invoke-Install } else { Invoke-Update }
} finally {
    @{
        action=$Action; no_service=[bool]$NoService
        staged_from=$env:COPILOT_PLUGIN_STAGED_FROM
        script_root=$PSScriptRoot; events=@($script:events)
        wheels=@($WheelProfile,$WheelDirectory,$WheelPython,$WheelUv)
    } | ConvertTo-Json -Depth 4 |
        Set-Content -Encoding UTF8 -LiteralPath (Join-Path $env:USERPROFILE 'reentry.json')
}
""",
        encoding="utf-8",
    )
    runtime = tmp_path / "runtime"
    if action == "update":
        runtime.mkdir()
    wheels = [str(tmp_path / "profile"), str(tmp_path / "wheels"),
              sys.executable, str(tmp_path / "uv.exe")]
    if boundary == "self-stage":
        arguments = {
            "Action": action, "InstallDir": str(runtime), "WheelProfile": wheels[0],
            "WheelDirectory": wheels[1], "WheelPython": wheels[2], "WheelUv": wheels[3],
        }
        hold = " -NoService" if requested else ""
        invocation = "\n".join(
            f"${name}='{value.replace(chr(39), chr(39) * 2)}'"
            for name, value in {"entry": str(entry), **arguments}.items()
        ) + f"\n& $entry -Action $Action -InstallDir $InstallDir{hold} " \
            "-WheelProfile $WheelProfile -WheelDirectory $WheelDirectory " \
            "-WheelPython $WheelPython -WheelUv $WheelUv\nexit $LASTEXITCODE\n"
        result = installer._run("ps1", invocation, tmp_path, environment)
    else:
        result = _run_participant(
            tmp_path, environment, payload=payload, runtime=runtime, shell=shell,
            action=action, wheels=wheels, requested=requested, lose_hold=lose_hold,
        )
    if result.returncode:
        print(result.stdout, end="")
        print(result.stderr, end="", file=sys.stderr)
    report_path = home / "reentry.json"
    assert report_path.is_file(), result.stdout + result.stderr
    report: dict[str, object] = json.loads(
        report_path.read_text(encoding="utf-8-sig"),
    )
    assert report["action"] == action and report["wheels"] == wheels
    staged_from = report["staged_from"]
    if boundary == "self-stage":
        assert staged_from == str(payload)
        assert report["script_root"] != str(scripts)
    else:
        assert not staged_from
    return result.returncode, report


@pytest.mark.parametrize("boundary", ["participant", "self-stage"])
@pytest.mark.parametrize("action", ["install", "update"])
@pytest.mark.parametrize("requested", [False, True])
def test_runtime_only_reentry(
    tmp_path: Path, native_shell: str, boundary: str, action: str,
    requested: bool,
) -> None:
    code, report = _reentry(
        tmp_path, native_shell, boundary, action, requested=requested,
    )
    assert code == 0, report
    assert report["no_service"] is requested
    expected = ["select", "venv", "package", "activate", "helpers", "bindings", "migrate", "manifest"]
    expected += ["verify"] if action == "install" else []
    expected += [] if requested else ["service-policy", "task-read", "config-read", "owner-start"]
    assert report["events"] == expected


def test_participant_reentry_uses_the_bounded_native_runner(
    tmp_path: Path, native_shell: str, request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ObservedParticipant(RuntimeError):
        pass

    def invoke(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert command[0] == sys.executable
        timeout = kwargs["timeout"]
        marker = request.node.get_closest_marker("timeout")
        assert marker is not None
        assert isinstance(timeout, (int, float))
        assert marker.args[0] > timeout
        assert kwargs["max_stdout_bytes"] == kwargs["max_stderr_bytes"] == 1024 * 1024
        raise ObservedParticipant

    monkeypatch.setattr(sys.modules[__name__], "run_caller_owned", invoke)
    with pytest.raises(ObservedParticipant):
        _reentry(tmp_path, native_shell, "participant", "update")


def test_participant_timeout_reaps_the_worker_and_native_child(
    tmp_path: Path, native_shell: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sys.modules[__name__], "installer_functional_timeout", lambda _seconds: 10,
    )
    with pytest.raises(subprocess.TimeoutExpired):
        _reentry(tmp_path, native_shell, "participant", "update", stall_native=True)
    identities = json.loads((tmp_path / "participant-identities.json").read_text(encoding="utf-8"))
    assert len(identities) == 2
    for pid, birth in identities:
        assert identity_state(pid, birth) in {"dead", "reused"}


@pytest.mark.parametrize("boundary", ["participant", "self-stage"])
def test_reentry_detects_a_lost_runtime_only_flag(
    tmp_path: Path, native_shell: str, boundary: str,
) -> None:
    code, report = _reentry(
        tmp_path, native_shell, boundary, "update", lose_hold=True,
    )
    assert code != 0
    assert report["no_service"] is False
    events = report["events"]
    assert isinstance(events, list) and "service-policy" in events
