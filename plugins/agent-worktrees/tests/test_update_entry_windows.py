"""Executable Windows-only scratch ACL and exact native Git argv contracts."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import time

import pytest
from agent_procutil import no_window_kwargs
from test_update_entry import (
    PLUGIN, _SUPPORTED_UPDATE_SHELL, _assert_cache_arguments, _copy_native_global_tools,
)
from test_update_entry import update_entry_case as shared_update_entry_case

update_entry_case = shared_update_entry_case

pytestmark = pytest.mark.skipif(os.name != "nt", reason="native Windows security/process contract")

_CACHE_PROBE = (
    "if '-c' in sys.argv:\n"
    "    print(json.dumps(next(arg.split('=', 1)[1] for arg in sys.argv "
    "if arg.startswith('pycache_prefix='))))\n"
    "    raise SystemExit(0)\n"
)


def _use_real_probe_python(monkeypatch):
    monkeypatch.setenv("PATH", str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"])


def _updater_profile_files(home):
    # PowerShell 7 writes its own startup caches before any update entry code runs.
    shell_cache = home / "AppData" / "Local" / "Microsoft" / "PowerShell"
    return {
        path.relative_to(home): path.read_bytes()
        for path in home.rglob("*") if path.is_file() and not path.is_relative_to(shell_cache)
    }


def _stub_admission_for_bash(tmp, native_powershell, bash):
    from native_shell_support import bash_path

    harness = tmp / "bash-native-entry.ps1"
    harness.write_text(
        _SUPPORTED_UPDATE_SHELL +
        "$index = [Array]::IndexOf($args, '-File')\n"
        "$entry = $args[$index + 1]\n"
        "$forward = @($args | Select-Object -Skip ($index + 2))\n"
        "& $entry @forward\nexit $LASTEXITCODE\n", encoding="utf-8",
    )
    (tmp / "tools" / "pwsh").write_text(
        "#!/bin/sh\nexec " + shlex.quote(bash_path(bash, Path(native_powershell))) +
        " -NoProfile -File " + shlex.quote(str(harness)) + ' "$@"\n',
        encoding="utf-8",
    )


def _job_preflight_entry(tmp, home, front, observer):
    from agent_worktrees import project_command_templates

    entry = PLUGIN / "bin" / "agent-worktrees.ps1"
    arguments = ["--project", "sample", "update", observer]
    if front == "payload":
        entry = PLUGIN / "bin" / "payload" / "agent-worktrees.ps1"
    elif front == "installed-project":
        user_bin = home / ".local" / "bin"
        user_bin.mkdir(parents=True)
        for name in ("agent-worktrees.ps1", "agent-worktrees-update-entry.ps1"):
            shutil.copy2(PLUGIN / "bin" / name, user_bin / name)
        entry = tmp / "sample.ps1"
        entry.write_text(project_command_templates.render(
            "sample", PLUGIN, windows=True, user_bin=user_bin,
        )["sample.ps1"], encoding="utf-8")
        arguments = ["update", observer]
    return entry, arguments


def _quoted(text):
    return "'" + str(text).replace("'", "''") + "'"


@pytest.mark.parametrize("front", ["global", "payload", "installed-project"])
@pytest.mark.parametrize("observer", ["--wait", "--detach", "--no-manager"])
def test_update_preflight_reports_capturable_error_when_wmi_queue_is_blocked(
    update_entry_case, native_powershell, monkeypatch, front, observer,
):
    """A Job that forbids breakaway queues through WMI; a blocked WMI is reported, never silent."""
    from agent_procutil import spawn_sync_in_kill_on_close_job

    # The durable WMI path is exercised explicitly; contained test mode refuses it.
    monkeypatch.delenv("COPILOT_EXTENSIONS_TEST_CONTAINED", raising=False)
    tmp, home, _resolver = update_entry_case
    entry, arguments = _job_preflight_entry(tmp, home, front, observer)
    runtime = home / ".agent-worktrees"
    (runtime / "bin" / "resolve-runtime.ps1").write_text(
        "throw 'update reached installed runtime'\n", encoding="utf-8",
    )
    pending = runtime / "pending-request.json"
    pending.write_text('{"state":"PENDING","request":"existing"}', encoding="utf-8")
    before = _updater_profile_files(home)
    captured = tmp / "captured-errors.txt"
    harness = tmp / "job-preflight.ps1"
    # Simulates a policy (for example the Defender ASR rule) refusing WMI process creation.
    harness.write_text(
        "function Invoke-CimMethod { throw 'Access denied by policy' }\n"
        f"$forward = @({','.join(_quoted(value) for value in arguments)})\n"
        f"& {_quoted(entry)} @forward 2> {_quoted(captured)}\n"
        "exit $LASTEXITCODE\n", encoding="utf-8",
    )
    process, job = spawn_sync_in_kill_on_close_job(
        [native_powershell, "-NoProfile", "-File", str(harness)],
        require_job=True, allow_breakaway=False,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        **no_window_kwargs(),
    )
    try:
        stdout, stderr = process.communicate(timeout=60)
        assert process.returncode == 1, stdout + stderr
        # PowerShell redirection captured the diagnostic: it is not a bare console write.
        raw = captured.read_bytes()
        encoding = "utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
        errors = " ".join(raw.decode(encoding, errors="replace").split())
        assert "WinError 5" in errors
        assert "Queuing the update through WMI failed" in errors
        assert "Access denied by policy" in errors
        assert "Block process creations originating from PSExec and WMI commands" in errors
        assert "No update was started or request submitted" in errors
        assert not (tmp / "bootstrap.args").exists()
        assert _updater_profile_files(home) == before
        assert not list(tmp.glob("agent-update-*"))
    finally:
        if job is not None:
            job.terminate_and_wait(15)
        process.wait(timeout=15)
        process.stdout.close()
        process.stderr.close()


_QUEUED_BOOTSTRAP = r"""
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class QueuedUpdateJobProbe {
    [DllImport("kernel32.dll")] static extern IntPtr GetCurrentProcess();
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool IsProcessInJob(IntPtr process, IntPtr job, out bool result);
    public static bool InJob() { bool r; IsProcessInJob(GetCurrentProcess(), IntPtr.Zero, out r); return r; }
}
'@
$proof = @{ args = @($args); in_job = [QueuedUpdateJobProbe]::InJob() }
[IO.File]::WriteAllText($env:QUEUED_PROOF, (ConvertTo-Json -Compress -InputObject $proof))
[Console]::Error.WriteLine('Update request queued-req-7; log: example.log')
if ($args -ccontains '--json') {
    [Console]::Out.WriteLine('{"request_id": "queued-req-7", "state": "pending"}')
}
exit [int]$env:QUEUED_EXIT
"""


# Typographic single quotes end PowerShell literals too; they must stay data.
_HOSTILE_PROJECT = "a\u2019; Set-Content -LiteralPath $env:QUEUED_INJECTED x; \u2018"


def _queued_update_case(update_entry_case, monkeypatch, *, exit_code=0):
    git = shutil.which("git.exe") or shutil.which("git")
    if git is None:
        pytest.skip("Git is required for the local bootstrap source")
    monkeypatch.delenv("COPILOT_EXTENSIONS_TEST_CONTAINED", raising=False)
    tmp, home, _resolver = update_entry_case
    source = tmp / "source"
    (source / "worktree-manager").mkdir(parents=True)
    (source / "worktree-manager" / "bootstrap.ps1").write_text(_QUEUED_BOOTSTRAP, encoding="utf-8")
    for command in (
        ["init", "--quiet", "--initial-branch", "main", str(source)],
        ["-C", str(source), "add", "-A"],
        ["-C", str(source), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
         "commit", "--quiet", "-m", "fixture"],
    ):
        subprocess.run([git, *command], check=True, capture_output=True, **no_window_kwargs())
    monkeypatch.setenv("WORKTREE_MANAGER_BOOTSTRAP_REPO", str(source))
    monkeypatch.setenv("WORKTREE_MANAGER_BOOTSTRAP_REF", "main")
    monkeypatch.setenv("QUEUED_PROOF", str(tmp / "queued.json"))
    monkeypatch.setenv("QUEUED_INJECTED", str(tmp / "injected.txt"))
    monkeypatch.setenv("QUEUED_EXIT", str(exit_code))
    (tmp / "tools" / "git").unlink()
    return tmp, home


def _run_in_strict_job(command, timeout=180):
    from agent_procutil import spawn_sync_in_kill_on_close_job

    process, job = spawn_sync_in_kill_on_close_job(
        command, require_job=True, allow_breakaway=False,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        **no_window_kwargs(),
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
        return process.returncode, stdout.decode(errors="replace"), stderr.decode(errors="replace")
    finally:
        if job is not None:
            job.terminate_and_wait(15)
        process.wait(timeout=15)
        process.stdout.close()
        process.stderr.close()


def test_queued_update_keeps_typographic_quotes_as_data_and_json_on_stdout(
    update_entry_case, native_powershell, monkeypatch,
):
    tmp, home = _queued_update_case(update_entry_case, monkeypatch)
    entry, _ = _job_preflight_entry(tmp, home, "global", "--detach")
    arguments = ["--project", _HOSTILE_PROJECT, "update", "--detach", "--json"]
    code, stdout, stderr = _run_in_strict_job(
        [native_powershell, "-NoProfile", "-File", str(entry), *arguments],
    )
    assert code == 0, stdout + stderr
    assert json.loads(stdout) == {"request_id": "queued-req-7", "state": "pending"}
    proof = json.loads((tmp / "queued.json").read_text(encoding="utf-8-sig"))
    assert proof["args"] == [
        "--bootstrap-update", "update", "--project", _HOSTILE_PROJECT, "--detach", "--json",
    ]
    assert not (tmp / "injected.txt").exists(), "a typographic quote ended a PowerShell literal"


def test_queued_update_reports_a_failed_submission_even_with_a_request_id(
    update_entry_case, native_powershell, monkeypatch,
):
    tmp, home = _queued_update_case(update_entry_case, monkeypatch, exit_code=1)
    entry, arguments = _job_preflight_entry(tmp, home, "global", "--detach")
    code, stdout, stderr = _run_in_strict_job(
        [native_powershell, "-NoProfile", "-File", str(entry), *arguments],
    )
    output = " ".join((stdout + stderr).split())
    assert code == 1, output
    assert "submission exited 1 for request queued-req-7" in output
    assert "Update request queued-req-7 submitted" not in output


@pytest.mark.parametrize("front", ["global", "git-bash"])
@pytest.mark.parametrize("observer", ["--wait", "--detach"])
def test_update_preflight_queues_detached_update_outside_strict_caller_job(
    update_entry_case, native_powershell, monkeypatch, front, observer,
):
    """The queued update runs through WMI outside the caller's no-breakaway kill-on-close Job."""
    from agent_procutil import spawn_sync_in_kill_on_close_job

    tmp, home = _queued_update_case(update_entry_case, monkeypatch)
    entry, arguments = _job_preflight_entry(tmp, home, "global", observer)
    command = [native_powershell, "-NoProfile", "-File", str(entry), *arguments]
    if front == "git-bash":
        from native_shell_support import ShellUnavailable, admit_native_bash, bash_path

        try:
            bash = admit_native_bash()
        except ShellUnavailable as error:
            pytest.skip(str(error))
        command = [bash, bash_path(bash, PLUGIN / "bin" / "agent-worktrees"), *arguments]
    process, job = spawn_sync_in_kill_on_close_job(
        command,
        require_job=True, allow_breakaway=False,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        **no_window_kwargs(),
    )
    try:
        stdout, stderr = process.communicate(timeout=180)
        output = " ".join((stdout + stderr).decode(errors="replace").split())
        assert process.returncode == 0, output
        assert "WinError 5" in output
        assert "queued outside the caller's Job through WMI" in output
        assert "Update request queued-req-7 submitted" in output
        assert "--request-status queued-req-7" in output
        proof = json.loads((tmp / "queued.json").read_text(encoding="utf-8-sig"))
        assert proof["in_job"] is False
        assert proof["args"] == ["--bootstrap-update", "update", "--project", "sample", "--detach"]
        assert len(list(tmp.glob("agent-update-queued-*/submission.log"))) == 1
    finally:
        if job is not None:
            job.terminate_and_wait(15)
        process.wait(timeout=15)
        process.stdout.close()
        process.stderr.close()


def _check_supported_breakaway_entry(root, native_powershell, front):
    import psutil
    from agent_procutil import spawn_sync_in_kill_on_close_job

    helpers = PLUGIN.parents[1] / "libs" / "agent-procutil" / "tests"
    sys.path.insert(0, str(helpers))
    from caller_owned_fixture import identity
    from test_caller_owned_native import PinnedProcess

    root.mkdir()
    home, scratch = root / "home", root / "scratch"
    home.mkdir()
    scratch.mkdir()
    environment = dict(os.environ)
    for name in list(environment):
        if name.startswith(("WORKTREE_MANAGER_", "AGENT_WORKTREES_INTERNAL_")) or name in (
            "COPILOT_EXTENSIONS_CONTEXT", "AGENT_WORKTREES_CONTROL_PLANE_PROVIDER",
            "AGENT_WORKTREES_CONTROL_PLANE_PROVIDERS_DIR",
        ):
            environment.pop(name)
    environment.update(
        HOME=str(home), USERPROFILE=str(home), AGENT_HOME=str(home),
        AGENT_SCRATCH_ROOT=str(scratch), TEMP=str(scratch), TMP=str(scratch),
        WORKTREE_MANAGER_ROOT=str(home / ".worktree-manager"),
        AGENT_WORKTREES_NO_SELFPROVISION="1",
    )
    entry = PLUGIN / "bin" / "agent-worktrees.ps1"
    if front == "payload":
        entry = PLUGIN / "bin" / "payload" / "agent-worktrees.ps1"
    ready = root / "ready"
    harness = root / "supported.ps1"
    escaped = lambda text: "'" + str(text).replace("'", "''") + "'"
    harness.write_text(
        "function Invoke-WebRequest { param($Uri,$OutFile,[switch]$UseBasicParsing)\n"
        "  [IO.File]::WriteAllText($OutFile, 'exit 23')\n"
        "}\n"
        f"& {escaped(entry)} --project sample update --wait\n"
        "$code = $LASTEXITCODE\n"
        f"[IO.File]::WriteAllText({escaped(ready)}, [string]$code)\n"
        "Start-Sleep -Seconds 5\nexit $code\n", encoding="utf-8",
    )
    launch_started = time.time()
    process, job = spawn_sync_in_kill_on_close_job(
        [native_powershell, "-NoProfile", "-File", str(harness)],
        env=environment, cwd=root, require_job=True, allow_breakaway=True,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        **no_window_kwargs(),
    )
    pins = {}

    def pin_owned_children():
        # Retain the caller's Popen handle through the final scan so its PID cannot be recycled.
        for child in psutil.process_iter(["pid", "ppid", "create_time"]):
            info = child.info
            if (
                info["ppid"] != process.pid or info["create_time"] is None
                or info["create_time"] < launch_started or info["pid"] in pins
            ):
                continue
            pin = None
            try:
                pin = PinnedProcess(identity(info["pid"]))
                observed = psutil.Process(info["pid"])
                if observed.ppid() != process.pid or observed.create_time() != info["create_time"]:
                    pin.close()
                    continue
                pins[info["pid"]] = (pin, False)
                probe = (
                    observed.name().casefold() == Path(native_powershell).name.casefold()
                    and Path(observed.exe()).samefile(native_powershell)
                )
                pins[info["pid"]] = (pin, probe)
            except (psutil.NoSuchProcess, ProcessLookupError, FileNotFoundError):
                if pin is not None:
                    pins.pop(info["pid"], None)
                    pin.close()
            except Exception:
                if pin is not None and info["pid"] not in pins:
                    pin.close()
                raise

    try:
        deadline = time.monotonic() + 15
        while not ready.is_file() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        pin_owned_children()
        if not ready.is_file() and process.poll() is not None:
            stdout, stderr = process.communicate(timeout=5)
            pytest.fail(f"native breakaway owner was not admitted: {stdout!r} {stderr!r}")
        assert ready.is_file(), "supported entry did not reach the offline bootstrap"
        assert ready.read_text() == "23"
        probes = [pin for pin, probe in pins.values() if probe]
        deadline = time.monotonic() + 2
        while not all(pin.gone() for pin in probes) and time.monotonic() < deadline:
            time.sleep(0.02)
        assert all(pin.gone() for pin in probes), "successful admission left a probe alive"
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 23, stdout + stderr
        job.terminate_and_wait(10)
        deadline = time.monotonic() + 3
        while not all(pin.gone() for pin, _probe in pins.values()) and time.monotonic() < deadline:
            time.sleep(0.02)
        assert all(pin.gone() for pin, _probe in pins.values()), "fixture cleanup left a child alive"
        assert not _updater_profile_files(home)
        assert not list(scratch.iterdir())
    finally:
        try:
            if job.handle is not None:
                job.terminate_and_wait(10)
            process.wait(timeout=10)
        finally:
            try:
                failures = []
                try:
                    pin_owned_children()
                except Exception as error:
                    failures.append(error)
                for pin, _probe in pins.values():
                    try:
                        pin.kill()
                    except Exception as error:
                        failures.append(error)
                    try:
                        pin.close()
                    except Exception as error:
                        failures.append(error)
                assert not failures, f"native fixture child cleanup failed: {failures!r}"
            finally:
                process.stdout.close()
                process.stderr.close()


@pytest.mark.parametrize("failure", ["reported-failure", "early-exit", "observer-timeout", "metadata-error"])
def test_native_success_harness_settles_late_breakaway_children(
    tmp_path, native_powershell, monkeypatch, failure,
):
    import io
    import types
    helpers = PLUGIN.parents[1] / "libs" / "agent-procutil" / "tests"
    monkeypatch.syspath_prepend(str(helpers))
    import caller_owned_fixture
    import test_caller_owned_native
    import agent_procutil

    events = []
    job = types.SimpleNamespace(handle=True)
    child = types.SimpleNamespace(
        info={"pid": 54321, "ppid": 12345, "create_time": 0},
        ppid=lambda: 12345,
        name=lambda: Path(native_powershell).name, exe=lambda: native_powershell,
    )
    child.create_time = lambda: child.info["create_time"]
    if failure == "metadata-error":
        def metadata_error():
            raise OSError("fixture metadata query failed")
        child.exe = metadata_error
    process = types.SimpleNamespace(
        pid=12345, stdout=io.BytesIO(), stderr=io.BytesIO(), returncode=1,
        communicate=lambda **_kwargs: (b"", b""), wait=lambda **_kwargs: 1,
    )

    def poll():
        if failure == "observer-timeout":
            raise subprocess.TimeoutExpired("native fixture", 15)
        return 1

    def stop(_timeout):
        events.append("job-stopped")
        job.handle = None

    def spawn(_command, **kwargs):
        child.info["create_time"] = time.time()
        if failure == "reported-failure":
            (kwargs["cwd"] / "ready").write_text("1")
        return process, job

    class Pin:
        def __init__(self, _record):
            events.append("birth-pinned")

        def kill(self):
            events.append("child-killed")

        def close(self):
            events.append("handle-closed")

    job.terminate_and_wait = stop
    process.poll = poll
    fake_psutil = types.SimpleNamespace(
        process_iter=lambda _attrs: [child] if job.handle is None else [],
        Process=lambda _pid: child, NoSuchProcess=ProcessLookupError,
    )
    monkeypatch.setitem(sys.modules, "psutil", fake_psutil)
    monkeypatch.setattr(caller_owned_fixture, "identity", lambda pid: {"pid": pid})
    monkeypatch.setattr(test_caller_owned_native, "PinnedProcess", Pin)
    monkeypatch.setattr(agent_procutil, "spawn_sync_in_kill_on_close_job", spawn)
    with pytest.raises((AssertionError, pytest.fail.Exception, subprocess.TimeoutExpired)):
        _check_supported_breakaway_entry(tmp_path / "failure", native_powershell, "global")
    assert events == ["job-stopped", "birth-pinned", "child-killed", "handle-closed"]
    assert process.stdout.closed and process.stderr.closed


@pytest.mark.parametrize("front", ["global", "payload"])
@pytest.mark.skipif(
    os.environ.get("COPILOT_EXTENSIONS_TEST_CONTAINED") == "1",
    reason="native breakaway success needs the explicit outside-Job check, not contained pytest",
)
def test_update_preflight_permissive_job_retires_probe(tmp_path, native_powershell, front):
    _check_supported_breakaway_entry(tmp_path / front, native_powershell, front)


@pytest.mark.parametrize("arguments", [
    ["status"], ["update", "--help"], ["update", "--auth-report"],
    ["update", "--request-status", "existing"], ["update", "--recovery-status"],
])
def test_update_preflight_leaves_read_only_calls_available(
    update_entry_case, native_powershell, arguments,
):
    tmp, _home, _resolver = update_entry_case
    entry = str(PLUGIN / "bin" / "agent-worktrees-update-entry.ps1").replace("'", "''")
    quoted = ",".join("'" + value.replace("'", "''") + "'" for value in arguments)
    harness = tmp / "read-only.ps1"
    harness.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        "function Add-Type { throw 'read-only call attempted launch admission' }\n"
        "function Invoke-WebRequest { throw 'read-only call attempted download' }\n"
        f"$forward = @({quoted})\n"
        f". '{entry}' @forward\n"
        "if ($_awUpdateHandled) { throw 'read-only call was intercepted' }\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [native_powershell, "-NoProfile", "-File", str(harness)],
        capture_output=True, timeout=15, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("front", ["global", "payload"])
def test_supervised_debug_keeps_foreground_bootstrap_route(
    update_entry_case, native_powershell, front,
):
    tmp, _home, _resolver = update_entry_case
    entry = PLUGIN / "bin" / "agent-worktrees.ps1"
    if front == "payload":
        entry = PLUGIN / "bin" / "payload" / "agent-worktrees.ps1"
    harness = tmp / "supervised-bootstrap.ps1"
    harness.write_text(
        "function Invoke-WebRequest { param($Uri,$OutFile,[switch]$UseBasicParsing)\n"
        "  [IO.File]::WriteAllText($OutFile, @'\n"
        "[IO.File]::WriteAllText($env:BOOT_ARGS, (ConvertTo-Json -InputObject @($args)))\n"
        "exit 23\n"
        "'@)\n}\n"
        f"& '{entry}' --project sample update --supervised-debug\n"
        "exit $LASTEXITCODE\n", encoding="utf-8",
    )
    result = subprocess.run(
        [native_powershell, "-NoProfile", "-File", str(harness)],
        capture_output=True, timeout=20, **no_window_kwargs(),
    )
    assert result.returncode == 23, result.stdout + result.stderr
    assert json.loads((tmp / "bootstrap.args").read_text()) == [
        "--bootstrap-update", "update", "--project", "sample", "--supervised-debug",
    ]


@pytest.mark.parametrize("front", ["git-sh", "git-bash"])
def test_admitted_git_sh_no_manager_reaches_installed_runtime(
    update_entry_case, native_powershell, monkeypatch, front,
):
    tmp, home, _resolver = update_entry_case
    _use_real_probe_python(monkeypatch)
    runtime = home / ".agent-worktrees"
    capture = tmp / "installed-runtime.py"
    capture.write_text(
        "import json, os, sys\n"
        + _CACHE_PROBE +
        "with open(os.environ['RUNTIME_ARGUMENTS'], 'w') as stream:\n"
        "    json.dump({'args': sys.argv[1:], 'msys': os.environ.get('MSYS2_ARG_CONV_EXCL')}, stream)\n"
        "raise SystemExit(47)\n", encoding="utf-8",
    )
    command = tmp / "installed-runtime.cmd"
    command.write_text(
        f'@"{sys.executable}" -I -S -B "{capture}" %*\n@exit /b %ERRORLEVEL%\n',
        encoding="utf-8",
    )
    (runtime / "bin" / "resolve-runtime.ps1").write_text(
        "$AwPy = '" + str(command).replace("'", "''") + "'\n", encoding="utf-8",
    )
    (runtime / "current-version").write_text("selected\n", encoding="utf-8")
    proof = tmp / "runtime-arguments.json"
    monkeypatch.setenv("RUNTIME_ARGUMENTS", str(proof))
    arguments = ["--project", "sample", "update", "--no-manager", "--force"]
    if front == "git-sh":
        monkeypatch.setenv("AGENT_WORKTREES_INTERNAL_UPDATE_PAYLOAD", "0")
        harness = tmp / "no-manager.ps1"
        entry = PLUGIN / "bin" / "agent-worktrees-update-entry.ps1"
        harness.write_text(
            _SUPPORTED_UPDATE_SHELL +
            f"& '{entry}' --project sample update --no-manager --force\n"
            "exit $LASTEXITCODE\n", encoding="utf-8",
        )
        argv = [native_powershell, "-NoProfile", "-File", str(harness)]
    else:
        from native_shell_support import ShellUnavailable, admit_native_bash, bash_path

        try:
            bash = admit_native_bash()
        except ShellUnavailable as error:
            pytest.skip(str(error))
        _stub_admission_for_bash(tmp, native_powershell, bash)
        argv = [bash, bash_path(bash, PLUGIN / "bin" / "agent-worktrees"), *arguments]
    result = subprocess.run(argv, capture_output=True, timeout=20, **no_window_kwargs())
    assert result.returncode == 47, result.stdout + result.stderr
    record = json.loads(proof.read_text())
    assert record["msys"] is None
    forwarded = record["args"]
    _assert_cache_arguments(forwarded[:2], runtime)
    assert forwarded[2:] == ["-m", "agent_worktrees", *arguments]
    assert not list(tmp.glob("agent-update-*"))


@pytest.mark.parametrize("project_position", ["before", "after"])
@pytest.mark.parametrize("front", [
    "global", "installed-global", "native-global", "git-sh", "git-bash", "native-git-bash",
])
@pytest.mark.parametrize("runtime_source", ["marketplace", "git"])
def test_configured_project_update_routes_before_manager_bootstrap(
    update_entry_case, native_powershell, monkeypatch, project_position, front, runtime_source,
):
    tmp, home, _resolver = update_entry_case
    _use_real_probe_python(monkeypatch)
    manager = home / ".worktree-manager"
    manager.mkdir()
    policy = (
        '[runtime_source]\nchannel = "marketplace"\n'
        if runtime_source == "marketplace" else
        '[runtime_source]\nchannel = "exact"\n'
        'repo = "https://example.invalid/tools.git"\nref = "' + ("a" * 40) + '"\n'
    )
    (manager / "config.toml").write_text(policy, encoding="utf-8")
    runtime = home / ".agent-worktrees"
    (runtime / "current-version").write_text("selected\n")
    engine = tmp / "engine.py"
    engine.write_text(
        "import json, os, sys\n"
        + _CACHE_PROBE +
        "with open(os.environ['ENGINE_CALLS'], 'a', encoding='utf-8') as stream:\n"
        "    stream.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "if 'get' in sys.argv:\n"
        "    print('tools/update.py')\n"
        "    raise SystemExit(0)\n"
        "with open(os.environ['ENGINE_ENV'], 'w', encoding='utf-8') as stream:\n"
        "    json.dump({'msys_excl': os.environ.get('MSYS2_ARG_CONV_EXCL')}, stream)\n"
        "raise SystemExit(47)\n",
        encoding="utf-8",
    )
    command = tmp / "engine.cmd"
    command.write_text(f'@"{sys.executable}" -I -S -B "{engine}" %*\n@exit /b %ERRORLEVEL%\n')
    resolver = runtime / "bin" / "resolve-runtime.ps1"
    resolver.write_text("$AwPy = '" + str(command).replace("'", "''") + "'\n")
    monkeypatch.setenv("ENGINE_CALLS", str(tmp / "engine-calls.jsonl"))
    monkeypatch.setenv("ENGINE_ENV", str(tmp / "engine-env.json"))
    arguments = (
        ["--project", "sample", "update", "--force", "--skip-modules", "a b"]
        if project_position == "before"
        else ["update", "--force", "--project", "sample", "--skip-modules", "a b"]
    )
    if front.startswith("native-"):
        _copy_native_global_tools(home, monkeypatch, "ps1")
    if front in ("global", "installed-global", "native-global"):
        if front in ("installed-global", "native-global"):
            user_bin = home / ".local" / "bin"
            if front == "installed-global":
                user_bin.mkdir(parents=True)
                for name in ("agent-worktrees.ps1", "agent-worktrees-update-entry.ps1"):
                    shutil.copy2(PLUGIN / "bin" / name, user_bin / name)
                (runtime / "payload-dir").write_text(str(PLUGIN), encoding="utf-8")
            entry_path = user_bin / "agent-worktrees.ps1"
        else:
            entry_path = PLUGIN / "bin" / "agent-worktrees.ps1"
        entry = str(entry_path).replace("'", "''")
        harness = tmp / "project-controller-entry.ps1"
        quoted = ",".join("'" + value.replace("'", "''") + "'" for value in arguments)
        harness.write_text(
            _SUPPORTED_UPDATE_SHELL +
            "function Invoke-WebRequest { throw 'Manager bootstrap was started' }\n"
            f"$forward = @({quoted})\n"
            f"& '{entry}' @forward\n"
            "exit $LASTEXITCODE\n",
            encoding="utf-8",
        )
        command = [native_powershell, "-NoProfile", "-File", str(harness)]
    elif front == "git-sh":
        monkeypatch.setenv("AGENT_WORKTREES_INTERNAL_UPDATE_PAYLOAD", "0")
        monkeypatch.setenv("MSYS2_ARG_CONV_EXCL", "*")
        monkeypatch.setenv("WORKTREE_MANAGER_BOOTSTRAP_REPO", "-disabled-for-test")
        harness = tmp / "git-sh-entry.ps1"
        quoted = ",".join("'" + value.replace("'", "''") + "'" for value in arguments)
        harness.write_text(
            _SUPPORTED_UPDATE_SHELL +
            f"$forward = @({quoted})\n"
            f"& '{PLUGIN / 'bin' / 'agent-worktrees-update-entry.ps1'}' @forward\n"
            "exit $LASTEXITCODE\n", encoding="utf-8",
        )
        command = [native_powershell, "-NoProfile", "-File", str(harness)]
    else:
        from native_shell_support import ShellUnavailable, admit_native_bash, bash_path

        try:
            bash = admit_native_bash()
        except ShellUnavailable as error:
            pytest.skip(str(error))
        _stub_admission_for_bash(tmp, native_powershell, bash)
        monkeypatch.setenv("WORKTREE_MANAGER_BOOTSTRAP_REPO", "-disabled-for-test")
        command = [
            bash, bash_path(bash, (
                home / ".local" / "bin" if front == "native-git-bash" else PLUGIN / "bin"
            ) / "agent-worktrees"), *arguments,
        ]
    result = subprocess.run(
        command, capture_output=True, timeout=30, **no_window_kwargs(),
    )
    assert result.returncode == 47, result.stdout + result.stderr
    calls = [
        json.loads(line)
        for line in (tmp / "engine-calls.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(calls) == 2, calls
    assert calls[0][-4:] == ["--project", "sample", "get", "update-controller"]
    assert calls[1][-len(arguments):] == arguments
    assert json.loads((tmp / "engine-env.json").read_text(encoding="utf-8"))["msys_excl"] is None
    assert (manager / "config.toml").read_text(encoding="utf-8") == policy


@pytest.mark.parametrize("selection", ["old-engine", "unconfigured", "opt-out"])
def test_project_controller_probe_preserves_manager_fallback(
    update_entry_case, native_powershell, monkeypatch, selection,
):
    tmp, home, _resolver = update_entry_case
    _use_real_probe_python(monkeypatch)
    runtime = home / ".agent-worktrees"
    (runtime / "current-version").write_text("selected\n")
    engine = tmp / "probe.py"
    engine.write_text(
        "import json, os, sys\n"
        + _CACHE_PROBE +
        "from pathlib import Path\n"
        "Path(os.environ['PROBE_CALLED']).write_text('called')\n"
        "raise SystemExit(2 if os.environ['PROBE_MODE'] == 'old-engine' else 0)\n",
        encoding="utf-8",
    )
    command = tmp / "probe.cmd"
    command.write_text(f'@"{sys.executable}" -I -S -B "{engine}" %*\n@exit /b %ERRORLEVEL%\n')
    (runtime / "bin" / "resolve-runtime.ps1").write_text(
        "$AwPy = '" + str(command).replace("'", "''") + "'\n",
    )
    monkeypatch.setenv("PROBE_CALLED", str(tmp / "probe-called"))
    monkeypatch.setenv("PROBE_MODE", selection)
    entry = str(PLUGIN / "bin" / "agent-worktrees.ps1").replace("'", "''")
    harness = tmp / "fallback.ps1"
    extra = "'--no-project-controller'" if selection == "opt-out" else ""
    harness.write_text(
        _SUPPORTED_UPDATE_SHELL +
        "function Invoke-WebRequest {\n"
        "  param($Uri, $OutFile, [switch]$UseBasicParsing)\n"
        "  [IO.File]::WriteAllText($OutFile, 'exit 23')\n"
        "}\n"
        f"& '{entry}' --project sample update {extra}\n"
        "exit $LASTEXITCODE\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [native_powershell, "-NoProfile", "-File", str(harness)],
        capture_output=True, timeout=30, **no_window_kwargs(),
    )
    assert result.returncode == 23, result.stdout + result.stderr
    assert (tmp / "probe-called").exists() is (selection != "opt-out")


def test_namespaced_payload_controller_routes_before_manager_bootstrap(
    update_entry_case, native_powershell, monkeypatch,
):
    tmp, home, _resolver = update_entry_case
    _use_real_probe_python(monkeypatch)
    payload = tmp / "payload"
    (payload / "bin").mkdir(parents=True)
    (payload / "scripts" / "installation-context").mkdir(parents=True)
    shutil.copy2(PLUGIN / "bin" / "agent-worktrees-update-entry.ps1", payload / "bin")
    shutil.copy2(PLUGIN / "scripts" / "invoke-payload-runtime.ps1", payload / "scripts")
    shutil.copy2(PLUGIN / "scripts" / "update_controller_probe.py", payload / "scripts")
    for name in ("shell-caller-owned.ps1", "daemon_instance_runtime.pyz"):
        shutil.copy2(
            PLUGIN / "scripts" / "installation-context" / name,
            payload / "scripts" / "installation-context" / name,
        )
    runtime = tmp / "namespaced-runtime"
    runtime.mkdir()
    context = tmp / "a" / "b" / "c" / "d" / "context.json"
    context.parent.mkdir(parents=True)
    context.write_text("{}", encoding="utf-8")
    engine = tmp / "namespaced-engine.py"
    engine.write_text(
        "import json, os, sys\n"
        "with open(os.environ['ENGINE_CALLS'], 'a', encoding='utf-8') as stream:\n"
        "    stream.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "if '-c' in sys.argv:\n"
        "    print('cache')\n"
        "    raise SystemExit(0)\n"
        "if 'get' in sys.argv:\n"
        "    print('tools/update.py')\n"
        "    raise SystemExit(0)\n"
        "raise SystemExit(47)\n",
        encoding="utf-8",
    )
    command = tmp / "namespaced-engine.cmd"
    command.write_text(f'@"{sys.executable}" -I -S -B "{engine}" %*\n@exit /b %ERRORLEVEL%\n')
    (payload / "scripts" / "resolve-runtime.ps1").write_text(
        "$AgentRtPy = '" + str(command).replace("'", "''") + "'\n",
    )
    (payload / "scripts" / "install.ps1").write_text("throw 'unexpected install'\n")
    status = {
        "status": "ready", "reason": "namespaced-active",
        "actualMode": "namespaced", "desiredMode": "namespaced",
        "activationGeneration": 1, "installGeneration": 2,
        "runtimeRoot": str(runtime), "context": str(context),
        "contextValidation": {"namespaceGeneration": 3, "generation": 2},
    }
    script = payload / "scripts" / "installation-context" / "installation-context.ps1"
    script.write_text(
        "param($Action,$PayloadRoot,$PluginId,$LegacyRoot,[switch]$WithContextValidation,"
        "$ExpectedPluginId,$ExpectedPayloadRoot,$Context,$DurableHome)\n"
        "'" + json.dumps(status).replace("'", "''") + "'\n"
        "exit 0\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_WORKTREES_PAYLOAD_ROOT", str(payload))
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(context))
    monkeypatch.setenv("ENGINE_CALLS", str(tmp / "namespaced-engine-calls.jsonl"))
    entry = str(payload / "scripts" / "invoke-payload-runtime.ps1").replace("'", "''")
    harness = tmp / "namespaced-entry.ps1"
    harness.write_text(
        _SUPPORTED_UPDATE_SHELL +
        "function Invoke-WebRequest { throw 'Manager bootstrap was started' }\n"
        f"& '{entry}' --project sample update --force\n"
        "exit $LASTEXITCODE\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [native_powershell, "-NoProfile", "-File", str(harness)],
        capture_output=True, timeout=40, **no_window_kwargs(),
    )
    assert result.returncode == 47, result.stdout + result.stderr
    calls = [
        json.loads(line)
        for line in (tmp / "namespaced-engine-calls.jsonl").read_text(
            encoding="utf-8",
        ).splitlines()
    ]
    routed = [args for args in calls if "-m" in args]
    assert len(routed) == 2, calls
    assert routed[0][-4:] == ["--project", "sample", "get", "update-controller"]
    assert routed[1][-4:] == ["--project", "sample", "update", "--force"]


@pytest.mark.parametrize("front", ["shell", "driver"])
def test_controller_probe_timeout_retires_its_descendants(
    update_entry_case, native_powershell, monkeypatch, front,
):
    tmp, home, _resolver = update_entry_case
    _use_real_probe_python(monkeypatch)
    helpers = PLUGIN.parents[1] / "libs" / "agent-procutil" / "tests"
    monkeypatch.syspath_prepend(str(helpers))
    from caller_owned_fixture import identity
    from test_caller_owned_native import PinnedProcess

    payload = tmp / "probe-payload"
    (payload / "bin").mkdir(parents=True)
    scripts = payload / "scripts"
    (scripts / "installation-context").mkdir(parents=True)
    (payload / "plugin.json").write_text('{"name":"agent-worktrees"}', encoding="utf-8")
    shutil.copy2(PLUGIN / "bin" / "agent-worktrees.ps1", payload / "bin")
    entry = (PLUGIN / "bin" / "agent-worktrees-update-entry.ps1").read_text(encoding="utf-8")
    assert entry.count("-TimeoutSeconds 30") == 1
    (payload / "bin" / "agent-worktrees-update-entry.ps1").write_text(
        entry.replace("-TimeoutSeconds 30", "-TimeoutSeconds 5"), encoding="utf-8",
    )
    shutil.copy2(PLUGIN / "scripts" / "update_controller_probe.py", scripts)
    for name in ("shell-caller-owned.ps1", "daemon_instance_runtime.pyz"):
        shutil.copy2(
            PLUGIN / "scripts" / "installation-context" / name,
            scripts / "installation-context" / name,
        )
    runtime = home / ".agent-worktrees"
    (runtime / "current-version").write_text("selected\n", encoding="utf-8")
    slow = tmp / "slow-engine.py"
    slow.write_text(
        "import json, os, subprocess, sys, time\n"
        + _CACHE_PROBE +
        "from pathlib import Path\n"
        "sys.path.insert(0, os.environ['PROBE_TEST_HELPERS'])\n"
        "from caller_owned_fixture import identity\n"
        "child = subprocess.Popen([sys.executable, '-I', '-S', '-c', "
        "'import time; time.sleep(30)'], creationflags=subprocess.CREATE_NO_WINDOW)\n"
        "root = Path(os.environ['PROBE_TEST_ROOT'])\n"
        "pending = root / 'pending.json'\n"
        "pending.write_text(json.dumps([identity(), identity(child.pid)]), encoding='utf-8')\n"
        "pending.replace(root / 'ready.json')\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    command = tmp / "slow-engine.cmd"
    command.write_text(f'@"{sys.executable}" -I -S -B "{slow}" %*\n@exit /b %ERRORLEVEL%\n')
    (runtime / "bin" / "resolve-runtime.ps1").write_text(
        "$AwPy = '" + str(command).replace("'", "''") + "'\n",
    )
    monkeypatch.setenv("PROBE_TEST_HELPERS", str(helpers))
    monkeypatch.setenv("PROBE_TEST_ROOT", str(tmp))
    monkeypatch.setenv("WORKTREE_MANAGER_BOOTSTRAP_REPO", "-disabled-for-test")
    if front == "shell":
        harness = tmp / "shell-probe.ps1"
        harness.write_text(
            _SUPPORTED_UPDATE_SHELL +
            f"& '{payload / 'bin' / 'agent-worktrees.ps1'}' --project sample update\n"
            "exit $LASTEXITCODE\n", encoding="utf-8",
        )
        argv = [
            native_powershell, "-NoProfile", "-File", str(harness),
        ]
    else:
        harness = tmp / "driver-probe.py"
        paths = [str(Path(entry).resolve()) for entry in sys.path if entry]
        harness.write_text(
            "import argparse, sys\n"
            f"sys.path[:0] = {paths!r}\n"
            "from agent_worktrees import config, project_update_controller as controller\n"
            "config.set_active_project('sample')\n"
            f"controller._probe_command = lambda _project: [sys.executable, '-I', '-S', '-B', {str(slow)!r}]\n"
            "try:\n"
            f"    controller.probe_for_submission(argparse.Namespace(), cwd={str(tmp)!r}, timeout=5)\n"
            "except ValueError as error:\n"
            "    print(error, file=sys.stderr)\n"
            "    raise SystemExit(124 if 'probe timed out' in str(error) else 1)\n"
            "raise SystemExit(1)\n",
            encoding="utf-8",
        )
        argv = [sys.executable, "-I", "-B", str(harness)]
    process = subprocess.Popen(
        argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **no_window_kwargs(),
    )
    pins = []
    try:
        deadline = time.monotonic() + 10
        ready = tmp / "ready.json"
        while not ready.is_file() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        if not ready.is_file() and process.poll() is not None:
            stdout, stderr = process.communicate(timeout=5)
            pytest.fail(f"controller probe exited before descendants started: {stdout!r} {stderr!r}")
        assert ready.is_file(), "the controller probe did not start its descendants"
        pins = [PinnedProcess(row) for row in json.loads(ready.read_text(encoding="utf-8"))]
        stdout, stderr = process.communicate(timeout=15)
        assert process.returncode == 124, stdout + stderr
        deadline = time.monotonic() + 3
        while not all(pin.gone() for pin in pins) and time.monotonic() < deadline:
            time.sleep(0.02)
        assert all(pin.gone() for pin in pins), "controller probe descendants survived timeout"
    finally:
        if process.poll() is None:
            parent = PinnedProcess(identity(process.pid))
            try:
                parent.kill()
            finally:
                parent.close()
            process.wait(timeout=5)
        for pin in pins:
            try:
                pin.kill()
            finally:
                pin.close()
        process.stdout.close()
        process.stderr.close()


@pytest.mark.parametrize("observer", ["default", "supervised"])
@pytest.mark.parametrize("no_manager", [False, True])
@pytest.mark.timeout(90)
def test_native_entry_controller_custody_requires_supported_launch(
    update_entry_case, native_powershell, monkeypatch, observer, no_manager,
):
    from agent_procutil.owned_process import run_installation_owned
    from agent_procutil.daemon_instances import REGISTRY_ENV, daemon_test_lifecycle

    tmp, home, _resolver = update_entry_case
    _use_real_probe_python(monkeypatch)
    monkeypatch.setenv("COPILOT_EXTENSIONS_TEST_CONTAINED", "1")
    runtime = home / ".agent-worktrees"
    (runtime / "current-version").write_text("selected\n")
    engine = tmp / "controller-engine.py"
    engine.write_text(
        "import json, subprocess, sys\n"
        + _CACHE_PROBE +
        "if 'get' in sys.argv:\n"
        "    print('tools/update.py')\n"
        "    raise SystemExit(0)\n"
        "raise SystemExit(subprocess.run([sys.executable, *sys.argv[1:]], "
        "stdin=subprocess.DEVNULL, stdout=sys.stdout, stderr=sys.stderr, "
        "creationflags=subprocess.CREATE_NO_WINDOW).returncode)\n",
        encoding="utf-8",
    )
    command = tmp / "controller-engine.cmd"
    command.write_text(
        f'@"{sys.executable}" -I -S -B "{engine}" %*\n@exit /b %ERRORLEVEL%\n',
        encoding="utf-8",
    )
    (runtime / "bin" / "resolve-runtime.ps1").write_text(
        "$AwPy = '" + str(command).replace("'", "''") + "'\n",
    )
    anchor = tmp / "project"
    config = anchor / ".copilot-extensions" / "agent-worktrees"
    config.mkdir(parents=True)
    controller = anchor / "tools" / "update.py"
    controller.parent.mkdir()
    receipt = tmp / "controller-receipt.json"
    controller.write_text(
        "import json, sys\n"
        "from pathlib import Path\n"
        "from update_driver import borrowed_custody\n"
        "with borrowed_custody() as custody:\n"
        "    custody.assert_owned()\n"
        f"    Path({str(receipt)!r}).write_text(json.dumps(sys.argv[1:]), encoding='utf-8')\n"
        "print('controller completed')\n",
        encoding="utf-8",
    )
    (config / "config.yaml").write_text(json.dumps({
        "update_controller": {"windows": "tools/update.py"},
    }), encoding="utf-8")
    (runtime / "projects.yaml").write_text(json.dumps({
        "projects": {"sample": {"anchor": str(anchor)}},
    }), encoding="utf-8")
    (runtime / "repos.yaml").write_text(json.dumps({
        "repos": {"sample": {"class": "worktree", "windows": str(anchor)}},
    }), encoding="utf-8")
    monkeypatch.setenv("AGENT_HOME", str(home))
    entry = str(PLUGIN / "bin" / "agent-worktrees.ps1").replace("'", "''")
    harness = tmp / "real-controller-entry.ps1"
    mode = "--supervised-debug" if observer == "supervised" else ""
    skip = "--no-manager" if no_manager else ""
    harness.write_text(
        "function Invoke-WebRequest { throw 'Manager bootstrap was started' }\n"
        f"& '{entry}' --project sample update {mode} {skip} --force\n"
        "exit $LASTEXITCODE\n",
        encoding="utf-8",
    )
    registry = tmp / "daemon-instances"
    cleanup = []
    with daemon_test_lifecycle(
        owner="project-controller-entry-test", registry_root=registry, cleanup_results=cleanup,
    ) as overlay:
        for key, value in overlay.items():
            monkeypatch.setenv(key, value)
        monkeypatch.setenv(REGISTRY_ENV, str(registry))
        monkeypatch.chdir(anchor)
        try:
            result = run_installation_owned(
                [native_powershell, "-NoProfile", "-File", str(harness)],
                timeout=75, max_stdout_bytes=1024 * 1024, max_stderr_bytes=1024 * 1024,
            )
        except subprocess.TimeoutExpired as error:
            logs = {
                str(path.relative_to(home)): path.read_text(errors="replace")[-8000:]
                for path in home.rglob("*.log")
            }
            pytest.fail(f"controller timeout: {error.stdout!r} {error.stderr!r}; logs={logs!r}")
    assert all(item.status != "held" for item in cleanup), cleanup
    if observer == "default":
        assert result.returncode == 1, (result.stdout, result.stderr)
        assert "detached/outside-Job shell mode" in result.stderr
        assert not receipt.exists()
        assert not (home / ".copilot-extensions" / "update-driver").exists()
        return
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert json.loads(receipt.read_text(encoding="utf-8")) == (
        (["--no-manager"] if no_manager else []) + ["--force"]
    )
    assert "controller completed" in result.stdout + result.stderr


@pytest.mark.parametrize(("front", "available"), [
    ("global", False), ("global", True),
    ("installed-project", False), ("installed-project", True),
    ("payload", False),
])
@pytest.mark.parametrize("flags", [
    ["--phase", "preflight", "--plugin", "agent-example"],
    ["--phase=runtimes", "--plugin=agent-example"],
    ["--plugin", "agent-example"],
    ["--inventory", "--json"],
    ["--target", "agent-example"],
    ["--target=agent-example"],
    ["--runtime-backend", "agent-worktrees=cpu"],
    ["--runtime-backend=agent-worktrees=cpu"],
])
def test_selected_update_operation_bypasses_native_repair_and_implicit_provision(
    update_entry_case, native_powershell, monkeypatch, front, available, flags,
):
    from agent_worktrees import project_command_templates

    tmp, home, _resolver = update_entry_case
    monkeypatch.delenv("AGENT_WORKTREES_NO_SELFPROVISION", raising=False)
    capture = tmp / "phase-capture.py"
    capture.write_text(
        "import json, os, sys\n"
        + _CACHE_PROBE +
        "with open(os.environ['PHASE_ARGUMENTS'], 'w') as stream:\n"
        "    json.dump(sys.argv[1:], stream)\n"
        "raise SystemExit(29)\n",
    )
    command = tmp / "phase-python.cmd"
    command.write_text(f'@"{sys.executable}" -I -S -B "{capture}" %*\n@exit /b %ERRORLEVEL%\n')
    if available:
        resolver = home / ".agent-worktrees" / "bin" / "resolve-runtime.ps1"
        resolver.parent.mkdir(parents=True, exist_ok=True)
        resolver.write_text("$AwPy = '" + str(command).replace("'", "''") + "'\n")
    entry = PLUGIN / "bin" / "agent-worktrees.ps1"
    arguments = ["--project", "sample", "update", *flags]
    if front == "payload":
        entry = PLUGIN / "bin" / "payload" / "agent-worktrees.ps1"
        # Native context resolution needs a real Python, not the POSIX repair sentinel.
        monkeypatch.setenv("PATH", str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"])
    elif front == "installed-project":
        user_bin = home / ".local" / "bin"
        user_bin.mkdir(parents=True)
        for name in ("agent-worktrees.ps1", "agent-worktrees-update-entry.ps1"):
            shutil.copy2(PLUGIN / "bin" / name, user_bin / name)
        entry = tmp / "sample.ps1"
        entry.write_text(project_command_templates.render(
            "sample", PLUGIN, windows=True, user_bin=user_bin,
        )["sample.ps1"])
        arguments = ["update", *flags]
    proof = tmp / "phase-arguments.json"
    escaped = lambda text: "'" + str(text).replace("'", "''") + "'"
    harness = tmp / "phase-entry.ps1"
    compilation_guard = (
        "function Add-Type { throw 'phase attempted source compilation' }\n"
        if front != "payload" else ""
    )
    harness.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        "function Invoke-WebRequest { throw 'phase attempted bootstrap download' }\n"
        f"{compilation_guard}"
        f"$env:PHASE_ARGUMENTS = {escaped(proof)}\n"
        f"$phaseArgs = @({','.join(escaped(value) for value in arguments)})\n"
        f"& {escaped(entry)} @phaseArgs\n"
        "exit $LASTEXITCODE\n",
    )
    result = subprocess.run(
        [native_powershell, "-NoProfile", "-File", str(harness)],
        capture_output=True, timeout=20, **no_window_kwargs(),
    )
    assert result.returncode == (29 if available else 127), result.stdout + result.stderr
    if available:
        args = json.loads(proof.read_text())
        _assert_cache_arguments(args[:2], home / ".agent-worktrees")
        assert args[2:] == [
            "-m", "agent_worktrees", "--project", "sample", "update", *flags,
        ]
    else:
        assert not proof.exists()
        assert b"requires a provisioned runtime" in result.stderr
    assert not list(tmp.glob("agent-update-*"))


@pytest.mark.parametrize("original", ["absent", "empty", "selected"])
@pytest.mark.parametrize("edition", ["configured", "core"])
def test_update_entry_windows_scrubs_and_restores_git_selector_presence(
    update_entry_case, native_powershell, original, edition,
):
    tmp, _home, _resolver = update_entry_case
    if edition == "core":
        native_powershell = shutil.which("pwsh")
        if native_powershell is None:
            pytest.skip("PowerShell Core is unavailable")
    harness = tmp / "git-environment.ps1"
    entry = str(PLUGIN / "bin/agent-worktrees.ps1").replace("'", "''")
    proof = str(tmp / "git-environment.json").replace("'", "''")
    harness.write_text(
        _SUPPORTED_UPDATE_SHELL +
        "$ErrorActionPreference = 'Stop'\n"
        "$names = @('GIT_DIR','GIT_WORK_TREE','GIT_COMMON_DIR','GIT_INDEX_FILE',"
        "'GIT_PREFIX','GIT_OBJECT_DIRECTORY','GIT_ALTERNATE_OBJECT_DIRECTORIES')\n"
        f"$original = '{original}'\n"
        "foreach ($name in $names) {\n"
        "  if ($original -eq 'absent') {\n"
        "    [Environment]::SetEnvironmentVariable($name, "
        "[System.Management.Automation.Language.NullString]::Value)\n"
        "  } else {\n"
        "    $value = if ($original -eq 'empty') { '' } else { 'synthetic-routing' }\n"
        "    [Environment]::SetEnvironmentVariable($name, $value)\n"
        "  }\n"
        "}\n"
        "$before = @{}\n"
        "foreach ($name in $names) { $before[$name] = "
        "[Environment]::GetEnvironmentVariable($name) }\n"
        "function Invoke-WebRequest {\n"
        "  param($Uri, $OutFile, [switch]$UseBasicParsing)\n"
        "  [IO.File]::WriteAllText($OutFile, @'\n"
        "$names = @('GIT_DIR','GIT_WORK_TREE','GIT_COMMON_DIR','GIT_INDEX_FILE',"
        "'GIT_PREFIX','GIT_OBJECT_DIRECTORY','GIT_ALTERNATE_OBJECT_DIRECTORIES')\n"
        "foreach ($name in $names) {\n"
        "  if ($null -ne [Environment]::GetEnvironmentVariable($name)) {\n"
        "    throw \"Bootstrap inherited Git selector: $name\"\n"
        "  }\n"
        "}\n"
        "exit 23\n"
        "'@)\n"
        "}\n"
        f"& '{entry}' update\n"
        "$code = $LASTEXITCODE\n"
        "$after = @{}\n"
        "foreach ($name in $names) { $after[$name] = "
        "[Environment]::GetEnvironmentVariable($name) }\n"
        f"[IO.File]::WriteAllText('{proof}', "
        "(@{before=$before;after=$after} | ConvertTo-Json))\n"
        "exit $code\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [native_powershell, "-NoProfile", "-File", str(harness)],
        capture_output=True, timeout=45, **no_window_kwargs(),
    )
    assert result.returncode == 23, result.stdout + result.stderr
    state = json.loads((tmp / "git-environment.json").read_text())
    if original == "absent":
        assert all(value is None for value in state["before"].values())
    assert state["after"] == state["before"]


def test_update_entry_windows_scratch_token_user_acl(
    update_entry_case, native_powershell, monkeypatch,
):
    tmp, home, _resolver = update_entry_case
    root = home / ".worktree-manager"
    root.mkdir()
    (root / "current-version").write_text("missing-runtime\n")
    monkeypatch.setenv("ACL_PROOF", str(tmp / "acl.json"))
    harness = tmp / "acl.ps1"
    entry = str(PLUGIN / "bin/agent-worktrees.ps1").replace("'", "''")
    harness.write_text(
        _SUPPORTED_UPDATE_SHELL + r"""
function Get-NativeAcl([string]$Path) {
    $item = if ([IO.Directory]::Exists($Path)) {
        [IO.DirectoryInfo]::new($Path)
    } else { [IO.FileInfo]::new($Path) }
    if ($PSVersionTable.PSEdition -eq 'Core') {
        return [IO.FileSystemAclExtensions]::GetAccessControl($item)
    }
    return $item.GetAccessControl()
}
function Invoke-WebRequest {
    param($Uri, $OutFile, [switch]$UseBasicParsing)
    $root = Split-Path $OutFile
    $user = [Security.Principal.WindowsIdentity]::GetCurrent().User
    $acl = Get-NativeAcl $root
    $collisionRefused = $false
    try {
        [CopilotExtensions.UpdateScratchDirectory]::Create(
            $root, $acl.GetSecurityDescriptorBinaryForm())
    }
    catch { $collisionRefused = $_.Exception.InnerException.NativeErrorCode -eq 183 }
    $child = Join-Path $root 'child'
    [IO.Directory]::CreateDirectory($child) | Out-Null
    $probe = Join-Path $child 'probe'
    [IO.File]::WriteAllText($probe, 'probe')
    $rights = [Security.AccessControl.FileSystemRights]::FullControl
    $inherit = [Security.AccessControl.InheritanceFlags]::ContainerInherit -bor
        [Security.AccessControl.InheritanceFlags]::ObjectInherit
    $own = @($acl.Access | Where-Object {
        $_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]) -eq $user -and
        ($_.FileSystemRights -band $rights) -eq $rights -and
        ($_.InheritanceFlags -band $inherit) -eq $inherit
    })
    $childAccess = @((Get-NativeAcl $probe).Access | Where-Object {
        $_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]) -eq $user -and
        $_.IsInherited -and ($_.FileSystemRights -band $rights) -eq $rights
    })
    $allowed = @($user.Value, 'S-1-5-18', 'S-1-5-32-544')
    $foreign = @($acl.Access | Where-Object {
        $sid = $_.IdentityReference.Translate([Security.Principal.SecurityIdentifier])
        $sid.Value -notin $allowed
    })
    $proof = @{
        ownerIsTokenUser = $acl.GetOwner([Security.Principal.SecurityIdentifier]) -eq $user
        protected = $acl.AreAccessRulesProtected
        fullInheritedControl = $own.Count -gt 0
        childHasUserControl = $childAccess.Count -gt 0
        noForeignAce = $foreign.Count -eq 0
        collisionRefused = $collisionRefused
        unchangedAfterCollision = (Get-NativeAcl $root).Sddl -eq $acl.Sddl
        path = $root
    }
    [IO.File]::WriteAllText($env:ACL_PROOF, ($proof | ConvertTo-Json))
    [IO.File]::WriteAllText($OutFile, 'exit 23')
}
"""
        + f"& '{entry}' update\nexit $LASTEXITCODE\n",
    )
    result = subprocess.run(
        [native_powershell, "-NoProfile", "-File", str(harness)],
        capture_output=True, timeout=45, **no_window_kwargs(),
    )
    assert result.returncode == 23, result.stderr.decode(errors="replace")
    proof = json.loads((tmp / "acl.json").read_text())
    path = proof.pop("path")
    assert all(proof.values()), proof
    assert not os.path.exists(path)


@pytest.mark.parametrize("phase", ["healthy", "fetch-failure"])
def test_update_entry_windows_generic_git_exact_argv(
    update_entry_case, native_powershell, monkeypatch, phase,
):
    tmp, home, _resolver = update_entry_case
    root = home / ".worktree-manager"
    root.mkdir()
    repo = r"C:\source with spaces\selected.git"
    ref = "refs/tags/selected"
    (root / "config.toml").write_text(
        f"[source]\nrepo = {json.dumps(repo)}\nref = {json.dumps(ref)}\n",
    )
    monkeypatch.setenv("WORKTREE_MANAGER_BOOTSTRAP_REPO", repo)
    monkeypatch.setenv("WORKTREE_MANAGER_BOOTSTRAP_REF", ref)
    (tmp / "tools/git").unlink()
    monkeypatch.setenv("GIT_ARGS", str(tmp / "git.args"))
    monkeypatch.setenv("GIT_FAIL", "42" if phase == "fetch-failure" else "0")
    downloaded = tmp / "download.ps1"
    downloaded.write_text(
        '$json = ConvertTo-Json -Compress -InputObject @($args)\n'
        '[IO.File]::WriteAllText($env:BOOT_ARGS, $json)\nexit 23\n',
    )
    monkeypatch.setenv("DOWNLOAD_SCRIPT", str(downloaded))
    harness = tmp / "git.ps1"
    entry = str(PLUGIN / "bin/agent-worktrees.ps1").replace("'", "''")
    executable = str(tmp / "tools/git.exe").replace("'", "''")
    harness.write_text(
        _SUPPORTED_UPDATE_SHELL + r"""
if ($PSVersionTable.PSEdition -eq 'Core') { exit 64 }
Add-Type -TypeDefinition @'
using System;
using System.IO;
public class UpdateGitDouble {
    public static int Main(string[] args) {
        File.AppendAllText(Environment.GetEnvironmentVariable("GIT_ARGS"),
            String.Join("\0", args) + "\0\n");
        if (args[0] == "init") {
            Directory.CreateDirectory(args[args.Length - 1]);
        } else if (Array.IndexOf(args, "fetch") >= 0) {
            return Int32.Parse(Environment.GetEnvironmentVariable("GIT_FAIL"));
        } else if (Array.IndexOf(args, "checkout") >= 0) {
            string destination = Path.Combine(args[1], "worktree-manager");
            Directory.CreateDirectory(destination);
            File.Copy(Environment.GetEnvironmentVariable("DOWNLOAD_SCRIPT"),
                Path.Combine(destination, "bootstrap.ps1"));
        } else { return 99; }
        return 0;
    }
}
'@ -OutputType ConsoleApplication -OutputAssembly """
        + f"'{executable}'\n& '{entry}' --project sample update --force\nexit $LASTEXITCODE\n",
    )
    result = subprocess.run(
        [native_powershell, "-NoProfile", "-File", str(harness)],
        capture_output=True, timeout=45, **no_window_kwargs(),
    )
    if result.returncode == 64:
        pytest.skip("fake executable compilation requires Windows PowerShell/.NET Framework")
    expected = 42 if phase == "fetch-failure" else 23
    assert result.returncode == expected, result.stderr.decode(errors="replace")
    calls = [
        line.decode().split("\0")[:-1] for line in (tmp / "git.args").read_bytes().splitlines()
    ]
    checkout = calls[0][-1]
    assert calls[1] == [
        "-C", checkout, "fetch", "--quiet", "--depth", "1", "--", repo, ref,
    ]
    if phase == "healthy":
        assert json.loads((tmp / "bootstrap.args").read_text()) == [
            "--bootstrap-update", "update", "--project", "sample", "--force",
        ]
    else:
        assert not (tmp / "bootstrap.args").exists()
    assert not os.path.exists(os.path.dirname(checkout))
    assert not (tmp / "curl.args").exists()


@pytest.mark.parametrize("override", [False, True])
@pytest.mark.parametrize("front", ["global", "installed-project", "legacy-caller"])
@pytest.mark.parametrize("edition", ["configured", "core"])
def test_main_bootstrap_resolves_runtime_source_after_transport(
    update_entry_case: tuple[Path, Path, Path], native_powershell: str,
    monkeypatch: pytest.MonkeyPatch, override: bool, front: str, edition: str,
) -> None:
    from agent_worktrees import project_command_templates

    if edition == "core":
        core = shutil.which("pwsh")
        if core is None:
            pytest.skip("PowerShell Core is unavailable")
        native_powershell = core
    tmp, home, resolver = update_entry_case
    resolver.unlink()
    root = home / ".worktree-manager"
    root.mkdir()
    repo, ref = r"C:\runtime source\selected.git", "b" * 40
    config = (
        '[runtime_source]\nchannel = "dev"\nallow_dev = true\n'
        f"[source]\nrepo = {json.dumps(repo)}\nref = {json.dumps(ref)}\n"
    )
    if front == "legacy-caller":
        config = '[source]\nrepo = "unterminated\n'
    (root / "config.toml").write_text(config, encoding="utf-8")
    (root / "current-version").write_text("missing-manager")
    if override:
        monkeypatch.setenv("WORKTREE_MANAGER_BOOTSTRAP_REPO", "https://github.com/example/orchestrator.git")
        monkeypatch.setenv("WORKTREE_MANAGER_BOOTSTRAP_REF", "transport-branch")
    tools = tmp / "tools"
    capture = tools / "capture.py"
    capture.write_text(
        "import json, os, sys\n"
        "with open(os.environ['FRESH_ARGS'], 'w') as stream:\n"
        "    json.dump(sys.argv[1:], stream)\n"
        "raise SystemExit(37)\n", encoding="utf-8",
    )
    python = tools / "fresh-python.cmd"
    python.write_text(
        f'@"{sys.executable}" -I -S -B "{capture}" %*\n@exit /b %ERRORLEVEL%\n',
        encoding="utf-8",
    )
    (tools / "uv.ps1").write_text(f"Write-Output '{python}'\nexit 0\n", encoding="utf-8")
    monkeypatch.setenv("FRESH_ARGS", str(tmp / "fresh.args"))
    monkeypatch.setenv("FETCH_ARGS", str(tmp / "fetch.args"))
    monkeypatch.setenv("TRANSPORT_URL", str(tmp / "transport.url"))
    entry = PLUGIN / "bin/agent-worktrees.ps1"
    if front == "installed-project":
        commands = home / ".local/bin"
        commands.mkdir(parents=True)
        for name in ("agent-worktrees.ps1", "agent-worktrees-update-entry.ps1"):
            shutil.copy2(PLUGIN / "bin" / name, commands / name)
        entry = tmp / "sample.ps1"
        entry.write_text(project_command_templates.render(
            "sample", PLUGIN, windows=True, user_bin=commands,
        )["sample.ps1"], encoding="utf-8")
    bootstrap = PLUGIN.parents[1] / "worktree-manager/bootstrap.ps1"
    escaped = lambda value: "'" + str(value).replace("'", "''") + "'"
    arguments = ["update", "--skip-modules", "a b"]
    if front == "global":
        arguments = ["--project", "sample", *arguments]
    elif front == "legacy-caller":
        entry = bootstrap
        arguments = ["--repair-update", "update", ref, "update", "--project", "sample",
                     "--skip-modules", "a b"]
    harness = tmp / "main-transport.ps1"
    harness.write_text(
        _SUPPORTED_UPDATE_SHELL +
        "function Invoke-WebRequest {\n"
        "  param($Uri, $OutFile, [switch]$UseBasicParsing)\n"
        "  [IO.File]::WriteAllText($env:TRANSPORT_URL, [string]$Uri)\n"
        f"  [IO.File]::Copy({escaped(bootstrap)}, $OutFile)\n"
        "}\n"
        "function git {\n"
        "  if ($args[0] -eq 'init') { [IO.Directory]::CreateDirectory($args[-1]) | Out-Null }\n"
        "  if ($args -contains 'fetch') {\n"
        "    [IO.File]::WriteAllText($env:FETCH_ARGS, (ConvertTo-Json -Compress -InputObject @($args)))\n"
        "  }\n"
        "  if ($args -contains 'checkout') {\n"
        "    [IO.Directory]::CreateDirectory((Join-Path $args[1] 'worktree-manager')) | Out-Null\n"
        "  }\n"
        "  $global:LASTEXITCODE = 0\n"
        "}\n"
        f"$forward = @({','.join(escaped(arg) for arg in arguments)})\n"
        f"& {escaped(entry)} @forward\nexit $LASTEXITCODE\n", encoding="utf-8",
    )
    result = subprocess.run(
        [native_powershell, "-NoProfile", "-File", str(harness)],
        capture_output=True, timeout=30, cwd=tmp, **no_window_kwargs(),
    )
    assert result.returncode == 37, result.stdout + result.stderr
    transport = "example/orchestrator/transport-branch" if override else "ThomasMichon/copilot-extensions/main"
    if front == "legacy-caller":
        assert not (tmp / "transport.url").exists()
    else:
        assert (tmp / "transport.url").read_text() == (
            f"https://raw.githubusercontent.com/{transport}/worktree-manager/bootstrap.ps1"
        )
    expected_repo = str(tmp / "update") if front == "legacy-caller" else repo
    assert json.loads((tmp / "fetch.args").read_text())[-2:] == [expected_repo, ref]
    assert json.loads((tmp / "fresh.args").read_text())[-7:] == [
        "--repair-update", "--", "update", "--project", "sample", "--skip-modules", "a b",
    ]
    assert (root / "config.toml").read_text() == config
    assert (root / "current-version").read_text() == "missing-manager"
    assert not (tmp / "resolved").exists()
    assert not list(tmp.glob("agent-update-*"))


@pytest.mark.parametrize("source", [
    'repo = "unterminated',
    'repo = "unsupported\\qescape"',
    'repo = ""',
    'ref = "first"\nref = "second"',
    'repo = "selected"\n[source]\nref = "other"',
])
def test_canonical_bootstrap_rejects_malformed_runtime_source_before_effects(
    update_entry_case: tuple[Path, Path, Path], native_powershell: str, source: str,
) -> None:
    _tmp, home, _resolver = update_entry_case
    root = home / ".worktree-manager"
    root.mkdir()
    config = root / "config.toml"
    content = "[source]\n" + source + "\n"
    config.write_text(content, encoding="utf-8")
    bootstrap = PLUGIN.parents[1] / "worktree-manager/bootstrap.ps1"
    result = subprocess.run(
        [native_powershell, "-NoProfile", "-File", str(bootstrap), "--bootstrap-update", "update"],
        capture_output=True, timeout=15, **no_window_kwargs(),
    )
    assert result.returncode != 0
    assert b"source" in result.stderr.lower()
    assert config.read_text() == content
    assert not (root / "staging").exists()
    assert not (root / "current-version").exists()


@pytest.mark.parametrize("name", ["WORKTREE_MANAGER_BOOTSTRAP_REPO", "WORKTREE_MANAGER_BOOTSTRAP_REF"])
@pytest.mark.parametrize("value", ["-invalid", "invalid\nsource"])
def test_invalid_windows_transport_never_downloads(
    update_entry_case: tuple[Path, Path, Path], native_powershell: str,
    monkeypatch: pytest.MonkeyPatch, name: str, value: str,
) -> None:
    tmp, _home, _resolver = update_entry_case
    monkeypatch.setenv(name, value)
    entry = PLUGIN / "bin/agent-worktrees.ps1"
    harness = tmp / "invalid-transport.ps1"
    harness.write_text(
        _SUPPORTED_UPDATE_SHELL +
        f"& '{str(entry).replace(chr(39), chr(39) * 2)}' update\n"
        "exit $LASTEXITCODE\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [native_powershell, "-NoProfile", "-File", str(harness)],
        capture_output=True, timeout=15, **no_window_kwargs(),
    )
    assert result.returncode != 0
    assert b"Invalid bootstrap transport" in result.stderr
    assert not list(tmp.glob("agent-update-*"))
    assert not (tmp / "resolved").exists()


if __name__ == "__main__":
    import argparse
    import tempfile

    parser = argparse.ArgumentParser(description="Unmocked, offline native breakaway admission check.")
    parser.add_argument("--native-breakaway-root", type=Path, required=True)
    parser.add_argument("--powershell", type=Path, required=True)
    options = parser.parse_args()
    if os.name != "nt" or os.environ.get("COPILOT_EXTENSIONS_TEST_CONTAINED") == "1":
        parser.error("run this native check from an explicit detached/outside-Job host shell")
    if not options.native_breakaway_root.is_absolute() or not options.native_breakaway_root.is_dir():
        parser.error("--native-breakaway-root must name existing private caller-owned scratch")
    if not options.powershell.is_absolute() or not options.powershell.is_file():
        parser.error("--powershell must name an existing absolute native host")
    with tempfile.TemporaryDirectory(prefix="native-breakaway-", dir=options.native_breakaway_root) as directory:
        for selected_front in ("global", "payload"):
            _check_supported_breakaway_entry(
                Path(directory) / selected_front, str(options.powershell), selected_front,
            )
            print(f"PASS {selected_front}: actual admission, offline bootstrap, probe and Job cleanup")


_WAIT_SUBMISSION = r"""
$ast = [Management.Automation.Language.Parser]::ParseFile($env:ENTRY_SCRIPT, [ref]$null, [ref]$null)
$definition = $ast.Find({ param($node)
    $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
    $node.Name -eq 'Wait-AgentQueuedSubmission' }, $true)
. ([scriptblock]::Create($definition.Extent.Text))
$result = Wait-AgentQueuedSubmission ([int]$env:SUBMISSION_PID) ([DateTime]::Parse($env:SUBMITTED_AT)) $env:EXIT_FILE
if ($null -eq $result) { 'none' } else { "code=$result" }
"""


@pytest.mark.parametrize("case", ["exited", "reused", "late-reuse", "unreported"])
def test_queued_submission_that_exits_before_it_is_opened_reports_its_recorded_code(
    tmp_path, native_powershell, case,
):
    """A fast-exiting submission is judged by its recorded exit code, never a reused PID."""
    exit_file = tmp_path / "submission.exit"
    if case != "unreported":
        exit_file.write_text("3", encoding="utf-8")
    submitted_at = time.strftime("%Y-%m-%dT%H:%M:%S")
    unrelated = None
    if case == "exited":
        finished = subprocess.run(
            [native_powershell, "-NoProfile", "-Command", "$PID"],
            capture_output=True, text=True, check=True, **no_window_kwargs(),
        )
        submission_pid = int(finished.stdout.strip())
    elif case == "late-reuse":
        # A live, unrelated process that took the PID after the submission was queued.
        time.sleep(1.5)
        unrelated = subprocess.Popen(
            [native_powershell, "-NoProfile", "-Command", "Start-Sleep 120; exit 9"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            **no_window_kwargs(),
        )
        submission_pid = unrelated.pid
    else:
        # A long-lived process started before the submission stands in for a reused PID.
        submission_pid = os.getpid()
    env = dict(
        os.environ,
        ENTRY_SCRIPT=str(PLUGIN / "bin" / "agent-worktrees-update-entry.ps1"),
        SUBMISSION_PID=str(submission_pid),
        SUBMITTED_AT=submitted_at,
        EXIT_FILE=str(exit_file),
    )
    try:
        started = time.monotonic()
        result = subprocess.run(
            [native_powershell, "-NoProfile", "-NonInteractive", "-Command", _WAIT_SUBMISSION],
            capture_output=True, text=True, env=env, timeout=60, **no_window_kwargs(),
        )
        elapsed = time.monotonic() - started
    finally:
        if unrelated is not None:
            unrelated.kill()
            unrelated.wait(timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    expected = "none" if case == "unreported" else "code=3"
    assert result.stdout.strip().splitlines()[-1] == expected, result.stdout + result.stderr
    assert elapsed < 30, "a reused PID must never be waited on"