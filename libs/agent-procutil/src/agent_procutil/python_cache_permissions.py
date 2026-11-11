"""Native protection for executable, user-owned bytecode cache entries."""
from __future__ import annotations

import importlib.util
import os
import stat
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def _owner():
    name = __name__ + "._owner"
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).with_name("_daemon_instance_owner.py"),
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def mkdir(path: Path) -> None:
    """Create one private directory without altering an existing object."""
    _owner().mkdir_private(path, exist_ok=True)


def _windows_check(path: Path, *, ancestor: bool) -> None:
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
        writes = 0x500D0140 if ancestor else 0x500D0156
        for index in range(info[0]):
            ace = ctypes.c_void_p()
            if not advapi.GetAce(acl, index, ctypes.byref(ace)):
                raise ctypes.WinError(ctypes.get_last_error())
            header = ctypes.string_at(ace, 8)
            kind, flags = header[0], header[1]
            if flags & 8 or kind in (1, 2, 17):  # Inherit-only, deny, audit, integrity.
                continue
            if kind != 0:
                raise PermissionError("unsupported bytecode cache access rule")
            mask = int.from_bytes(header[4:8], "little")
            if mask & writes and owner._sid_text(ace.value + 8) not in trusted:
                raise PermissionError("bytecode cache permits another user's writes")
    finally:
        kernel.LocalFree(descriptor)


def check(path: Path, *, ancestor: bool = False) -> None:
    """Refuse indirection, foreign ownership and unprivileged mutation."""
    info = path.lstat()
    if (
        not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
        or getattr(info, "st_file_attributes", 0) & 0x400
    ):
        raise PermissionError("bytecode cache directory is not ordinary")
    if os.name == "nt":
        _windows_check(path, ancestor=ancestor)
    else:
        allowed = {os.getuid(), 0} if ancestor else {os.getuid()}
        if info.st_uid not in allowed:
            raise PermissionError("bytecode cache has a foreign owner")
        if info.st_mode & 0o022 and not (ancestor and info.st_mode & stat.S_ISVTX):
            raise PermissionError("bytecode cache directory permits another user's writes")


def check_ancestors(path: Path) -> None:
    """Include parent replacement authority, not just the leaf's permissions."""
    for parent in path.parents:
        check(parent, ancestor=True)
