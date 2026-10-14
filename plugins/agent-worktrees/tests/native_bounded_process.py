"""Exercise the production captured-tree seam from a consoleless interpreter."""

import ctypes
import json
import os
import subprocess
import sys
import threading
import traceback
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, sys.argv[1])
from agent_worktrees import push_timeout  # noqa: E402

user = ctypes.WinDLL("user32", use_last_error=True)
kernel = ctypes.WinDLL("kernel32", use_last_error=True)
kernel.GetConsoleWindow.restype = wintypes.HWND
user.GetForegroundWindow.restype = wintypes.HWND
user.IsWindowVisible.argtypes = [wintypes.HWND]
callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
stop = threading.Event()
ready = threading.Event()
new_visible = set()
focus_changes = []
errors = []


def observe():
    baseline = None
    prior = None
    try:
        while not stop.is_set():
            visible = set()

            @callback_type
            def inspect(handle, _param):
                if user.IsWindowVisible(handle):
                    visible.add(int(handle))
                return True

            if not user.EnumWindows(inspect, 0):
                raise ctypes.WinError(ctypes.get_last_error())
            if baseline is None:
                baseline = visible
            new_visible.update(visible - baseline)
            foreground = user.GetForegroundWindow()
            if prior is not None and foreground != prior:
                focus_changes.append(1)
            prior = foreground
            ready.set()
            stop.wait(0.005)
    except Exception as exc:
        errors.append(str(exc))
        ready.set()


workspace = Path(sys.argv[3])
report = {"cycles": 0, "timeout": False, "failure": ""}
observer = threading.Thread(target=observe)
observer.start()
try:
    assert not kernel.GetConsoleWindow(), "native driver must have no console"
    assert not hasattr(subprocess.Popen.__init__, "_aw_headless")
    assert ready.wait(5)
    env = dict(os.environ)
    env.pop("__PYVENV_LAUNCHER__", None)
    for _ in range(2):
        result = push_timeout.run_bounded(
            [sys.argv[2], "-c", "import sys,time; print('output'); print('error',file=sys.stderr); time.sleep(.2); sys.exit(7)"],
            cwd=workspace, env=env, timeout=5,
        )
        assert (result.returncode, result.stdout, result.stderr) == (7, "output\n", "error\n")
        report["cycles"] += 1
    binary = push_timeout.run_bounded(
        [sys.argv[2], "-c", "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read()); sys.stderr.buffer.write(b'\\xfe')"],
        cwd=workspace, env=env, timeout=5, binary=True, input=b"\0\xff\r\n",
    )
    assert (binary.returncode, binary.stdout, binary.stderr) == (0, b"\0\xff\r\n", b"\xfe")
    grandchild = workspace / "grandchild.py"
    grandchild.write_text(
        "import os,time\n"
        "open('grandchild.pid','w').write(str(os.getpid()))\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    child = workspace / "child.py"
    child.write_text(
        "import subprocess,sys,time\n"
        "try:\n"
        " subprocess.Popen([sys.executable,'grandchild.py'],creationflags=0x01000000)\n"
        "except OSError as exc:\n"
        " assert exc.winerror==5\n"
        " subprocess.Popen([sys.executable,'grandchild.py'])\n"
        "print('partial',flush=True)\n"
        "print('diagnostic',file=sys.stderr,flush=True)\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    try:
        push_timeout.run_bounded(
            [sys.argv[2], str(child)], cwd=workspace, env=env, timeout=3,
        )
        raise AssertionError("stall did not time out")
    except subprocess.TimeoutExpired as exc:
        assert (exc.stdout, exc.stderr) == ("partial\n", "diagnostic\n")
        report["timeout"] = True
    assert (workspace / "grandchild.pid").is_file(), "descendant never started"
    pid = int((workspace / "grandchild.pid").read_text())
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x00100000, False, pid)
    if handle:
        try:
            assert kernel.WaitForSingleObject(handle, 5000) == 0, "descendant survived timeout"
        finally:
            kernel.CloseHandle(handle)
    else:
        assert ctypes.get_last_error() == 87, "descendant liveness could not be verified"
except BaseException:
    report["failure"] = traceback.format_exc()
finally:
    stop.set()
    observer.join(5)
    report.update(
        breakaway_contained=True,
        new_visible_windows=len(new_visible),
        foreground_transitions=len(focus_changes),
        observer_errors=errors,
    )
    Path(sys.argv[4]).write_text(json.dumps(report), encoding="utf-8")
assert not observer.is_alive() and not errors
assert not report["failure"], report["failure"]
assert not new_visible and not focus_changes
