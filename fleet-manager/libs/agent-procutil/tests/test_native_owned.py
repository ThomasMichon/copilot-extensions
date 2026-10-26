"""Native prerequisite writers are admitted suspended and retained to Job settlement."""

import base64
import json
import os
import subprocess
import time
from pathlib import Path

import pytest

import agent_procutil as pu

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows native prerequisite custody")
HELPER = Path(__file__).resolve().parents[1] / "src" / "agent_procutil" / "native_owned.ps1"


def quoted(path):
    return "'" + str(path).replace("'", "''") + "'"


def encoded(text):
    return base64.b64encode(text.encode("utf-16le")).decode("ascii")


@pytest.mark.parametrize("timeout", [False, True])
def test_native_owned_waits_for_descendants_and_attests_timeout_cleanup(tmp_path, timeout):
    shell = Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
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
    # Allow compilation/startup to finish before imposing the actual owned-tree deadline.
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
        "$e -isnot [CopilotExtensions.NativeOwnedProcessException]) { $e=$e.InnerException }; "
        "@{ code=$null; settled=$e.CleanupConfirmed; error=$e.Message } | ConvertTo-Json -Compress }"
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


def test_native_owned_job_retires_writers_when_its_owner_dies(tmp_path):
    from agent_procutil.daemon_broker import WindowsDaemonProcess

    shell = Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
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


def test_native_owned_rejects_all_partially_qualified_paths_before_opening_log(tmp_path):
    shell = Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
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
