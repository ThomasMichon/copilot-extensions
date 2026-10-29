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


def observed_worker(details):
    from agent_procutil.daemon_broker import WindowsDaemonProcess
    from agent_procutil._daemon_instance_identity import _windows_birth

    process = WindowsDaemonProcess(details["pid"], ["native-bootstrap-fixture"])
    try:
        if _windows_birth(process._handle) != details["birth"]:
            raise ProcessLookupError("native fixture worker birth identity changed")
    except BaseException:
        process.close()
        raise
    return process


@pytest.mark.parametrize("mode", ["production", "contained"])
def test_python_free_bootstrap_refuses_strict_escape_and_preserves_contained_custody(tmp_path, monkeypatch, mode):
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
                    code = parent.wait(timeout=30)
                    if mode == "production":
                        assert code != 0, "strict caller Job must refuse durable escape"
                        assert not (root / "worker.json").exists()
                        assert not (root / "started").exists()
                        continue
                    assert code == 0, (root / "submitter.log").read_text()
                    details = json.loads((root / "worker.json").read_text())
                    worker = observed_worker(details)
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
                    worker.wait(timeout=5)
                    assert not (root / "result").exists()
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


@pytest.mark.parametrize("scopes", [
    "", "Local\\ce-owned-job-not-a-uuid", "Local\\ce-owned-job-" + "a" * 32 + ";",
    ";".join(["Local\\ce-owned-job-" + "a" * 32] * 2),
    ";".join(f"Local\\ce-owned-job-{value:032x}" for value in range(65)),
    "x" * 4097,
    "Local\\ce-owned-job-" + "a" * 32,
])
def test_native_direct_refusal_never_runs_foreground(tmp_path, scopes):
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
    script = (
        f"$ErrorActionPreference='Stop'; . {quoted(helper)}; "
        "$env:COPILOT_EXTENSIONS_TEST_CONTAINED=$null; "
        "try { "
        f"$p = Start-NativeDaemon -Script {quoted(fixture)} -Arguments @('worker') "
        f"-WorkingDirectory {quoted(tmp_path)} -LogPath {quoted(tmp_path / 'worker.log')}; "
        "$p.Dispose(); exit 91 "
        "} catch { $e=$_.Exception; "
        "while ($e.InnerException -and $e -isnot [CopilotExtensions.NativeDaemonSpawnException]) "
        "{ $e=$e.InnerException }; "
        "@{ settled=$e.CleanupConfirmed; type=$e.GetType().Name; error=$e.InnerException.Message } "
        "| ConvertTo-Json -Compress }"
    )
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    result = subprocess.run(
        [str(shell), "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        capture_output=True, text=True, timeout=45, **pu.no_window_kwargs(),
        env={**os.environ, "COPILOT_EXTENSIONS_OWNED_JOB_SCOPES": scopes},
    )
    assert result.returncode == 0, result.stderr
    outcome = json.loads(result.stdout)
    assert outcome["type"] == "NativeDaemonSpawnException"
    assert outcome["settled"] is True
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


@pytest.mark.parametrize("cycle", [0, 1])
def test_native_contained_handshake_with_credential_free_private_profile(tmp_path, cycle):
    repository = Path(__file__).resolve().parents[3]
    fixture_path = repository / "tools" / "windows_broker_fixture.py"
    spec = importlib.util.spec_from_file_location("_native_private_profile", fixture_path)
    fixture_environment = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture_environment)
    helper = Path(__file__).resolve().parents[1] / "src" / "agent_procutil" / "native_daemon.ps1"
    shell = Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    profile = tmp_path / "private-profile"
    roaming, local = profile / "AppData" / "Roaming", profile / "AppData" / "Local"
    roaming.mkdir(parents=True)
    local.mkdir(parents=True)
    environment = fixture_environment.fixture_environment(dict(os.environ), control=True)
    environment.update({
        key: value for key, value in os.environ.items()
        if key.startswith("COPILOT_EXTENSIONS_TEST_") or key in (
            "COPILOT_EXTENSIONS_OWNED_JOB_SCOPES", "COPILOT_EXTENSIONS_DAEMON_TEST_CONTEXT",
        )
    })
    environment.update({
        "HOME": str(profile), "USERPROFILE": str(profile),
        "APPDATA": str(roaming), "LOCALAPPDATA": str(local),
        "HOMEDRIVE": profile.drive, "HOMEPATH": str(profile)[len(profile.drive):],
        "COPILOT_EXTENSIONS_TEST_CONTAINED": "1",
    })

    def literal(path):
        return "'" + str(path).replace("'", "''") + "'"

    directory = tmp_path / f"private-{cycle}"
    directory.mkdir()
    argument = "quote'`;\n\u2603"
    encoded_argument = base64.b64encode(argument.encode("utf-16le")).decode("ascii")
    worker = directory / "worker.ps1"
    worker.write_text(
        "$ErrorActionPreference='Stop'\n"
        f"[IO.File]::WriteAllText({literal(directory / 'started')}, $args[0])\n"
        "$deadline=[DateTime]::UtcNow.AddSeconds(15)\n"
        f"while (-not (Test-Path -LiteralPath {literal(directory / 'continue')})) {{\n"
        " if ([DateTime]::UtcNow -gt $deadline) { throw 'caller did not release worker' }\n"
        " Start-Sleep -Milliseconds 50\n}\n",
        encoding="utf-8",
    )
    caller = directory / "caller.ps1"
    caller.write_text(
        "$ErrorActionPreference='Stop'\n"
        "Write-Output 'native caller loading admission'\n"
        f". {literal(helper)}\n"
        "Write-Output 'native caller starting daemon'\n"
        f"$p=Start-NativeDaemon -Script {literal(worker)} -Arguments @("
        f"([Text.Encoding]::Unicode.GetString([Convert]::FromBase64String('{encoded_argument}')))) "
        f"-WorkingDirectory {literal(directory)} -LogPath {literal(directory / 'daemon.log')}\n"
        "Write-Output 'native caller received admission'\n"
        "@{pid=$p.Id;birth=[CopilotExtensions.DaemonInstanceNative]::Birth($p)} | ConvertTo-Json -Compress "
        f"| Set-Content -LiteralPath {literal(directory / 'outcome.json')} -Encoding UTF8\n"
        "$p.Dispose()\n",
        encoding="utf-8",
    )
    daemon = None
    env = {**environment, "COPILOT_EXTENSIONS_DAEMON_REGISTRY": str(directory / "instances")}
    with (directory / "caller.log").open("wb") as log:
        process, job = pu.spawn_sync_in_kill_on_close_job(
            [str(shell), "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(caller)],
            env=env, cwd=directory, require_job=True, allow_breakaway=False,
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, **pu.no_window_kwargs(),
        )
    try:
        code = process.wait(timeout=30)
        assert code == 0, (directory / "caller.log").read_text(errors="replace")
        outcome = json.loads((directory / "outcome.json").read_text(encoding="utf-8-sig"))
        daemon = observed_worker(outcome)
        observe(lambda: (directory / "started").exists() or daemon.poll() is not None)
        assert (directory / "started").exists(), (directory / "daemon.log").read_text(errors="replace")
        assert (directory / "started").read_text(encoding="utf-8") == argument
        stages = (directory / "daemon.log").read_text(errors="replace")
        for stage in ("prepared", "creating", "created-suspended", "resumed", "bootstrap",
                      "peer-ready", "connected", "offered"):
            assert f"native-daemon-stage:{stage}" in stages
        assert daemon.poll() is None
        job.close()
        daemon.wait(timeout=5)
    except BaseException:
        print("\n".join(
            path.name + ":\n" + path.read_text(encoding="utf-8", errors="replace")
            for path in directory.glob("*.log")
        ), flush=True)
        raise
    finally:
        if job.handle:
            job.terminate_and_wait(5)
        process.wait(timeout=5)
        if daemon is not None:
            try:
                if daemon.poll() is None:
                    daemon.kill()
                daemon.wait(timeout=5)
            finally:
                daemon.close()
