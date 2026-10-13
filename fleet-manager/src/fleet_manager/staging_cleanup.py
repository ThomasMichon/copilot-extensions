"""Identity-bound cleanup for a caller-owned ephemeral artifact stage."""

from contextlib import ExitStack, contextmanager
import os
from pathlib import Path
import stat

from .planning import PlanError


def _identity(info):
    return info.st_dev, info.st_ino


def _ordinary(info):
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise PlanError("artifact cleanup refuses linked or reparse entries")
    if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
        raise PlanError("artifact cleanup refuses nonordinary entries")


@contextmanager
def _windows_handle(path, *, delete=False):
    import ctypes
    from ctypes import wintypes
    import msvcrt

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = (
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    )
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.GetFileInformationByHandleEx.argtypes = (
        wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
    )
    # No FILE_SHARE_DELETE: the opened identity cannot be renamed or replaced.
    handle = kernel.CreateFileW(
        str(path), 0x80000000 | (0x10000 if delete else 0), 3, None, 3,
        0x00200000 | 0x02000000, None,
    )
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    descriptor = None
    try:
        attributes = (wintypes.DWORD * 2)()
        if not kernel.GetFileInformationByHandleEx(
            handle, 9, attributes, ctypes.sizeof(attributes),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        if attributes[0] & 0x400:
            raise PlanError("artifact cleanup refuses linked or reparse entries")
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY)
        info = os.fstat(descriptor)
        _ordinary(info)
        yield kernel, handle, info
    finally:
        if descriptor is None:
            kernel.CloseHandle(handle)
        else:
            os.close(descriptor)


def _windows_delete(kernel, handle):
    import ctypes
    from ctypes import wintypes

    class Disposition(ctypes.Structure):
        _fields_ = [("delete", wintypes.BOOLEAN)]

    kernel.SetFileInformationByHandle.argtypes = (
        wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
    )
    kernel.SetFileInformationByHandle.restype = wintypes.BOOL
    disposition = Disposition(True)
    if not kernel.SetFileInformationByHandle(
        handle, 4, ctypes.byref(disposition), ctypes.sizeof(disposition),
    ):
        raise ctypes.WinError(ctypes.get_last_error())


def _windows_identity(kernel, handle):
    import ctypes
    from ctypes import wintypes

    class FileId(ctypes.Structure):
        _fields_ = [("volume", ctypes.c_ulonglong), ("identifier", ctypes.c_ubyte * 16)]

    kernel.GetFileInformationByHandleEx.argtypes = (
        wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
    )
    kernel.GetFileInformationByHandleEx.restype = wintypes.BOOL
    info = FileId()
    if not kernel.GetFileInformationByHandleEx(handle, 18, ctypes.byref(info), ctypes.sizeof(info)):
        raise ctypes.WinError(ctypes.get_last_error())
    return info.volume, int.from_bytes(bytes(info.identifier), "little")


def _windows_remove(path, expected):
    with _windows_handle(path, delete=True) as (kernel, handle, opened):
        if _windows_identity(kernel, handle) != expected:
            raise PlanError("artifact stage cleanup ownership is unconfirmed")
        if stat.S_ISDIR(opened.st_mode):
            with os.scandir(path) as entries:
                children = [(entry.name, os.lstat(path / entry.name)) for entry in entries]
            for name, info in children:
                _ordinary(info)
                _windows_remove(path / name, _identity(info))
        _windows_delete(kernel, handle)


def _posix_contents(descriptor):
    with os.scandir(descriptor) as entries:
        children = [(entry.name, entry.stat(follow_symlinks=False)) for entry in entries]
    for name, info in children:
        _ordinary(info)
        if stat.S_ISDIR(info.st_mode):
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=descriptor)
            try:
                if _identity(os.fstat(child)) != _identity(info):
                    raise PlanError("artifact cleanup directory identity changed")
                _posix_contents(child)
                if _identity(os.stat(name, dir_fd=descriptor, follow_symlinks=False)) != _identity(info):
                    raise PlanError("artifact cleanup directory identity changed")
                os.rmdir(name, dir_fd=descriptor)
            finally:
                os.close(child)
        else:
            if _identity(os.stat(name, dir_fd=descriptor, follow_symlinks=False)) != _identity(info):
                raise PlanError("artifact cleanup file identity changed")
            os.unlink(name, dir_fd=descriptor)


def remove_owned_stage(root: Path, expected: tuple[int, int]) -> None:
    """Anchor traversal to the admitted identity; never recursively follow its replacement."""
    try:
        if os.name == "nt":
            with ExitStack() as ancestors:
                for path in reversed(root.parents):
                    _, _, info = ancestors.enter_context(_windows_handle(path))
                    if not stat.S_ISDIR(info.st_mode):
                        raise PlanError("artifact cleanup ancestor is not a directory")
                _windows_remove(root, expected)
        else:
            required = (os.open, os.stat, os.unlink, os.rmdir)
            if not hasattr(os, "O_NOFOLLOW") or any(item not in os.supports_dir_fd for item in required):
                raise PlanError("identity-bound artifact cleanup is unavailable")
            parent = os.open(root.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                descriptor = os.open(root.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                     dir_fd=parent)
                try:
                    if _identity(os.fstat(descriptor)) != expected:
                        raise PlanError("artifact stage cleanup ownership is unconfirmed")
                    _posix_contents(descriptor)
                    if _identity(os.stat(root.name, dir_fd=parent, follow_symlinks=False)) != expected:
                        raise PlanError("artifact stage cleanup ownership is unconfirmed")
                    # Only remove an empty top-level name; recursive traversal used the fd.
                    os.rmdir(root.name, dir_fd=parent)
                finally:
                    os.close(descriptor)
            finally:
                os.close(parent)
    except OSError:
        raise PlanError("artifact stage cleanup could not safely complete") from None
