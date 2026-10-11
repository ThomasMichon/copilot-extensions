"""No-follow, pinned-directory I/O for the session metadata transaction."""
from __future__ import annotations

from contextlib import ExitStack
import errno
import os
from pathlib import Path
import secrets
import stat


def _windows_open(path: Path, *, directory: bool, create: bool = False) -> int:
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
    # No FILE_SHARE_DELETE: every ancestor stays pinned until publication ends.
    handle = kernel.CreateFileW(
        str(path), 0xC0000000 if create else 0x80000000, 3, None,
        4 if create else 3, 0x00200000 | (0x02000000 if directory else 0), None,
    )
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        info = (wintypes.DWORD * 2)()
        if not kernel.GetFileInformationByHandleEx(handle, 9, info, ctypes.sizeof(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        if info[0] & 0x400 or bool(info[0] & 0x10) != directory:
            raise ValueError(f"refusing linked stamp destination or wrong type: {path}")
        fd = msvcrt.open_osfhandle(handle, os.O_RDWR if create else os.O_RDONLY)
    except BaseException:
        kernel.CloseHandle(handle)
        raise
    return fd


def _windows_owned(fd: int) -> bool:
    import ctypes
    from ctypes import wintypes
    import msvcrt

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    security = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.LocalFree.argtypes = (wintypes.LPVOID,)
    security.OpenProcessToken.argtypes = (
        wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE),
    )
    security.GetTokenInformation.argtypes = (
        wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    )
    security.GetSecurityInfo.argtypes = (
        wintypes.HANDLE, ctypes.c_int, wintypes.DWORD,
        ctypes.POINTER(wintypes.LPVOID), wintypes.LPVOID, wintypes.LPVOID,
        wintypes.LPVOID, ctypes.POINTER(wintypes.LPVOID),
    )
    security.EqualSid.argtypes = (wintypes.LPVOID, wintypes.LPVOID)
    token = wintypes.HANDLE()
    if not security.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token)):
        raise ctypes.WinError(ctypes.get_last_error())
    descriptor = wintypes.LPVOID()
    try:
        size = wintypes.DWORD()
        security.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
        if ctypes.get_last_error() != 122:
            raise ctypes.WinError(ctypes.get_last_error())
        user = ctypes.create_string_buffer(size.value)
        if not security.GetTokenInformation(token, 1, user, size, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        owner = wintypes.LPVOID()
        code = security.GetSecurityInfo(
            msvcrt.get_osfhandle(fd), 1, 1, ctypes.byref(owner), None, None, None,
            ctypes.byref(descriptor),
        )
        if code:
            raise ctypes.WinError(code)
        user_sid = ctypes.cast(user, ctypes.POINTER(wintypes.LPVOID))[0]
        return bool(security.EqualSid(owner, user_sid))
    finally:
        if descriptor:
            kernel.LocalFree(descriptor)
        kernel.CloseHandle(token)


class Directory:
    """Hold every ancestor open; POSIX mutations are relative to directory fds."""

    def __init__(self, path: Path) -> None:
        if ".." in path.parts:
            raise ValueError(f"refusing parent traversal in stamp destination: {path}")
        self.path = Path(os.path.abspath(path))
        self.stack = ExitStack()
        self.fd = -1
        self.bindings: list[tuple[int | None, Path, int]] = []

    def __enter__(self) -> Directory:
        try:
            current = Path(self.path.anchor)
            self.fd = self._directory(current, None)
            for component in self.path.parts[1:]:
                current = current / component
                self.fd = self._directory(current, self.fd)
            if not (_windows_owned(self.fd) if os.name == "nt" else
                    os.fstat(self.fd).st_uid == os.geteuid()):
                raise ValueError(f"stamp directory is not owned by this user: {self.path}")
            return self
        except BaseException:
            self.stack.close()
            raise

    def __exit__(self, *args: object) -> None:
        self.stack.close()

    def _directory(self, path: Path, parent: int | None) -> int:
        try:
            fd = (_windows_open(path, directory=True) if os.name == "nt" else
                  os.open(str(path) if parent is None else path.name,
                          os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                          dir_fd=parent))
        except FileNotFoundError:
            if parent is None:
                raise
            try:
                if os.name == "nt":
                    path.mkdir()
                else:
                    os.mkdir(path.name, 0o755, dir_fd=parent)
            except FileExistsError:
                pass
            return self._directory(path, parent)
        except OSError as error:
            if error.errno in (errno.ELOOP, errno.ENOTDIR):
                raise ValueError(f"refusing linked stamp destination: {path}") from error
            raise
        self.stack.callback(os.close, fd)
        if not stat.S_ISDIR(os.fstat(fd).st_mode):
            raise ValueError(f"stamp ancestor is not a directory: {path}")
        self.bindings.append((parent, path, fd))
        return fd

    def verify(self) -> None:
        for parent, path, fd in self.bindings:
            info = os.fstat(fd)
            entry = (os.stat(path, follow_symlinks=False)
                     if os.name == "nt" or parent is None else
                     os.stat(path.name, dir_fd=parent, follow_symlinks=False))
            if (not stat.S_ISDIR(entry.st_mode)
                    or (info.st_dev, info.st_ino) != (entry.st_dev, entry.st_ino)):
                raise ValueError(f"stamp directory identity changed: {path}")

    def open(self, name: str, *, create: bool = False, exclusive: bool = False) -> int:
        self.verify()
        if os.name == "nt":
            if exclusive:
                # Exclusive creation cannot follow an existing link.
                fd = os.open(self.path / name, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
            else:
                fd = _windows_open(self.path / name, directory=False, create=create)
        else:
            flags = (os.O_RDWR if create else os.O_RDONLY) | os.O_NOFOLLOW | os.O_NONBLOCK
            if create:
                flags |= os.O_CREAT
            if exclusive:
                flags |= os.O_EXCL
            try:
                fd = os.open(name, flags, 0o600, dir_fd=self.fd)
            except OSError as error:
                if error.errno == errno.ELOOP:
                    raise ValueError(f"refusing linked stamp destination: {name}") from error
                raise
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError(f"stamp file must be a single-link regular file: {name}")
            if not (_windows_owned(fd) if os.name == "nt" else info.st_uid == os.geteuid()):
                raise ValueError(f"stamp file is not owned by this user: {name}")
            entry = os.stat(self.path / name, follow_symlinks=False) if os.name == "nt" else (
                os.stat(name, dir_fd=self.fd, follow_symlinks=False)
            )
            if (info.st_dev, info.st_ino) != (entry.st_dev, entry.st_ino):
                raise ValueError(f"stamp file identity changed: {name}")
            return fd
        except BaseException:
            os.close(fd)
            raise

    def read(self, name: str) -> bytes | None:
        try:
            fd = self.open(name)
        except FileNotFoundError:
            return None
        with os.fdopen(fd, "rb") as stream:
            return stream.read()

    def publish(self, name: str, data: bytes, *, executable: bool = False) -> None:
        try:
            fd = self.open(name)
        except FileNotFoundError:
            fd = None
        if fd is not None:
            with os.fdopen(fd, "rb") as stream:
                if stream.read() == data:
                    if executable:
                        os.fchmod(stream.fileno(), 0o755) if os.name != "nt" else (
                            os.chmod(self.path / name, 0o755)
                        )
                    return
        temporary = ".session-stamp-" + secrets.token_hex(16)
        fd = self.open(temporary, create=True, exclusive=True)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                if executable and os.name != "nt":
                    os.fchmod(stream.fileno(), 0o755)
            if os.name == "nt":
                self.verify()
                os.replace(self.path / temporary, self.path / name)
            else:
                self.verify()
                os.replace(temporary, name, src_dir_fd=self.fd, dst_dir_fd=self.fd)
        finally:
            try:
                if os.name == "nt":
                    os.unlink(self.path / temporary)
                else:
                    os.unlink(temporary, dir_fd=self.fd)
            except FileNotFoundError:
                pass


def lock(fd: int) -> bool:
    """Only an actual nonblocking lock conflict is a benign no-op."""
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        import msvcrt
        class Overlapped(ctypes.Structure):
            _fields_ = [
                ("Internal", ctypes.c_size_t), ("InternalHigh", ctypes.c_size_t),
                ("Offset", wintypes.DWORD), ("OffsetHigh", wintypes.DWORD),
                ("hEvent", wintypes.HANDLE),
            ]
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.LockFileEx.argtypes = (
            wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
            wintypes.DWORD, ctypes.POINTER(Overlapped),
        )
        overlapped = Overlapped()
        if not kernel.LockFileEx(msvcrt.get_osfhandle(fd), 3, 0, 1, 0,
                                 ctypes.byref(overlapped)):
            code = ctypes.get_last_error()
            if code == 33:
                return False
            raise ctypes.WinError(code)
    else:
        import fcntl
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            if error.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                return False
            raise
    return True
