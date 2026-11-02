"""Memoize positive project identity probes, not activation or policy decisions."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import stat
from functools import lru_cache
from pathlib import Path

from .state import PluginStateError, read_json_object, write_json_object_atomic

_LOG = logging.getLogger(__name__)
_VERSION = 1


def _regular(info: os.stat_result, *, directory: bool = False) -> bool:
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    return kind(info.st_mode) and not (
        info.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT
        if hasattr(info, "st_file_attributes") else False
    )


@lru_cache(maxsize=1)
def _windows_file_api():
    import ctypes
    from ctypes import wintypes

    class BasicInfo(ctypes.Structure):
        _fields_ = [(name, ctypes.c_int64) for name in (
            "created", "accessed", "modified", "changed",
        )] + [("attributes", wintypes.DWORD)]

    class FileId(ctypes.Structure):
        _fields_ = [("volume", ctypes.c_uint64), ("id", ctypes.c_ubyte * 16)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.GetFileInformationByHandleEx.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
    ]
    kernel.GetFileInformationByHandleEx.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    def open_file(path):
        handle = kernel.CreateFileW(str(path), 0x80, 7, None, 3, 0x02200000, None)
        if handle == ctypes.c_void_p(-1).value:
            raise OSError("project identity write-version handle unavailable")
        return handle

    def read_version(handle):
        basic, identity = BasicInfo(), FileId()
        for kind, info in ((0, basic), (18, identity)):
            if not kernel.GetFileInformationByHandleEx(handle, kind, ctypes.byref(info), ctypes.sizeof(info)):
                raise OSError("project identity write-version query unavailable")
        if basic.changed <= 0 or basic.attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise OSError("project identity write-version evidence uncertain")
        return identity.volume, int.from_bytes(bytes(identity.id), "little"), basic.changed

    return open_file, read_version, kernel.CloseHandle


def _windows_write_version(path: Path) -> tuple[int, ...]:
    open_file, read_version, close = _windows_file_api()
    handle = open_file(path)
    try:
        return read_version(handle)
    finally:
        if not close(handle):
            raise OSError("project identity write-version handle close failed")


def _write_version(info: os.stat_result, path: Path) -> tuple[int, ...]:
    # Windows stat ctime is creation time, not an in-place write version.
    native = _windows_write_version(path) if os.name == "nt" else ()
    return (
        info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
        info.st_size, info.st_mtime_ns, info.st_ctime_ns, *native,
    )


def _owned_directory(path: Path, info: os.stat_result) -> bool:
    if os.name != "nt":
        return info.st_uid == os.geteuid()
    try:
        from agent_procutil._daemon_instance_owner import assert_path_owner
    except ImportError:
        return False
    assert_path_owner(path)
    return True


def identity_inputs(root: Path, expected_remote: str, git: str | None) -> str | None:
    """Return a cheap complete input key only for non-indirect repository layouts.

    Git runs without global/system config or inherited GIT_* overrides. Linked
    gitfiles, common directories, includes and core.worktree can add dependencies
    outside this footprint; those layouts deliberately keep real Git probes.
    """
    if not git:
        return None
    try:
        executable = Path(git).resolve(strict=True)
        executable_info = executable.stat()
        dotgit = root / ".git"
        gitdir = dotgit if os.path.lexists(dotgit) else root
        if any(os.path.lexists(path) for path in (
            gitdir / "commondir", gitdir / "remotes" / "origin",
            gitdir / "branches" / "origin",
        )):
            return None
        directories = []
        optional = (gitdir / "remotes", gitdir / "branches")
        for path in dict.fromkeys((root, gitdir, gitdir / "objects", gitdir / "refs", *optional)):
            try:
                info = path.lstat()
            except FileNotFoundError:
                if path not in optional:
                    return None
                directories.append((str(path), None))
                continue
            if not _regular(info, directory=True) or not _owned_directory(path, info):
                return None
            directories.append((str(path), _write_version(info, path)))
        bindings = []
        for parent in root.parents:
            info = parent.lstat()
            if not _regular(info, directory=True):
                return None
            bindings.append((str(parent), _write_version(info, parent)))
        files = []
        for name in ("HEAD", "config", "config.worktree"):
            path = gitdir / name
            try:
                before = path.lstat()
            except FileNotFoundError:
                if name != "config.worktree":
                    return None
                files.append((name, None))
                continue
            if not _regular(before):
                return None
            raw = path.read_bytes()
            after = path.lstat()
            before_version = _write_version(before, path)
            after_version = _write_version(after, path)
            if before_version != after_version:
                return None
            if name != "HEAD" and (
                b"include" in raw.lower() or re.search(rb"\bworktree\b", raw, re.I)
            ):
                return None
            files.append((name, after_version, hashlib.sha256(raw).hexdigest()))
        inputs = [
            _VERSION, str(root), expected_remote, str(executable),
            _write_version(executable_info, executable),
            directories, bindings, files,
        ]
        return hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()
    except (OSError, ValueError):
        return None  # unsupported/uncertain inputs still take the authoritative probe


def read_identities(path: Path) -> dict[str, str]:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return {}
    except OSError:
        _LOG.warning("Project identity memo is unreadable: %s", path)
        return {}
    try:
        if not _regular(info):
            raise PluginStateError("memo must be a regular non-reparse file")
        _, data = read_json_object(path)
        if type(data.get("version")) is not int or data["version"] != _VERSION:
            return {}
        verified = data.get("verified")
        if not isinstance(verified, dict) or any(
            not isinstance(key, str) or not isinstance(value, str)
            or re.fullmatch("[0-9a-f]{64}", value) is None
            for key, value in verified.items()
        ):
            raise PluginStateError("memo verified entries are invalid")
        return verified
    except PluginStateError:
        _LOG.warning("Project identity memo is invalid or unreadable: %s", path)
        return {}


def write_identities(path: Path, verified: dict[str, str]) -> None:
    try:
        write_json_object_atomic(path, {"version": _VERSION, "verified": verified})
    except OSError:
        _LOG.warning("Project identity memo could not be published: %s", path)
