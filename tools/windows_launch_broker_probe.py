"""Synthetic native evidence only; not a production broker or authenticated IPC."""

from __future__ import annotations

import ctypes
import json
import os
import re
import subprocess
import sys
import time
from ctypes import wintypes
from multiprocessing.connection import Client, Listener
from pathlib import Path


def kernel():
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    api.OpenProcess.restype = wintypes.HANDLE
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.CloseHandle.restype = wintypes.BOOL
    api.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    api.GetProcessTimes.restype = wintypes.BOOL
    api.IsProcessInJob.argtypes = [
        wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL),
    ]
    api.IsProcessInJob.restype = wintypes.BOOL
    api.GetNamedPipeServerProcessId.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.ULONG)]
    api.GetNamedPipeServerProcessId.restype = wintypes.BOOL
    api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    api.WaitForSingleObject.restype = wintypes.DWORD
    api.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    api.TerminateProcess.restype = wintypes.BOOL
    return api


class Process:
    def __init__(self, pid: int, expected_birth: int | None = None):
        self.api = kernel()
        self.pid = pid
        self.handle = self.api.OpenProcess(0x100000 | 0x1000 | 0x0800 | 1, False, pid)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            values = [wintypes.FILETIME() for _ in range(4)]
            if not self.api.GetProcessTimes(
                self.handle, *(ctypes.byref(value) for value in values),
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            self.birth = (values[0].dwHighDateTime << 32) | values[0].dwLowDateTime
            if expected_birth is not None and self.birth != expected_birth:
                raise RuntimeError("fixture process generation changed")
        except BaseException:
            self.close()
            raise

    def in_job(self) -> bool:
        result = wintypes.BOOL()
        if not self.api.IsProcessInJob(self.handle, None, ctypes.byref(result)):
            raise ctypes.WinError(ctypes.get_last_error())
        return bool(result.value)

    def wait(self, milliseconds: int) -> bool:
        result = self.api.WaitForSingleObject(self.handle, milliseconds)
        if result not in (0, 0x102):
            raise ctypes.WinError(ctypes.get_last_error())
        return result == 0

    def retire(self):
        if not self.wait(0) and not self.api.TerminateProcess(self.handle, 1):
            error = ctypes.get_last_error()
            if not self.wait(0):
                raise ctypes.WinError(error)
        if not self.wait(5000):
            raise RuntimeError("fixture process exit unconfirmed")

    def close(self):
        if self.handle:
            if not self.api.CloseHandle(self.handle):
                raise ctypes.WinError(ctypes.get_last_error())
            self.handle = None


def receive(connection) -> dict:
    if not connection.poll(10):
        raise TimeoutError("fixture pipe response deadline")
    value = json.loads(connection.recv_bytes(4096))
    if not isinstance(value, dict):
        raise ValueError("fixture pipe expected an object")
    return value


def send(connection, value: dict):
    data = json.dumps(value).encode("utf-8")
    if len(data) > 4096:
        raise ValueError("fixture frame budget")
    connection.send_bytes(data)


def broker(address: str, root: Path) -> int:
    child = None
    committed = False
    process = Process(os.getpid())
    try:
        # This credential-free fixture is deliberately not production IPC.
        with Listener(address, family="AF_PIPE", authkey=None) as listener:
            with listener.accept() as connection:
                send(connection, {
                    "pid": process.pid, "birth": process.birth,
                    "broker_in_any_job": process.in_job(),
                })
                python = str(Path(sys.executable).with_name("python.exe"))
                marker = root / (address.rsplit("-", 1)[-1] + ".resumed")
                program = (
                    "import ctypes,time\nfrom pathlib import Path\n"
                    "k=ctypes.WinDLL('kernel32');u=ctypes.WinDLL('user32')\n"
                    "k.GetConsoleWindow.restype=ctypes.c_void_p\n"
                    "u.IsWindowVisible.argtypes=[ctypes.c_void_p]\n"
                    "assert not u.IsWindowVisible(k.GetConsoleWindow()), 'visible fixture console'\n"
                    f"Path({str(marker)!r}).write_text('resumed')\n"
                    "time.sleep(60)\n"
                )
                try:
                    child = subprocess.Popen(
                        [python, "-I", "-c", program], cwd=root,
                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL, close_fds=True,
                        # Synthetic breakaway capability probe, never a fallback.
                        creationflags=0x01000000 | 0x08000000 | 0x00000004,
                    )
                except OSError as error:
                    send(connection, {"launch_error": error.winerror})
                    return 1
                pinned = Process(child.pid)
                try:
                    send(connection, {
                        "pid": pinned.pid, "birth": pinned.birth,
                        "child_in_any_job": pinned.in_job(),
                    })
                    if receive(connection) != {"adopt": pinned.pid}:
                        raise RuntimeError("fixture child not adopted")
                    native = ctypes.WinDLL("ntdll")
                    native.NtResumeProcess.argtypes = [wintypes.HANDLE]
                    native.NtResumeProcess.restype = ctypes.c_long
                    if native.NtResumeProcess(pinned.handle) != 0:
                        raise OSError("fixture resume failed")
                    committed = True
                    send(connection, {"started": pinned.pid})
                finally:
                    pinned.close()
        return 0
    finally:
        try:
            if child is not None and not committed:
                child.kill()
                child.wait(timeout=5)
        finally:
            process.close()


def inspect(address: str, root: Path) -> dict:
    deadline = time.monotonic() + 15
    while True:
        try:
            connection = Client(address, family="AF_PIPE", authkey=None)
            break
        except FileNotFoundError:
            if time.monotonic() >= deadline:
                raise TimeoutError("fixture broker did not publish its pipe") from None
            time.sleep(.05)
    bootstrap = child = None
    report = {}
    try:
        with connection:
            hello = receive(connection)
            report.update(hello)
            peer = wintypes.ULONG()
            if not kernel().GetNamedPipeServerProcessId(connection.fileno(), ctypes.byref(peer)):
                raise ctypes.WinError(ctypes.get_last_error())
            if hello.get("pid") != peer.value:
                raise RuntimeError("fixture pipe server PID mismatch")
            bootstrap = Process(peer.value, hello["birth"])
            staged = receive(connection)
            if "launch_error" in staged:
                report.update(staged, child_escape_proved=False)
                return report
            child = Process(staged["pid"], staged["birth"])
            if child.in_job() or staged.get("child_in_any_job") is not False:
                report.update(child_escape_proved=False, reason="child-still-in-job")
                return report
            send(connection, {"adopt": child.pid})
            if receive(connection) != {"started": child.pid}:
                raise RuntimeError("fixture resume acknowledgement mismatch")
        if not bootstrap.wait(5000):
            raise RuntimeError("finite fixture broker did not exit")
        marker = root / (address.rsplit("-", 1)[-1] + ".resumed")
        deadline = time.monotonic() + 5
        while not marker.is_file() and not child.wait(0) and time.monotonic() < deadline:
            time.sleep(.02)
        if not marker.is_file():
            raise RuntimeError("actual child did not run headlessly")
        for _ in range(2):
            if child.wait(250):
                raise RuntimeError("fixture child did not outlive finite broker")
        report.update(
            child_escape_proved=True, actual_child_pid=child.pid,
            finite_broker_exited=True, headless_child_resumed=True,
        )
        return report
    finally:
        if child is not None:
            child.retire()
            child.close()
        report["child_cleanup_confirmed"] = True
        if bootstrap is not None:
            bootstrap.close()


def main() -> int:
    role, directory, nonce = sys.argv[1:]
    root = Path(directory).resolve()
    if not re.fullmatch(r"[0-9a-f]{32}", nonce):
        raise ValueError("fixture nonce")
    if role == "broker":
        return broker(r"\\.\pipe\ce-task-probe-" + nonce, root)
    if role != "inspect" or (
        os.environ.get("GITHUB_ACTIONS") != "true"
        or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"
        or not root.is_relative_to(Path(os.environ["RUNNER_TEMP"]).resolve())
    ):
        raise RuntimeError("probe requires its explicitly authorized ephemeral CI runner")
    print(json.dumps(inspect(r"\\.\pipe\ce-task-probe-" + nonce, root)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
