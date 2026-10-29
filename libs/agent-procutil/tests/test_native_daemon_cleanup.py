"""Real pre-adoption process handles, refusal, and conservative cleanup outcomes."""

import json
import base64
import os
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

import agent_procutil as pu

pytestmark = pytest.mark.skipif(
    os.name != "nt" or os.environ.get("PROCUTIL_NATIVE_DAEMON_LIFETIME") != "1",
    reason="explicit Windows native bootstrap lifetime qualification",
)


def test_reported_worker_reuse_never_grants_cleanup_custody(monkeypatch):
    from agent_procutil import daemon_broker, _daemon_instance_identity
    from test_native_daemon import observed_worker

    unrelated = Mock(_handle=123)
    monkeypatch.setattr(daemon_broker, "WindowsDaemonProcess", Mock(return_value=unrelated))
    monkeypatch.setattr(_daemon_instance_identity, "_windows_birth", lambda handle: "reused")
    with pytest.raises(ProcessLookupError, match="birth identity changed"):
        observed_worker({"pid": 321, "birth": "expected"})
    unrelated.close.assert_called_once_with()
    unrelated.kill.assert_not_called()
    unrelated.terminate.assert_not_called()
    unrelated.wait.assert_not_called()


def test_native_worker_failure_preserves_original_error_in_redirected_log(tmp_path):
    helper = Path(__file__).resolve().parents[1] / "src" / "agent_procutil" / "native_daemon.ps1"
    shell = Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"

    def quoted(value):
        return "'" + str(value).replace("'", "''") + "'"

    worker = tmp_path / "worker.ps1"
    worker.write_text("throw 'fixture-native-original-error'\n", encoding="utf-8")
    log = tmp_path / "worker.log"
    body = (
        "$ErrorActionPreference='Stop'; $env:COPILOT_EXTENSIONS_TEST_CONTAINED='1'; "
        f". {quoted(helper)}; "
        f"$p=Start-NativeDaemon -Script {quoted(worker)} -Arguments @('fixture') "
        f"-WorkingDirectory {quoted(tmp_path)} -LogPath {quoted(log)}; "
        "try { if (-not $p.WaitForExit(15000)) { throw 'worker did not exit' }; "
        "@{code=$p.ExitCode} | ConvertTo-Json -Compress } finally { $p.Dispose() }"
    )
    encoded = base64.b64encode(body.encode("utf-16le")).decode("ascii")
    result = subprocess.run(
        [str(shell), "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        capture_output=True, text=True, timeout=30, **pu.no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["code"] == 1
    assert "fixture-native-original-error" in log.read_text(encoding="utf-8", errors="replace")


@pytest.mark.parametrize("mode", ["cleanup", "mismatch", "unconfirmed-cleanup"])
def test_native_preack_cleanup_requires_verified_handles_and_observed_exit(tmp_path, mode):
    from test_native_daemon import observer

    base = Path(__file__).resolve().parents[2]
    helper = base / "agent-procutil" / "src" / "agent_procutil" / "native_daemon.ps1"
    fixture = Path(__file__).with_name("fixture_native_daemon_cleanup.ps1")
    shell = Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    with observer() as observation:
        result = subprocess.run(
            [str(shell), "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(fixture),
             mode, str(helper), str(tmp_path)],
            capture_output=True, text=True, timeout=50, **pu.no_window_kwargs(),
        )
    assert result.returncode == 0, result.stderr
    outcome = json.loads(result.stdout)
    assert outcome["settled"] is (mode != "unconfirmed-cleanup"), outcome
    if mode == "mismatch":
        assert "client identity changed" in outcome["error"]
    elif mode == "cleanup":
        assert "fixture preack failure" in outcome["error"]
    else:
        assert "native daemon cleanup failed" in outcome["error"]
    assert outcome["workerCode"] in (1, 125), "the pinned worker must actually exit before cleanup is attested"
    assert not (tmp_path / "started").exists()
    assert observation.surfaced == {}
    assert observation.focus_changes == {}


@pytest.mark.parametrize("fault", ["start", "preack"])
def test_native_unconfirmed_cleanup_transfers_exact_live_handle_to_caller(tmp_path, fault):
    helper = Path(__file__).resolve().parents[1] / "src" / "agent_procutil" / "native_daemon.ps1"
    shell = Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"

    def quoted(value):
        return "'" + str(value).replace("'", "''") + "'"

    if fault == "start":
        injection = (
            "$source=$source.Replace("
            "'flags, env, cwd, ref startup, out child));',"
            "'flags, env, cwd, ref startup, out child)); "
            'if (child.Process != IntPtr.Zero) throw new IOException("fixture post-create failure");'
            "'); "
            "$source=$source.Replace('Check(TerminateProcess(child.Process, 125));',"
            "'throw new IOException(\"fixture cleanup refusal\");'); "
        )
    else:
        injection = (
            "$source=$source.Replace('private IntPtr handle;',"
            "'private IntPtr handle; private bool fixtureStopFailed;'); "
            "$source=$source.Replace('public bool Stop() {',"
            "'public bool Stop() { if (!fixtureStopFailed) { fixtureStopFailed=true; "
            'throw new IOException("fixture cleanup refusal"); }'
            "'); "
        )
    body = (
        "$ErrorActionPreference='Stop'; $env:COPILOT_EXTENSIONS_TEST_CONTAINED='1'; "
        f"$source=Get-Content -LiteralPath {quoted(helper)} -Raw; "
        + injection
        + "$source=$source.Replace('exit 1','Start-Sleep -Seconds 10; exit 1'); "
        "Invoke-Expression $source; "
        f"$script:NativeDaemonSource={quoted(helper)}; "
        "function Read-NativeDaemonFrame { "
        "param([IO.Pipes.PipeStream]$Pipe,[int]$Length,[int]$Timeout=15000); "
        "throw 'fixture preack failure' }; "
        "Add-Type -TypeDefinition 'using System; using System.Runtime.InteropServices; "
        "public static class NativeCustodyObserver { "
        '[DllImport("kernel32.dll")] public static extern uint GetProcessId(IntPtr handle); '
        '[DllImport("kernel32.dll")] public static extern uint WaitForSingleObject(IntPtr handle,uint timeout); '
        "}'; "
        f"$entry={quoted(tmp_path / 'entry.ps1')}; "
        "[IO.File]::WriteAllText($entry, 'throw \"unexpected entry point\"'); "
        "$failure=$null; $custody=$null; "
        "try { try { "
        f"$p=Start-NativeDaemon -Script $entry -Arguments @('unused') -WorkingDirectory {quoted(tmp_path)} "
        f"-LogPath {quoted(tmp_path / 'worker.log')}; "
        "$p.Dispose(); throw 'fixture expected refusal' "
        "} catch { $failure=$_.Exception; "
        "while ($failure.InnerException -and $failure -isnot [CopilotExtensions.NativeDaemonSpawnException]) "
        "{ $failure=$failure.InnerException } }; "
        "$custody=$failure.RetainedChild; "
        "if ($null -eq $custody) { throw 'unconfirmed cleanup discarded native custody' }; "
        "$field=$custody.GetType().GetField('handle',"
        "[Reflection.BindingFlags]'Instance,NonPublic'); "
        "$handle=[IntPtr]$field.GetValue($custody); "
        "$exact=([NativeCustodyObserver]::GetProcessId($handle) -eq $custody.Pid); "
        "$live=([NativeCustodyObserver]::WaitForSingleObject($handle,0) -eq 258); "
        "$settled=$custody.Stop(); "
        "$observed=([NativeCustodyObserver]::WaitForSingleObject($handle,0) -eq 0); "
        "@{confirmed=$failure.CleanupConfirmed;exact=$exact;live=$live;settled=$settled;observed=$observed} "
        "| ConvertTo-Json -Compress "
        "} finally { if ($custody) { $custody.Dispose() } }"
    )
    encoded = base64.b64encode(body.encode("utf-16le")).decode("ascii")
    result = subprocess.run(
        [str(shell), "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        capture_output=True, text=True, timeout=40, **pu.no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "confirmed": False, "exact": True, "live": True, "settled": True, "observed": True,
    }
