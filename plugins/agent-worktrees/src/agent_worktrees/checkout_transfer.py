"""Lossless plain-file transfer; special storage semantics are explicit refusals."""

from __future__ import annotations

import hashlib
import itertools
import os
import re
import shutil
import stat
from pathlib import Path

from agent_procutil.owned_process import run_caller_owned

MAX_ENTRIES = 10_000
MAX_BYTES = 1024 * 1024 * 1024


def _require_no_mounts(root: Path) -> None:
    with Path("/proc/self/mountinfo").open("rb") as stream:
        raw = stream.read(MAX_BYTES // 1024 + 1)
    if not raw or len(raw) > MAX_BYTES // 1024:
        raise ValueError("native mount inventory is unavailable or exceeds its budget")
    entries = raw.decode("utf-8", errors="surrogateescape").splitlines()
    if len(entries) > MAX_ENTRIES:
        raise ValueError("native mount inventory exceeds its entry budget")
    for line in entries:
        fields = line.split()
        if len(fields) < 10 or "-" not in fields[6:]:
            raise ValueError("native mount inventory is malformed")
        mount = Path(re.sub(r"\\([0-7]{3})", lambda match: chr(int(match[1], 8)), fields[4]))
        if not mount.is_absolute():
            raise ValueError("native mount inventory is malformed")
        if mount == root or root in mount.parents:
            raise ValueError("relocation v1 refuses mounted checkout roots and subtrees")


def _windows_access(path: Path, access: int) -> None:
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.CreateFileW(str(path), access, 7, None, 3, 0x02000000 if path.is_dir() else 0, None)
    if handle in (None, wintypes.HANDLE(-1).value):
        raise ValueError("relocation requires confirmed source delete/write and destination creation access")
    if not kernel.CloseHandle(handle):
        raise ctypes.WinError(ctypes.get_last_error())


def require_source_access(source: Path, destination_parent: Path) -> None:
    if os.name == "nt":
        _windows_access(destination_parent, 0x46)
    elif (
        source.parent.stat().st_uid != os.getuid()
        or stat.S_IMODE(source.parent.stat().st_mode) & 0o300 != 0o300
    ):
        raise ValueError("relocation v1 requires an owned writable source parent")
    for index, path in enumerate(itertools.chain((source,), source.rglob("*"))):
        if index >= MAX_ENTRIES:
            raise ValueError("source access inventory exceeds its budget")
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("source access inventory is redirected")
        if os.name == "nt":
            _windows_access(path, 0x10000 | (0x46 if path.is_dir() else 2))
        elif (
            info.st_uid != os.getuid()
            or stat.S_ISDIR(info.st_mode) and stat.S_IMODE(info.st_mode) & 0o300 != 0o300
        ):
            raise ValueError("relocation v1 requires owned writable source directories")


def require_file_mutation(path: Path) -> None:
    info, parent = path.lstat(), path.parent.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("relocation metadata is redirected or linked")
    if os.name == "nt":
        if getattr(info, "st_file_attributes", 0) & 0x401:
            raise ValueError("relocation metadata is read-only or redirected")
        _windows_access(path.parent, 0x46)
        _windows_access(path, 0x10002)
    elif (
        info.st_uid != os.getuid() or parent.st_uid != os.getuid()
        or not stat.S_IMODE(info.st_mode) & 0o200
        or stat.S_IMODE(parent.st_mode) & 0o300 != 0o300
    ):
        raise ValueError("relocation metadata requires owned writable files and parents")


def manifest(root: Path) -> dict[str, tuple]:
    if os.name != "nt":
        _require_no_mounts(root)
    result = {}
    total = 0
    for index, path in enumerate(itertools.chain((root,), root.rglob("*"))):
        if index >= MAX_ENTRIES:
            raise ValueError("relocation v1 checkout exceeds its bounded entry inventory")
        info = path.lstat()
        relative = path.relative_to(root).as_posix()
        name = path.name.casefold() if os.name == "nt" else path.name
        root_git = name == ".git" and path.parent == root
        attributes = getattr(info, "st_file_attributes", 0)
        if name == ".git" and not root_git:
            raise ValueError("relocation v1 refuses nested Git checkouts")
        if (
            stat.S_ISLNK(info.st_mode) or attributes & 0x144DE00
            or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode))
            or stat.S_ISREG(info.st_mode) and info.st_nlink != 1
            or stat.S_ISREG(info.st_mode) and attributes & 1
        ):
            raise ValueError("relocation v1 refuses links, hardlinks and special storage attributes")
        if name in {"pyvenv.cfg", ".gitmodules"}:
            raise ValueError("relocation v1 refuses path-bound venvs and submodules")
        if os.name != "nt" and (
            hasattr(os, "listxattr") and os.listxattr(path)
            or stat.S_ISREG(info.st_mode) and getattr(info, "st_blocks", 0) * 512 < info.st_size
        ):
            raise ValueError("relocation v1 refuses extended attributes and sparse storage")
        if os.name == "nt":
            _require_plain_stream(path)
        if root_git:
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
    expected, staged = manifest(source), manifest(destination)
    if any(
        name not in expected or (entry[2] is None) != (expected[name][2] is None)
        for name, entry in staged.items()
    ):
        raise ValueError("owned copy stage contains unexpected checkout entries")
    if os.name == "nt":
        require_safe_destination(source, destination)
        access = _access(source)
        if any(_access(path) != access for path in destination.rglob("*")):
            raise ValueError("owned copy stage access changed")
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
        if any(entry[4][0] != os.getuid() for entry in staged.values()):
            raise ValueError("owned copy stage has a foreign file owner")
        for path in [destination, *(path for path in destination.rglob("*") if path.is_dir())]:
            path.chmod(stat.S_IMODE(path.stat().st_mode) | stat.S_IRWXU)
        shutil.copytree(source, destination, dirs_exist_ok=True, symlinks=True, copy_function=_retry_file)


def _retry_file(source, destination):
    target = Path(destination)
    if target.exists():
        target.unlink()
    return shutil.copy2(source, destination)


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
        entry[1] &= ~0x10 if inheritance else ~0x17
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
