"""Lossless plain-file transfer; special storage semantics are explicit refusals."""

from __future__ import annotations

import hashlib
import itertools
import os
import shutil
import stat
from pathlib import Path

from agent_procutil.owned_process import run_caller_owned

MAX_ENTRIES = 10_000
MAX_BYTES = 1024 * 1024 * 1024


def normalized_ace_flags(flags: int, *, inheritance: bool = False) -> int:
    return flags & (~0x10 if inheritance else ~0x17)


def manifest(root: Path) -> dict[str, tuple]:
    result = {}
    total = 0
    for index, path in enumerate(itertools.chain((root,), root.rglob("*"))):
        if index >= MAX_ENTRIES:
            raise ValueError("relocation v1 checkout exceeds its bounded entry inventory")
        info = path.lstat()
        relative = path.relative_to(root).as_posix()
        attributes = getattr(info, "st_file_attributes", 0)
        if (
            stat.S_ISLNK(info.st_mode) or attributes & 0x144DE00
            or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode))
            or stat.S_ISREG(info.st_mode) and info.st_nlink != 1
            or stat.S_ISREG(info.st_mode) and attributes & 1
        ):
            raise ValueError("relocation v1 refuses links, hardlinks and special storage attributes")
        if path.name == "pyvenv.cfg" or path.name == ".gitmodules":
            raise ValueError("relocation v1 refuses path-bound venvs and submodules")
        if os.name != "nt" and (
            hasattr(os, "listxattr") and os.listxattr(path)
            or stat.S_ISREG(info.st_mode) and getattr(info, "st_blocks", 0) * 512 < info.st_size
        ):
            raise ValueError("relocation v1 refuses extended attributes and sparse storage")
        if os.name == "nt":
            _require_plain_stream(path)
        if relative == ".git":
            continue
        digest = None
        if path.is_file():
            total += info.st_size
            if total > MAX_BYTES:
                raise ValueError("relocation v1 checkout exceeds its bounded byte inventory")
            with path.open("rb") as stream:
                hasher = hashlib.sha256()
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    hasher.update(block)
                digest = hasher.hexdigest()
        result[relative] = (
            stat.S_IMODE(info.st_mode), info.st_size if digest else 0, digest,
            info.st_mtime_ns if digest else 0,
            (info.st_uid, info.st_gid) if os.name != "nt" else None,
            _access(path, inheritance=True) if os.name == "nt" else None,
            attributes,
        )
    return result


def _require_plain_stream(path: Path) -> None:
    import ctypes
    from ctypes import wintypes

    class StreamData(ctypes.Structure):
        _fields_ = [("size", ctypes.c_longlong), ("name", wintypes.WCHAR * 296)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.FindFirstStreamW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(StreamData), wintypes.DWORD]
    kernel.FindFirstStreamW.restype = wintypes.HANDLE
    kernel.FindNextStreamW.argtypes = [wintypes.HANDLE, ctypes.POINTER(StreamData)]
    kernel.FindNextStreamW.restype = wintypes.BOOL
    kernel.FindClose.argtypes = [wintypes.HANDLE]
    data = StreamData()
    handle = kernel.FindFirstStreamW(str(path), 0, ctypes.byref(data), 0)
    if handle == wintypes.HANDLE(-1).value:
        if ctypes.get_last_error() == 38:
            return
        raise OSError("native stream inventory unavailable")
    try:
        while True:
            if data.name != "::$DATA":
                raise ValueError("relocation v1 refuses alternate data streams")
            if not kernel.FindNextStreamW(handle, ctypes.byref(data)):
                if ctypes.get_last_error() != 38:
                    raise OSError("native stream inventory incomplete")
                break
    finally:
        kernel.FindClose(handle)


def copy(source: Path, destination: Path) -> None:
    if os.name == "nt":
        result = run_caller_owned(
            ["robocopy", str(source), str(destination), "/E", "/COPY:DATS", "/SL",
             "/DCOPY:DAT", "/XJ", "/R:0", "/W:0", "/NFL", "/NDL", "/NJH", "/NJS", "/UNICODE"],
            timeout=300, max_stdout_bytes=32 * 1024 * 1024,
            max_stderr_bytes=1024 * 1024, binary=True,
        )
        if result.returncode >= 8:
            data = (result.stdout or b"")[-8192:]
            diagnostic = data.decode("utf-16-le" if b"\0" in data else "utf-8", errors="replace").strip()
            raise OSError(f"native checkout copy failed (robocopy {result.returncode}): {diagnostic}")
    else:
        shutil.copytree(source, destination, dirs_exist_ok=True, symlinks=True, copy_function=shutil.copy2)


def require_safe_destination(source: Path, parent: Path) -> None:
    if os.name == "nt":
        expected = _access(source)
        if _access(parent) != expected or any(_access(p) != expected for p in source.rglob("*")):
            raise ValueError("relocation v1 requires uniform checkout access and an equally restricted destination parent")
    elif stat.S_IMODE(parent.stat().st_mode) & 0o077:
        raise ValueError("relocation v1 requires a private destination parent")


def _access(path: Path, *, inheritance: bool = False) -> tuple:
    import ctypes
    from ctypes import wintypes

    api = ctypes.WinDLL("advapi32", use_last_error=True)
    api.GetFileSecurityW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                                    ctypes.POINTER(wintypes.DWORD)]
    api.GetFileSecurityW.restype = wintypes.BOOL
    length = wintypes.DWORD()
    api.GetFileSecurityW(str(path), 7, None, 0, ctypes.byref(length))
    data = ctypes.create_string_buffer(length.value)
    if not length.value or not api.GetFileSecurityW(str(path), 7, data, length, ctypes.byref(length)):
        raise ctypes.WinError(ctypes.get_last_error())
    pointers = []
    for name in ("GetSecurityDescriptorOwner", "GetSecurityDescriptorGroup"):
        getter = getattr(api, name)
        getter.argtypes = [wintypes.LPVOID, ctypes.POINTER(wintypes.LPVOID), ctypes.POINTER(wintypes.BOOL)]
        pointer, defaulted = wintypes.LPVOID(), wintypes.BOOL()
        if not getter(data, ctypes.byref(pointer), ctypes.byref(defaulted)):
            raise ctypes.WinError(ctypes.get_last_error())
        api.GetLengthSid.argtypes = [wintypes.LPVOID]
        api.GetLengthSid.restype = wintypes.DWORD
        pointers.append(ctypes.string_at(pointer, api.GetLengthSid(pointer)))
    present, defaulted, acl = wintypes.BOOL(), wintypes.BOOL(), wintypes.LPVOID()
    api.GetSecurityDescriptorDacl.argtypes = [
        wintypes.LPVOID, ctypes.POINTER(wintypes.BOOL), ctypes.POINTER(wintypes.LPVOID), ctypes.POINTER(wintypes.BOOL),
    ]
    if not api.GetSecurityDescriptorDacl(data, ctypes.byref(present), ctypes.byref(acl), ctypes.byref(defaulted)):
        raise ctypes.WinError(ctypes.get_last_error())
    if not present.value or not acl.value:
        raise ValueError("relocation v1 refuses an absent or unrestricted DACL")
    header = ctypes.string_at(acl, 8)
    count = int.from_bytes(header[4:6], "little")
    entries, offset = [], 8
    for _ in range(count):
        prefix = ctypes.string_at(acl.value + offset, 4)
        size = int.from_bytes(prefix[2:4], "little")
        entry = bytearray(ctypes.string_at(acl.value + offset, size))
        entry[1] = normalized_ace_flags(entry[1], inheritance=inheritance)
        entries.append(bytes(entry))
        offset += size
    protected = bool(int.from_bytes(data.raw[2:4], "little") & 0x1000)
    return (
        *(value.hex() for value in pointers), tuple(value.hex() for value in entries),
        protected if inheritance else None,
    )


def preserve_security(source: Path, destination: Path) -> None:
    if os.name != "nt":
        return
    import ctypes
    from ctypes import wintypes

    api = ctypes.WinDLL("advapi32", use_last_error=True)
    api.GetFileSecurityW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    api.GetFileSecurityW.restype = wintypes.BOOL

    def descriptor(path):
        length = wintypes.DWORD()
        flags = 7  # Owner, group and DACL; no privileged SACL acquisition.
        api.GetFileSecurityW(str(path), flags, None, 0, ctypes.byref(length))
        if not length.value:
            raise OSError("native checkout security inventory unavailable")
        data = ctypes.create_string_buffer(length.value)
        if not api.GetFileSecurityW(str(path), flags, data, length, ctypes.byref(length)):
            raise ctypes.WinError(ctypes.get_last_error())
        return data

    for path in [source, *sorted(source.rglob("*"))]:
        if path.relative_to(source).as_posix() != ".git":
            target = destination / path.relative_to(source)
            source_security = descriptor(path)
            protected = bool(int.from_bytes(source_security.raw[2:4], "little") & 0x1000)
            api.SetFileSecurityW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.LPVOID]
            api.SetFileSecurityW.restype = wintypes.BOOL
            flags = 7 | (0x80000000 if protected else 0x20000000)
            if not api.SetFileSecurityW(str(target), flags, source_security):
                raise ctypes.WinError(ctypes.get_last_error())
            if _access(path, inheritance=True) != _access(target, inheritance=True):
                raise ValueError("native copy did not preserve checkout security; original retained")
