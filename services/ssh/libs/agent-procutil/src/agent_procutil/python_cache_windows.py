"""Bounded Windows process-reference reads for the installation principal."""
from __future__ import annotations

from contextlib import contextmanager
import ctypes as c
from ctypes import wintypes as w
import os
from typing import Any, Callable, Iterator

from ._daemon_instance_identity import _windows_birth
from .python_cache_processes import Unavailable


class Reader:
    def __init__(self, *, check: Callable[[], None]) -> None:
        if os.name != "nt" or c.sizeof(c.c_void_p) != 8:
            raise OSError("native cache observation requires 64-bit Windows")
        self.check = check
        api: Any = c
        self.kernel = api.WinDLL("kernel32", use_last_error=True)
        self.native = api.WinDLL("ntdll")
        self.wts = api.WinDLL("wtsapi32", use_last_error=True)
        self.security = api.WinDLL("advapi32", use_last_error=True)
        signatures = (
            (self.kernel, "OpenProcess", [w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
            (self.kernel, "CloseHandle", [w.HANDLE], w.BOOL),
            (self.kernel, "LocalFree", [w.LPVOID], w.LPVOID),
            (self.kernel, "ReadProcessMemory",
             [w.HANDLE, w.LPCVOID, w.LPVOID, c.c_size_t, c.POINTER(c.c_size_t)], w.BOOL),
            (self.native, "NtQueryInformationProcess",
             [w.HANDLE, c.c_int, w.LPVOID, w.ULONG, c.POINTER(w.ULONG)], c.c_long),
            (self.native, "NtQuerySystemInformation",
             [c.c_int, w.LPVOID, w.ULONG, c.POINTER(w.ULONG)], c.c_long),
            (self.wts, "WTSEnumerateProcessesW",
             [w.HANDLE, w.DWORD, w.DWORD, c.POINTER(w.LPVOID), c.POINTER(w.DWORD)], w.BOOL),
            (self.wts, "WTSFreeMemory", [w.LPVOID], None),
            (self.security, "ConvertSidToStringSidW",
             [w.LPVOID, c.POINTER(w.LPWSTR)], w.BOOL),
        )
        for dll, name, arguments, result in signatures:
            function = getattr(dll, name)
            function.argtypes, function.restype = arguments, result
        self.principal = ""
        self._read_bytes = 0

    def _births(self) -> dict[int, int]:
        size = 1024 * 1024
        while size <= 64 * 1024 * 1024:
            self.check()
            buffer = c.create_string_buffer(size)
            needed = w.ULONG()
            status = self.native.NtQuerySystemInformation(
                5, buffer, size, c.byref(needed),
            )
            if status == 0:
                break
            if status & 0xFFFFFFFF != 0xC0000004:
                raise OSError("native process census unavailable")
            size = max(size * 2, needed.value)
        else:
            raise ValueError("native process census oversized")
        length = needed.value
        if not 104 <= length <= size:
            raise ValueError("native process census framing unavailable")
        data, offset = buffer.raw[:length], 0
        result: dict[int, int] = {}
        while True:
            self.check()
            if offset + 104 > length or len(result) >= 32768:
                raise ValueError("native process census framing unavailable")
            following = int.from_bytes(data[offset:offset + 4], "little")
            birth = int.from_bytes(data[offset + 32:offset + 40], "little")
            pid = int.from_bytes(data[offset + 80:offset + 88], "little")
            if pid not in (0, 4):
                if pid in result or not birth:
                    raise ValueError("native process identity unavailable")
                result[pid] = birth
            if not following:
                return result
            if following < 104 or following % 8:
                raise ValueError("native process census framing unavailable")
            offset += following

    def _principals(self) -> dict[int, str]:
        class Process(c.Structure):
            _fields_ = [
                ("session", w.DWORD), ("pid", w.DWORD),
                ("name", w.LPWSTR), ("sid", w.LPVOID),
            ]

        data, count = w.LPVOID(), w.DWORD()
        self.check()
        if not self.wts.WTSEnumerateProcessesW(None, 0, 1, c.byref(data), c.byref(count)):
            api: Any = c
            raise api.WinError(api.get_last_error())
        try:
            if count.value > 32768:
                raise ValueError("native principal census oversized")
            rows = c.cast(data, c.POINTER(Process))
            result = {}
            for index in range(count.value):
                self.check()
                row = rows[index]
                if row.pid in (0, 4):
                    continue
                text = w.LPWSTR()
                if not row.sid or not self.security.ConvertSidToStringSidW(row.sid, c.byref(text)):
                    raise Unavailable("process-principal-unavailable")
                try:
                    if row.pid in result:
                        raise ValueError("native principal census changed")
                    if not text.value:
                        raise Unavailable("process-principal-unavailable")
                    result[row.pid] = text.value
                finally:
                    self.kernel.LocalFree(text)
            return result
        finally:
            self.wts.WTSFreeMemory(data)

    def snapshot(self) -> dict[int, tuple[int, str]]:
        for _ in range(3):
            before = self._births()
            principals = self._principals()
            after = self._births()
            if before != after or set(before) != set(principals):
                continue
            principal = principals.get(os.getpid())
            if not principal or self.principal and principal != self.principal:
                raise Unavailable("installation-principal-unavailable")
            self.principal = principal
            return {pid: (birth, principals[pid]) for pid, birth in before.items()}
        raise Unavailable("process-census-changed")

    @contextmanager
    def pin(self, pid: int, birth: int) -> Iterator[Any]:
        self.check()
        handle = self.kernel.OpenProcess(0x1000 | 0x0010 | 0x100000, False, pid)
        if not handle:
            api: Any = c
            error = api.get_last_error()
            if error in (87, 1168):
                raise ProcessLookupError(pid)
            raise api.WinError(error)
        try:
            if _windows_birth(handle) != f"windows:{birth}":
                raise ValueError("native process identity changed")
            yield handle
        finally:
            self.kernel.CloseHandle(handle)

    def _read(self, handle: Any, address: int, length: int) -> bytes:
        self.check()
        if not address or not 0 < length <= 1024 * 1024:
            raise ValueError("native process read exceeds its bound")
        if self._read_bytes + length > 64 * 1024 * 1024:
            raise ValueError("native process reads exceed aggregate bound")
        output, read = c.create_string_buffer(length), c.c_size_t()
        if not self.kernel.ReadProcessMemory(handle, address, output, length, c.byref(read)):
            api: Any = c
            raise api.WinError(api.get_last_error())
        if read.value != length:
            raise OSError("native process read incomplete")
        self._read_bytes += length
        return output.raw

    def texts(self, handle: Any) -> Iterator[str]:
        self.check()
        wow64 = c.c_size_t()
        if self.native.NtQueryInformationProcess(
            handle, 26, c.byref(wow64), c.sizeof(wow64), None,
        ):
            raise OSError("native process bitness unavailable")
        if wow64.value:
            peb, pointer, parameters_offset = wow64.value, 4, 0x10
            command_offset, environment_offset = 0x40, 0x48
        else:
            basic = (c.c_void_p * 6)()
            if self.native.NtQueryInformationProcess(
                handle, 0, c.byref(basic), c.sizeof(basic), None,
            ):
                raise OSError("native process parameters unavailable")
            peb, pointer, parameters_offset = basic[1] or 0, 8, 0x20
            command_offset, environment_offset = 0x70, 0x80
        if not peb:
            raise ValueError("native process parameters unavailable")
        parameters = int.from_bytes(self._read(handle, peb + parameters_offset, pointer), "little")
        command = self._read(handle, parameters + command_offset, pointer * 2)
        length = int.from_bytes(command[:2], "little")
        address = int.from_bytes(command[pointer:pointer * 2], "little")
        if length % 2:
            raise ValueError("native command framing unavailable")
        yield self._read(handle, address, length).decode("utf-16-le") if length else ""
        address = int.from_bytes(
            self._read(handle, parameters + environment_offset, pointer), "little",
        )
        data = bytearray()
        while len(data) < 1024 * 1024:
            chunk = min(4096 - address % 4096, 1024 * 1024 - len(data))
            data.extend(self._read(handle, address, chunk))
            address += chunk
            end = data.find(b"\0\0\0\0")
            while end >= 0:
                if end % 2 == 0:
                    yield bytes(data[:end]).decode("utf-16-le")
                    return
                end = data.find(b"\0\0\0\0", end + 1)
        raise ValueError("native process environment exceeds its bound")
