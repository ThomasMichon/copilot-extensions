"""Portable protocol controls and bounded native worker checks; never request UAC."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
SKILL = PLUGIN / "skills" / "requesting-windows-elevation"
WORKER = SKILL / "scripts" / "elevation-worker.ps1"
INVOKE = SKILL / "scripts" / "invoke-windows-elevation.ps1"
PWSH = shutil.which("pwsh")
RUN_ID = "a" * 32
NATIVE = SKILL / "assets" / "ElevationNative.cs"


def quote(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def desktop_binding_verified(baseline, observer, target) -> bool:
    return bool(target and baseline and observer and baseline.casefold() == observer.casefold() == target.casefold())


def run_ps(*arguments: str) -> subprocess.CompletedProcess[str]:
    assert PWSH
    return subprocess.run(
        [PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", *arguments],
        capture_output=True, text=True, encoding="utf-8", timeout=25,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


def test_owned_projection_and_discoverable_skill():
    declarations = json.loads((PLUGIN / "instruction-projections.json").read_text())
    projection, = [row for row in declarations["projections"] if row["id"] == "elevation-fallback"]
    assert projection["deliveryMode"] == "selector"
    assert projection["template"] == "instructions/elevation-fallback.instructions.md"
    assert projection["destination"].endswith("/agent-conduct-guidance/elevation-fallback.instructions.md")
    content = (PLUGIN / projection["template"]).read_text(encoding="utf-8")
    assert "requesting-windows-elevation" in content
    assert "agent-conduct-guidance@" in content
    assert len(content.encode()) < 2048
    skill = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    assert "name: requesting-windows-elevation\n" in skill
    assert "RunLevel=Highest" in skill and "cannot register" in skill
    assert "not administrator success" in skill.replace("**", "")
    assert "wait" in skill.lower() and "1223" in skill


@pytest.mark.parametrize(
    ("baseline", "observer", "target", "bound"),
    [
        ("2|WinSta0|Default", "2|WinSta0|Default", "2|WinSta0|Default", True),
        ("2|winsta0|default", "2|WinSta0|Default", "2|WinSta0|Default", True),
        (None, "2|WinSta0|Default", "2|WinSta0|Default", False),
        ("2|WinSta0|Default", None, "2|WinSta0|Default", False),
        ("2|WinSta0|Default", "2|WinSta0|Default", None, False),
        ("0|WinSta0|Default", "0|WinSta0|Default", "2|WinSta0|Default", False),
        ("2|WinSta0|Other", "2|WinSta0|Other", "2|WinSta0|Default", False),
        ("2|WinSta0|Default", "2|WinSta0|Other", "2|WinSta0|Default", False),
    ],
)
def test_window_observation_requires_actual_desktop_binding(baseline, observer, target, bound):
    assert desktop_binding_verified(baseline, observer, target) is bound


@pytest.mark.skipif(not PWSH, reason="PowerShell 7 is unavailable")
def test_discovery_excludes_inaccessible_entries_but_requires_positive_owner_match():
    control = r"""
public static class ElevationDiscoveryControl {
    public static int[] Select() {
        return AgentConduct.ElevationNative.OwnedInteractiveSessions(
            new [] { 1, 2, 3, 4 }, "owner", id => {
                if (id == 1) throw new System.ComponentModel.Win32Exception(5);
                if (id == 2) throw new System.Security.Principal.IdentityNotMappedException();
                return id == 3 ? "owner" : "other";
            });
    }
    public static int Unexpected() {
        try {
            AgentConduct.ElevationNative.OwnedInteractiveSessions(
                new [] { 1 }, "owner", id => { throw new System.ComponentModel.Win32Exception(87); });
        } catch (System.ComponentModel.Win32Exception error) { return error.NativeErrorCode; }
        return 0;
    }
}
"""
    result = run_ps(
        "-Command",
        f"$source = [IO.File]::ReadAllText({quote(NATIVE)}) + {quote(control)}; "
        "Add-Type -TypeDefinition $source; "
        "@{selected=@([ElevationDiscoveryControl]::Select());unexpected=[ElevationDiscoveryControl]::Unexpected()} "
        "| ConvertTo-Json -Compress",
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"selected": [3], "unexpected": 87}
    assert "inaccessible desktop session" in result.stderr
    assert "no resolvable owner SID" in result.stderr


@pytest.mark.skipif(not PWSH, reason="PowerShell 7 is unavailable")
def test_same_account_capability_uses_linked_token_not_filtered_group_membership():
    result = run_ps(
        "-Command",
        f"Add-Type -Path {quote(NATIVE)}; "
        "@([AgentConduct.ElevationNative]::SupportsSameAccount(3,$false),"
        "[AgentConduct.ElevationNative]::SupportsSameAccount(1,$false),"
        "[AgentConduct.ElevationNative]::SupportsSameAccount(2,$true),"
        "[AgentConduct.ElevationNative]::SupportsSameAccount(1,$true)) | ConvertTo-Json -Compress",
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [True, False, True, True]


@pytest.mark.skipif(os.name != "nt" or not PWSH, reason="Windows same-account preflight")
def test_unsupported_credential_elevation_stops_before_staging_or_prompt(tmp_path):
    fake = "namespace AgentConduct { public static class ElevationNative { public static bool CanElevateSameAccount() { return false; } } }"
    log = tmp_path / "not-created.log"
    result = run_ps(
        "-Command",
        f"Add-Type -TypeDefinition {quote(fake)}; "
        f"try {{ & {quote(INVOKE)} -ScriptPath {quote(SKILL / 'assets' / 'probe.ps1')} "
        f"-LogPath {quote(log)} -ConsentConfirmed; exit $LASTEXITCODE }} "
        "catch { [Console]::Error.WriteLine($_.Exception.Message); exit 1 }",
    )
    assert result.returncode != 0
    assert "alternate-account credentials are unsupported" in result.stderr
    assert not log.exists()


@pytest.mark.skipif(not PWSH, reason="PowerShell 7 is unavailable")
@pytest.mark.parametrize(
    ("state", "code", "elevated"),
    [("success", 0, True), ("failed", 7, True), ("canceled", 1223, False), ("probe", 0, False)],
)
def test_terminal_round_trip_uses_exact_log_and_actual_result(tmp_path, state, code, elevated):
    log = tmp_path / "quoted ' log.log"
    request = {"runId": RUN_ID, "logPath": str(log)}
    command = (
        f". {quote(WORKER)} -RequestPath unused -LibraryOnly; "
        f"$r = {quote(json.dumps(request))} | ConvertFrom-Json; "
        f"Write-ElevationTerminal $r {quote(state)} {code} ${str(elevated).lower()}; "
        f"Read-ElevationTerminal {quote(log)} {quote(RUN_ID)} | ConvertTo-Json -Compress"
    )
    result = run_ps("-Command", command)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "schema": 1, "runId": RUN_ID, "state": state, "exitCode": code, "elevated": elevated,
    }
    assert log.read_text(encoding="utf-8").startswith("AGENT_ELEVATION_END ")


@pytest.mark.skipif(not PWSH, reason="PowerShell 7 is unavailable")
@pytest.mark.parametrize(
    "value",
    [
        {"schema": 1, "runId": "b" * 32, "state": "success", "exitCode": 0, "elevated": True},
        {"schema": 1, "runId": RUN_ID, "state": "success", "exitCode": 7, "elevated": True},
        {"schema": 1, "runId": RUN_ID, "state": "success", "exitCode": 0, "elevated": False},
        {"schema": 1, "runId": RUN_ID, "state": "success", "exitCode": True, "elevated": True},
        {"schema": 1, "runId": RUN_ID, "state": "unknown", "exitCode": 0, "elevated": True},
        {"schema": 1, "runId": RUN_ID, "state": "failed", "exitCode": 0, "elevated": True},
        {"schema": 1, "runId": RUN_ID, "state": "canceled", "exitCode": 0, "elevated": False},
        {"schema": 1, "runId": RUN_ID, "state": "probe", "exitCode": 7, "elevated": False},
    ],
)
def test_terminal_refuses_stale_or_success_shaped_failures(tmp_path, value):
    log = tmp_path / "operation.log"
    log.write_text("AGENT_ELEVATION_END " + json.dumps(value) + "\n", encoding="utf-8")
    result = run_ps(
        "-Command",
        f". {quote(WORKER)} -RequestPath unused -LibraryOnly; "
        f"Read-ElevationTerminal {quote(log)} {quote(RUN_ID)}",
    )
    assert result.returncode != 0


@pytest.mark.skipif(not PWSH, reason="PowerShell 7 is unavailable")
def test_incomplete_terminal_is_not_completion(tmp_path):
    log = tmp_path / "operation.log"
    log.write_text('AGENT_ELEVATION_END {"schema":', encoding="utf-8")
    result = run_ps(
        "-Command",
        f". {quote(WORKER)} -RequestPath unused -LibraryOnly; "
        f"$r = Read-ElevationTerminal {quote(log)} {quote(RUN_ID)}; "
        "if ($null -ne $r) { throw 'Incomplete marker accepted' }",
    )
    assert result.returncode == 0, result.stderr


def native_request(tmp_path: Path, script: str, *, seconds: int = 10) -> tuple[Path, Path]:
    import ctypes
    from ctypes import wintypes

    session = wintypes.DWORD()
    assert ctypes.windll.kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(session))
    payload = tmp_path / "payload ' spaced.ps1"
    payload.write_text(script, encoding="utf-8")
    log = tmp_path / "operation.log"
    log.write_text("begin\n", encoding="utf-8")
    request = tmp_path / "request.json"
    request.write_text(json.dumps({
        "schema": 1, "runId": RUN_ID, "scriptPath": str(payload),
        "scriptHash": hashlib.sha256(payload.read_bytes()).hexdigest().upper(),
        "scriptArguments": ['spaces "quotes" ; $literal \u00e9'],
        "workingDirectory": str(tmp_path), "logPath": str(log),
        "sessionId": session.value, "shellPath": PWSH,
        "timeoutSeconds": seconds, "interactiveProbe": True,
        "deadline": (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat(),
        "admissionDeadline": (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat(),
    }), encoding="utf-8")
    return request, log


@pytest.mark.skipif(os.name != "nt" or not PWSH, reason="Native Windows PowerShell worker")
@pytest.mark.parametrize("code", [0, 7])
def test_native_worker_preserves_both_streams_arguments_and_exit(tmp_path, code):
    request, log = native_request(tmp_path, (
        "param([string]$Value)\n"
        "Write-Output ($Value | ConvertTo-Json -Compress)\n"
        "[Console]::Error.WriteLine('stderr-native-control')\n"
        f"exit {code}\n"
    ))
    result = run_ps("-File", str(WORKER), "-RequestPath", str(request), "-Worker")
    assert result.returncode == code, result.stderr
    lines = log.read_text(encoding="utf-8").splitlines()
    assert json.dumps('spaces "quotes" ; $literal \u00e9', ensure_ascii=False) in lines
    assert "stderr-native-control" in lines
    terminal = json.loads(lines[-1].removeprefix("AGENT_ELEVATION_END "))
    assert terminal["exitCode"] == code
    assert terminal["state"] == ("probe" if code == 0 else "failed")


@pytest.mark.skipif(os.name != "nt" or not PWSH, reason="Native Windows PowerShell worker")
def test_native_worker_deadline_is_not_a_success_marker(tmp_path):
    request, log = native_request(tmp_path, "Start-Sleep -Seconds 60\n", seconds=2)
    result = run_ps("-File", str(WORKER), "-RequestPath", str(request), "-Worker")
    assert result.returncode == 124, result.stderr
    text = log.read_text(encoding="utf-8")
    assert "deadline expired" in text
    assert "AGENT_ELEVATION_END " not in text


@pytest.mark.skipif(os.name != "nt" or not PWSH, reason="Native Windows PowerShell worker")
def test_native_worker_refuses_script_drift_before_start(tmp_path):
    request, log = native_request(tmp_path, "Write-Output 'should-not-run'\n")
    data = json.loads(request.read_text())
    Path(data["scriptPath"]).write_text("Write-Output 'changed'\n", encoding="utf-8")
    result = run_ps("-File", str(WORKER), "-RequestPath", str(request), "-Worker")
    assert result.returncode != 0
    assert not (tmp_path / "worker-started.json").exists()
    assert "changed before execution" in log.read_text(encoding="utf-8")


@pytest.mark.skipif(os.name != "nt" or not PWSH, reason="Windows consent preflight")
def test_normal_invocation_requires_prior_explicit_consent(tmp_path):
    result = run_ps(
        "-File", str(INVOKE), "-ScriptPath", str(SKILL / "assets" / "probe.ps1"),
        "-LogPath", str(tmp_path / "not-created.log"),
    )
    assert result.returncode != 0
    assert "explicit operation-specific consent" in result.stderr
    assert not (tmp_path / "not-created.log").exists()


@pytest.mark.skipif(os.name != "nt" or not PWSH, reason="Windows broker failure controls")
@pytest.mark.parametrize("canceled", [False, True])
def test_broker_reports_cancellation_and_missing_worker_without_requesting_uac(tmp_path, canceled):
    request, log = native_request(tmp_path, "exit 0\n")
    data = json.loads(request.read_text())
    data["interactiveProbe"] = False
    request.write_text(json.dumps(data), encoding="utf-8")
    behavior = (
        "throw [ComponentModel.Win32Exception]::new(1223)"
        if canceled else "[pscustomobject]@{ExitCode = 0}"
    )
    result = run_ps(
        "-Command",
        f"function Start-Process {{ {behavior} }}; "
        f"& {quote(WORKER)} -RequestPath {quote(request)}; exit $LASTEXITCODE",
    )
    expected = 1223 if canceled else 1
    assert result.returncode == expected, result.stdout + result.stderr
    terminal = json.loads(log.read_text(encoding="utf-8").splitlines()[-1].removeprefix("AGENT_ELEVATION_END "))
    assert terminal["exitCode"] == expected
    assert terminal["state"] == ("canceled" if canceled else "failed")
    assert terminal["elevated"] is False
    assert not (tmp_path / "worker-started.json").exists()


@pytest.mark.skipif(os.name != "nt" or not PWSH, reason="Windows consent preflight")
def test_interactive_probe_does_not_authorize_another_script(tmp_path):
    script = tmp_path / "not-the-probe.ps1"
    script.write_text("throw 'must not execute'\n", encoding="utf-8")
    result = run_ps(
        "-File", str(INVOKE), "-ScriptPath", str(script),
        "-LogPath", str(tmp_path / "not-created.log"), "-InteractiveProbe",
    )
    assert result.returncode != 0
    assert "only the bundled harmless probe" in result.stderr
    assert not (tmp_path / "not-created.log").exists()


@pytest.mark.skipif(not PWSH, reason="PowerShell 7 is unavailable")
def test_completion_observes_task_stop_before_reading_its_terminal(tmp_path):
    log = tmp_path / "completion-race.log"
    log.write_text("begin\n", encoding="utf-8")
    command = (
        f". {quote(WORKER)} -RequestPath unused -LibraryOnly; "
        f"$script:receipt = @{{runId={quote(RUN_ID)};logPath={quote(log)}}}; "
        "$task = [pscustomobject]@{State=4;LastTaskResult=267009}; "
        "$task | Add-Member ScriptMethod GetInstances { param($flags) "
        "Write-ElevationTerminal $script:receipt success 0 $true; "
        "$this.State=3; $this.LastTaskResult=0; return [pscustomobject]@{Count=0} }; "
        f"Get-ElevationTaskCompletion $task {quote(log)} {quote(RUN_ID)} | ConvertTo-Json -Compress"
    )
    result = run_ps("-Command", command)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["state"] == "success"


@pytest.mark.skipif(not PWSH, reason="PowerShell 7 is unavailable")
@pytest.mark.parametrize(("state", "last"), [(4, 267009), (3, 267011), (2, 267009)])
def test_startup_and_running_states_are_not_missing_completion(tmp_path, state, last):
    log = tmp_path / "starting.log"
    log.write_text("begin\n", encoding="utf-8")
    result = run_ps(
        "-Command",
        f". {quote(WORKER)} -RequestPath unused -LibraryOnly; "
        f"$task = [pscustomobject]@{{State={state};LastTaskResult={last}}}; "
        "$task | Add-Member ScriptMethod GetInstances { param($flags) [pscustomobject]@{Count=0} }; "
        f"$r = Get-ElevationTaskCompletion $task {quote(log)} {quote(RUN_ID)}; "
        "if ($null -ne $r) { throw 'Early completion accepted' }",
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(not PWSH, reason="PowerShell 7 is unavailable")
@pytest.mark.parametrize("line", [
    "AGENT_ELEVATION_END " + json.dumps(
        {"schema": 1, "runId": "b" * 32, "state": "success", "exitCode": 0, "elevated": True}
    ),
    'AGENT_ELEVATION_END {"schema":',
])
def test_running_task_output_is_not_parsed_as_a_terminal_result(tmp_path, line):
    log = tmp_path / "running-output.log"
    log.write_text(line + "\n", encoding="utf-8")
    result = run_ps(
        "-Command",
        f". {quote(WORKER)} -RequestPath unused -LibraryOnly; "
        "$task=[pscustomobject]@{State=4;LastTaskResult=267009}; "
        "$task | Add-Member ScriptMethod GetInstances { param($flags) [pscustomobject]@{Count=1} }; "
        f"$r=Get-ElevationTaskCompletion $task {quote(log)} {quote(RUN_ID)}; "
        "if ($null -ne $r) { throw 'Running output was accepted as completion' }",
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(os.name != "nt" or not PWSH, reason="Native Windows worker admission")
def test_expired_request_cannot_start_payload_after_late_consent(tmp_path):
    request, log = native_request(tmp_path, "Write-Output 'must-not-run'\n")
    data = json.loads(request.read_text())
    data["deadline"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    request.write_text(json.dumps(data), encoding="utf-8")
    result = run_ps("-File", str(WORKER), "-RequestPath", str(request), "-Worker")
    assert result.returncode != 0
    text = log.read_text(encoding="utf-8")
    assert "deadline expired before payload admission" in text
    assert "must-not-run" not in text
    assert not (tmp_path / "worker-started.json").exists()


@pytest.mark.skipif(os.name != "nt" or not PWSH, reason="Windows resumed-wait controls")
def test_missing_task_without_terminal_is_not_completed(tmp_path):
    request = tmp_path / "missing-request.json"
    log = tmp_path / "missing.log"
    log.write_text("begin\n", encoding="utf-8")
    command = (
        "$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value; "
        "$id = [guid]::NewGuid().ToString('N'); "
        f"@{{schema=1;ownerSid=$sid;runId=$id;taskName=('AgentConduct-Elevation-'+$id);logPath={quote(log)}}} "
        f"| ConvertTo-Json | Set-Content -LiteralPath {quote(request)} -Encoding utf8; "
        f"& {quote(INVOKE)} -WaitFor {quote(request)}; exit $LASTEXITCODE"
    )
    result = run_ps("-Command", command)
    assert result.returncode != 0
    assert "missing without its matching terminal result" in result.stderr


@pytest.mark.skipif(os.name != "nt" or not PWSH, reason="Native Windows consent admission")
def test_expired_consent_cannot_borrow_payload_execution_budget(tmp_path):
    request, log = native_request(tmp_path, "Write-Output 'must-not-run'\n")
    data = json.loads(request.read_text())
    data["admissionDeadline"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    request.write_text(json.dumps(data), encoding="utf-8")
    result = run_ps("-File", str(WORKER), "-RequestPath", str(request), "-Worker")
    assert result.returncode != 0
    assert "Consent admission deadline expired" in log.read_text(encoding="utf-8")
    assert "must-not-run" not in log.read_text(encoding="utf-8")
    assert not (tmp_path / "worker-started.json").exists()


def wait_file(path: Path, process: subprocess.Popen, seconds: float = 12) -> None:
    deadline = time.monotonic() + seconds
    while not path.exists():
        assert process.poll() is None, "Native worker exited before its fixture signal"
        assert time.monotonic() < deadline, "Native worker did not reach its fixture signal"
        time.sleep(.02)


def start_worker(*arguments: str) -> subprocess.Popen:
    return subprocess.Popen(
        [PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", *arguments],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", creationflags=subprocess.CREATE_NO_WINDOW,
    )


def settle_worker(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.kill()
    process.communicate(timeout=5)


@pytest.mark.skipif(os.name != "nt" or not PWSH, reason="Native Windows script read sharing")
def test_verified_script_cannot_be_saved_or_replaced_before_execution(tmp_path):
    request, log = native_request(tmp_path, "Write-Output 'locked-script-ran'\nexit 0\n")
    payload = Path(json.loads(request.read_text())["scriptPath"])
    verified = tmp_path / "verified"
    release = tmp_path / "release"
    command = (
        "function Get-FileHash { param($LiteralPath,$InputStream,$Algorithm) "
        "$hash = if ($null -ne $InputStream) { "
        "Microsoft.PowerShell.Utility\\Get-FileHash -InputStream $InputStream -Algorithm $Algorithm "
        "} else { Microsoft.PowerShell.Utility\\Get-FileHash -LiteralPath $LiteralPath -Algorithm $Algorithm }; "
        f"[IO.File]::WriteAllText({quote(verified)}, 'ready'); "
        f"while (-not [IO.File]::Exists({quote(release)})) {{ Start-Sleep -Milliseconds 20 }}; "
        "return $hash }; "
        f"& {quote(WORKER)} -RequestPath {quote(request)} -Worker; exit $LASTEXITCODE"
    )
    process = start_worker("-Command", command)
    try:
        wait_file(verified, process)
        with pytest.raises(PermissionError):
            payload.write_text("Write-Output 'changed'\n", encoding="utf-8")
        replacement = tmp_path / "replacement.ps1"
        replacement.write_text("Write-Output 'replacement'\n", encoding="utf-8")
        with pytest.raises(PermissionError):
            os.replace(replacement, payload)
        release.write_text("go", encoding="utf-8")
        output, error = process.communicate(timeout=20)
        assert process.returncode == 0, output + error
        assert "locked-script-ran" in log.read_text(encoding="utf-8")
        payload.write_text("after-completion\n", encoding="utf-8")
        os.replace(replacement, payload)
    finally:
        settle_worker(process)


def descendant_fixture(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    child_pid = tmp_path / "child.pid"
    release = tmp_path / "release-parent"
    child = tmp_path / "child.ps1"
    child.write_text(
        f"[IO.File]::WriteAllText({quote(str(child_pid) + '.tmp')}, [string]$PID)\n"
        f"[IO.File]::Move({quote(str(child_pid) + '.tmp')}, {quote(child_pid)})\n"
        "Start-Sleep -Seconds 60\n",
        encoding="utf-8",
    )
    script = (
        f"$s = [Diagnostics.ProcessStartInfo]::new({quote(PWSH)})\n"
        "$s.UseShellExecute=$false; $s.CreateNoWindow=$true\n"
        f"foreach ($a in @('-NoLogo','-NoProfile','-NonInteractive','-File',{quote(child)})) "
        "{ $s.ArgumentList.Add($a) }\n"
        "$p = [Diagnostics.Process]::Start($s)\n"
        f"while (-not [IO.File]::Exists({quote(release)})) {{ Start-Sleep -Milliseconds 20 }}\n"
        "exit 0\n"
    )
    request, log = native_request(tmp_path, script, seconds=18)
    return request, log, child_pid, release


class ChildHandle:
    def __init__(self, pid: int):
        import ctypes
        from ctypes import wintypes

        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel.OpenProcess.restype = wintypes.HANDLE
        self.kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.kernel.WaitForSingleObject.restype = wintypes.DWORD
        self.kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.handle = self.kernel.OpenProcess(0x100000 | 0x1000 | 1, False, pid)
        assert self.handle, "Could not retain the exact fixture child's process handle"
        assert self.kernel.WaitForSingleObject(self.handle, 0) == 258

    def assert_stopped(self):
        assert self.kernel.WaitForSingleObject(self.handle, 5000) == 0, "Owned descendant survived"

    def close(self):
        if self.kernel.WaitForSingleObject(self.handle, 0) == 258:
            assert self.kernel.TerminateProcess(self.handle, 99)
            assert self.kernel.WaitForSingleObject(self.handle, 5000) == 0
        assert self.kernel.CloseHandle(self.handle)


@pytest.mark.skipif(os.name != "nt" or not PWSH, reason="Native Windows descendant custody")
@pytest.mark.parametrize("kill_worker", [False, True])
def test_native_job_retires_descendants_on_leftover_or_worker_exit(tmp_path, kill_worker):
    request, log, pid_file, release = descendant_fixture(tmp_path)
    process = start_worker("-File", str(WORKER), "-RequestPath", str(request), "-Worker")
    child = None
    try:
        wait_file(pid_file, process)
        child = ChildHandle(int(pid_file.read_text(encoding="utf-8")))
        if kill_worker:
            process.kill()
        else:
            release.write_text("go", encoding="utf-8")
        output, error = process.communicate(timeout=20)
        assert process.returncode == (1 if kill_worker else 125), output + error
        child.assert_stopped()
        assert "AGENT_ELEVATION_END " not in log.read_text(encoding="utf-8")
        if not kill_worker:
            assert "left descendants" in log.read_text(encoding="utf-8")
    finally:
        settle_worker(process)
        if child is not None:
            child.close()
