"""Native adapter loaded only by the selected Windows CI Job observer."""

from __future__ import annotations

from typing import Any


class WindowsJobAPI:
    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        self.ctypes: Any = ctypes
        self.wintypes: Any = wintypes
        self.kernel: Any = getattr(ctypes, "WinDLL")("kernel32", use_last_error=True)
        signatures = {
            "QueryInformationJobObject": (
                [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD, wintypes.LPVOID],
                wintypes.BOOL,
            ),
            "OpenProcess": ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            "IsProcessInJob": (
                [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)], wintypes.BOOL,
            ),
            "GetProcessTimes": (
                [wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4)], wintypes.BOOL,
            ),
            "WaitForSingleObject": ([wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
            "GetExitCodeProcess": ([wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
            "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self.kernel, name)
            function.argtypes = arguments
            function.restype = result

    def members(self, job: int, capacity: int) -> list[int]:
        ctypes = self.ctypes
        data = ctypes.create_string_buffer(8 + capacity * ctypes.sizeof(ctypes.c_size_t))
        if not self.kernel.QueryInformationJobObject(job, 3, data, ctypes.sizeof(data), None):
            raise OSError("membership-unavailable")
        assigned = ctypes.c_uint32.from_buffer(data, 0).value
        count = ctypes.c_uint32.from_buffer(data, 4).value
        if count != assigned or count > capacity:
            raise OSError("membership-incomplete")
        members = list((ctypes.c_size_t * count).from_buffer(data, 8))
        if len(set(members)) != count or any(pid <= 0 or pid > 0xFFFFFFFF for pid in members):
            raise OSError("membership-invalid")
        return members

    def open(self, pid: int) -> int:
        return int(self.kernel.OpenProcess(0x400 | 0x100000, False, pid) or 0)

    def inside(self, handle: int, job: int) -> bool:
        ctypes, wintypes = self.ctypes, self.wintypes
        inside = wintypes.BOOL()
        if not self.kernel.IsProcessInJob(handle, job, ctypes.byref(inside)):
            raise OSError("member-verification-unavailable")
        return bool(inside.value)

    def times(self, handle: int) -> tuple[int, int, int, int]:
        ctypes, wintypes = self.ctypes, self.wintypes
        values = [wintypes.FILETIME() for _ in range(4)]
        if not self.kernel.GetProcessTimes(handle, *(ctypes.byref(value) for value in values)):
            raise OSError("times-unavailable")
        birth, exit_time, kernel_time, user_time = (
            (int(value.dwHighDateTime) << 32) | int(value.dwLowDateTime) for value in values
        )
        return birth, exit_time, kernel_time, user_time

    def exit_code(self, handle: int) -> int | None:
        ctypes, wintypes = self.ctypes, self.wintypes
        wait = self.kernel.WaitForSingleObject(handle, 0)
        if wait == 258:
            return None
        if wait != 0:
            raise OSError("exit-state-unavailable")
        code = wintypes.DWORD()
        if not self.kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
            raise OSError("exit-code-unavailable")
        return int(code.value)

    def close(self, handle: int) -> bool:
        return bool(self.kernel.CloseHandle(handle))
