"""Atomic Job assignment for the opt-in synchronous caller-owned boundary."""

from __future__ import annotations

from contextlib import ExitStack
import ctypes
from ctypes import wintypes
from functools import lru_cache
import os
import subprocess

from agent_procutil import JobHandle, _build_job_extended_limit_info, _configure_job_api, _kernel32


class _StartupInfo(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR), ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        *[(name, wintypes.DWORD) for name in (
            "dwX", "dwY", "dwXSize", "dwYSize", "dwXCountChars", "dwYCountChars",
            "dwFillAttribute", "dwFlags",
        )],
        ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.POINTER(wintypes.BYTE)),
        ("hStdInput", wintypes.HANDLE), ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class _StartupInfoEx(ctypes.Structure):
    _fields_ = [("StartupInfo", _StartupInfo), ("lpAttributeList", wintypes.LPVOID)]


class _ProcessInfo(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD),
    ]


@lru_cache(maxsize=1)
def _api():
    kernel = _kernel32()
    _configure_job_api(kernel)
    signatures = {
        "InitializeProcThreadAttributeList": (
            [wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.c_size_t)],
            wintypes.BOOL,
        ),
        "UpdateProcThreadAttribute": (
            [wintypes.LPVOID, wintypes.DWORD, ctypes.c_size_t, wintypes.LPVOID,
             ctypes.c_size_t, wintypes.LPVOID, wintypes.LPVOID], wintypes.BOOL,
        ),
        "DeleteProcThreadAttributeList": ([wintypes.LPVOID], None),
        "CreateProcessW": (
            [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.LPVOID, wintypes.LPVOID,
             wintypes.BOOL, wintypes.DWORD, wintypes.LPVOID, wintypes.LPCWSTR,
             ctypes.POINTER(_StartupInfoEx), ctypes.POINTER(_ProcessInfo)], wintypes.BOOL,
        ),
        "ResumeThread": ([wintypes.HANDLE], wintypes.DWORD),
        "WaitForSingleObject": ([wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
        "GetExitCodeProcess": (
            [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL,
        ),
        "TerminateProcess": ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
        "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
    }
    for name, (arguments, result) in signatures.items():
        function = getattr(kernel, name)
        function.argtypes = arguments
        function.restype = result
    return kernel


class _Process:
    def __init__(self, argv, info, streams):
        self.args = argv
        self.pid = info.dwProcessId
        self._handle = info.hProcess
        self.stdin, self.stdout, self.stderr = streams
        self.returncode = None

    def wait(self, timeout=None):
        if self.returncode is not None:
            return self.returncode
        milliseconds = 0xFFFFFFFF if timeout is None else min(
            0xFFFFFFFE, max(0, int(timeout * 1000)),
        )
        status = _api().WaitForSingleObject(self._handle, milliseconds)
        if status == 0x102:
            raise subprocess.TimeoutExpired(self.args, timeout)
        if status != 0:
            raise ctypes.WinError(ctypes.get_last_error())
        code = wintypes.DWORD()
        if not _api().GetExitCodeProcess(self._handle, ctypes.byref(code)):
            raise ctypes.WinError(ctypes.get_last_error())
        self.returncode = code.value
        return self.returncode

    def poll(self):
        try:
            return self.wait(0)
        except subprocess.TimeoutExpired:
            return None

    def kill(self):
        if self.poll() is None and not _api().TerminateProcess(self._handle, 1):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self):
        if self._handle:
            if not _api().CloseHandle(self._handle):
                raise ctypes.WinError(ctypes.get_last_error())
            self._handle = None


def _environment_block(environment):
    if environment is None:
        return None
    entries = []
    for key, value in sorted(environment.items(), key=lambda item: str(item[0]).upper()):
        if not isinstance(key, str) or not isinstance(value, str):
            raise TypeError("native child environment must contain text keys and values")
        if not key or "\0" in key or "\0" in value or "=" in key[1:]:
            raise ValueError("native child environment contains an invalid key or NUL")
        entries.append(key + "=" + value)
    return ctypes.create_unicode_buffer("\0".join(entries) + "\0\0")


def spawn(argv: list[str], **kwargs):
    """Create the target in its strict Job atomically, before it can run."""
    import msvcrt
    from agent_procutil.owned_process import OwnedCleanupError

    environment = _environment_block(kwargs.get("env"))
    cwd = os.fspath(kwargs["cwd"]) if kwargs.get("cwd") is not None else None
    if cwd is not None and (not isinstance(cwd, str) or "\0" in cwd):
        raise ValueError("native child working directory must be NUL-free text")
    kernel = _api()
    handle = kernel.CreateJobObjectW(None, None)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    job = JobHandle(handle)
    process = None
    info = _ProcessInfo()
    streams = []
    try:
        limit = _build_job_extended_limit_info()()
        limit.BasicLimitInformation.LimitFlags = 0x2000
        if not kernel.SetInformationJobObject(
            handle, 9, ctypes.byref(limit), ctypes.sizeof(limit),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        with ExitStack() as resources, ExitStack() as parent_streams:
            child_fds = []
            for index, name in enumerate(("stdin", "stdout", "stderr")):
                if kwargs[name] == subprocess.PIPE:
                    reader, writer = os.pipe()
                    child, parent = (reader, writer) if index == 0 else (writer, reader)
                    resources.callback(os.close, child)
                    try:
                        stream = os.fdopen(parent, "wb" if index == 0 else "rb", buffering=0)
                    except BaseException:
                        os.close(parent)
                        raise
                    parent_streams.callback(stream.close)
                else:
                    child = os.open(
                        os.devnull, (os.O_RDONLY if index == 0 else os.O_WRONLY) | os.O_BINARY,
                    )
                    resources.callback(os.close, child)
                    stream = None
                child_fds.append(child)
                streams.append(stream)
            handles = (wintypes.HANDLE * 3)(*(msvcrt.get_osfhandle(fd) for fd in child_fds))
            jobs = (wintypes.HANDLE * 1)(handle)
            size = ctypes.c_size_t()
            kernel.InitializeProcThreadAttributeList(None, 2, 0, ctypes.byref(size))
            if not size.value:
                raise ctypes.WinError(ctypes.get_last_error())
            attributes = ctypes.create_string_buffer(size.value)
            if not kernel.InitializeProcThreadAttributeList(
                attributes, 2, 0, ctypes.byref(size),
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            resources.callback(kernel.DeleteProcThreadAttributeList, attributes)
            for key, value in ((0x20002, handles), (0x2000D, jobs)):
                if not kernel.UpdateProcThreadAttribute(
                    attributes, 0, key, value, ctypes.sizeof(value), None, None,
                ):
                    raise ctypes.WinError(ctypes.get_last_error())
            startup = _StartupInfoEx()
            startup.StartupInfo.cb = ctypes.sizeof(startup)
            startup.StartupInfo.dwFlags = 0x100 | 1
            startup.StartupInfo.hStdInput, startup.StartupInfo.hStdOutput, \
                startup.StartupInfo.hStdError = handles
            startup.lpAttributeList = ctypes.cast(attributes, wintypes.LPVOID)
            command = ctypes.create_unicode_buffer(subprocess.list2cmdline(argv))
            try:
                for fd in child_fds:
                    os.set_handle_inheritable(msvcrt.get_osfhandle(fd), True)
                if not kernel.CreateProcessW(
                    None, command, None, None, True,
                    0x80000 | 0x8000000 | 4 | (0x400 if environment is not None else 0),
                    environment, cwd, ctypes.byref(startup), ctypes.byref(info),
                ):
                    raise ctypes.WinError(ctypes.get_last_error())
                process = _Process(argv, info, streams)
                if kernel.ResumeThread(info.hThread) == 0xFFFFFFFF:
                    raise ctypes.WinError(ctypes.get_last_error())
            finally:
                if info.hThread:
                    kernel.CloseHandle(info.hThread)
            # Transfer only parent streams and native custody, never inheritable child handles.
            parent_streams.pop_all()
            return process, job
    except BaseException:
        if process is None and info.hProcess:
            process = _Process(argv, info, streams)
        if process is not None:
            try:
                job.terminate_and_wait(2.0)
                process.wait(2.0)
            except BaseException as error:
                raise OwnedCleanupError(process, job) from error
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
            process.close()
        else:
            job.close()
        raise
