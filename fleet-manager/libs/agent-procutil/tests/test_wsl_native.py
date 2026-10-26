"""Opt-in native WSL/Windows ownership proof, outside the ordinary CI matrix."""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import time
from pathlib import Path, PurePosixPath

import pytest

from agent_procutil import no_window_kwargs, spawn_sync_in_kill_on_close_job

pytestmark = pytest.mark.skipif(
    os.name != "nt" or not os.environ.get("AGENT_PROCUTIL_WSL_TEST_DISTRO"),
    reason="explicit native Windows/WSL qualification only",
)

_TARGET = """
import ctypes,json,os,pathlib,subprocess,sys,time
from ctypes import wintypes
kernel=ctypes.WinDLL('kernel32',use_last_error=True)
kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
kernel.OpenProcess.restype=wintypes.HANDLE
kernel.GetProcessTimes.argtypes=[wintypes.HANDLE]+[ctypes.POINTER(wintypes.FILETIME)]*4
kernel.GetProcessTimes.restype=wintypes.BOOL
kernel.CloseHandle.argtypes=[wintypes.HANDLE]
def identity(pid):
    handle=kernel.OpenProcess(0x1000,False,pid)
    if not handle:raise ctypes.WinError(ctypes.get_last_error())
    try:
        times=[wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle,*[ctypes.byref(value) for value in times]):
            raise ctypes.WinError(ctypes.get_last_error())
        return {'pid':pid,'created':(times[0].dwHighDateTime<<32)|times[0].dwLowDateTime}
    finally:
        kernel.CloseHandle(handle)
leaf=subprocess.Popen(
    [sys.executable,'-c','import time;time.sleep(120)'],
    creationflags=0x08000000,
)
ready=pathlib.Path(sys.argv[1])
stage=ready.with_suffix('.staging')
stage.write_text(json.dumps({
    'pid':identity(os.getpid()),'leaf':identity(leaf.pid),'owner':identity(os.getppid()),
    'console':ctypes.windll.kernel32.GetConsoleWindow(),
}))
stage.replace(ready)
time.sleep(120)
"""

_FORK_STATE = """
import json,os,pathlib,select,sys
identity=json.loads(pathlib.Path(sys.argv[1]).read_text())
try:
    fd=os.pidfd_open(identity['pid'])
except ProcessLookupError:
    print(json.dumps({'alive':False}))
else:
    try:
        observer=select.poll()
        observer.register(fd,select.POLLIN)
        try:
            fields=pathlib.Path('/proc/'+str(identity['pid'])+'/stat').read_text().rpartition(')')[2].split()
        except FileNotFoundError:
            events=observer.poll(0)
            if not events:
                raise RuntimeError('Unrelated fork child identity is unavailable')
        else:
            if int(fields[19])!=identity['birth']:
                raise RuntimeError('Unrelated fork child identity changed')
            events=observer.poll(3000 if sys.argv[2]=='wait' else 0)
        if any(mask & (select.POLLERR|select.POLLNVAL) for _,mask in events):
            raise RuntimeError('Unrelated fork child observation failed')
        print(json.dumps({'alive':not events}))
    finally:
        os.close(fd)
"""


def _linux_path(path: Path, distro: str) -> str:
    result = subprocess.run(
        ["wsl.exe", "-d", distro, "--exec", "wslpath", "-u", str(path)],
        capture_output=True, text=True, timeout=5, check=True, **no_window_kwargs(),
    )
    return result.stdout.strip()


def _fork_is_alive(distro: str, linux_gate: str, *, wait: bool = False) -> bool:
    ready = str(PurePosixPath(linux_gate).with_name("fork-ready.json"))
    result = subprocess.run(
        ["wsl.exe", "-d", distro, "--exec", "/usr/bin/python3", "-c", _FORK_STATE,
         ready, "wait" if wait else "now"],
        capture_output=True, text=True, timeout=5, check=True, **no_window_kwargs(),
    )
    state = json.loads(result.stdout)
    assert set(state) == {"alive"} and type(state["alive"]) is bool
    return state["alive"]


def _process_api():
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.GetProcessTimes.argtypes = [
        wintypes.HANDLE, *[ctypes.POINTER(wintypes.FILETIME)] * 4,
    ]
    kernel.GetProcessTimes.restype = wintypes.BOOL
    return kernel


def _creation_time(kernel, handle) -> int:
    from ctypes import wintypes

    times = [wintypes.FILETIME() for _ in range(4)]
    if not kernel.GetProcessTimes(handle, *[ctypes.byref(value) for value in times]):
        raise ctypes.WinError(ctypes.get_last_error())
    return (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime


def _open_owned(identity: dict):
    if (
        type(identity.get("pid")) is not int or identity["pid"] <= 0
        or type(identity.get("created")) is not int or identity["created"] <= 0
    ):
        raise ValueError("Fixture process identity is incomplete")
    kernel = _process_api()
    handle = kernel.OpenProcess(0x100000 | 0x1000 | 0x0001, False, identity["pid"])
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if _creation_time(kernel, handle) != identity["created"]:
            raise RuntimeError("Fixture process creation identity changed")
    except BaseException:
        kernel.CloseHandle(handle)
        raise
    return kernel, handle


def _exercise_case(tmp_path, case):
    distro = os.environ["AGENT_PROCUTIL_WSL_TEST_DISTRO"]
    source = Path(
        os.environ.get("AGENT_PROCUTIL_WSL_TEST_PACKAGE_ROOT")
        or Path(__file__).resolve().parents[1] / "src"
    )
    fixture = Path(__file__).with_name("wsl_caller_fixture.py")
    python = Path(sys._base_executable)
    pythonw = python.with_name("pythonw.exe")
    assert pythonw.is_file()
    ready, gate, config = (tmp_path / name for name in ("ready.json", "interrupt", "request.json"))
    linux_gate = _linux_path(gate, distro)
    config.write_text(json.dumps({
        "case": case, "source": _linux_path(source, distro),
        "pythonw": _linux_path(pythonw, distro), "gate": linux_gate,
        "argv": [str(python), "-c", _TARGET, str(ready)],
    }))
    process, outer_job = spawn_sync_in_kill_on_close_job(
        ["wsl.exe", "-d", distro, "--exec", "/usr/bin/python3", "-B",
         _linux_path(fixture, distro), _linux_path(config, distro)],
        require_job=True, allow_breakaway=False,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, **no_window_kwargs(),
    )
    handles = []
    try:
        deadline = time.monotonic() + 12
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(.02)
        assert ready.exists(), "Windows fixture did not establish its owned identities"
        identity = json.loads(ready.read_text())
        assert identity["console"] == 0
        for name in ("pid", "leaf", "owner"):
            handles.append(_open_owned(identity[name]))
        assert all(kernel.WaitForSingleObject(handle, 0) == 258 for kernel, handle in handles)
        if case not in {"timeout", "stdin-stall"}:
            gate.write_text("interrupt")
        if case == "caller-death-with-fork":
            exited = gate.with_name("caller-exited.json")
            deadline = time.monotonic() + 12
            while not exited.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(.02)
            assert exited.exists(), "caller did not exit while its WSL launcher remained alive"
            assert json.loads(exited.read_text())["returncode"] != 0
            assert process.poll() is None, "WSL launcher exited before the ownership assertion"
            assert _fork_is_alive(distro, linux_gate), "unrelated fork child exited with the caller"
        else:
            stdout, stderr = process.communicate(timeout=12)
            if case != "caller-death":
                assert process.returncode == 0, (stdout, stderr)
                assert json.loads(stdout)["outcome"] == case
            else:
                assert process.returncode != 0
        deadline = time.monotonic() + 5
        while (
            any(kernel.WaitForSingleObject(handle, 0) == 258 for kernel, handle in handles)
            and time.monotonic() < deadline
        ):
            time.sleep(.02)
        # This assertion precedes cleanup of the test harness's outer Job.
        assert all(kernel.WaitForSingleObject(handle, 0) == 0 for kernel, handle in handles)
        if case == "caller-death-with-fork":
            assert process.poll() is None, "WSL launcher cleanup masked caller-owned retirement"
            assert _fork_is_alive(distro, linux_gate), "unrelated fork child did not survive the caller"
    finally:
        gate.write_text("interrupt")
        for kernel, handle in handles:
            if kernel.WaitForSingleObject(handle, 0) == 258:
                kernel.TerminateProcess(handle, 1)
                kernel.WaitForSingleObject(handle, 3000)
            kernel.CloseHandle(handle)
        try:
            if case == "caller-death-with-fork":
                gate.with_name("fork-stop").write_text("stop")
                if gate.with_name("fork-ready.json").exists():
                    assert not _fork_is_alive(distro, linux_gate, wait=True)
        finally:
            if outer_job is not None:
                outer_job.terminate_and_wait(3)
            process.wait(timeout=3)
            process.stdout.close()
            process.stderr.close()


@pytest.mark.parametrize("case", [
    "timeout", "stdin-stall", "sigint", "caller-death", "caller-death-with-fork",
])
def test_real_windows_tree_is_gone_before_outer_job_cleanup(tmp_path, case):
    from test_spawn_daemon_windows_integration import _configure_win32, _WindowObserver

    with _WindowObserver(*_configure_win32()) as observer:
        for cycle in range(2):
            directory = tmp_path / str(cycle)
            directory.mkdir()
            _exercise_case(directory, case)
    assert observer.surfaced == {}, f"visible console windows: {observer.surfaced}"
    assert observer.focus_changes == {}, f"foreground transitions: {observer.focus_changes}"


def test_setup_sigint_retires_windows_owner_while_caller_retains_traceback(tmp_path):
    from test_spawn_daemon_windows_integration import _configure_win32, _WindowObserver

    distro = os.environ["AGENT_PROCUTIL_WSL_TEST_DISTRO"]
    source = Path(
        os.environ.get("AGENT_PROCUTIL_WSL_TEST_PACKAGE_ROOT")
        or Path(__file__).resolve().parents[1] / "src"
    )
    fixture = Path(__file__).with_name("wsl_caller_fixture.py")
    pythonw = Path(sys._base_executable).with_name("pythonw.exe")
    with _WindowObserver(*_configure_win32()) as observer:
        for cycle in range(2):
            directory = tmp_path / str(cycle)
            directory.mkdir()
            ready, gate, config = (
                directory / name for name in ("ready.json", "interrupt", "request.json")
            )
            config.write_text(json.dumps({
                "case": "setup-sigint", "source": _linux_path(source, distro),
                "pythonw": _linux_path(pythonw, distro),
                "gate": _linux_path(gate, distro), "ready": str(ready),
                "argv": [str(pythonw), "-c", "raise RuntimeError('target must not start')"],
            }))
            process, outer_job = spawn_sync_in_kill_on_close_job(
                ["wsl.exe", "-d", distro, "--exec", "/usr/bin/python3", "-B",
                 _linux_path(fixture, distro), _linux_path(config, distro)],
                require_job=True, allow_breakaway=False,
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, **no_window_kwargs(),
            )
            owned = None
            try:
                deadline = time.monotonic() + 12
                while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
                    time.sleep(.01)
                assert ready.exists(), (
                    "owner did not enter its pre-request boundary",
                    process.communicate(timeout=1) if process.poll() is not None else "caller alive",
                )
                owned = _open_owned(json.loads(ready.read_text()))
                kernel, handle = owned
                assert kernel.WaitForSingleObject(handle, 0) == 258
                gate.write_text("interrupt")
                caught = gate.with_name("caught.json")
                deadline = time.monotonic() + 5
                while not caught.exists() and process.poll() is None and time.monotonic() < deadline:
                    time.sleep(.01)
                assert caught.exists() and json.loads(caught.read_text()) == {"retained": True}
                assert process.poll() is None, "caller exited before owner observation"
                assert kernel.WaitForSingleObject(handle, 3000) == 0
                assert process.poll() is None, "outer cleanup masked owner retirement"
            finally:
                gate.write_text("interrupt")
                gate.with_name("stop").write_text("stop")
                if owned is not None:
                    kernel, handle = owned
                    if kernel.WaitForSingleObject(handle, 0) == 258:
                        kernel.TerminateProcess(handle, 1)
                        kernel.WaitForSingleObject(handle, 3000)
                    kernel.CloseHandle(handle)
                if outer_job is not None:
                    outer_job.terminate_and_wait(3)
                process.wait(timeout=3)
                process.stdout.close()
                process.stderr.close()
    assert observer.surfaced == {}, f"visible console windows: {observer.surfaced}"
    assert observer.focus_changes == {}, f"foreground transitions: {observer.focus_changes}"
