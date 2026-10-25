"""Private diagnostic filesystem access, not installation or lease authority."""

from __future__ import annotations

import os
from pathlib import Path
import stat


def ordinary(path):
    path = Path(path)
    for item in (path, *path.parents):
        if not os.path.lexists(item):
            continue
        value = item.lstat()
        if stat.S_ISLNK(value.st_mode) or getattr(value, "st_file_attributes", 0) & 0x400:
            raise ValueError("diagnostic paths must not contain links or reparse points")


def _native(path):
    value = os.path.abspath(path)
    if value.startswith("\\\\?\\"):
        return value
    return "\\\\?\\UNC\\" + value[2:] if value.startswith("\\\\") else "\\\\?\\" + value


class WindowsPrivacy:
    def __init__(self):
        import ctypes as c
        from ctypes import wintypes as w

        self.c, self.w = c, w
        self.kernel = c.WinDLL("kernel32", use_last_error=True)
        self.security = c.WinDLL("advapi32", use_last_error=True)

        class Attributes(c.Structure):
            _fields_ = [("length", w.DWORD), ("descriptor", w.LPVOID), ("inherit", w.BOOL)]

        class AclInfo(c.Structure):
            _fields_ = [("count", w.DWORD), ("used", w.DWORD), ("free", w.DWORD)]

        self.Attributes, self.AclInfo = Attributes, AclInfo
        signatures = (
            (self.kernel, "GetCurrentProcess", [], w.HANDLE),
            (self.kernel, "CloseHandle", [w.HANDLE], w.BOOL),
            (self.kernel, "LocalFree", [w.LPVOID], w.LPVOID),
            (self.kernel, "CreateDirectoryW", [w.LPCWSTR, c.POINTER(Attributes)], w.BOOL),
            (self.kernel, "CreateFileW",
             [w.LPCWSTR, w.DWORD, w.DWORD, c.POINTER(Attributes), w.DWORD, w.DWORD, w.HANDLE], w.HANDLE),
            (self.security, "OpenProcessToken", [w.HANDLE, w.DWORD, c.POINTER(w.HANDLE)], w.BOOL),
            (self.security, "GetTokenInformation",
             [w.HANDLE, c.c_int, w.LPVOID, w.DWORD, c.POINTER(w.DWORD)], w.BOOL),
            (self.security, "ConvertSidToStringSidW", [w.LPVOID, c.POINTER(w.LPWSTR)], w.BOOL),
            (self.security, "ConvertStringSecurityDescriptorToSecurityDescriptorW",
             [w.LPCWSTR, w.DWORD, c.POINTER(w.LPVOID), w.LPVOID], w.BOOL),
            (self.security, "GetSecurityInfo",
             [w.HANDLE, c.c_int, w.DWORD, c.POINTER(w.LPVOID), w.LPVOID,
              c.POINTER(w.LPVOID), w.LPVOID, c.POINTER(w.LPVOID)], w.DWORD),
            (self.security, "GetSecurityDescriptorControl",
             [w.LPVOID, c.POINTER(w.WORD), c.POINTER(w.DWORD)], w.BOOL),
            (self.security, "GetAclInformation", [w.LPVOID, w.LPVOID, w.DWORD, c.c_int], w.BOOL),
            (self.security, "GetAce", [w.LPVOID, w.DWORD, c.POINTER(w.LPVOID)], w.BOOL),
        )
        for dll, name, arguments, result in signatures:
            method = getattr(dll, name)
            method.argtypes, method.restype = arguments, result
        token = w.HANDLE()
        if not self.security.OpenProcessToken(self.kernel.GetCurrentProcess(), 8, c.byref(token)):
            raise c.WinError(c.get_last_error())
        try:
            size = w.DWORD()
            self.security.GetTokenInformation(token, 1, None, 0, c.byref(size))
            if c.get_last_error() != 122 or not 0 < size.value <= 65536:
                raise OSError("diagnostic TokenUser could not be read")
            buffer = c.create_string_buffer(size.value)
            if not self.security.GetTokenInformation(token, 1, buffer, size, c.byref(size)):
                raise c.WinError(c.get_last_error())
            self.account = self.sid(c.cast(buffer, c.POINTER(w.LPVOID)).contents.value)
        finally:
            self.kernel.CloseHandle(token)

    def sid(self, pointer):
        c, w = self.c, self.w
        text = w.LPWSTR()
        if not self.security.ConvertSidToStringSidW(pointer, c.byref(text)):
            raise c.WinError(c.get_last_error())
        try:
            return text.value
        finally:
            self.kernel.LocalFree(c.cast(text, w.LPVOID))

    def descriptor(self):
        c, w = self.c, self.w
        descriptor = w.LPVOID()
        sddl = f"O:{self.account}D:P(A;OICI;FA;;;{self.account})"
        if not self.security.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl, 1, c.byref(descriptor), None,
        ):
            raise c.WinError(c.get_last_error())
        return descriptor

    def validate(self, handle, directory):
        c, w = self.c, self.w
        owner, dacl, descriptor = w.LPVOID(), w.LPVOID(), w.LPVOID()
        code = self.security.GetSecurityInfo(
            handle, 1, 5, c.byref(owner), None, c.byref(dacl), None, c.byref(descriptor),
        )
        if code:
            raise c.WinError(code)
        try:
            if not owner or self.sid(owner) != self.account or not dacl:
                raise PermissionError("diagnostics require TokenUser ownership and a private DACL")
            control, revision = w.WORD(), w.DWORD()
            if not self.security.GetSecurityDescriptorControl(descriptor, c.byref(control), c.byref(revision)):
                raise c.WinError(c.get_last_error())
            if directory and not control.value & 0x1000:
                raise PermissionError("diagnostic directory DACL must be protected")
            info = self.AclInfo()
            if not self.security.GetAclInformation(dacl, c.byref(info), c.sizeof(info), 2):
                raise c.WinError(c.get_last_error())
            access = 0
            for index in range(info.count):
                ace = w.LPVOID()
                if not self.security.GetAce(dacl, index, c.byref(ace)):
                    raise c.WinError(c.get_last_error())
                kind, flags = c.string_at(ace, 2)
                mask = c.cast(ace.value + 4, c.POINTER(w.DWORD)).contents.value
                if kind != 0 or flags & 8 or self.sid(ace.value + 8) != self.account:
                    raise PermissionError("diagnostic DACL permits a foreign or unsupported ACE")
                access |= mask
            if access & 0x1F01FF != 0x1F01FF:
                raise PermissionError("diagnostic DACL must grant TokenUser full access")
        finally:
            self.kernel.LocalFree(descriptor)

    def mkdir(self, path):
        c = self.c
        descriptor = self.descriptor()
        try:
            attributes = self.Attributes(c.sizeof(self.Attributes), descriptor, False)
            if not self.kernel.CreateDirectoryW(_native(path), c.byref(attributes)):
                if c.get_last_error() != 183:
                    raise c.WinError(c.get_last_error())
        finally:
            self.kernel.LocalFree(descriptor)
        handle = self.kernel.CreateFileW(_native(path), 0x20000, 7, None, 3, 0x2200000, None)
        if handle == c.c_void_p(-1).value:
            raise c.WinError(c.get_last_error())
        try:
            self.validate(handle, True)
        finally:
            self.kernel.CloseHandle(handle)

    def open(self, path, create):
        import msvcrt

        c = self.c
        descriptor = self.descriptor() if create else None
        try:
            attributes = self.Attributes(c.sizeof(self.Attributes), descriptor, False) if create else None
            handle = self.kernel.CreateFileW(
                _native(path), 0xC0020000 if create else 0x80020000, 7,
                c.byref(attributes) if attributes else None, 1 if create else 3, 0x200080, None,
            )
        finally:
            if descriptor:
                self.kernel.LocalFree(descriptor)
        if handle == c.c_void_p(-1).value:
            raise c.WinError(c.get_last_error())
        try:
            self.validate(handle, False)
            fd = msvcrt.open_osfhandle(handle, (os.O_RDWR if create else os.O_RDONLY) | os.O_BINARY)
        except BaseException:
            self.kernel.CloseHandle(handle)
            raise
        return fd


_windows = None


def windows():
    global _windows
    if _windows is None:
        _windows = WindowsPrivacy()
    return _windows


def private_directory(path, create=False):
    path = Path(path)
    ordinary(path)
    if os.name == "nt":
        if not create and not path.is_dir():
            raise PermissionError("diagnostic directory is unavailable")
        windows().mkdir(path)
    elif create:
        path.mkdir(mode=0o700, exist_ok=True)
    value = path.lstat()
    if not stat.S_ISDIR(value.st_mode):
        raise PermissionError("diagnostics require an ordinary directory")
    if os.name != "nt" and (value.st_uid != os.getuid() or value.st_mode & 0o077):
        raise PermissionError("diagnostics require a user-owned 0700 directory")
    ordinary(path)
    return path


def open_file(path, create=False):
    path = Path(path)
    ordinary(path)
    before = path.lstat() if not create else None
    if os.name == "nt":
        fd = windows().open(path, create)
    else:
        flags = os.O_RDWR | os.O_CREAT | os.O_EXCL if create else os.O_RDONLY
        fd = os.open(path, flags | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        value = os.fstat(fd)
        ordinary(path)
        named = path.lstat()
        if (not stat.S_ISREG(value.st_mode) or value.st_nlink != 1
                or (value.st_dev, value.st_ino) != (named.st_dev, named.st_ino)
                or (before is not None and (before.st_dev, before.st_ino) != (value.st_dev, value.st_ino))):
            raise PermissionError("diagnostic file identity is not ordinary and exclusive")
        if os.name != "nt" and (value.st_uid != os.getuid() or value.st_mode & 0o077):
            raise PermissionError("diagnostic files require user-only access")
        return fd
    except BaseException:
        os.close(fd)
        raise
