"""Passive file-version admission for new Windows PSMux sessions."""

MINIMUM_VERSION = (3, 4, 0, 0)


def file_version(path: str) -> tuple[int, int, int, int] | None:
    import ctypes
    from ctypes import wintypes

    try:
        api = getattr(ctypes, "WinDLL")("version", use_last_error=True)
        api.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.c_void_p]
        api.GetFileVersionInfoSizeW.restype = wintypes.DWORD
        api.GetFileVersionInfoW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        ]
        api.GetFileVersionInfoW.restype = wintypes.BOOL
        api.VerQueryValueW.argtypes = [
            ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(wintypes.UINT),
        ]
        api.VerQueryValueW.restype = wintypes.BOOL
        size = api.GetFileVersionInfoSizeW(path, None)
        if not 0 < size <= 1024 * 1024:
            return None
        buffer = ctypes.create_string_buffer(size)
        if not api.GetFileVersionInfoW(path, 0, size, buffer):
            return None
        address = ctypes.c_void_p()
        length = wintypes.UINT()
        if (
            not api.VerQueryValueW(buffer, "\\", ctypes.byref(address), ctypes.byref(length))
            or not address.value or length.value < 13 * ctypes.sizeof(ctypes.c_uint32)
        ):
            return None
        fixed = ctypes.cast(address, ctypes.POINTER(ctypes.c_uint32 * 13)).contents
        if fixed[0] != 0xFEEF04BD:
            return None
        major_minor, build_revision = int(fixed[2]), int(fixed[3])
        return (
            major_minor >> 16, major_minor & 0xFFFF,
            build_revision >> 16, build_revision & 0xFFFF,
        )
    except (AttributeError, OSError, ValueError):
        return None


def require_creation_binary(binary: str) -> str:
    import shutil

    selected = shutil.which(binary)
    version = file_version(selected) if selected else None
    if selected is None or version is None or version < MINIMUM_VERSION:
        raise RuntimeError(
            "New PSMux sessions require version 3.4.0 or newer with readable file metadata; "
            "existing sessions were not changed"
        )
    return selected
