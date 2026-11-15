"""Pinned private scratch roots; paths and breadcrumbs are not cleanup authority."""

from __future__ import annotations

from contextlib import contextmanager, ExitStack
from dataclasses import dataclass
import os
from pathlib import Path
import stat
from typing import Iterator

from agent_procutil import scratch
from agent_procutil._daemon_instance_owner import mkdir_private


class RootAdmissionError(ValueError):
    """The selected root cannot safely serve this allocation."""


def directory_identity(info: os.stat_result) -> dict[str, int]:
    if not info.st_ino:
        raise RootAdmissionError("filesystem directory identity is unavailable")
    return {"device": info.st_dev, "inode": info.st_ino}


def _pin_private(parent: int | None, path: Path, info: os.stat_result) -> int:
    if os.name != "nt":
        return scratch._pin(parent, str(path), info)
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    # The cleanup pin lacks READ_CONTROL, needed for handle-bound DACL reads.
    handle = kernel.CreateFileW(str(path), 0x20081, 3, None, 3, 0x02200000, None)
    if handle in (None, ctypes.c_void_p(-1).value):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY)
    except BaseException:
        kernel.CloseHandle(handle)
        raise
    held = os.fstat(fd)
    if scratch._is_link(held) or directory_identity(held) != directory_identity(info):
        os.close(fd)
        raise RootAdmissionError("scratch directory changed during admission")
    return fd


def _windows_private(fd: int) -> None:
    import ctypes
    import msvcrt
    from ctypes import wintypes
    from agent_procutil._daemon_instance_owner import _apis, _current_sids, _sid_text

    advapi, kernel = _apis()
    advapi.GetSecurityInfo.argtypes = [
        wintypes.HANDLE, ctypes.c_int, wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi.GetSecurityInfo.restype = wintypes.DWORD
    advapi.GetSecurityDescriptorControl.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(wintypes.WORD), ctypes.POINTER(wintypes.DWORD),
    ]
    advapi.GetSecurityDescriptorControl.restype = wintypes.BOOL
    advapi.GetAce.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)]
    advapi.GetAce.restype = wintypes.BOOL
    owner, dacl, descriptor = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
    error = advapi.GetSecurityInfo(
        msvcrt.get_osfhandle(fd), 1, 5, ctypes.byref(owner), None,
        ctypes.byref(dacl), None, ctypes.byref(descriptor),
    )
    if error:
        raise ctypes.WinError(error)
    try:
        user = _current_sids(os.getpid())[0]
        if not owner.value or _sid_text(owner) != user or not dacl.value:
            raise RootAdmissionError("scratch root requires a private owner and DACL")
        control, revision = wintypes.WORD(), wintypes.DWORD()
        if not advapi.GetSecurityDescriptorControl(descriptor, ctypes.byref(control), ctypes.byref(revision)):
            raise ctypes.WinError(ctypes.get_last_error())
        if not control.value & 0x1000:  # SE_DACL_PROTECTED
            raise RootAdmissionError("scratch root DACL must not inherit parent changes")
        count = ctypes.c_ushort.from_address(dacl.value + 4).value
        allowed = {user, "S-1-5-18", "S-1-5-32-544"}
        for index in range(count):
            ace = ctypes.c_void_p()
            if not advapi.GetAce(dacl, index, ctypes.byref(ace)):
                raise ctypes.WinError(ctypes.get_last_error())
            kind = ctypes.c_ubyte.from_address(ace.value).value
            size = ctypes.c_ushort.from_address(ace.value + 2).value
            # Only ordinary allow/deny ACEs are understood. Unknown/object/
            # callback ACL entries cannot be assumed private.
            if kind not in (0, 1) or size < 12:
                raise RootAdmissionError("scratch root has an unsupported access entry")
            if kind == 0 and _sid_text(ctypes.c_void_p(ace.value + 8)) not in allowed:
                raise RootAdmissionError("scratch root grants access to another principal")
    finally:
        kernel.LocalFree(descriptor)


def _private(fd: int) -> None:
    info = os.fstat(fd)
    if scratch._is_link(info) or not stat.S_ISDIR(info.st_mode):
        raise RootAdmissionError("scratch root must be a real directory")
    if os.name == "nt":
        _windows_private(fd)
    elif info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise RootAdmissionError("scratch root must be owned by this user with private permissions")


def _selected(path: Path, forbidden: tuple[Path, ...]) -> Path:
    if not path.is_absolute() or ".." in path.parts or "\x00" in str(path):
        raise RootAdmissionError("scratch root must be an absolute normalized native path")
    path = Path(os.path.abspath(path))
    if path == Path(path.anchor):
        raise RootAdmissionError("a filesystem root is not a scratch compartment")
    if os.name == "nt" and (
        str(path).startswith("\\\\") or any(
            ":" in part or part.endswith((" ", ".")) for part in path.parts[1:]
        )
    ):
        raise RootAdmissionError("scratch roots require a local directory without stream names")
    for boundary in forbidden:
        if not boundary.is_absolute():
            raise RootAdmissionError("excluded storage homes must be absolute")
        # Reject both containment directions: cleanup must not enclose a
        # checkout/history home either.
        for root, excluded in ((path, boundary.absolute()), (path.resolve(), boundary.resolve())):
            if root.is_relative_to(excluded) or excluded.is_relative_to(root):
                raise RootAdmissionError("scratch root overlaps a protected storage home")
    return path


@dataclass(frozen=True)
class AdmittedRoot:
    path: Path
    fd: int
    identity: dict[str, int]

    def verify(self) -> None:
        """Recheck the path and effective privacy at each physical boundary."""
        info = self.path.lstat()
        if scratch._is_link(info) or directory_identity(info) != self.identity:
            raise RootAdmissionError("scratch root was replaced")
        _private(self.fd)

    @contextmanager
    def child(self, name: str) -> Iterator[tuple[int, Path]]:
        if not name or Path(name).name != name or name in (".", "..") or "\\" in name or ":" in name:
            raise RootAdmissionError("allocation name must be one plain path component")
        self.verify()
        path = self.path / name
        info = path.lstat() if os.name == "nt" else os.stat(name, dir_fd=self.fd, follow_symlinks=False)
        if scratch._is_link(info):
            raise RootAdmissionError("scratch allocation must not be a link")
        fd = _pin_private(self.fd, path, info)
        try:
            _private(fd)
            self.verify()
            yield fd, path
        finally:
            os.close(fd)

    def create_child(self, name: str) -> None:
        if not name or Path(name).name != name or name in (".", "..") or "\\" in name or ":" in name:
            raise RootAdmissionError("allocation name must be one plain path component")
        self.verify()
        if os.name == "nt":
            mkdir_private(self.path / name)
        else:
            os.mkdir(name, 0o700, dir_fd=self.fd)
            os.fsync(self.fd)
        self.verify()


@contextmanager
def admit_root(
    path: Path, *, forbidden: tuple[Path, ...], create: bool = False,
    expected: dict[str, int] | None = None,
) -> Iterator[AdmittedRoot]:
    """Admit one explicit root; never repair or adopt foreign/legacy contents.

    Only the final component may be created. Ancestors are opened without
    following links, and Windows handles deny rename/delete until exit.
    """
    path = _selected(path, forbidden)
    if expected is not None and (
        not isinstance(expected, dict) or set(expected) != {"device", "inode"}
        or any(type(value) is not int or value < 0 for value in expected.values())
        or not expected["inode"]
    ):
        raise RootAdmissionError("invalid expected scratch root identity")
    with ExitStack() as stack:
        parent_fd = None
        ancestors = list(reversed(path.parents)) + [path]
        for current in ancestors:
            try:
                info = current.lstat()
            except FileNotFoundError:
                if current != path or not create:
                    raise
                if os.name == "nt":
                    mkdir_private(current)
                else:
                    os.mkdir(current.name, 0o700, dir_fd=parent_fd)
                info = current.lstat()
            if scratch._is_link(info) or not stat.S_ISDIR(info.st_mode):
                raise RootAdmissionError("scratch root traversal cannot contain links")
            if os.name != "nt" and current != path and (
                info.st_uid not in (0, os.getuid())
                or (info.st_mode & 0o022 and not info.st_mode & stat.S_ISVTX)
            ):
                raise RootAdmissionError("scratch root has a replaceable ancestor")
            fd = (
                _pin_private(parent_fd, current, info) if current == path
                else scratch._pin(parent_fd, str(current), info)
            )
            stack.callback(os.close, fd)
            parent_fd = fd
        identity = directory_identity(os.fstat(parent_fd))
        if expected is not None and identity != expected:
            raise RootAdmissionError("scratch root identity does not match its reservation")
        result = AdmittedRoot(path, parent_fd, identity)
        result.verify()
        yield result
