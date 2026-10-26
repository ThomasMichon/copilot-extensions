"""Python-free native admission survives the actual submitting Job."""

import importlib.util
import base64
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

import agent_procutil as pu

pytestmark = pytest.mark.skipif(
    os.name != "nt" or os.environ.get("PROCUTIL_NATIVE_DAEMON_LIFETIME") != "1",
    reason="explicit Windows native bootstrap lifetime qualification",
)


def observe(predicate, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.02)
    raise AssertionError("native bootstrap observation deadline expired")


def observer():
    path = Path(__file__).with_name("test_spawn_daemon_windows_integration.py")
    spec = importlib.util.spec_from_file_location("_native_bootstrap_observer", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    user, kernel, callback = module._configure_win32()
    return module._WindowObserver(user, kernel, callback)


@pytest.mark.parametrize("mode", ["production", "contained"])
def test_python_free_bootstrap_survives_only_authorized_caller_job_escape(tmp_path, monkeypatch, mode):
    from agent_procutil.daemon_broker import WindowsDaemonProcess

    base = Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(base / "single-instance-lease" / "src"))
    from single_instance_lease.operation import OperationLock, OperationLockedError

    helper = base / "agent-procutil" / "src" / "agent_procutil" / "native_daemon.ps1"
    custody = base / "single-instance-lease" / "src" / "single_instance_lease" / "operation.ps1"
    fixture = Path(__file__).with_name("fixture_native_daemon.ps1")
    shell = Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    with observer() as observation:
        for cycle in range(2):
            root = tmp_path / f"{mode}-{cycle}"
            root.mkdir()
            env = dict(os.environ)
            # The native child has neither a Python nor uv command to discover.
            env["PATH"] = str(shell.parent)
            if mode == "production":
                env.pop("COPILOT_EXTENSIONS_TEST_CONTAINED", None)
            else:
                env["COPILOT_EXTENSIONS_TEST_CONTAINED"] = "1"
            with (root / "submitter.log").open("w") as log:
                parent, job = pu.spawn_sync_in_kill_on_close_job(
                    [str(shell), "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(fixture),
                     "submit", str(root), str(helper), str(custody)],
                    env=env, require_job=True, allow_breakaway=False,
                    stdin=subprocess.DEVNULL, stdout=log, stderr=log, **pu.no_window_kwargs(),
                )
                worker = None
                try:
                    assert job is not None
                    assert parent.wait(timeout=30) == 0, (root / "submitter.log").read_text()
                    details = json.loads((root / "worker.json").read_text())
                    worker = WindowsDaemonProcess(details["pid"], ["native-bootstrap-fixture"])
                    observe(lambda: (root / "started").exists() or worker.poll() is not None)
                    assert (root / "started").exists(), (
                        (root / "worker.log").read_text()
                        if (root / "worker.log").exists() else f"worker exited: {worker.poll()}"
                    )
                    with pytest.raises(OperationLockedError):
                        OperationLock(root / "bootstrap.lock").acquire()
                    assert worker.poll() is None
                    job.close()
                    job = None
                    if mode == "contained":
                        worker.wait(timeout=5)
                        assert not (root / "result").exists()
                    else:
                        assert worker.poll() is None
                        with pytest.raises(OperationLockedError):
                            OperationLock(root / "bootstrap.lock").acquire()
                        (root / "continue").write_text("go", encoding="ascii")
                        assert worker.wait(timeout=15) == 7
                        assert (root / "result").read_text() == "completed:7"
                        assert "post-caller native prerequisite" in (root / "owned.log").read_text()
                    with OperationLock(root / "bootstrap.lock"):
                        pass
                finally:
                    if worker is not None:
                        try:
                            if worker.poll() is None:
                                worker.terminate()
                                worker.wait(timeout=5)
                        finally:
                            worker.close()
                    if job is not None:
                        job.close()
                    if parent.poll() is None:
                        parent.kill()
                    parent.wait(timeout=5)
        assert observation.surfaced == {}
        assert observation.focus_changes == {}


@pytest.mark.parametrize("mode", ["refusal", "lost-response"])
def test_native_broker_outcome_never_invents_cleanup_or_runs_foreground(tmp_path, mode):
    base = Path(__file__).resolve().parents[2]
    helper = base / "agent-procutil" / "src" / "agent_procutil" / "native_daemon.ps1"
    fixture = tmp_path / "entry.ps1"
    shell = Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"

    def quoted(path):
        return "'" + str(path).replace("'", "''") + "'"

    fixture.write_text(
        f"[IO.File]::WriteAllText({quoted(tmp_path / 'started')}, 'unexpected entry point')",
        encoding="utf-8",
    )
    activation = (
        "$r = & $script:cim @PSBoundParameters; "
        "if ($r.ReturnValue -ne 0) { throw 'fixture WMI activation refused' }; "
        "$script:activation = [Diagnostics.Process]::GetProcessById([int]$r.ProcessId); "
        "[void]$script:activation.Handle; throw 'fixture response lost after actual activation'; "
        if mode == "lost-response" else "return [pscustomobject]@{ ReturnValue=8; ProcessId=0 }; "
    )
    script = (
        f"$ErrorActionPreference='Stop'; . {quoted(helper)}; "
        "$env:COPILOT_EXTENSIONS_TEST_CONTAINED=$null; "
        "$script:activation=$null; "
        "Import-Module (Join-Path $env:SystemRoot "
        "'System32\\WindowsPowerShell\\v1.0\\Modules\\CimCmdlets\\CimCmdlets.psd1'); "
        "$script:cim=Get-Command Invoke-CimMethod -CommandType Cmdlet; "
        "function Invoke-CimMethod { [CmdletBinding()] param("
        "[string]$ClassName,[string]$MethodName,[int]$OperationTimeoutSec,[hashtable]$Arguments); "
        + activation + "}; "
        "try { "
        f"$p = Start-NativeDaemon -Script {quoted(fixture)} -Arguments @('worker') "
        f"-WorkingDirectory {quoted(tmp_path)} -LogPath {quoted(tmp_path / 'worker.log')}; "
        "$p.Dispose(); exit 91 "
        "} catch { $e=$_.Exception; "
        "while ($e.InnerException -and $e -isnot [CopilotExtensions.NativeDaemonSpawnException]) "
        "{ $e=$e.InnerException }; "
        "@{ settled=$e.CleanupConfirmed; type=$e.GetType().Name; error=$e.InnerException.Message } "
        "| ConvertTo-Json -Compress } finally { "
        "if ($script:activation) { try { "
        "if (-not $script:activation.WaitForExit(20000)) { "
        "$script:activation.Kill(); [void]$script:activation.WaitForExit(5000); "
        "throw 'fixture activation did not retire after channel closure' } "
        "} finally { $script:activation.Dispose() } } }"
    )
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    result = subprocess.run(
        [str(shell), "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        capture_output=True, text=True, timeout=45, **pu.no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    outcome = json.loads(result.stdout)
    assert outcome["type"] == "NativeDaemonSpawnException"
    assert outcome["settled"] is (mode == "refusal")
    assert (
        "refused launch (8)" if mode == "refusal" else "response lost after actual activation"
    ) in outcome["error"]
    assert not (tmp_path / "started").exists()


def test_native_daemon_rejects_every_partially_qualified_path_before_activation(tmp_path):
    base = Path(__file__).resolve().parents[2]
    helper = base / "agent-procutil" / "src" / "agent_procutil" / "native_daemon.ps1"
    fixture = Path(__file__).with_name("fixture_native_daemon.ps1")
    shell = Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"

    def quoted(path):
        return "'" + str(path).replace("'", "''") + "'"

    script = (
        f"$ErrorActionPreference='Stop'; . {quoted(helper)}; $results=@(); "
        "foreach ($field in @('Script','WorkingDirectory','LogPath','helper')) { "
        "foreach ($path in @('C:worker.ps1','\\worker.ps1')) { "
        f"$script:NativeDaemonSource={quoted(helper)}; "
        f"$parameters=@{{Script={quoted(fixture)}; Arguments=@('unused'); "
        f"WorkingDirectory={quoted(tmp_path)}; LogPath={quoted(tmp_path / 'worker.log')} }}; "
        "if ($field -eq 'helper') { $script:NativeDaemonSource=$path } else { $parameters[$field]=$path }; "
        "try { $p=Start-NativeDaemon @parameters; $p.Dispose(); exit 91 } "
        "catch { $results+=@{field=$field;path=$path;error=$_.Exception.Message} } } }; "
        "ConvertTo-Json -InputObject $results -Compress"
    )
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    result = subprocess.run(
        [str(shell), "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        capture_output=True, text=True, timeout=30, **pu.no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    outcomes = json.loads(result.stdout)
    assert len(outcomes) == 8
    assert all("fully qualified" in item["error"] for item in outcomes), outcomes
    assert not (tmp_path / "worker.log").exists()


def test_native_daemon_rejects_real_powershell_core_before_initialization():
    shell = shutil.which("pwsh")
    if shell is None:
        pytest.skip("PowerShell Core is not installed for the explicit host-refusal control")
    base = Path(__file__).resolve().parents[2]
    helper = base / "agent-procutil" / "src" / "agent_procutil" / "native_daemon.ps1"
    literal = "'" + str(helper).replace("'", "''") + "'"
    script = (
        "$ErrorActionPreference='Stop'; "
        f"try {{ . {literal}; exit 91 }} catch {{ "
        "@{error=$_.Exception.Message; "
        "initialized=($null -ne ('CopilotExtensions.NativeDaemonAdmission' -as [type]))} "
        "| ConvertTo-Json -Compress }"
    )
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    result = subprocess.run(
        [shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        capture_output=True, text=True, timeout=30, **pu.no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    outcome = json.loads(result.stdout)
    assert "stock Windows PowerShell 5.1" in outcome["error"]
    assert outcome["initialized"] is False
