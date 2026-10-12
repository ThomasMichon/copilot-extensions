"""Finite, file-only processes for the Windows durable-launch regression."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback

import agent_procutil as pu


def kernel():
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    api.OpenProcess.restype = wintypes.HANDLE
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.CloseHandle.restype = wintypes.BOOL
    api.GetProcessTimes.argtypes = [wintypes.HANDLE] + [
        ctypes.POINTER(wintypes.FILETIME)
    ] * 4
    api.GetProcessTimes.restype = wintypes.BOOL
    api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    api.WaitForSingleObject.restype = wintypes.DWORD
    api.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    api.TerminateProcess.restype = wintypes.BOOL
    api.IsProcessInJob.argtypes = [
        wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL),
    ]
    api.IsProcessInJob.restype = wintypes.BOOL
    return api


def birth(api, handle):
    times = [wintypes.FILETIME() for _ in range(4)]
    assert api.GetProcessTimes(handle, *(ctypes.byref(t) for t in times))
    return (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime


def identity(pid):
    api = kernel()
    handle = api.OpenProcess(0x1000, False, pid)
    assert handle, f"cannot identify owned process {pid}"
    try:
        return {"pid": pid, "birth": birth(api, handle)}
    finally:
        assert api.CloseHandle(handle)


def console_state():
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.GetConsoleWindow.restype = wintypes.HWND
    window = api.GetConsoleWindow()
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.IsWindowVisible.argtypes = [wintypes.HWND]
    return {"hwnd": int(window or 0), "visible": bool(user.IsWindowVisible(window))}


def publish(path, value):
    pending = path.with_suffix(".pending")
    pending.write_text(json.dumps(value), encoding="utf-8")
    pending.replace(path)


def read_json(path):
    deadline = time.monotonic() + 2
    while True:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(.02)


def wait_file(path, seconds=40):
    deadline = time.monotonic() + seconds
    while not path.exists():
        if time.monotonic() >= deadline:
            raise AssertionError(f"fixture handshake timed out: {path.name}")
        time.sleep(.02)


def service(root, launcher_pid=None):
    # No queue, listener, credentials, service installation, or live backend.
    # Even an interrupted ownership handshake has a finite lifetime.
    owners = {"service": identity(os.getpid())}
    if launcher_pid:
        owners["launcher"] = identity(launcher_pid)
    publish(root / "console-service.json", console_state())
    publish(root / "ready.json", owners)
    deadline = time.monotonic() + 90
    jobs = []
    children = []
    try:
        while not (root / "cycles-start").exists():
            if (root / "stop").exists() or time.monotonic() >= deadline:
                return
            time.sleep(.02)
        for cycle in range(2):
            # Deliberately no console flags: these console-subsystem children
            # must inherit the actual production seam's windowless host.
            child, job = pu.spawn_sync_in_kill_on_close_job(
                [sys.executable, __file__, "console-child", str(root), str(cycle)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            children.append(child)
            jobs.append(job)
            assert job is not None
            publish(root / f"cycle-{cycle}.json", identity(child.pid))
            assert child.wait(timeout=15) == 0
            job.close()
        publish(root / "cycles-done.json", {"cycles": 2})
        while not (root / "stop").exists() and time.monotonic() < deadline:
            time.sleep(.02)
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=3)
        for job in jobs:
            if job is not None:
                job.close()


def caller(root, site):
    ordinary = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(90)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **pu.no_window_kwargs(),
    )
    publish(root / "ordinary.json", identity(ordinary.pid))
    escaped = []
    handed_off = False
    try:
        if site == "coordinator":
            from agent_dispatch import __main__ as main, procutil

            procutil.resolve_own_runtime_python = lambda: sys.executable
            real_popen = subprocess.Popen

            def fake_payload(argv, **kwargs):
                # Replace ONLY the backend payload, never the launch options:
                # production _spawn_coordinator_process still constructs them.
                assert argv == [sys.executable, "-m", "agent_dispatch", "serve"]
                child = real_popen(
                    [sys.executable, __file__, "service", str(root)], **kwargs,
                )
                escaped.append(child)
                publish(root / "spawned.json", identity(child.pid))
                return child

            subprocess.Popen = fake_payload
            try:
                main._spawn_coordinator_process()
            finally:
                subprocess.Popen = real_popen
        else:
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-File", str(root / "caller.ps1")],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=40,
                **pu.no_window_kwargs(),
            )
            if result.returncode:
                raise AssertionError(result.stderr.decode(errors="replace"))
        wait_file(root / "ready.json")
        wait_file(root / "pinned")
        handed_off = True
    finally:
        if not handed_off:
            (root / "stop").touch()
            for child in escaped:
                if child.poll() is None:
                    child.kill()
                child.wait(timeout=3)
        # Ordinary descendants intentionally remain alive until the owner Job
        # closes; retained Popen identity prevents PID-based cleanup on failure.
        if not handed_off:
            if ordinary.poll() is None:
                ordinary.kill()
            ordinary.wait(timeout=3)


def powershell_scripts(root, installer):
    def quote(value):
        return "'" + str(value).replace("'", "''") + "'"

    # Parse, select, and execute ONLY the real helper's AST. Never dot-source
    # install.ps1: its registry/task/service and provisioning paths must not run.
    (root / "caller.ps1").write_text(
        "$ErrorActionPreference = 'Stop'\n"
        f"$LinkPython = {quote(sys.executable)}\n"
        f"$InstallDir = {quote(root)}\n"
        "$tokens = $null; $errors = $null\n"
        "$ast = [Management.Automation.Language.Parser]::ParseFile("
        f"{quote(installer)}, [ref]$tokens, [ref]$errors)\n"
        "if ($errors.Count) { throw 'Installer AST parse failed' }\n"
        "$functions = @($ast.FindAll({ param($node)\n"
        " $node -is [Management.Automation.Language.FunctionDefinitionAst] "
        "-and $node.Name -eq 'Start-DispatchDetachedLogonLauncher'\n"
        "}, $true))\n"
        "if ($functions.Count -ne 1) { throw 'Expected one production helper' }\n"
        "Invoke-Expression $functions[0].Extent.Text\n"
        "Start-DispatchDetachedLogonLauncher "
        f"-Launcher {quote(root / 'fake launcher.ps1')} "
        "-LauncherArguments @('-EnvFile', 'argument with spaces')\n"
        "exit 0\n",
        encoding="utf-8",
    )
    (root / "fake launcher.ps1").write_text(
        "param([string]$EnvFile)\n"
        "$ErrorActionPreference = 'Stop'\n"
        "if ($EnvFile -ne 'argument with spaces') { throw 'Lost launcher arguments' }\n"
        f"if ([IO.Directory]::GetCurrentDirectory() -ne {quote(root)}) "
        "{ throw 'Wrong launcher cwd' }\n"
        f"& {quote(sys.executable)} {quote(Path(__file__).resolve())} "
        f"service {quote(root)} $PID\n"
        "exit $LASTEXITCODE\n",
        encoding="utf-8",
    )


def entry():
    mode, directory = sys.argv[1:3]
    root = Path(directory)
    if mode == "caller":
        caller(root, sys.argv[3])
    elif mode == "service":
        service(root, int(sys.argv[3]) if len(sys.argv) > 3 else None)
    elif mode == "console-child":
        publish(root / f"console-cycle-{sys.argv[3]}.json", console_state())
        time.sleep(2)
    else:
        raise SystemExit(f"unknown fixture mode: {mode}")


if __name__ == "__main__":
    try:
        entry()
    except BaseException:
        publish(Path(sys.argv[2]) / f"failure-{sys.argv[1]}.json", {
            "traceback": traceback.format_exc(),
        })
        raise
