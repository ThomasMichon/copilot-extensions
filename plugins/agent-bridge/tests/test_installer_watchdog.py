"""The native staging watchdog distinguishes progress from a quiet stall."""
from __future__ import annotations

import os
import ctypes
import json
from pathlib import Path
import shutil
import subprocess
import time
from typing import cast

import pytest
from agent_procutil import no_window_flags

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
pytestmark = pytest.mark.skipif(os.name != "nt", reason="native Windows staging watchdog")


def _block(name: str) -> str:
    text = (SCRIPTS / "install.ps1").read_text(encoding="utf-8")
    start = text.index(f"# === install-contract:v4 {name}")
    end = f"# === end install-contract:v4 {name} ==="
    return text[start:text.index(end, start) + len(end)]


def _process_kernel() -> ctypes.CDLL:
    loader = cast(type[ctypes.CDLL], getattr(ctypes, "WinDLL"))
    kernel = loader("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    kernel.WaitForSingleObject.restype = ctypes.c_ulong
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    return kernel


def _wait_child_pid(receipt: Path, process: subprocess.Popen[bytes]) -> int:
    deadline = time.monotonic() + 10
    last_error = None
    while time.monotonic() < deadline:
        try:
            return int(receipt.read_text())
        except (OSError, ValueError) as error:
            last_error = error
        if process.poll() is not None:
            break
        time.sleep(0.02)
    pytest.fail(f"no complete PID receipt: {last_error}; parent exit={process.poll()}")


@pytest.mark.parametrize("host", ["powershell.exe", "pwsh"])
@pytest.mark.parametrize("mode", [
    "progress", "stdout", "stderr", "quiet", "late-quiet", "override", "disabled", "failure",
])
def test_native_staging_watchdog(tmp_path: Path, host: str, mode: str) -> None:
    executable = shutil.which(host)
    if executable is None:
        pytest.skip(f"{host} unavailable")
    home = tmp_path / "home"
    home.mkdir()
    payload = tmp_path / ".copilot" / "installed-plugins" / "example" / "agent-example"
    scripts = payload / "scripts"
    scripts.mkdir(parents=True)
    (payload / "plugin.json").write_text('{"name":"agent-example"}', encoding="utf-8")
    entry = scripts / "install.ps1"
    output = ""
    if mode != "stderr":
        output += "[Console]::Out.Write(('x' * 8192)); [Console]::Out.Flush()\n"
    if mode != "stdout":
        output += "[Console]::Error.Write(('y' * 8192)); [Console]::Error.Flush()\n"
    cycles = 1 if mode == "failure" else 20
    body = f"for ($i = 0; $i -lt {cycles}; $i++) {{\n" + output + "Start-Sleep -Milliseconds 500\n}\n"
    if mode in {"quiet", "late-quiet"}:
        body = ("[Console]::Out.Write('progress'); [Console]::Out.Flush()\n"
                if mode == "late-quiet" else "") + "Start-Sleep -Seconds 12\n"
    # Native shell startup and shutdown share the outer, unchanged 20-second cap.
    staging = _block("self-stage").replace("$__wdDeadline = 480", "$__wdDeadline = 8")
    staging = staging.replace(
        "$__wdExpired = $true",
        "[IO.File]::WriteAllText((Join-Path $env:USERPROFILE 'timeout.json'), "
        "([ordered]@{exited=$__wdChild.HasExited;stdout_done=$__wdPipes[0].Done;"
        "stderr_done=$__wdPipes[1].Done;elapsed_ms=$__wdClock.ElapsedMilliseconds;"
        "quiet_ms=($__wdClock.ElapsedMilliseconds-$__wdLastOutput)} | ConvertTo-Json -Compress))\n"
        "                            $__wdExpired = $true",
    )
    entry.write_text(
        "param([string]$Action = 'install')\n$ErrorActionPreference = 'Stop'\n"
        + _block("payload-copy") + "\n"
        + staging
        + "\n" + body
        + "[IO.File]::WriteAllText((Join-Path $env:USERPROFILE 'completed'), 'ready')\n"
        + ("[Environment]::Exit(7)\n" if mode == "failure" else "[Environment]::Exit(0)\n"),
        encoding="utf-8",
    )
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("COPILOT_", "AGENT_"))
    }
    env.update(HOME=str(home), USERPROFILE=str(home), COPILOT_EXTENSIONS_TEST_CONTAINED="1")
    if mode in {"override", "disabled"}:
        env["AGENT_EXAMPLE_INSTALL_DEADLINE_SEC"] = "1" if mode == "override" else "0"
    started = time.monotonic()
    result = subprocess.run(
        [executable, "-NoProfile", "-File", str(entry)], cwd=home, env=env,
        capture_output=True, timeout=20, creationflags=no_window_flags(),
    )
    expected = 124 if mode in {"quiet", "late-quiet", "override"} else 7 if mode == "failure" else 0
    log = home / ".agent-example" / "reconcile.err.log"
    diagnostic = {
        "exit": result.returncode, "elapsed": time.monotonic() - started,
        "stdout_bytes": len(result.stdout), "stderr_bytes": len(result.stderr),
        "completed": (home / "completed").exists(),
        "watchdog": log.read_text() if log.exists() else "",
    }
    if result.returncode != expected:
        timeout = home / "timeout.json"
        if timeout.exists():
            diagnostic["timeout"] = json.loads(timeout.read_text())
        print(json.dumps(diagnostic))
    assert result.returncode == expected, diagnostic
    assert (home / "completed").exists() == (expected != 124)
    if expected != 124:
        assert result.stdout == (b"" if mode == "stderr" else b"x" * (8192 * cycles))
        assert result.stderr == (b"" if mode == "stdout" else b"y" * (8192 * cycles))
    else:
        assert "WATCHDOG-KILL" in (home / ".agent-example" / "reconcile.err.log").read_text()


@pytest.mark.parametrize("host", ["powershell.exe", "pwsh"])
def test_native_staging_watchdog_settles_descendants(tmp_path: Path, host: str) -> None:
    from agent_procutil import spawn_sync_in_kill_on_close_job

    executable = shutil.which(host)
    if executable is None:
        pytest.skip(f"{host} unavailable")
    home = tmp_path / "home"
    home.mkdir()
    payload = tmp_path / ".copilot" / "installed-plugins" / "example" / "agent-example"
    scripts = payload / "scripts"
    scripts.mkdir(parents=True)
    (payload / "plugin.json").write_text('{"name":"agent-example"}', encoding="utf-8")
    entry = scripts / "install.ps1"
    entry.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        + _block("payload-copy") + "\n"
        + _block("self-stage").replace("$__wdDeadline = 480", "$__wdDeadline = 3") + "\n"
        + "$start = New-Object Diagnostics.ProcessStartInfo\n"
        "$start.FileName = (Get-Process -Id $PID).Path\n"
        "$start.Arguments = '-NoProfile -Command Start-Sleep -Seconds 60'\n"
        "$start.UseShellExecute = $false; $start.CreateNoWindow = $true\n"
        "$child = [Diagnostics.Process]::Start($start)\n"
        "[IO.File]::WriteAllText((Join-Path $env:USERPROFILE 'child.pending'), [string]$child.Id)\n"
        "[IO.File]::Move((Join-Path $env:USERPROFILE 'child.pending'), (Join-Path $env:USERPROFILE 'child'))\n"
        "Start-Sleep -Seconds 60\n",
        encoding="utf-8",
    )
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("COPILOT_", "AGENT_"))}
    env.update(HOME=str(home), USERPROFILE=str(home), COPILOT_EXTENSIONS_TEST_CONTAINED="1")
    kernel = _process_kernel()
    process, job = spawn_sync_in_kill_on_close_job(
        [executable, "-NoProfile", "-File", str(entry)], cwd=home, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=no_window_flags(),
        require_job=True, allow_breakaway=False,
    )
    assert job is not None
    handle = None
    try:
        child_pid = _wait_child_pid(home / "child", process)
        handle = kernel.OpenProcess(0x00100000, False, child_pid)  # SYNCHRONIZE
        assert handle, getattr(ctypes, "get_last_error")()
        assert process.wait(timeout=15) == 124
        assert kernel.WaitForSingleObject(handle, 0) == 0, "watchdog left its grandchild running"
    finally:
        job.terminate_and_wait(5)
        process.wait(timeout=5)
        if handle:
            kernel.CloseHandle(handle)


@pytest.mark.parametrize("host", ["powershell.exe", "pwsh"])
@pytest.mark.parametrize("disabled", [False, True])
def test_native_staging_watchdog_preserves_exited_root(
    tmp_path: Path, host: str, disabled: bool,
) -> None:
    from agent_procutil import spawn_sync_in_kill_on_close_job

    executable = shutil.which(host)
    if executable is None:
        pytest.skip(f"{host} unavailable")
    home = tmp_path / "home"
    home.mkdir()
    payload = tmp_path / ".copilot" / "installed-plugins" / "example" / "agent-example"
    scripts = payload / "scripts"
    scripts.mkdir(parents=True)
    (payload / "plugin.json").write_text('{"name":"agent-example"}', encoding="utf-8")
    entry = scripts / "install.ps1"
    entry.write_text(
        "$ErrorActionPreference = 'Stop'\n" + _block("payload-copy") + "\n"
        + _block("self-stage").replace("$__wdDeadline = 480", "$__wdDeadline = 3") + "\n"
        + "$start = New-Object Diagnostics.ProcessStartInfo\n"
        "$start.FileName = (Get-Process -Id $PID).Path\n"
        "$start.Arguments = '-NoProfile -Command Start-Sleep -Seconds 60'\n"
        "$start.UseShellExecute = $false; $start.CreateNoWindow = $true\n"
        "$child = [Diagnostics.Process]::Start($start)\n"
        "[IO.File]::WriteAllText((Join-Path $env:USERPROFILE 'child.pending'), [string]$child.Id)\n"
        "[IO.File]::Move((Join-Path $env:USERPROFILE 'child.pending'), (Join-Path $env:USERPROFILE 'child'))\n"
        "[Console]::Out.Write(('x' * 8192)); [Console]::Out.Flush()\n"
        "[Environment]::Exit(7)\n",
        encoding="utf-8",
    )
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("COPILOT_", "AGENT_"))}
    env.update(HOME=str(home), USERPROFILE=str(home), COPILOT_EXTENSIONS_TEST_CONTAINED="1")
    if disabled:
        env["AGENT_EXAMPLE_INSTALL_DEADLINE_SEC"] = "0"
    kernel = _process_kernel()
    handle = None
    with (tmp_path / "stdout").open("wb") as output:
        process, job = spawn_sync_in_kill_on_close_job(
            [executable, "-NoProfile", "-File", str(entry)], cwd=home, env=env,
            stdout=output, stderr=subprocess.DEVNULL, creationflags=no_window_flags(),
            require_job=True, allow_breakaway=False,
        )
        assert job is not None
        try:
            child_pid = _wait_child_pid(home / "child", process)
            handle = kernel.OpenProcess(0x00100000, False, child_pid)
            assert handle, getattr(ctypes, "get_last_error")()
            assert process.wait(timeout=12) == 7
            assert kernel.WaitForSingleObject(handle, 0) == 258, "root exit killed its live descendant"
            assert (tmp_path / "stdout").read_bytes() == b"x" * 8192
        finally:
            job.terminate_and_wait(5)
            process.wait(timeout=5)
            if handle:
                kernel.CloseHandle(handle)


@pytest.mark.parametrize("host", ["powershell.exe", "pwsh"])
@pytest.mark.parametrize("plugin", ["agent-worktrees", "agent-dispatch"])
@pytest.mark.parametrize("explicit", [False, True])
def test_native_staging_watchdog_builtin_budget(
    tmp_path: Path, host: str, plugin: str, explicit: bool,
) -> None:
    executable = shutil.which(host)
    if executable is None:
        pytest.skip(f"{host} unavailable")
    source = (SCRIPTS.parents[1] / plugin / "scripts" / "install.ps1").read_text(encoding="utf-8")
    if plugin == "agent-worktrees":
        preamble = source.split("# Keep native staging", 1)[1].split("if ($InstallSource", 1)[0]
        preamble = "# Keep native staging" + preamble
    else:
        preamble = source.split("# Raise the shared", 1)[1].split(
            "# === install-contract:test-persistent-environment", 1,
        )[0]
        preamble = "# Raise the shared" + preamble
    preamble = preamble.replace("'900'", "'8'").replace("'1050'", "'8'")
    home = tmp_path / "home"
    home.mkdir()
    payload = tmp_path / ".copilot" / "installed-plugins" / "example" / plugin
    scripts = payload / "scripts"
    scripts.mkdir(parents=True)
    (payload / "plugin.json").write_text(json.dumps({"name": plugin}), encoding="utf-8")
    entry = scripts / "install.ps1"
    entry.write_text(
        "$ErrorActionPreference = 'Stop'\n" + preamble + "\n" + _block("payload-copy") + "\n"
        + _block("self-stage") + "\n"
        + "for ($i = 0; $i -lt 20; $i++) {\n"
        "[Console]::Out.Write('x'); [Console]::Out.Flush(); Start-Sleep -Milliseconds 500\n}\n"
        "[Environment]::Exit(7)\n",
        encoding="utf-8",
    )
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("COPILOT_", "AGENT_"))}
    env.update(HOME=str(home), USERPROFILE=str(home), COPILOT_EXTENSIONS_TEST_CONTAINED="1")
    if explicit:
        env["COPILOT_PLUGIN_INSTALL_DEADLINE_SEC"] = "1"
    result = subprocess.run(
        [executable, "-NoProfile", "-File", str(entry)], cwd=home, env=env,
        capture_output=True, timeout=20, creationflags=no_window_flags(),
    )
    assert result.returncode == (124 if explicit else 7), (result.stdout, result.stderr)
    if not explicit:
        assert result.stdout == b"x" * 20


@pytest.mark.parametrize("host,channel,explicit", [
    ("powershell.exe", "stdout", False),
    ("powershell.exe", "stderr", True),
    ("pwsh", "stderr", False),
    ("pwsh", "stdout", True),
])
def test_native_staging_watchdog_undrained_output(
    tmp_path: Path, host: str, channel: str, explicit: bool,
) -> None:
    from agent_procutil import spawn_sync_in_kill_on_close_job

    executable = shutil.which(host)
    if executable is None:
        pytest.skip(f"{host} unavailable")
    home = tmp_path / "home"
    home.mkdir()
    payload = tmp_path / ".copilot" / "installed-plugins" / "example" / "agent-example"
    scripts = payload / "scripts"
    scripts.mkdir(parents=True)
    (payload / "plugin.json").write_text('{"name":"agent-example"}', encoding="utf-8")
    staging = _block("self-stage").replace("$__wdDeadline = 480", "$__wdDeadline = 5")
    read = "$__wdCount = $__wdPipe.Read.GetAwaiter().GetResult()"
    assert staging.count(read) == 1
    staging = staging.replace(
        read, read + "\n"
        "                                if ($__wdCount -gt 0 -and -not [IO.File]::Exists((Join-Path $env:USERPROFILE 'forwarding'))) {\n"
        "                                    [IO.File]::WriteAllText((Join-Path $env:USERPROFILE 'forwarding.pending'), [string]$__wdChild.Id)\n"
        "                                    [IO.File]::Move((Join-Path $env:USERPROFILE 'forwarding.pending'), (Join-Path $env:USERPROFILE 'forwarding'))\n"
        "                                }",
    )
    entry = scripts / "install.ps1"
    stream = "OpenStandardOutput" if channel == "stdout" else "OpenStandardError"
    entry.write_text(
        "$ErrorActionPreference = 'Stop'\n" + _block("payload-copy") + "\n" + staging + "\n"
        + f"$stream = [Console]::{stream}()\n"
        "$buffer = New-Object byte[] 8192\n"
        "for ($i = 0; $i -lt 10000; $i++) { $stream.Write($buffer, 0, $buffer.Length) }\n"
        "Start-Sleep -Seconds 60\n",
        encoding="utf-8",
    )
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("COPILOT_", "AGENT_"))}
    env.update(HOME=str(home), USERPROFILE=str(home), COPILOT_EXTENSIONS_TEST_CONTAINED="1")
    if explicit:
        env["AGENT_EXAMPLE_INSTALL_DEADLINE_SEC"] = "5"
    process, job = spawn_sync_in_kill_on_close_job(
        [executable, "-NoProfile", "-File", str(entry)], cwd=home, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=no_window_flags(),
        require_job=True, allow_breakaway=False,
    )
    assert job is not None
    kernel = _process_kernel()
    handle = None
    try:
        child_pid = _wait_child_pid(home / "forwarding", process)
        handle = kernel.OpenProcess(0x00100000, False, child_pid)
        assert handle, getattr(ctypes, "get_last_error")()
        # Neither pipe is drained until the watchdog has returned.
        assert process.wait(timeout=12) == 124
        assert kernel.WaitForSingleObject(handle, 0) == 0, "staged writer survived watchdog return"
        assert "WATCHDOG-KILL" in (home / ".agent-example" / "reconcile.err.log").read_text()
    finally:
        job.terminate_and_wait(5)
        process.wait(timeout=5)
        if handle:
            kernel.CloseHandle(handle)
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()
