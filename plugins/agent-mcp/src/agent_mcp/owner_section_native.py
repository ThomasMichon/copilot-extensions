"""Windows pagefile sections for the opt-in native-owner handoff."""

from __future__ import annotations

import ctypes as c

from zdd.private_windows_native import BOOL, DWORD, HANDLE, PTR, SECURITY_ATTRIBUTES, Native

SECTION_BYTES = 4096
READ = 4
WRITE = 2


class SectionNative(Native):
    """Reuse the existing principal and process-handle owner, not PID ancestry."""

    def __init__(self) -> None:
        super().__init__()
        for name, arguments, result in (
            ("CreateFileMappingW",
             [HANDLE, c.POINTER(SECURITY_ATTRIBUTES), DWORD, DWORD, DWORD, c.c_wchar_p],
             HANDLE),
            ("OpenFileMappingW", [DWORD, BOOL, c.c_wchar_p], HANDLE),
            ("MapViewOfFile", [HANDLE, DWORD, DWORD, DWORD, c.c_size_t], PTR),
            ("UnmapViewOfFile", [PTR], BOOL),
        ):
            function = getattr(self.k, name)
            function.argtypes, function.restype = arguments, result

    def require(self, value):
        if not value:
            raise c.WinError(c.get_last_error())
        return value

    def create(self, name: str) -> int:
        sid = self.current_sid()
        descriptor = PTR()
        self.require(self.a.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            f"O:{sid}D:P(A;;GA;;;{sid})", 1, c.byref(descriptor), None,
        ))
        try:
            security = SECURITY_ATTRIBUTES(c.sizeof(SECURITY_ATTRIBUTES), descriptor, False)
            handle = self.k.CreateFileMappingW(
                HANDLE(-1), c.byref(security), 4, 0, SECTION_BYTES, name,
            )
            error = c.get_last_error()
        finally:
            self.free(descriptor)
        if not handle:
            raise c.WinError(error)
        if error == 183:
            self.close(handle)
            raise FileExistsError("Native owner section already exists")
        return handle

    def readonly_duplicate(self, handle: int) -> int:
        result = HANDLE()
        current = self.k.GetCurrentProcess()
        self.require(self.k.DuplicateHandle(
            current, handle, current, c.byref(result), READ, True, 0,
        ))
        return result.value

    def read(self, handle: int) -> bytes:
        view = self.require(self.k.MapViewOfFile(handle, READ, 0, 0, SECTION_BYTES))
        try:
            return c.string_at(view, SECTION_BYTES)
        finally:
            self.require(self.k.UnmapViewOfFile(view))

    def write(self, handle: int, packet: bytes) -> None:
        if len(packet) != SECTION_BYTES:
            raise ValueError("Invalid native owner section size")
        view = self.require(self.k.MapViewOfFile(handle, WRITE, 0, 0, SECTION_BYTES))
        try:
            c.memmove(view, packet, SECTION_BYTES)
        finally:
            self.require(self.k.UnmapViewOfFile(view))
