"""Caller-contained ConPTY, with bounded output and pinned process cleanup."""

from __future__ import annotations

import ctypes
from ctypes import wintypes as w
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time

from agent_procutil import _assign_suspended_to_kill_on_close_job


class Coord(ctypes.Structure):
    _fields_ = [("X", ctypes.c_short), ("Y", ctypes.c_short)]


class StartupInfo(ctypes.Structure):
    _fields_ = [
        ("cb", w.DWORD), ("reserved", w.LPWSTR), ("desktop", w.LPWSTR),
        ("title", w.LPWSTR), ("x", w.DWORD), ("y", w.DWORD),
        ("width", w.DWORD), ("height", w.DWORD), ("chars_x", w.DWORD),
        ("chars_y", w.DWORD), ("fill", w.DWORD), ("flags", w.DWORD),
        ("show", w.WORD), ("reserved_size", w.WORD),
        ("reserved_bytes", ctypes.POINTER(w.BYTE)),
        ("stdin", w.HANDLE), ("stdout", w.HANDLE), ("stderr", w.HANDLE),
    ]


class StartupInfoEx(ctypes.Structure):
    _fields_ = [("startup", StartupInfo), ("attributes", w.LPVOID)]


class ProcessInfo(ctypes.Structure):
    _fields_ = [
        ("process", w.HANDLE), ("thread", w.HANDLE),
        ("pid", w.DWORD), ("tid", w.DWORD),
    ]


class ProcessEntry(ctypes.Structure):
    _fields_ = [
        ("size", w.DWORD), ("usage", w.DWORD), ("pid", w.DWORD),
        ("heap", ctypes.c_size_t), ("module", w.DWORD), ("threads", w.DWORD),
        ("parent", w.DWORD), ("priority", w.LONG), ("flags", w.DWORD),
        ("name", w.WCHAR * 260),
    ]


class Console:
    def __init__(self, argv, *, env, cwd):
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "CreatePipe": ([ctypes.POINTER(w.HANDLE), ctypes.POINTER(w.HANDLE), w.LPVOID, w.DWORD], w.BOOL),
            "CreatePseudoConsole": ([Coord, w.HANDLE, w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE)], ctypes.c_long),
            "ResizePseudoConsole": ([w.HANDLE, Coord], ctypes.c_long),
            "ClosePseudoConsole": ([w.HANDLE], None),
            "InitializeProcThreadAttributeList": ([w.LPVOID, w.DWORD, w.DWORD, ctypes.POINTER(ctypes.c_size_t)], w.BOOL),
            "UpdateProcThreadAttribute": ([w.LPVOID, w.DWORD, ctypes.c_size_t, w.LPVOID, ctypes.c_size_t, w.LPVOID, w.LPVOID], w.BOOL),
            "DeleteProcThreadAttributeList": ([w.LPVOID], None),
            "CreateProcessW": ([w.LPCWSTR, w.LPWSTR, w.LPVOID, w.LPVOID, w.BOOL, w.DWORD, w.LPVOID, w.LPCWSTR, w.LPVOID, ctypes.POINTER(ProcessInfo)], w.BOOL),
            "ResumeThread": ([w.HANDLE], w.DWORD),
            "WaitForSingleObject": ([w.HANDLE, w.DWORD], w.DWORD),
            "GetExitCodeProcess": ([w.HANDLE, ctypes.POINTER(w.DWORD)], w.BOOL),
            "TerminateProcess": ([w.HANDLE, w.UINT], w.BOOL),
            "ReadFile": ([w.HANDLE, w.LPVOID, w.DWORD, ctypes.POINTER(w.DWORD), w.LPVOID], w.BOOL),
            "WriteFile": ([w.HANDLE, w.LPCVOID, w.DWORD, ctypes.POINTER(w.DWORD), w.LPVOID], w.BOOL),
            "CloseHandle": ([w.HANDLE], w.BOOL),
            "OpenProcess": ([w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
            "CreateToolhelp32Snapshot": ([w.DWORD, w.DWORD], w.HANDLE),
            "Process32FirstW": ([w.HANDLE, ctypes.POINTER(ProcessEntry)], w.BOOL),
            "Process32NextW": ([w.HANDLE, ctypes.POINTER(ProcessEntry)], w.BOOL),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self.api, name)
            function.argtypes, function.restype = arguments, result
        self.handles = []
        self.pseudo = w.HANDLE()
        self.process = ProcessInfo()
        self.job = None
        self.reader = None
        self.output = bytearray()
        self.read_error = None
        try:
            input_read, self.input = self._pipe()
            self.output_handle, output_write = self._pipe()
            self._hresult(self.api.CreatePseudoConsole(
                Coord(100, 30), input_read, output_write, 0, ctypes.byref(self.pseudo),
            ))
            self._close(input_read)
            self._close(output_write)
            self.reader = threading.Thread(target=self._drain, daemon=True)
            self.reader.start()
            size = ctypes.c_size_t()
            self.api.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
            attributes = ctypes.create_string_buffer(size.value)
            self._check(self.api.InitializeProcThreadAttributeList(
                attributes, 1, 0, ctypes.byref(size),
            ))
            try:
                self._check(self.api.UpdateProcThreadAttribute(
                    attributes, 0, 0x20016, self.pseudo, ctypes.sizeof(w.HANDLE), None, None,
                ))
                startup = StartupInfoEx()
                startup.startup.cb = ctypes.sizeof(startup)
                # Null explicit std handles prevent inheriting pytest's pipes.
                startup.startup.flags = 0x100  # STARTF_USESTDHANDLES
                startup.attributes = ctypes.addressof(attributes)
                environment = ctypes.create_unicode_buffer(
                    "\0".join(f"{key}={value}" for key, value in sorted(env.items())) + "\0\0",
                )
                # ConPTY supplies a headless console; no desktop window is created.
                self._check(self.api.CreateProcessW(
                    None, ctypes.create_unicode_buffer(subprocess.list2cmdline(argv)),
                    None, None, False, 0x80000 | 0x400 | 4,
                    environment, str(cwd), ctypes.byref(startup), ctypes.byref(self.process),
                ))
                self.handles.extend([self.process.process, self.process.thread])
                self.job = _assign_suspended_to_kill_on_close_job(
                    self.process.pid, allow_breakaway=False,
                )
                if self.job is None:
                    self._check(self.api.TerminateProcess(self.process.process, 1))
                    raise AssertionError("ConPTY root did not acquire its strict cleanup Job")
                if self.api.ResumeThread(self.process.thread) == 0xFFFFFFFF:
                    raise ctypes.WinError(ctypes.get_last_error())
            finally:
                self.api.DeleteProcThreadAttributeList(attributes)
        except BaseException:
            self.close()
            raise

    def _check(self, result):
        if not result:
            raise ctypes.WinError(ctypes.get_last_error())

    @staticmethod
    def _hresult(result):
        if result < 0:
            raise OSError(f"ConPTY HRESULT {result & 0xFFFFFFFF:#x}")

    def _pipe(self):
        read, write = w.HANDLE(), w.HANDLE()
        self._check(self.api.CreatePipe(ctypes.byref(read), ctypes.byref(write), None, 0))
        self.handles.extend([read.value, write.value])
        return read.value, write.value

    def _close(self, handle):
        self._check(self.api.CloseHandle(handle))
        self.handles.remove(handle)

    def _drain(self):
        buffer = ctypes.create_string_buffer(4096)
        count = w.DWORD()
        while self.api.ReadFile(self.output_handle, buffer, len(buffer), ctypes.byref(count), None):
            remaining = 1024 * 1024 - len(self.output)
            self.output.extend(buffer.raw[:min(count.value, remaining)])
            if count.value > remaining:
                self.read_error = "ConPTY output exceeded 1 MiB"
        error = ctypes.get_last_error()
        if error not in (0, 109, 232):
            self.read_error = f"ConPTY output failed: {error}"

    def send(self, value):
        count = w.DWORD()
        self._check(self.api.WriteFile(self.input, value, len(value), ctypes.byref(count), None))
        assert count.value == len(value)

    def resize(self, width, height):
        self._hresult(self.api.ResizePseudoConsole(self.pseudo, Coord(width, height)))

    def pin(self, pid):
        handle = self.api.OpenProcess(0x100000, False, pid)  # SYNCHRONIZE
        self._check(handle)
        self.handles.append(handle)
        return handle

    def pin_descendants(self):
        snapshot = self.api.CreateToolhelp32Snapshot(2, 0)
        if snapshot == w.HANDLE(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        entries = {}
        entry = ProcessEntry()
        entry.size = ctypes.sizeof(entry)
        try:
            self._check(self.api.Process32FirstW(snapshot, ctypes.byref(entry)))
            while True:
                entries[entry.pid] = (entry.parent, entry.name)
                if not self.api.Process32NextW(snapshot, ctypes.byref(entry)):
                    assert ctypes.get_last_error() == 18  # ERROR_NO_MORE_FILES
                    break
        finally:
            self._check(self.api.CloseHandle(snapshot))
        descendants = {self.process.pid}
        while True:
            found = {pid for pid, (parent, _) in entries.items() if parent in descendants}
            if found <= descendants:
                break
            descendants |= found
        return {
            pid: (entries[pid][1], self.pin(pid))
            for pid in descendants - {self.process.pid}
        }

    def exited(self, handle, timeout=0):
        result = self.api.WaitForSingleObject(handle, timeout)
        if result == 0xFFFFFFFF:
            raise ctypes.WinError(ctypes.get_last_error())
        return result == 0

    def wait(self, timeout=15000):
        assert self.exited(self.process.process, timeout), self.output.decode("utf-8", "replace")
        code = w.DWORD()
        self._check(self.api.GetExitCodeProcess(self.process.process, ctypes.byref(code)))
        return code.value

    def hangup(self):
        self.api.ClosePseudoConsole(self.pseudo)
        self.pseudo = w.HANDLE()
        assert self.exited(self.process.process, 5000), "Terminal close did not stop its root"

    def close(self):
        if self.job is not None:
            self.job.close()
            self.job = None
        if self.process.process:
            assert self.exited(self.process.process, 5000), "ConPTY root survived cleanup"
        if self.pseudo:
            self.api.ClosePseudoConsole(self.pseudo)
            self.pseudo = w.HANDLE()
        if self.reader is not None:
            self.reader.join(5)
            assert not self.reader.is_alive(), "ConPTY output reader survived cleanup"
        for handle in list(reversed(self.handles)):
            self._close(handle)
        assert self.read_error is None, self.read_error

    def __enter__(self):
        return self

    def __exit__(self, *exception):
        self.close()


def _fixture(root, layer, mode):
    from worktree_manager import engine_client, terminal_launch

    if layer == "outer":
        repository = Path(__file__).resolve().parents[3]
        sys.path[:0] = [
            str(repository / "plugins" / "agent-worktrees" / "src"),
            *(str(path) for path in (repository / "libs").glob("*/src")),
        ]
        from agent_worktrees import manager_launch_cli

        # Only engine discovery is synthetic; the forwarding and Job owner run.
        manager_launch_cli._agent_worktrees_launch_command = lambda _: [
            "fixture-engine", "literal engine",
        ]
        (root / "outer.json").write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
        manager_launch_cli._exec_worktree_manager([
            shutil.which("pwsh"), "-NoProfile", "-NoLogo", "-File", str(root / "nested.ps1"),
        ], None)
        raise AssertionError("Windows forwarding unexpectedly returned")
    outer_record = root / "outer.json"
    outer = json.loads(outer_record.read_text(encoding="utf-8"))["pid"] if outer_record.exists() else 0
    (root / "prepared.json").write_text(json.dumps({
        "pid": os.getpid(), "outer": outer, "request": os.environ["CE_TERMINAL_LAUNCH"],
    }), encoding="utf-8")
    deadline = time.monotonic() + 20
    while not (root / "release").exists():
        if time.monotonic() > deadline:
            raise RuntimeError("Controller did not release preparation")
        time.sleep(.025)
    if mode == "cancel":
        return 0
    if mode == "failed":
        return 7
    if mode == "malformed":
        request = Path(os.environ["CE_TERMINAL_LAUNCH"])
        (request.parent / "result.json").write_text("{}", encoding="utf-8")
        return 0
    engine_client.engine_base_command = lambda: ["fixture-engine", "literal engine"]
    from worktree_manager.launch_attempts import LaunchAttempts

    attempt = "d" * 32
    LaunchAttempts().record_intent(
        attempt, action="resume", project="fixture", surface="native", is_local=True,
    )
    literal = json.loads((root / "literal.json").read_text(encoding="utf-8"))
    selected = root / "selected \u65e5.ps1"
    if mode == "production-refusal":
        selected = Path(__file__).resolve().parents[2] / "bin" / "launch-session.ps1"
        literal = ["--project", "fixture", "--worktree-id", "fixture-wt"]
    assert terminal_launch.prepare(selected, literal, attempt)
    return 0


if __name__ == "__main__":
    raise SystemExit(_fixture(Path(sys.argv[1]), sys.argv[2], sys.argv[3]))
