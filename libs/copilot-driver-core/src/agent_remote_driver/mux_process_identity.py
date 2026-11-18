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


ERROR_ACCESS_DENIED = 5
_ERROR_INVALID_PARAMETER = 87
_STILL_ACTIVE = 259


def access(pid: int) -> tuple[str, int]:
    """Classify an unreadable process as ``gone``, ``denied``, ``live`` or ``unknown``.

    Returns the state with the Win32 error that justified it.  ``gone`` is
    reserved for verified absence: the PID no longer names a process, or the
    process has already exited.
    """
    kernel = _kernel()
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        error = ctypes.get_last_error()
        if error == _ERROR_INVALID_PARAMETER:
            return "gone", error
        return ("denied" if error == ERROR_ACCESS_DENIED else "unknown"), error
    try:
        kernel.GetExitCodeProcess.argtypes = [w.HANDLE, ctypes.POINTER(w.DWORD)]
        kernel.GetExitCodeProcess.restype = w.BOOL
        code = w.DWORD()
        if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
            return "unknown", ctypes.get_last_error()
        return ("live" if code.value == _STILL_ACTIVE else "gone"), 0
    finally:
        kernel.CloseHandle(handle)


def system_start_times() -> dict[int, str]:
    """Read every process creation time without opening any process.

    Uses the same FILETIME token as :func:`process_start_time`, so processes
    whose DACL denies this token (for example elevated ones) remain identifiable.
    """

    class UnicodeString(ctypes.Structure):
        _fields_ = [("length", w.USHORT), ("maximum_length", w.USHORT), ("buffer", ctypes.c_void_p)]

    class ProcessInformation(ctypes.Structure):
        _fields_ = [
            ("next", w.ULONG), ("threads", w.ULONG), ("private", ctypes.c_longlong),
            ("faults", w.ULONG), ("watermark", w.ULONG), ("cycles", ctypes.c_ulonglong),
            ("created", ctypes.c_longlong), ("user", ctypes.c_longlong),
            ("kernel", ctypes.c_longlong), ("image", UnicodeString),
            ("priority", ctypes.c_long), ("pid", ctypes.c_void_p),
        ]

    query = ctypes.WinDLL("ntdll", use_last_error=True).NtQuerySystemInformation
    query.argtypes = [ctypes.c_int, ctypes.c_void_p, w.ULONG, ctypes.POINTER(w.ULONG)]
    query.restype = ctypes.c_long
    size = 1 << 20
    while True:
        buffer = ctypes.create_string_buffer(size)
        needed = w.ULONG()
        status = query(5, buffer, size, ctypes.byref(needed))
        if status == 0:
            break
        if status & 0xFFFFFFFF != 0xC0000004 or size >= 1 << 26:
            return {}
        size = max(size * 2, needed.value + 65536)
    result: dict[int, str] = {}
    offset = 0
    while offset + ctypes.sizeof(ProcessInformation) <= size:
        entry = ProcessInformation.from_buffer(buffer, offset)
        if entry.pid:
            result[int(entry.pid)] = str(entry.created)
        if not entry.next:
            return result
        offset += entry.next
    return {}
