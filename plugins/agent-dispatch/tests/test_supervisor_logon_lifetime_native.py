"""Opt-in production-lifetime check of the extracted installer launch seam."""

from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest


REPO = Path(__file__).resolve().parents[3]
SCRIPT = Path(__file__).resolve()
INSTALLER = SCRIPT.parents[1] / "scripts" / "install.ps1"


def _procutil():
    previous = sys.path.copy()
    try:
        sys.path.insert(0, str(REPO / "libs" / "agent-procutil" / "src"))
        import agent_procutil
    finally:
        sys.path[:] = previous

    assert Path(agent_procutil.__file__).resolve().is_relative_to(REPO)
    return agent_procutil


def _wait_file(path: Path, caller: subprocess.Popen) -> None:
    deadline = time.monotonic() + 30
    while not path.exists():
        if caller.poll() is not None:
            raise AssertionError((path.parent / "caller.log").read_text(encoding="utf-8"))
        if time.monotonic() >= deadline:
            diagnostics = path.parent / "inner.log"
            raise TimeoutError(diagnostics.read_text(encoding="utf-8") if diagnostics.exists() else path.name)
        time.sleep(0.05)


def _pin_process(kernel, record, birth):
    handle = kernel.OpenProcess(0x100000 | 0x1000 | 1, False, record["pid"])
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        assert birth(kernel, handle) == record["birth"], "fixture process identity changed"
    except BaseException:
        kernel.CloseHandle(handle)
        raise
    return handle


@pytest.mark.skipif(
    os.name != "nt"
    or os.environ.get("AGENT_DISPATCH_TEST_LOGON_LIFETIME") != "1"
    or os.environ.get("COPILOT_EXTENSIONS_TEST_CONTAINED") == "1",
    reason="explicit Windows production-lifetime lane, separate from contained tests",
)
def test_logon_launcher_survives_nested_caller_jobs(tmp_path):
    from . import windows_durable_launch_fixture as fixture

    pu = _procutil()
    kernel = fixture.kernel()
    source = INSTALLER.read_text(encoding="utf-8-sig")
    start = source.index("function Start-DispatchDetachedLogonLauncher {")
    function = source[start:source.index("\nfunction ", start + 1)]
    publisher = (
        "function Write-FixtureRecord { param([string]$Path,[hashtable]$Record)\n"
        "$pending=$Path+'.pending'\n"
        "[IO.File]::WriteAllText($pending,($Record|ConvertTo-Json -Compress),"
        "[Text.UTF8Encoding]::new($false))\n"
        "[IO.File]::Move($pending,$Path)\n"
        "}\n"
    )
    for cycle in range(2):
        root = tmp_path / f"cycle-{cycle}"
        root.mkdir()
        launcher = root / "fixture launcher.ps1"
        launcher.write_text(
            "# agent-dispatch-supervisor-launch-v1\n"
            "param([switch]$DescribeLaunch)\n"
            "if (-not $DescribeLaunch) { throw 'Expected description' }\n"
            f"$value=@{{version=1;executable='{sys.executable}';"
            f"arguments=@('-I','-B','{SCRIPT}','resident','{root}');"
            f"log_path='{root / 'supervise-launcher.log'}'}}|ConvertTo-Json -Compress\n"
            "[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($value))\n",
            encoding="utf-8",
        )
        leaf = root / "fixture caller.ps1"
        leaf.write_text(
            "$ErrorActionPreference='Stop'\n"
            f"$InstallDir=$PSScriptRoot; $LinkPython='{sys.executable}'\n"
            + publisher + function + "\n"
            "$psi=[Diagnostics.ProcessStartInfo]::new();"
            "$psi.FileName=$LinkPython;"
            "$psi.Arguments='-c \"import time; time.sleep(90)\"';"
            "$psi.UseShellExecute=$false;$psi.CreateNoWindow=$true;"
            "$psi.RedirectStandardOutput=$true;$psi.RedirectStandardError=$true\n"
            "$control=[Diagnostics.Process]::Start($psi)\n"
            "Write-FixtureRecord (Join-Path $PSScriptRoot 'bounded.json') "
            "@{pid=$control.Id;birth=$control.StartTime.ToFileTimeUtc()}\n"
            "Start-DispatchDetachedLogonLauncher "
            "-Launcher (Join-Path $PSScriptRoot 'fixture launcher.ps1')\n"
            "$deadline=[DateTime]::UtcNow.AddSeconds(30)\n"
            "while(-not(Test-Path (Join-Path $PSScriptRoot 'release'))){"
            "if([DateTime]::UtcNow -ge $deadline){throw 'fixture release timeout'};"
            "Start-Sleep -Milliseconds 50}\n",
            encoding="utf-8",
        )
        handles = {}
        with (root / "caller.log").open("wb") as log:
            caller = subprocess.Popen(
                [sys.executable, "-I", "-B", str(SCRIPT), "outer", str(root)],
                stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                env=dict(os.environ, PYTHONPATH=str(REPO / "libs" / "agent-procutil" / "src"),
                         COPILOT_EXTENSIONS_DAEMON_REGISTRY=str(root / "instances")),
                **pu.no_window_kwargs(),
            )
            try:
                for name in ("bounded", "resident"):
                    record = root / f"{name}.json"
                    _wait_file(record, caller)
                    handles[name] = _pin_process(
                        kernel, json.loads(record.read_text(encoding="utf-8-sig")), fixture.birth,
                    )
                _wait_file(root / "heartbeat-2.json", caller)
                (root / "release").touch()
                assert caller.wait(timeout=15) == 0, (root / "caller.log").read_text()
                assert kernel.WaitForSingleObject(handles["bounded"], 2000) == 0
                assert kernel.WaitForSingleObject(handles["resident"], 500) == 0x102
                assert json.loads((root / "heartbeat-2.json").read_text()) == {"iteration": 2}
                assert (root / "supervise-launcher.log").is_file()
            finally:
                (root / "release").touch()
                if caller.poll() is None:
                    caller.kill()
                    caller.wait(timeout=5)
                for handle in handles.values():
                    try:
                        if kernel.WaitForSingleObject(handle, 0) == 0x102:
                            if not kernel.TerminateProcess(handle, 1):
                                raise ctypes.WinError(ctypes.get_last_error())
                        assert kernel.WaitForSingleObject(handle, 5000) == 0
                    finally:
                        kernel.CloseHandle(handle)


def _run_fixture(mode: str, root: Path) -> None:
    pu = _procutil()
    if mode == "outer":
        from agent_procutil.owned_process import run_caller_owned

        result = run_caller_owned(
            [sys.executable, "-I", "-B", str(SCRIPT), "inner", str(root)],
            timeout=60, max_stdout_bytes=65536, max_stderr_bytes=65536,
        )
        assert result.returncode == 0, (result.stdout, result.stderr)
    elif mode == "inner":
        with (root / "inner.log").open("wb") as log:
            process, job = pu.spawn_sync_in_kill_on_close_job(
                [
                    "powershell.exe", "-NoProfile", "-NonInteractive",
                    "-ExecutionPolicy", "Bypass", "-File", str(root / "fixture caller.ps1"),
                ],
                require_job=True, stdout=log, stderr=log, **pu.no_window_kwargs(),
            )
            try:
                assert process.wait(timeout=45) == 0, (root / "inner.log").read_text(encoding="utf-8")
            finally:
                job.close()
                if process.poll() is None:
                    process.wait(timeout=5)
    elif mode == "resident":
        from agent_procutil.daemon_instances import admit_daemon_instance
        sys.path.insert(0, str(SCRIPT.parent))
        import windows_durable_launch_fixture as fixture

        with admit_daemon_instance("dispatch-logon-fixture"):
            fixture.publish(root / "resident.json", fixture.identity(os.getpid()))
            for iteration in (1, 2):
                fixture.publish(root / f"heartbeat-{iteration}.json", {"iteration": iteration})
                time.sleep(.1)
            time.sleep(90)
    else:
        raise ValueError(mode)


if __name__ == "__main__":
    _run_fixture(sys.argv[1], Path(sys.argv[2]))
