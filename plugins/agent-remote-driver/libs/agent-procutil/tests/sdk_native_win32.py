"""Native process and pipe bindings without desktop or window APIs."""

from __future__ import annotations

import ctypes as ct
from ctypes import wintypes as wt
import os
from typing import Any

from sdk_desktop_observer import ObservationBlocked


class ProcessPin:
    def __init__(
        self, api: NativeProcessAPI, pid: int, birth: int | None = None, *, owned: bool = False,
    ) -> None:
        self.api, self.pid, self.owned = api, pid, owned
        self.handle = api.kernel.OpenProcess(0x1000 | 0x100000 | (1 if owned else 0), False, pid)
        api.check(self.handle, "pin process")
        try:
            self.birth = api.birth(self.handle)
            if birth is not None and birth != self.birth:
                raise ObservationBlocked("process birth changed before pin admission")
        except BaseException:
            self.close()
            raise

    def gone(self) -> bool:
        state = self.api.kernel.WaitForSingleObject(self.handle, 0)
        if state not in (0, 0x102):
            raise ObservationBlocked(f"process wait is unobservable: {state}")
        return state == 0

    def terminate_owned(self) -> None:
        if not self.owned:
            raise ObservationBlocked("foreign process termination is not authorized")
        if not self.gone():
            terminated = self.api.kernel.TerminateProcess(self.handle, 125)
            error = ct.get_last_error() if not terminated else 0
            if not terminated and not self.gone():
                raise ObservationBlocked(f"retire owned fixture: WinError {error}")

    def close(self) -> None:
        if self.handle:
            handle, self.handle = self.handle, None
            self.api.check(self.api.kernel.CloseHandle(handle), "close pinned process")


class NativeProcessAPI:
    def __init__(self) -> None:
        if os.name != "nt":
            raise ObservationBlocked("native Windows process qualification required")
        self.kernel = ct.WinDLL("kernel32", use_last_error=True)
        self.advapi = ct.WinDLL("advapi32", use_last_error=True)
        self._bind(self.kernel, {
            "GetCurrentProcess": ([], wt.HANDLE),
            "GetCurrentThreadId": ([], wt.DWORD),
            "GetTickCount64": ([], ct.c_ulonglong),
            "OpenProcess": ([wt.DWORD, wt.BOOL, wt.DWORD], wt.HANDLE),
            "CloseHandle": ([wt.HANDLE], wt.BOOL),
            "WaitForSingleObject": ([wt.HANDLE, wt.DWORD], wt.DWORD),
            "TerminateProcess": ([wt.HANDLE, wt.UINT], wt.BOOL),
            "IsProcessInJob": ([wt.HANDLE, wt.HANDLE, ct.POINTER(wt.BOOL)], wt.BOOL),
            "GetProcessTimes": ([wt.HANDLE] + [ct.POINTER(wt.FILETIME)] * 4, wt.BOOL),
            "PeekNamedPipe": ([wt.HANDLE, wt.LPVOID, wt.DWORD, ct.POINTER(wt.DWORD),
                               ct.POINTER(wt.DWORD), ct.POINTER(wt.DWORD)], wt.BOOL),
            "GetNamedPipeServerProcessId": ([wt.HANDLE, ct.POINTER(wt.ULONG)], wt.BOOL),
        })
        self._bind(self.advapi, {
            "OpenProcessToken": ([wt.HANDLE, wt.DWORD, ct.POINTER(wt.HANDLE)], wt.BOOL),
            "GetTokenInformation": (
                [wt.HANDLE, ct.c_int, wt.LPVOID, wt.DWORD, ct.POINTER(wt.DWORD)], wt.BOOL,
            ),
        })

    @staticmethod
    def _bind(library: Any, signatures: dict[str, tuple[list[Any], Any]]) -> None:
        for name, (arguments, result) in signatures.items():
            function = getattr(library, name)
            function.argtypes, function.restype = arguments, result

    @staticmethod
    def check(result: Any, operation: str) -> None:
        if not result:
            raise ObservationBlocked(f"{operation}: WinError {ct.get_last_error()}")

    def birth(self, handle: int) -> int:
        times = [wt.FILETIME() for _ in range(4)]
        self.check(self.kernel.GetProcessTimes(handle, *(ct.byref(t) for t in times)), "process birth")
        return (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime

    def identity(self) -> dict[str, int]:
        return {"pid": os.getpid(), "birth": self.birth(self.kernel.GetCurrentProcess())}

    def pin(self, pid: int, birth: int | None = None, *, owned: bool = False) -> ProcessPin:
        return ProcessPin(self, pid, birth, owned=owned)

    def require_parent_pipe(self, request: dict[str, Any]) -> ProcessPin:
        import msvcrt

        parent = ProcessPin(self, request["parent_pid"], request["parent_birth"])
        try:
            server = wt.ULONG()
            self.check(self.kernel.GetNamedPipeServerProcessId(
                msvcrt.get_osfhandle(0), ct.byref(server),
            ), "input pipe server identity")
            if server.value != parent.pid or parent.gone():
                raise ObservationBlocked("private input pipe does not name its original live caller")
            return parent
        except BaseException:
            parent.close()
            raise

    def pipe_bytes(self) -> int | None:
        import msvcrt

        available = wt.DWORD()
        if not self.kernel.PeekNamedPipe(
            msvcrt.get_osfhandle(0), None, 0, None, ct.byref(available), None,
        ):
            error = ct.get_last_error()
            if error == 109:
                return None
            raise ObservationBlocked(f"private command pipe: WinError {error}")
        return int(available.value)

    def pump(self) -> None:
        """Process-only polling has no desktop event queue to dispatch."""
        return None

    def elevated(self) -> bool:
        token, elevation, size = wt.HANDLE(), wt.DWORD(), wt.DWORD()
        self.check(self.advapi.OpenProcessToken(
            self.kernel.GetCurrentProcess(), 8, ct.byref(token),
        ), "read own token")
        try:
            self.check(self.advapi.GetTokenInformation(
                token, 20, ct.byref(elevation), ct.sizeof(elevation), ct.byref(size),
            ), "read elevation")
        finally:
            self.check(self.kernel.CloseHandle(token), "close own token")
        return bool(elevation.value)

    def require_process_owner(self) -> dict[str, bool]:
        contained = wt.BOOL()
        self.check(self.kernel.IsProcessInJob(
            self.kernel.GetCurrentProcess(), None, ct.byref(contained),
        ), "verify inherited test containment")
        if not contained.value:
            raise ObservationBlocked("process qualification requires its admitted outer Job owner")
        elevated = self.elevated()
        if elevated:
            raise ObservationBlocked("process qualification requires an unelevated test owner")
        return {"inherited_job_membership": True, "elevated": False}
