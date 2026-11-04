"""Windows physical-object retirement, without ownership or admission policy."""
from __future__ import annotations

import errno
import os
import stat
from contextlib import ExitStack
from pathlib import Path


class PinnedPath:
    """Pin one ordinary object and its directory ancestry until handle disposal.

    ``protect`` adds directory scopes to the rename/reparse exclusion. ``fd``
    and ``state()`` describe the opened target, not a second pathname lookup.
    Callers still supply ownership, selection, lifetime and recovery authority.
    POSIX has no qualified implementation and never falls back to path unlink.
    """

    def __init__(self, path: Path, *, directory: bool, protect: tuple[Path, ...] = ()):
        if os.name != "nt":
            raise OSError(errno.ENOTSUP, "native path retirement is Windows-only")
        import ctypes
        from ctypes import wintypes

        self.path = Path(path)
        if (not self.path.is_absolute() or ".." in self.path.parts
                or any(value in str(self.path) for value in ("\r", "\n", "\0"))):
            raise ValueError("Pinned retirement requires an explicit absolute path")
        if type(directory) is not bool:
            raise ValueError("Pinned retirement requires an explicit object type")
        self.directory = directory
        self.directories = set(self.path.parents)
        for value in protect:
            value = Path(value)
            if (not value.is_absolute() or ".." in value.parts
                    or any(char in str(value) for char in ("\r", "\n", "\0"))):
                raise ValueError("Pinned protection requires absolute directory scopes")
            self.directories.update((value, *value.parents))
        if self.path in self.directories:
            raise ValueError("The retirement target cannot also be a protected directory")
        self.ctypes = ctypes
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.stack = ExitStack()
        self.fd: int | None = None
        self.handle = None
        self.disposed = False
        self.kernel.CreateFileW.argtypes = (
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
            wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
        )
        self.kernel.CreateFileW.restype = wintypes.HANDLE
        self.kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        self.kernel.CloseHandle.restype = wintypes.BOOL
        self.kernel.GetFileInformationByHandle.argtypes = (wintypes.HANDLE, wintypes.LPVOID)
        self.kernel.GetFileInformationByHandle.restype = wintypes.BOOL
        self.kernel.SetFileInformationByHandle.argtypes = (
            wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
        )
        self.kernel.SetFileInformationByHandle.restype = wintypes.BOOL

        class Information(ctypes.Structure):
            _fields_ = [
                ("attributes", wintypes.DWORD), ("creation", wintypes.FILETIME),
                ("access", wintypes.FILETIME), ("write", wintypes.FILETIME),
                ("volume", wintypes.DWORD), ("size_high", wintypes.DWORD),
                ("size_low", wintypes.DWORD), ("links", wintypes.DWORD),
                ("index_high", wintypes.DWORD), ("index_low", wintypes.DWORD),
            ]
        self.information = Information

    @staticmethod
    def _native_name(path: Path) -> str:
        name = str(path)
        if name.startswith("\\\\?\\"):
            return name
        if name.startswith("\\\\"):
            return "\\\\?\\UNC\\" + name[2:]
        return "\\\\?\\" + name

    def _open(self, path: Path, *, target: bool):
        directory = self.directory if target else True
        # Ancestors permit child writes, but never their own rename/disposition.
        access = (0x10000 | 0x20000 | (0x80 if directory else 0x80000000)) if target else 0x80
        share = 3 if directory else 1
        flags = 0x00200000 | (0x02000000 if directory else 0)
        handle = self.kernel.CreateFileW(self._native_name(path), access, share, None, 3, flags, None)
        if handle == self.ctypes.c_void_p(-1).value:
            raise self.ctypes.WinError(self.ctypes.get_last_error())
        return handle

    def _close(self, handle):
        if not self.kernel.CloseHandle(handle):
            raise self.ctypes.WinError(self.ctypes.get_last_error())

    def _info(self, handle):
        value = self.information()
        if not self.kernel.GetFileInformationByHandle(handle, self.ctypes.byref(value)):
            raise self.ctypes.WinError(self.ctypes.get_last_error())
        return value

    def __enter__(self):
        import msvcrt

        try:
            for path in sorted(self.directories, key=lambda value: (len(value.parts), str(value))):
                handle = self._open(path, target=False)
                self.stack.callback(self._close, handle)
                information = self._info(handle)
                if information.attributes & 0x400 or not information.attributes & 0x10:
                    raise ValueError("Pinned ancestor is not an ordinary directory")
            self.handle = self._open(self.path, target=True)
            try:
                self.fd = msvcrt.open_osfhandle(self.handle, os.O_RDONLY | os.O_BINARY)
            except BaseException:
                self._close(self.handle)
                raise
            self.stack.callback(os.close, self.fd)
            self.state()
            return self
        except BaseException:
            self.stack.close()
            raise

    def __exit__(self, *error):
        try:
            return self.stack.__exit__(*error)
        finally:
            self.fd = None
            self.handle = None

    def state(self) -> dict:
        """Return verified physical state of the opened, non-aliased target."""
        if self.fd is None or self.disposed:
            raise ValueError("Pinned retirement target is not open")
        information = self._info(self.handle)
        if (information.attributes & 0x400
                or bool(information.attributes & 0x10) != self.directory
                or information.links != 1):
            raise ValueError("Pinned target is redirected, aliased or has a different type")
        opened = os.fstat(self.fd)
        named = Path(self._native_name(self.path)).lstat()
        correct_type = stat.S_ISDIR(opened.st_mode) if self.directory else stat.S_ISREG(opened.st_mode)
        if (not correct_type or not os.path.samestat(named, opened)
                or stat.S_ISLNK(named.st_mode)
                or getattr(named, "st_file_attributes", 0) & 0x400):
            raise ValueError("Pinned target physical identity changed")
        # Windows pathname stat derives executable bits from the suffix;
        # descriptor stat has no filename. Normalize that view only after
        # proving both observations describe this same pinned native object.
        return {
            "kind": "directory" if self.directory else "file",
            "device": opened.st_dev, "inode": opened.st_ino, "mode": named.st_mode,
            "links": information.links,
        }

    def dispose(self) -> None:
        """Mark only the original handle for deletion; never re-resolve a name."""
        self.state()
        # FILE_DISPOSITION_INFO contains a BOOLEAN, not a four-byte BOOL.
        disposition = self.ctypes.c_ubyte(1)
        if not self.kernel.SetFileInformationByHandle(
            self.handle, 4, self.ctypes.byref(disposition), self.ctypes.sizeof(disposition),
        ):
            raise self.ctypes.WinError(self.ctypes.get_last_error())
        self.disposed = True
