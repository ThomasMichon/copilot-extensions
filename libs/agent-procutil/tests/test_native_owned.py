"""Native prerequisite writers are admitted suspended and retained to Job settlement."""

import base64
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

import agent_procutil as pu

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows native prerequisite custody")
HELPER = Path(__file__).resolve().parents[1] / "src" / "agent_procutil" / "native_owned.ps1"


@pytest.mark.parametrize("mode", ["success", "overflow", "timeout"])
def test_transient_native_query_bounds_capture_and_retires_its_helper(tmp_path, native_shell, mode):
    marker = tmp_path / "query.pid"
    release = tmp_path / "query.release"
    program = (
        "import os,pathlib,sys,time;"
        f"pathlib.Path({str(marker)!r}).write_text(str(os.getpid()))\n"
        f"while not pathlib.Path({str(release)!r}).exists(): time.sleep(.01)\n"
        + {
            "success": "print('finite response');sys.exit(7)",
            "overflow": "sys.stdout.write('x'*100000);sys.stdout.flush();time.sleep(30)",
            "timeout": "time.sleep(30)",
        }[mode]
    )
    body = (
        f"$ErrorActionPreference='Stop'; . {quoted(HELPER.with_name('daemon_instances.ps1'))}; "
        "$environment=@{}; foreach($entry in [Environment]::GetEnvironmentVariables().GetEnumerator())"
        "{$environment[[string]$entry.Key]=[string]$entry.Value}; "
        "$clock=[Diagnostics.Stopwatch]::StartNew(); "
        "try { "
        "$result=Invoke-DaemonInstanceRequest -Transient @{command='run-query';parameters=@{"
        f"executable={quoted(sys.executable)};arguments=@('-I','-B','-c',{quoted(program)});"
        f"cwd={quoted(tmp_path)};environment=$environment;timeout_ms=5000;limit=4096}}}}; "
        "@{result=$result;error=$null;elapsed=$clock.ElapsedMilliseconds;"
        "globalHelper=($null -ne [AppDomain]::CurrentDomain.GetData('copilot-daemon-instance-shell-v1'))}"
        " | ConvertTo-Json -Compress "
        "}catch{"
        "@{result=$null;error=$_.Exception.Message;elapsed=$clock.ElapsedMilliseconds;"
        "globalHelper=($null -ne [AppDomain]::CurrentDomain.GetData('copilot-daemon-instance-shell-v1'))}"
        "|ConvertTo-Json -Compress}"
    )
    process, custody = pu.spawn_sync_in_kill_on_close_job(
        [str(native_shell), "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded(body)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **pu.no_window_kwargs(),
    )
    target = None
    try:
        deadline = time.monotonic() + 15
        while not marker.exists() or not marker.read_text().isdigit():
            if process.poll() is not None:
                output, error = process.communicate()
                pytest.fail(output + error)
            assert time.monotonic() < deadline, "query did not reach its native observation gate"
            time.sleep(.02)
        from agent_procutil.daemon_broker import WindowsDaemonProcess

        target = WindowsDaemonProcess(int(marker.read_text()), ["query-target"])
        assert target.poll() is None
        release.touch()
        stdout, stderr = process.communicate(timeout=35)
        assert process.returncode == 0, stderr
        target.wait(timeout=2)
    finally:
        if custody is not None:
            custody.close()
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        if target is not None:
            try:
                target.wait(timeout=2)
            finally:
                target.close()
        for stream in (process.stdout, process.stderr):
            stream.close()
    outcome = json.loads(stdout)
    assert not outcome["globalHelper"], outcome
    assert outcome["elapsed"] < 30000
    assert marker.exists(), outcome
    if mode == "success":
        assert outcome["result"] == {"returncode": 7, "stdout": "finite response\n", "stderr": ""}
        assert outcome["error"] is None
    else:
        assert outcome["result"] is None and outcome["error"], outcome


def quoted(path):
    return "'" + str(path).replace("'", "''") + "'"


def encoded(text):
    return base64.b64encode(text.encode("utf-16le")).decode("ascii")


@pytest.fixture(params=["powershell", "pwsh"])
def native_shell(request):
    if request.param == "powershell":
        return Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    shell = shutil.which("pwsh")
    assert shell, "the admitted matrix requires native PowerShell 7"
    return Path(shell)


@pytest.mark.parametrize("timeout", [False, True])
def test_native_owned_waits_for_descendants_and_attests_timeout_cleanup(tmp_path, timeout, native_shell):
    shell = native_shell
    marker = tmp_path / "descendant"
    delay = 30000 if timeout else 500
    grandchild = encoded(
        f"Start-Sleep -Milliseconds {delay}; [IO.File]::WriteAllText({quoted(marker)}, 'settled')"
    )
    child = encoded(
        "$s = New-Object Diagnostics.ProcessStartInfo; "
        f"$s.FileName={quoted(shell)}; "
        f"$s.Arguments='-NoLogo -NoProfile -NonInteractive -EncodedCommand {grandchild}'; "
        "$s.UseShellExecute=$false; $s.CreateNoWindow=$true; "
        "[void][Diagnostics.Process]::Start($s); "
        "[Console]::Out.WriteLine('root exits before its descendant'); exit 7"
    )
    log = tmp_path / "owned.log"
    # The actual target deadline excludes the caller-owned transport's startup.
    deadline = 2500 if timeout else 20000
    body = (
        f"$ErrorActionPreference='Stop'; . {quoted(HELPER)}; "
        "try { "
        f"$code = Invoke-NativeOwnedProcess -FilePath {quoted(shell)} "
        f"-Arguments @('-NoLogo','-NoProfile','-NonInteractive','-EncodedCommand','{child}') "
        f"-WorkingDirectory {quoted(tmp_path)} -LogPath {quoted(log)} -TimeoutMilliseconds {deadline}; "
        "@{ code=$code; settled=$true } | ConvertTo-Json -Compress "
        "} catch { "
        "$e = $_.Exception; while ($e.InnerException -and "
        "-not $e.Data.Contains('CleanupConfirmed')) { $e=$e.InnerException }; "
        "@{ code=$null; settled=$e.Data['CleanupConfirmed']; error=$e.Message } | ConvertTo-Json -Compress }"
    )
    result = subprocess.run(
        [str(shell), "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded(body)],
        capture_output=True, text=True, timeout=40, **pu.no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    outcome = json.loads(result.stdout)
    assert outcome["settled"] is True, outcome
    if timeout:
        assert outcome["code"] is None and "deadline" in outcome["error"], outcome
        assert not marker.exists()
    else:
        assert outcome["code"] == 7, outcome
        assert marker.read_text() == "settled"
    assert "root exits before its descendant" in log.read_text()


def test_native_owned_job_retires_writers_when_its_owner_dies(tmp_path, native_shell):
    from agent_procutil.daemon_broker import WindowsDaemonProcess

    shell = native_shell
    root_pid, child_pid = tmp_path / "root.pid", tmp_path / "child.pid"
    marker = tmp_path / "escaped-writer"
    grandchild = encoded(
        f"[IO.File]::WriteAllText({quoted(child_pid)}, [string]$PID); "
        f"Start-Sleep -Seconds 25; [IO.File]::WriteAllText({quoted(marker)}, 'unsafe')"
    )
    child = encoded(
        "$s = New-Object Diagnostics.ProcessStartInfo; "
        f"$s.FileName={quoted(shell)}; "
        f"$s.Arguments='-NoLogo -NoProfile -NonInteractive -EncodedCommand {grandchild}'; "
        "$s.UseShellExecute=$false; $s.CreateNoWindow=$true; "
        "[void][Diagnostics.Process]::Start($s); "
        f"[IO.File]::WriteAllText({quoted(root_pid)}, [string]$PID); Start-Sleep -Seconds 25"
    )
    body = (
        f"$ErrorActionPreference='Stop'; . {quoted(HELPER)}; "
        f"Invoke-NativeOwnedProcess -FilePath {quoted(shell)} "
        f"-Arguments @('-NoLogo','-NoProfile','-NonInteractive','-EncodedCommand','{child}') "
        f"-WorkingDirectory {quoted(tmp_path)} -LogPath {quoted(tmp_path / 'owned.log')} "
        "-TimeoutMilliseconds 40000"
    )
    parent = subprocess.Popen(
        [str(shell), "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded(body)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        **pu.no_window_kwargs(),
    )
    writers = []
    try:
        deadline = time.monotonic() + 15
        while not all(p.exists() and p.read_text().strip().isdigit() for p in (root_pid, child_pid)):
            assert parent.poll() is None, "native owner exited before its writers started"
            if time.monotonic() > deadline:
                pytest.fail("native writers did not reach their observation gate")
            time.sleep(.02)
        for path in (root_pid, child_pid):
            writers.append(WindowsDaemonProcess(int(path.read_text()), ["native-owned-writer"]))
        assert all(writer.poll() is None for writer in writers)
        parent.kill()
        parent.wait(timeout=5)
        for writer in writers:
            writer.wait(timeout=5)
        assert not marker.exists()
    finally:
        if parent.poll() is None:
            parent.kill()
        parent.wait(timeout=5)
        for writer in writers:
            try:
                if writer.poll() is None:
                    writer.terminate()
                    writer.wait(timeout=5)
            finally:
                writer.close()


def test_native_owned_rejects_all_partially_qualified_paths_before_opening_log(tmp_path, native_shell):
    shell = native_shell
    log = tmp_path / "owned.log"
    body = (
        f"$ErrorActionPreference='Stop'; . {quoted(HELPER)}; $results=@(); "
        "foreach ($field in @('FilePath','WorkingDirectory','LogPath')) { "
        "foreach ($path in @('C:tool.exe','\\owned.log')) { "
        f"$parameters=@{{FilePath={quoted(shell)}; Arguments=@('-NoProfile','-Command','exit 91'); "
        f"WorkingDirectory={quoted(tmp_path)}; LogPath={quoted(log)} }}; "
        "$parameters[$field]=$path; "
        "try { [void](Invoke-NativeOwnedProcess @parameters); exit 91 } "
        "catch { $results+=@{field=$field;path=$path;error=$_.Exception.Message} } } }; "
        "ConvertTo-Json -InputObject $results -Compress"
    )
    result = subprocess.run(
        [str(shell), "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded(body)],
        capture_output=True, text=True, timeout=30, **pu.no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    outcomes = json.loads(result.stdout)
    assert len(outcomes) == 6
    assert all("fully qualified" in item["error"] for item in outcomes), outcomes
    assert not log.exists()


def test_native_owned_publishes_one_fresh_scope_without_changing_parent_environment(tmp_path, native_shell):
    shell = native_shell
    report = tmp_path / "scopes.txt"
    child = encoded(
        f"[IO.File]::WriteAllText({quoted(report)}, $env:COPILOT_EXTENSIONS_OWNED_JOB_SCOPES)"
    )
    body = (
        f"$ErrorActionPreference='Stop'; . {quoted(HELPER)}; "
        "$before=$env:COPILOT_EXTENSIONS_OWNED_JOB_SCOPES; "
        f"$code=Invoke-NativeOwnedProcess -FilePath {quoted(shell)} "
        f"-Arguments @('-NoProfile','-NonInteractive','-EncodedCommand','{child}') "
        f"-WorkingDirectory {quoted(tmp_path)} -LogPath {quoted(tmp_path / 'owned.log')}; "
        "@{code=$code;before=$before;after=$env:COPILOT_EXTENSIONS_OWNED_JOB_SCOPES} "
        "| ConvertTo-Json -Compress"
    )
    result = subprocess.run(
        [str(shell), "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded(body)],
        capture_output=True, text=True, timeout=30, **pu.no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    outcome = json.loads(result.stdout)
    assert outcome["code"] == 0 and outcome["before"] == outcome["after"]
    before = outcome["before"].split(";") if outcome["before"] else []
    after = report.read_text().split(";")
    assert after[:-1] == before
    assert re.fullmatch(r"Local\\ce-owned-job-[0-9a-f]{32}", after[-1])
    assert after[-1] not in before


@pytest.mark.parametrize("scopes", [
    "", "Local\\ce-owned-job-invalid",
    ";".join(["Local\\ce-owned-job-" + "a" * 32] * 2),
    ";".join(f"Local\\ce-owned-job-{value:032x}" for value in range(64)),
    "x" * 4097,
], ids=["empty", "malformed", "duplicate", "full", "oversized"])
def test_native_owned_refuses_invalid_or_full_scope_chain_before_child_start(tmp_path, scopes, native_shell):
    shell = native_shell
    report = tmp_path / "unexpected.txt"
    child = encoded(f"[IO.File]::WriteAllText({quoted(report)}, 'unexpected')")
    body = (
        f"$ErrorActionPreference='Stop'; . {quoted(HELPER)}; "
        "try { "
        f"Invoke-NativeOwnedProcess -FilePath {quoted(shell)} "
        f"-Arguments @('-NoProfile','-NonInteractive','-EncodedCommand','{child}') "
        f"-WorkingDirectory {quoted(tmp_path)} -LogPath {quoted(tmp_path / 'owned.log')}; exit 91 "
        "} catch { "
        "if ($_.Exception.Data['CleanupConfirmed'] -cne $true) { throw 'Scope refusal custody unconfirmed' }; "
        "$_.Exception.ToString() }"
    )
    result = subprocess.run(
        [str(shell), "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded(body)],
        env={**os.environ, "COPILOT_EXTENSIONS_OWNED_JOB_SCOPES": scopes},
        capture_output=True, text=True, timeout=30, **pu.no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    assert "Job scope" in result.stdout
    assert not report.exists()
    assert not (tmp_path / "owned.log").exists()
