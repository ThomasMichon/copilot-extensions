"""Native read-only process identity for complete Windows mux coverage."""

import ctypes
from ctypes import wintypes as w


def _kernel():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    kernel.OpenProcess.restype = w.HANDLE
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.CloseHandle.restype = w.BOOL
    return kernel


def process_start_time(pid: int) -> str | None:
    kernel = _kernel()
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return None
    try:
        fields = [w.FILETIME() for _ in range(4)]
        kernel.GetProcessTimes.argtypes = [w.HANDLE, *([ctypes.POINTER(w.FILETIME)] * 4)]
        kernel.GetProcessTimes.restype = w.BOOL
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(value) for value in fields)):
            return None
        return str((fields[0].dwHighDateTime << 32) | fields[0].dwLowDateTime)
    finally:
        kernel.CloseHandle(handle)


def command_line(pid: int) -> str | None:
    kernel = _kernel()
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return None
    try:
        query = ctypes.WinDLL("ntdll", use_last_error=True).NtQueryInformationProcess
        query.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.ULONG, ctypes.POINTER(w.ULONG)]
        query.restype = ctypes.c_long
        needed = w.ULONG()
        query(handle, 60, None, 0, ctypes.byref(needed))

        class UnicodeString(ctypes.Structure):
            _fields_ = [("length", w.USHORT), ("maximum_length", w.USHORT), ("buffer", ctypes.c_void_p)]

        if not ctypes.sizeof(UnicodeString) <= needed.value <= 131072:
            return None
        buffer = ctypes.create_string_buffer(needed.value)
        if query(handle, 60, buffer, len(buffer), ctypes.byref(needed)) != 0:
            return None
        value = UnicodeString.from_buffer(buffer)
        start, pointer = ctypes.addressof(buffer), value.buffer or 0
        if (
            value.length % 2 or value.length > value.maximum_length
            or pointer < start + ctypes.sizeof(UnicodeString)
            or pointer + value.length > start + len(buffer)
        ):
            return None
        return ctypes.string_at(pointer, value.length).decode("utf-16-le")
    finally:
        kernel.CloseHandle(handle)
