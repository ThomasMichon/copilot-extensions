"""Kernel ownership for runtime construction, retention and publication."""
from __future__ import annotations

import errno
import math
import os
import re
import stat
import threading
import time
from contextlib import contextmanager, nullcontext
from pathlib import Path
from typing import Callable

_construction_owners = threading.local()


def windows_owned_file(fd: int) -> bool:
    """Admit the current user/enabled owner groups and a non-public write DACL."""
    import ctypes
    from ctypes import wintypes
    import msvcrt

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    security = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.LocalFree.argtypes = (wintypes.LPVOID,)
    kernel.LocalFree.restype = wintypes.LPVOID
    security.OpenProcessToken.argtypes = (wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE))
    security.OpenProcessToken.restype = wintypes.BOOL
    security.GetTokenInformation.argtypes = (wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID,
                                             wintypes.DWORD, ctypes.POINTER(wintypes.DWORD))
    security.GetTokenInformation.restype = wintypes.BOOL
    security.GetSecurityInfo.argtypes = (wintypes.HANDLE, ctypes.c_int, wintypes.DWORD,
        ctypes.POINTER(wintypes.LPVOID), wintypes.LPVOID, ctypes.POINTER(wintypes.LPVOID),
        wintypes.LPVOID, ctypes.POINTER(wintypes.LPVOID))
    security.GetSecurityInfo.restype = wintypes.DWORD
    security.ConvertSidToStringSidW.argtypes = (wintypes.LPVOID, ctypes.POINTER(wintypes.LPWSTR))
    security.ConvertSidToStringSidW.restype = wintypes.BOOL
    security.GetAclInformation.argtypes = (wintypes.LPVOID, wintypes.LPVOID, wintypes.DWORD, ctypes.c_int)
    security.GetAclInformation.restype = wintypes.BOOL
    security.GetAce.argtypes = (wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.LPVOID))
    security.GetAce.restype = wintypes.BOOL

    class SidAttributes(ctypes.Structure):
        _fields_ = [("sid", wintypes.LPVOID), ("attributes", wintypes.DWORD)]

    class Groups(ctypes.Structure):
        _fields_ = [("count", wintypes.DWORD), ("entries", SidAttributes * 1)]

    class AclSize(ctypes.Structure):
        _fields_ = [("count", wintypes.DWORD), ("used", wintypes.DWORD), ("free", wintypes.DWORD)]

    class Ace(ctypes.Structure):
        _fields_ = [("kind", ctypes.c_ubyte), ("flags", ctypes.c_ubyte),
                    ("size", wintypes.WORD), ("mask", wintypes.DWORD), ("sid", wintypes.DWORD)]

    def sid_string(sid: int) -> str:
        value = wintypes.LPWSTR()
        if not security.ConvertSidToStringSidW(sid, ctypes.byref(value)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return value.value
        finally:
            kernel.LocalFree(ctypes.cast(value, wintypes.LPVOID))

    token, descriptor = wintypes.HANDLE(), wintypes.LPVOID()
    if not security.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        def token_info(kind: int) -> ctypes.Array:
            size = wintypes.DWORD()
            security.GetTokenInformation(token, kind, None, 0, ctypes.byref(size))
            if ctypes.get_last_error() != 122:
                raise ctypes.WinError(ctypes.get_last_error())
            value = ctypes.create_string_buffer(size.value)
            if not security.GetTokenInformation(token, kind, value, size, ctypes.byref(size)):
                raise ctypes.WinError(ctypes.get_last_error())
            return value

        user, groups = token_info(1), token_info(2)
        owners = {sid_string(ctypes.cast(user, ctypes.POINTER(SidAttributes)).contents.sid)}
        count = ctypes.cast(groups, ctypes.POINTER(Groups)).contents.count
        entries = (SidAttributes * count).from_buffer(groups, Groups.entries.offset)
        owners.update(sid_string(entry.sid) for entry in entries
                      if entry.attributes & 0xC == 0xC and not entry.attributes & 0x10)
        owner, dacl = wintypes.LPVOID(), wintypes.LPVOID()
        code = security.GetSecurityInfo(msvcrt.get_osfhandle(fd), 1, 5,
                                       ctypes.byref(owner), None, ctypes.byref(dacl), None,
                                       ctypes.byref(descriptor))
        if code:
            raise ctypes.WinError(code)
        if not owner or sid_string(owner) not in owners or not dacl:
            return False
        size = AclSize()
        if not security.GetAclInformation(dacl, ctypes.byref(size), ctypes.sizeof(size), 2):
            raise ctypes.WinError(ctypes.get_last_error())
        trusted = owners | {"S-1-5-18", "S-1-5-32-544", "S-1-3-4"}
        for index in range(size.count):
            pointer = wintypes.LPVOID()
            if not security.GetAce(dacl, index, ctypes.byref(pointer)):
                raise ctypes.WinError(ctypes.get_last_error())
            ace = ctypes.cast(pointer, ctypes.POINTER(Ace)).contents
            if ace.flags & 8 or ace.kind == 1:
                continue
            if ace.kind != 0 or (ace.mask & 0x500D0156 and sid_string(pointer.value + Ace.sid.offset) not in trusted):
                return False
        return True
    finally:
        if descriptor:
            kernel.LocalFree(descriptor)
        kernel.CloseHandle(token)


class ConstructionTimeout(TimeoutError):
    """The construction owner did not release its kernel lease in budget."""

class RetentionTimeout(TimeoutError):
    """Cleanup or publication did not release kernel ownership in budget."""

def _ownership_key(root: Path, version: str) -> tuple[int, str, str]:
    return os.getpid(), os.path.normcase(str(root.resolve())), version.lower()

@contextmanager
def construction_lease(root: Path, version: str, *, timeout: float = 60.0,
                       poll_interval: float = 0.05):
    """Yield a borrowed descriptor under exclusive construction ownership.

    This is an installer-facing Python primitive, not a shell acquire/release
    command: the descriptor must remain in the process doing construction.
    Borrowers must not close it; POSIX guardians may inherit its custody.
    Recheck ``check_admission`` inside the context before any slot writes.
    Release before unrelated reconciliation or activation/cutover locking.

    POSIX uses flock; Windows uses a nonblocking LockFileEx byte lock. Closing
    the final descriptor (including process death) releases ownership. The
    persistent lock file is never unlinked: replacing it could create a second lock inode.
    No PID file, stale timestamp, or mkdir fallback grants ownership.

    This does not pin a completed candidate against GC or replace a repair's
    outer cutover guard. Those protocols remain separate adoption prerequisites.
    """
    with _kernel_lease(
        root, version, kind="construction", shared=False,
        timeout=timeout, poll_interval=poll_interval,
    ) as descriptor:
        owners = getattr(_construction_owners, "keys", None)
        if owners is None:
            owners = _construction_owners.keys = set()
        key = _ownership_key(root, version)
        owners.add(key)
        try:
            yield descriptor
        finally:
            owners.remove(key)

def _publication_lease(root: Path, *, timeout: float = 60.0):
    return _kernel_lease(root, "selectors", kind="publication", shared=False, timeout=timeout)

@contextmanager
def _cleanup_lease(root: Path, version: str, *, allow_construction_owner: bool = False):
    owns_construction = _ownership_key(root, version) in getattr(_construction_owners, "keys", ())
    if owns_construction and not allow_construction_owner:
        yield False
        return
    construction = nullcontext() if owns_construction else construction_lease(root, version, timeout=0)
    # Catch acquisition contention only; storage failures and errors in the
    # cleanup body propagate rather than appearing as a harmless busy slot.
    from contextlib import ExitStack
    with ExitStack() as stack:
        try:
            stack.enter_context(construction)
            stack.enter_context(_kernel_lease(
                root, version, kind="retention", shared=False, timeout=0,
            ))
            stack.enter_context(_publication_lease(root, timeout=0))
        except (ConstructionTimeout, RetentionTimeout):
            yield False
            return
        yield True

@contextmanager
def _kernel_lease(root: Path, version: str, *, kind: str, shared: bool,
                  timeout: float = 60.0, poll_interval: float = 0.05,
                  open_lock: Callable[[Path, str], int] | None = None):
    import hashlib

    if not isinstance(version, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]*", version):
        raise ValueError(f"{kind} version must be a single ordinary slot name")
    if version.endswith("."):
        raise ValueError(f"{kind} version must not end in a dot")
    if not math.isfinite(timeout) or timeout < 0:
        raise ValueError(f"{kind} timeout must be finite and non-negative")
    if not math.isfinite(poll_interval) or poll_interval <= 0:
        raise ValueError(f"{kind} poll interval must be finite and positive")

    # Case aliases share ownership, including on case-insensitive POSIX volumes.
    key = hashlib.sha256(version.lower().encode("utf-8")).hexdigest()
    lock_dir = root / f".{kind}-leases"
    lock_path = lock_dir / (key + ".lock")
    if open_lock is None:
        lock_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(lock_path, flags, 0o600)
    else:
        # The caller keeps its verified directory handles alive through release.
        fd = open_lock(lock_dir, lock_path.name)
    try:
        opened = os.fstat(fd)
        named = os.lstat(lock_path)
        if (not stat.S_ISREG(named.st_mode) or
                (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)):
            raise OSError(f"{kind} lease path is not the opened ordinary file")
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
            kernel.LockFileEx.argtypes = [
                wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(Overlapped),
            ]
            kernel.LockFileEx.restype = wintypes.BOOL
            kernel.UnlockFileEx.argtypes = [
                wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                wintypes.DWORD, ctypes.POINTER(Overlapped),
            ]
            kernel.UnlockFileEx.restype = wintypes.BOOL
            handle = msvcrt.get_osfhandle(fd)
            overlapped = Overlapped()

            def try_lock():
                flags = 1 | (0 if shared else 2)  # fail immediately; exclusive
                if not kernel.LockFileEx(handle, flags, 0, 1, 0, ctypes.byref(overlapped)):
                    raise ctypes.WinError(ctypes.get_last_error())

            def unlock():
                if not kernel.UnlockFileEx(handle, 0, 1, 0, ctypes.byref(overlapped)):
                    raise ctypes.WinError(ctypes.get_last_error())

            def is_busy(exc):
                return exc.winerror == 33  # ERROR_LOCK_VIOLATION
        else:
            import fcntl

            def try_lock():
                mode = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
                fcntl.flock(fd, mode | fcntl.LOCK_NB)

            # Final close preserves inherited guardian custody; LOCK_UN would
            # also unlock the guardian's copy of the open file description.
            def is_busy(exc):
                return exc.errno in {errno.EAGAIN, errno.EWOULDBLOCK}
        deadline = time.monotonic() + timeout
        while True:
            try:
                try_lock()
                break
            except OSError as exc:
                if not is_busy(exc):
                    raise
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timeout_type = ConstructionTimeout if kind == "construction" else RetentionTimeout
                    raise timeout_type(
                        f"Timed out waiting for {kind} of versions/{version}; "
                        "the lease owner is not identified by the surviving lock file"
                    ) from exc
                time.sleep(min(poll_interval, remaining))
        try:
            named = os.lstat(lock_path)
            if (not stat.S_ISREG(named.st_mode) or
                    (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)):
                raise OSError(f"{kind} lease path changed while waiting")
            yield fd
        finally:
            if os.name == "nt":
                unlock()
    finally:
        os.close(fd)
