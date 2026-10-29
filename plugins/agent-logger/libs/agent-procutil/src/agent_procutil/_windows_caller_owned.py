"""Atomic Job assignment for the opt-in synchronous caller-owned boundary."""

from __future__ import annotations

from contextlib import ExitStack
import ctypes
from ctypes import wintypes
from dataclasses import dataclass, field
from functools import lru_cache
import os
import subprocess
import math
import time

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


def _job_pids(handle, *, kernel=None):
    kernel = _api() if kernel is None else kernel
    kernel.QueryInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel.QueryInformationJobObject.restype = wintypes.BOOL
    capacity = 64
    for _ in range(3):
        if capacity > 65536:
            raise OSError("Job membership exceeds its read budget")
        data = ctypes.create_string_buffer(8 + capacity * ctypes.sizeof(ctypes.c_size_t))
        success = kernel.QueryInformationJobObject(handle, 3, data, ctypes.sizeof(data), None)
        assigned = ctypes.c_uint32.from_buffer(data, 0).value
        count = ctypes.c_uint32.from_buffer(data, 4).value
        if success:
            if count > capacity or count > assigned:
                raise OSError("Job returned invalid membership")
            if count == assigned:
                members = set((ctypes.c_size_t * count).from_buffer(data, 8))
                if len(members) != count or any(pid <= 0 or pid > 0xFFFFFFFF for pid in members):
                    raise OSError("Job returned invalid membership")
                return members
        else:
            error = ctypes.get_last_error()
            if error != 234:
                raise ctypes.WinError(error)
        capacity = max(capacity * 2, assigned)
    raise OSError("Job membership changed during its bounded read")


def _job_total(kernel, handle):
    import agent_procutil as pu

    accounting = pu._JobBasicAccountingInformation()
    returned = wintypes.DWORD()
    if not kernel.QueryInformationJobObject(
        handle, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), ctypes.byref(returned),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    if returned.value != ctypes.sizeof(accounting):
        raise OSError("Job accounting returned an unexpected structure size")
    return accounting.TotalProcesses


@dataclass
class _Retirement:
    duplicate: wintypes.HANDLE = field(default_factory=wintypes.HANDLE)
    handles: list[wintypes.HANDLE | int] = field(default_factory=list)
    pinned: dict[int, wintypes.HANDLE | int] = field(default_factory=dict)
    stable_total: int | None = None
    error: BaseException | None = None
    censused: bool = False
    confirmed: bool = False

    def close(self, kernel):
        error = None
        for handle in reversed(tuple(self.handles)):
            try:
                if not kernel.CloseHandle(handle):
                    raise ctypes.WinError(ctypes.get_last_error())
            except OSError as failure:
                error = failure
            else:
                self.handles.remove(handle)
        if error is not None:
            raise error


def _retire_job(job, timeout):
    """Pin current members before termination, then await their actual exit."""
    import agent_procutil as pu

    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Job retirement timeout must be finite and positive")
    retirement = getattr(job, "_native_retirement", None)
    if retirement is None and not job.handle:
        raise RuntimeError("closed Job cannot attest retirement")
    kernel = _api()
    pu._configure_job_teardown_api(kernel)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.DuplicateHandle.argtypes = [
        wintypes.HANDLE, wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.HANDLE),
        wintypes.DWORD, wintypes.BOOL, wintypes.DWORD,
    ]
    kernel.DuplicateHandle.restype = wintypes.BOOL
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.IsProcessInJob.argtypes = [
        wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL),
    ]
    kernel.IsProcessInJob.restype = wintypes.BOOL
    deadline = time.monotonic() + timeout
    if retirement is None:
        retirement = job._native_retirement = _Retirement()
    if retirement.confirmed:
        retirement.close(kernel)
        del job._native_retirement
        return
    if not retirement.censused:
        duplicate = retirement.duplicate
        current = kernel.GetCurrentProcess()
        try:
            if not kernel.DuplicateHandle(
                current, job.handle, current, ctypes.byref(duplicate), 0, False, 2,
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            if not duplicate.value:
                raise OSError("Job duplicate identity is unavailable")
            retirement.handles.append(duplicate)
            for _ in range(3):
                if time.monotonic() >= deadline:
                    raise TimeoutError("Job member census exceeded its retirement budget")
                before = _job_total(kernel, job.handle)
                for pid in _job_pids(job.handle, kernel=kernel):
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Job member census exceeded its retirement budget")
                    if pid in retirement.pinned:
                        continue
                    handle = kernel.OpenProcess(0x1000 | 0x100000, False, pid)
                    if not handle:
                        if ctypes.get_last_error() == 87:
                            continue
                        raise ctypes.WinError(ctypes.get_last_error())
                    retirement.handles.append(handle)
                    inside = wintypes.BOOL()
                    if not kernel.IsProcessInJob(handle, job.handle, ctypes.byref(inside)):
                        raise ctypes.WinError(ctypes.get_last_error())
                    if inside.value:
                        retirement.pinned[pid] = handle
                after = _job_total(kernel, job.handle)
                if before == after:
                    if after < len(retirement.pinned):
                        raise OSError("Job accounting omitted pinned members")
                    retirement.stable_total = after
                    break
            if retirement.stable_total is None:
                raise OSError("Job membership did not stabilize before retirement")
        except BaseException as failure:
            retirement.error = failure
        finally:
            retirement.censused = True
    remaining = deadline - time.monotonic()
    if retirement.error is not None or remaining <= 0:
        handle = job.handle or retirement.duplicate
        if not kernel.TerminateJobObject(handle, 1):
            raise ctypes.WinError(ctypes.get_last_error())
        if retirement.error is not None:
            raise retirement.error
        raise TimeoutError("Job retirement budget expired before exit confirmation")
    if job.handle:
        job.terminate_and_wait(remaining)
    for handle in retirement.pinned.values():
        status = kernel.WaitForSingleObject(handle, 0)
        if status == 0:
            continue
        if status != 0x102:
            raise ctypes.WinError(ctypes.get_last_error())
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Job member exit was not confirmed")
        status = kernel.WaitForSingleObject(handle, math.ceil(remaining * 1000))
        if status == 0x102:
            raise TimeoutError("Job member exit was not confirmed")
        if status != 0:
            raise ctypes.WinError(ctypes.get_last_error())
    if _job_total(kernel, retirement.duplicate) != retirement.stable_total:
        retirement.error = OSError("Job gained an unpinned process during retirement")
        raise retirement.error
    retirement.confirmed = True
    retirement.close(kernel)
    del job._native_retirement


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

    def retire_job(self, job, timeout):
        _retire_job(job, timeout)


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

    from . import _job_scopes

    job_name = _job_scopes.new_name()
    environment = _environment_block(
        _job_scopes.child_environment(kwargs.get("env"), append=job_name),
    )
    cwd = os.fspath(kwargs["cwd"]) if kwargs.get("cwd") is not None else None
    if cwd is not None and (not isinstance(cwd, str) or "\0" in cwd):
        raise ValueError("native child working directory must be NUL-free text")
    kernel = _api()
    handle = kernel.CreateJobObjectW(None, job_name)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    if ctypes.get_last_error() == 183:
        kernel.CloseHandle(handle)
        raise OSError("owned Job identity already exists")
    job = JobHandle(handle, name=job_name)
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
