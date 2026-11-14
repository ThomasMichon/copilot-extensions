"""Native protection for executable, user-owned bytecode cache entries."""
from __future__ import annotations

import os
import stat
import sys
import time
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def _owner():
    from . import _daemon_instance_owner

    return _daemon_instance_owner


def mkdir(path: Path) -> None:
    """Create one private directory without altering an existing object."""
    _owner().mkdir_private(path, exist_ok=True)


def _windows_check(path: Path, *, ancestor: bool, private: bool) -> None:
    import ctypes
    from ctypes import wintypes

    owner = _owner()
    advapi, kernel = owner._apis()
    advapi.GetNamedSecurityInfoW.argtypes = [
        wintypes.LPWSTR, ctypes.c_int, wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi.GetNamedSecurityInfoW.restype = wintypes.DWORD
    advapi.GetAclInformation.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.c_int,
    ]
    advapi.GetAclInformation.restype = wintypes.BOOL
    advapi.GetAce.argtypes = [
        ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi.GetAce.restype = wintypes.BOOL
    current = owner._current_sids(os.getpid())[0]
    trusted = {current, "S-1-5-18", "S-1-5-32-544"}
    if ancestor:
        # Windows Modules Installer can own protected system ancestors.
        trusted.add("S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464")
    sid, acl, descriptor = (ctypes.c_void_p() for _ in range(3))
    error = advapi.GetNamedSecurityInfoW(
        str(path), 1, 5, ctypes.byref(sid), None, ctypes.byref(acl), None,
        ctypes.byref(descriptor),
    )
    if error:
        raise ctypes.WinError(error)
    try:
        if owner._sid_text(sid) not in (trusted if ancestor else {current}) or not acl:
            raise PermissionError("bytecode cache ownership is not private")
        trusted.add("S-1-3-4")  # OWNER RIGHTS refers to the verified object owner.
        # ACL_SIZE_INFORMATION: AceCount, AclBytesInUse, AclBytesFree.
        info = (wintypes.DWORD * 3)()
        if not advapi.GetAclInformation(acl, info, ctypes.sizeof(info), 2):
            raise ctypes.WinError(ctypes.get_last_error())
        writes = 0xF00F01FF if private else 0x500D0140 if ancestor else 0x500D0156
        for index in range(info[0]):
            ace = ctypes.c_void_p()
            if not advapi.GetAce(acl, index, ctypes.byref(ace)):
                raise ctypes.WinError(ctypes.get_last_error())
            header = ctypes.string_at(ace, 8)
            kind = header[0]
            if ancestor and header[1] & 8:
                # Existing descendants are checked separately; new cache
                # directories use protected, non-inheriting private DACLs.
                continue
            if kind in (1, 2, 17):  # Deny, audit, integrity.
                continue
            if kind != 0:
                raise PermissionError("unsupported bytecode cache access rule")
            mask = int.from_bytes(header[4:8], "little")
            if mask & writes and owner._sid_text(ace.value + 8) not in trusted:
                raise PermissionError("bytecode cache permits another user's writes")
    finally:
        kernel.LocalFree(descriptor)


def _macos_check(path: Path) -> None:
    import ctypes
    import errno

    library = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
    library.acl_get_file.argtypes = [ctypes.c_char_p, ctypes.c_uint]
    library.acl_get_file.restype = ctypes.c_void_p
    library.acl_get_entry.argtypes = [
        ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p),
    ]
    library.acl_get_entry.restype = ctypes.c_int
    library.acl_get_tag_type.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint)]
    library.acl_get_tag_type.restype = ctypes.c_int
    library.acl_free.argtypes = [ctypes.c_void_p]
    library.acl_free.restype = ctypes.c_int
    acl = library.acl_get_file(os.fsencode(path), 0x100)  # ACL_TYPE_EXTENDED.
    if not acl:
        raise PermissionError("macOS bytecode cache ACL could not be verified")
    try:
        entry, tag = ctypes.c_void_p(), ctypes.c_uint()
        selector = 0
        while True:
            ctypes.set_errno(0)
            found = library.acl_get_entry(acl, selector, ctypes.byref(entry))
            if found != 0:
                if found == -1 and ctypes.get_errno() == errno.EINVAL:
                    return
                raise PermissionError("macOS bytecode cache ACL could not be verified")
            if library.acl_get_tag_type(entry, ctypes.byref(tag)) != 0:
                raise PermissionError("macOS bytecode cache ACL could not be verified")
            # Denials cannot expand access. Other extended entries remain
            # unsupported rather than being inferred safe from mode bits.
            if tag.value != 2:
                raise PermissionError("macOS bytecode cache has an unverified ACL grant")
            selector = -1
    finally:
        library.acl_free(acl)


def check(path: Path, *, ancestor: bool = False, private: bool = False) -> None:
    """Refuse indirection, foreign ownership and unprivileged mutation."""
    info = path.lstat()
    if (
        not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
        or getattr(info, "st_file_attributes", 0) & 0x400
    ):
        raise PermissionError("bytecode cache directory is not ordinary")
    if os.name == "nt":
        _windows_check(path, ancestor=ancestor, private=private)
    else:
        allowed = {os.getuid(), 0} if ancestor else {os.getuid()}
        if info.st_uid not in allowed:
            raise PermissionError("bytecode cache has a foreign owner")
        if info.st_mode & 0o022 and not (ancestor and info.st_mode & stat.S_ISVTX):
            raise PermissionError("bytecode cache directory permits another user's writes")
        if private and info.st_mode & 0o077:
            raise PermissionError("bytecode cache traversal is not private")
        if sys.platform == "darwin":
            _macos_check(path)


def check_ancestors(path: Path) -> None:
    """Include parent replacement authority, not just the leaf's permissions."""
    for parent in path.parents:
        check(parent, ancestor=True)


def check_file(path: Path, *, private: bool = True) -> None:
    """Protect cache metadata too, including independently granted Windows files."""
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise PermissionError("bytecode cache metadata is not ordinary")
    if os.name == "nt":
        _windows_check(path, ancestor=False, private=private)
    elif info.st_uid != os.getuid() or info.st_mode & (0o077 if private else 0o022):
        raise PermissionError("bytecode cache metadata is not private")
    elif sys.platform == "darwin":
        _macos_check(path)


def check_bytecode(path: Path, *, max_entries: int = 256, seconds: float = 0.05) -> None:
    """Bound Windows descendant admission; private POSIX traversal suffices."""
    if os.name != "nt":
        return
    deadline = time.monotonic() + seconds
    pending = [path]
    visited = 0
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as entries:
            while True:
                if time.monotonic() >= deadline:
                    raise PermissionError("bytecode cache protection exceeds its time budget")
                entry = next(entries, None)
                if entry is None:
                    break
                visited += 1
                if visited > max_entries:
                    raise PermissionError("bytecode cache protection exceeds its entry budget")
                info = entry.stat(follow_symlinks=False)
                if (
                    not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode))
                    or getattr(info, "st_file_attributes", 0) & 0x400
                    or stat.S_ISLNK(info.st_mode)
                ):
                    raise PermissionError("bytecode cache descendant is not ordinary")
                child = Path(entry.path)
                _windows_check(child, ancestor=False, private=True)
                if stat.S_ISDIR(info.st_mode):
                    pending.append(child)
    if time.monotonic() >= deadline:
        raise PermissionError("bytecode cache protection exceeds its time budget")
