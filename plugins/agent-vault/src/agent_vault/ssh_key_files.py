"""Owner-private, create-only local OpenSSH files; no Vault or network effects."""

from __future__ import annotations

import ctypes
from contextlib import contextmanager
import os
import stat
import time
import uuid
from pathlib import Path


class WindowsSecurity:
    """Create native objects with their final owner/DACL, before any secret write."""

    def __init__(self):
        from ctypes import wintypes as w

        class Attributes(ctypes.Structure):
            _fields_ = [("length", w.DWORD), ("descriptor", w.LPVOID), ("inherit", w.BOOL)]

        self.attributes_type = Attributes
        self.security = ctypes.WinDLL("advapi32", use_last_error=True)
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "OpenProcessToken": ([w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE)], w.BOOL),
            "GetTokenInformation": (
                [w.HANDLE, ctypes.c_int, w.LPVOID, w.DWORD, ctypes.POINTER(w.DWORD)], w.BOOL,
            ),
            "ConvertSidToStringSidW": ([w.LPVOID, ctypes.POINTER(w.LPWSTR)], w.BOOL),
            "ConvertStringSecurityDescriptorToSecurityDescriptorW": (
                [w.LPCWSTR, w.DWORD, ctypes.POINTER(w.LPVOID), w.LPVOID], w.BOOL,
            ),
            "GetFileSecurityW": (
                [w.LPCWSTR, w.DWORD, w.LPVOID, w.DWORD, ctypes.POINTER(w.DWORD)], w.BOOL,
            ),
            "ConvertSecurityDescriptorToStringSecurityDescriptorW": (
                [w.LPVOID, w.DWORD, w.DWORD, ctypes.POINTER(w.LPWSTR), w.LPVOID], w.BOOL,
            ),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self.security, name)
            function.argtypes, function.restype = arguments, result
        for name, arguments, result in (
            ("GetCurrentProcess", [], w.HANDLE),
            ("CloseHandle", [w.HANDLE], w.BOOL),
            ("LocalFree", [w.LPVOID], w.LPVOID),
            ("CreateDirectoryW", [w.LPCWSTR, ctypes.POINTER(Attributes)], w.BOOL),
            ("CreateFileW", [
                w.LPCWSTR, w.DWORD, w.DWORD, ctypes.POINTER(Attributes),
                w.DWORD, w.DWORD, w.HANDLE,
            ], w.HANDLE),
            ("MoveFileExW", [w.LPCWSTR, w.LPCWSTR, w.DWORD], w.BOOL),
        ):
            function = getattr(self.kernel, name)
            function.argtypes, function.restype = arguments, result
        token = w.HANDLE()
        self.checked(self.security.OpenProcessToken(
            self.kernel.GetCurrentProcess(), 8, ctypes.byref(token),
        ))
        try:
            size = w.DWORD()
            self.security.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
            if ctypes.get_last_error() != 122 or not size.value:
                raise OSError("Cannot determine the SSH key account")
            user = ctypes.create_string_buffer(size.value)
            self.checked(self.security.GetTokenInformation(
                token, 1, user, size, ctypes.byref(size),
            ))
            sid = ctypes.cast(user, ctypes.POINTER(w.LPVOID))[0]
            text = w.LPWSTR()
            self.checked(self.security.ConvertSidToStringSidW(sid, ctypes.byref(text)))
            try:
                self.sid = text.value
            finally:
                self.kernel.LocalFree(text)
        finally:
            self.kernel.CloseHandle(token)

    @staticmethod
    def checked(ok):
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error())

    def descriptor(self, directory: bool) -> str:
        flags = "OICI" if directory else ""
        return f"O:{self.sid}D:P(A;{flags};FA;;;{self.sid})(A;{flags};FA;;;SY)"

    def create(self, path: Path, *, directory: bool):
        from ctypes import wintypes as w

        descriptor = w.LPVOID()
        self.checked(self.security.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            self.descriptor(directory), 1, ctypes.byref(descriptor), None,
        ))
        try:
            attributes = self.attributes_type(
                ctypes.sizeof(self.attributes_type), descriptor, False,
            )
            if directory:
                if not self.kernel.CreateDirectoryW(str(path), ctypes.byref(attributes)):
                    error = ctypes.get_last_error()
                    if error != 183:
                        raise ctypes.WinError(error)
                return None
            handle = self.kernel.CreateFileW(
                str(path), 0x40000000, 1, ctypes.byref(attributes), 1, 0x80, None,
            )
            if handle == ctypes.c_void_p(-1).value:
                raise ctypes.WinError(ctypes.get_last_error())
            import msvcrt

            try:
                return msvcrt.open_osfhandle(handle, os.O_WRONLY | os.O_BINARY)
            except OSError:
                self.kernel.CloseHandle(handle)
                raise
        finally:
            self.kernel.LocalFree(descriptor)

    def require(self, path: Path, *, directory: bool):
        from ctypes import wintypes as w

        size = w.DWORD()
        self.security.GetFileSecurityW(str(path), 5, None, 0, ctypes.byref(size))
        if ctypes.get_last_error() != 122 or not size.value:
            raise OSError("SSH key security descriptor unavailable")
        descriptor = ctypes.create_string_buffer(size.value)
        self.checked(self.security.GetFileSecurityW(
            str(path), 5, descriptor, size, ctypes.byref(size),
        ))
        text = w.LPWSTR()
        self.checked(self.security.ConvertSecurityDescriptorToStringSecurityDescriptorW(
            descriptor, 1, 5, ctypes.byref(text), None,
        ))
        try:
            if text.value != self.descriptor(directory):
                raise PermissionError("SSH key location is not owner-private")
        finally:
            self.kernel.LocalFree(text)

    def publish(self, source: Path, target: Path):
        # WRITE_THROUGH, deliberately without REPLACE_EXISTING.
        if not self.kernel.MoveFileExW(str(source), str(target), 8):
            error = ctypes.get_last_error()
            if error in (80, 183):
                raise FileExistsError("SSH key destination already exists")
            raise ctypes.WinError(error)


def _ordinary(path: Path, *, directory: bool):
    info = path.lstat()
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if (
        not expected(info.st_mode)
        or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        or (not directory and info.st_nlink != 1)
    ):
        raise PermissionError("SSH key location must not be a link or special file")
    return info


def _require(path: Path, windows, *, directory: bool):
    info = _ordinary(path, directory=directory)
    if windows:
        windows.require(path, directory=directory)
    elif info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != (0o700 if directory else 0o600):
        raise PermissionError("SSH key location is not owner-private")
    return info


def _read(path: Path, windows) -> bytes:
    before = _require(path, windows, directory=False)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
    with os.fdopen(descriptor, "rb") as handle:
        opened = os.fstat(handle.fileno())
        if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise PermissionError("SSH key location changed while opening")
        value = handle.read(16385)
    after = _require(path, windows, directory=False)
    fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
    if any(getattr(before, field) != getattr(after, field) for field in fields) or len(value) > 16384:
        raise PermissionError("SSH key file changed or exceeds its size limit")
    return value


def _sync(directory: Path):
    if os.name != "nt":
        descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _write(directory: Path, target: Path, value: bytes, windows):
    temporary = directory / (".ssh-key-" + uuid.uuid4().hex + ".tmp")
    descriptor = (
        windows.create(temporary, directory=False) if windows
        else os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            _require(temporary, windows, directory=False)
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        _require(directory, windows, directory=True)
        try:
            if windows:
                windows.publish(temporary, target)
            else:
                os.link(temporary, target, follow_symlinks=False)
                temporary.unlink()
        except FileExistsError:
            if _read(target, windows) != value:
                raise FileExistsError("SSH key destination contains a different key") from None
        _sync(directory)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def _publication_lock(directory: Path):
    if os.name == "nt":
        yield
        return
    import fcntl

    descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        deadline = time.monotonic() + 5.0
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("SSH key directory is busy") from None
                time.sleep(0.01)
        yield
    finally:
        os.close(descriptor)


def install(directory: Path, private: bytes, public: bytes) -> dict:
    """Publish canonical files exclusively; matching partial pairs may be completed."""
    directory = Path(os.path.abspath(directory.expanduser()))
    windows = WindowsSecurity() if os.name == "nt" else None
    for parent in reversed(directory.parents):
        _ordinary(parent, directory=True)
        if os.name != "nt":
            info = parent.lstat()
            if info.st_mode & 0o022 and not info.st_mode & stat.S_ISVTX:
                raise PermissionError("SSH key ancestor permits untrusted replacement")
    if windows:
        windows.create(directory, directory=True)
    else:
        directory.mkdir(mode=0o700, exist_ok=True)
    _require(directory, windows, directory=True)
    with _publication_lock(directory):
        return _install_pair(directory, private, public, windows)


def _install_pair(directory: Path, private: bytes, public: bytes, windows) -> dict:
    files = {
        directory / "id_ed25519": private,
        directory / "id_ed25519.pub": public,
    }
    present = {}
    for path, value in files.items():
        present[path] = os.path.lexists(path)
        if present[path] and _read(path, windows) != value:
            raise FileExistsError("SSH key destination contains a different key")
    for path, value in files.items():
        _require(directory, windows, directory=True)
        if not present[path]:
            _write(directory, path, value, windows)
    _sync(directory)
    _sync(directory.parent)
    for path, value in files.items():
        if _read(path, windows) != value:
            raise OSError("SSH key publication could not be confirmed")
    return {
        "status": "reused" if all(present.values()) else "recovered" if any(present.values()) else "installed",
        "private_key_path": str(directory / "id_ed25519"),
        "public_key_path": str(directory / "id_ed25519.pub"),
    }
