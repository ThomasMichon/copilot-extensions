"""Bounded stdlib-only reader for producer-published caller guard facts."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
from typing import NamedTuple

SCHEMA = "agent-worktrees.guarded-anchors"
VERSION = 1
MAX_BYTES = 1024 * 1024
MAX_ROOTS = 4096


def _open_map(path: Path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise ValueError("nonregular or indirected guard input")
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise ValueError("nonregular guard input")
            return os.fdopen(descriptor, "rb")
        except BaseException:
            os.close(descriptor)
            raise
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
    kernel.CloseHandle.restype = wintypes.BOOL
    native = os.path.abspath(path)
    if not native.startswith("\\\\?\\"):
        native = "\\\\?\\UNC\\" + native[2:] if native.startswith("\\\\") else "\\\\?\\" + native
    # Match endpoint-rendezvous: readers must permit atomic replace and removal.
    handle = kernel.CreateFileW(native, 0x80000000, 0x7, None, 3, 0x00200080, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        kernel.CloseHandle(handle)
        raise
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("nonregular guard input")
        return os.fdopen(descriptor, "rb")
    except BaseException:
        os.close(descriptor)
        raise


def canonical(path: str | Path) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def _text(path: Path) -> str:
    with _open_map(path) as stream:
        raw = stream.read(8193)
    if len(raw) > 8192:
        raise ValueError("Git metadata exceeds its bound")
    return raw.decode("utf-8").strip()


def repository_identity(start: str | Path) -> dict | None:
    """Nearest checkout/common directory, including new targets and bare roots.

    Only Git's filesystem markers are inspected; no config, index, object
    enumeration, Git invocation or environment-directed Git overrides.
    """
    try:
        here = Path(canonical(start))
        for root in (here, *here.parents):
            marker = root / ".git"
            if marker.is_dir():
                git_dir = marker
            elif marker.is_file():
                text = _text(marker)
                if not text.lower().startswith("gitdir:"):
                    return None
                pointer = text[7:].strip()
                if not pointer:
                    return None
                git_dir = Path(pointer)
                if not git_dir.is_absolute():
                    git_dir = root / git_dir
            elif (root / "HEAD").is_file() and (root / "objects").is_dir() and (root / "refs").is_dir():
                git_dir = root
            else:
                continue
            if not (git_dir / "HEAD").is_file():
                return None
            common = git_dir
            if (git_dir / "commondir").is_file():
                pointer = _text(git_dir / "commondir")
                if not pointer:
                    return None
                common = Path(pointer)
                if not common.is_absolute():
                    common = git_dir / common
            if not common.is_dir():
                return None
            return {
                "root": canonical(root), "git_dir": canonical(git_dir),
                "common_dir": canonical(common),
            }
    except (OSError, UnicodeError, ValueError, RuntimeError):
        return None
    return None


def map_path(source_root: str | Path, registry_root: str | Path) -> Path:
    key = hashlib.sha256(canonical(source_root).encode("utf-8")).hexdigest()
    return Path(registry_root) / "guarded-anchors" / f"{key}.json"


class MapRead(NamedTuple):
    status: str
    roots: list[dict]
    reason: str = ""


def read_map(source_root: str | Path, registry_root: str | Path) -> MapRead:
    """Unavailable is distinct from a complete, deliberately empty inventory."""
    try:
        path = map_path(source_root, registry_root)
        if path.is_symlink() or path.parent.is_symlink():
            raise ValueError("indirected guard map")
        with _open_map(path) as stream:
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("oversized guard map")
        data = json.loads(raw)
        if (
            not isinstance(data, dict) or data.get("schema") != SCHEMA
            or type(data.get("version")) is not int or data["version"] != VERSION
            or data.get("source_root") != canonical(source_root)
            or data.get("registry_root") != canonical(registry_root)
            or data.get("source_identity") != repository_identity(source_root)
            or not isinstance(data.get("source_identity"), dict)
            or data.get("inventory_authority") != "complete"
            or not isinstance(data.get("generation"), str) or not data["generation"]
            or not isinstance(data.get("roots"), list) or len(data["roots"]) > MAX_ROOTS
        ):
            raise ValueError("invalid guard-map schema, scope or completeness")
        for row in data["roots"]:
            if (
                not isinstance(row, dict)
                or any(not isinstance(row.get(key), str) or not row[key]
                       for key in ("name", "delegate", "path"))
                or row["delegate"] == "none" or not os.path.isabs(row["path"])
                or not isinstance(row.get("locus"), (dict, type(None)))
                or not isinstance(row.get("identity"), dict)
                or any(not isinstance(row["identity"].get(key), str)
                       or not os.path.isabs(row["identity"][key])
                       for key in ("root", "git_dir", "common_dir"))
            ):
                raise ValueError("invalid guarded anchor")
            if row["identity"]["root"] != canonical(row["path"]):
                raise ValueError("guarded anchor path and identity disagree")
        return MapRead("ready", data["roots"])
    except FileNotFoundError:
        return MapRead("missing", [], "guard map not prepared")
    except (OSError, UnicodeError, ValueError, TypeError, RuntimeError) as exc:
        return MapRead("unusable", [], str(exc))


def matches(path: str, guarded: dict, *, identity_cache: dict | None = None) -> bool:
    """Do not attribute nested repositories to a containing guarded checkout."""
    identity = guarded.get("identity")
    if identity is None:
        child, root = canonical(path), canonical(guarded["path"])
        return child == root or child.startswith(root + os.sep)
    cache = identity_cache if identity_cache is not None else {}
    if path not in cache:
        cache[path] = repository_identity(path)
    target = cache[path]
    return bool(target and target["common_dir"] == identity["common_dir"])
