"""Read-only kernel boot identity; never recovery or native-scope authority."""

import sys


def windows_boot_id() -> str:
    """Observe the kernel boot, not a process scope or proof of its absence."""
    if sys.platform != "win32":
        return ""
    import ctypes
    from uuid import UUID

    try:
        query = ctypes.WinDLL("ntdll").NtQuerySystemInformation
        query.argtypes = [
            ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint32),
        ]
        query.restype = ctypes.c_int32
        # SystemBootEnvironmentInformation includes a GUID, firmware type and flags.
        data = ctypes.create_string_buffer(32)
        length = ctypes.c_uint32()
        if query(90, data, len(data), ctypes.byref(length)) != 0 or length.value != len(data):
            return ""
        identifier = UUID(bytes_le=data.raw[:16])
        return str(identifier) if identifier.int else ""
    except (AttributeError, OSError, ValueError):
        return ""
