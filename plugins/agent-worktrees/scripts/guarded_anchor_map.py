"""Bounded stdlib-only reader for producer-published caller guard facts."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Callable, NamedTuple

SCHEMA = "agent-worktrees.guarded-anchors"
VERSION = 1
MAX_BYTES = 1024 * 1024
MAX_ROOTS = 4096


def validate_directory(path: Path) -> None:
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise ValueError("non-directory or indirected guard-map store")


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
    return raw.decode("utf-8").removesuffix("\n").removesuffix("\r")


def _path_mode(path: Path) -> int:
    try:
        info = path.lstat()
    except (FileNotFoundError, NotADirectoryError):
        return 0
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        info = path.stat()
    return info.st_mode


def repository_identity(
    start: str | Path, *, observe: Callable[[Path], None] | None = None,
) -> dict | None:
    """Nearest checkout/common directory, including new targets and bare roots.

    Only Git's filesystem markers are inspected; no config, index, object
    enumeration, Git invocation or environment-directed Git overrides.
    """
    def mode(path: Path) -> int:
        if observe is not None:
            observe(path)
        return _path_mode(path)

    def text_at(path: Path) -> str:
        if observe is not None:
            observe(path)
        return _text(path)

    try:
        if observe is not None:
            observe(Path(os.path.abspath(start)))
        here = Path(canonical(start))
        for root in (here, *here.parents):
            marker = root / ".git"
            marker_mode = mode(marker)
            if stat.S_ISDIR(marker_mode):
                git_dir = marker
            elif stat.S_ISREG(marker_mode):
                text = text_at(marker)
                if not text.lower().startswith("gitdir:"):
                    return None
                pointer = text[7:].removeprefix(" ")
                if not pointer:
                    return None
                git_dir = Path(pointer)
                if not git_dir.is_absolute():
                    git_dir = root / git_dir
            elif marker_mode:
                return None
            elif (
                stat.S_ISREG(mode(root / "HEAD"))
                and stat.S_ISDIR(mode(root / "objects"))
                and stat.S_ISDIR(mode(root / "refs"))
            ):
                git_dir = root
            else:
                continue
            if not stat.S_ISREG(mode(git_dir / "HEAD")):
                return None
            common = git_dir
            common_mode = mode(git_dir / "commondir")
            if common_mode:
                if not stat.S_ISREG(common_mode):
                    return None
                pointer = text_at(git_dir / "commondir")
                if not pointer:
                    return None
                common = Path(pointer)
                if not common.is_absolute():
                    common = git_dir / common
            if not stat.S_ISDIR(mode(common)):
                return None
            if not (
                stat.S_ISDIR(mode(common / "objects"))
                and stat.S_ISDIR(mode(common / "refs"))
            ):
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
    postures: list[dict] | None = None
    posture_errors: list[str] | None = None
    anchor_inventory: dict | None = None


def unavailable_anchor_inventory(reason: str) -> dict:
    return {
        "version": 1, "status": "unavailable", "qualification": "managed-observation",
        "rows": [], "observations": [], "reason": reason,
    }


def read_anchor_inventory(value: object, *, row_limit: int | None = None) -> dict:
    """Validate supplemental original-owner facts without probing their paths."""
    invalid = unavailable_anchor_inventory("invalid-anchor-inventory")
    if (
        not isinstance(value, dict) or type(value.get("version")) is not int
        or value["version"] != 1 or not isinstance(value.get("status"), str)
        or value["status"] not in {"observed", "unavailable"}
        or value.get("qualification") != "managed-observation"
        or not isinstance(value.get("rows"), list)
        or len(value["rows"]) > (MAX_ROOTS if row_limit is None else min(MAX_ROOTS, row_limit))
        or not isinstance(value.get("observations"), list) or len(value["observations"]) > 2
        or not isinstance(value.get("reason", ""), str) or len(value.get("reason", "")) > 128
    ):
        return invalid
    for row in value["rows"]:
        if (
            not isinstance(row, dict) or set(row) != {"name", "path"}
            or any(not isinstance(row[key], str) or len(row[key]) > 4096 or "\0" in row[key]
                   for key in ("name", "path"))
            or not row["path"]
        ):
            return invalid
    for item in value["observations"]:
        if (
            not isinstance(item, dict) or set(item) != {"source", "parser", "status", "reason"}
            or not isinstance(item["source"], str) or not item["source"] or len(item["source"]) > 4096
            or "\0" in item["source"]
            or not isinstance(item["parser"], str)
            or item["parser"] not in {"pyyaml", "fallback", "none", "opaque"}
            or not isinstance(item["status"], str) or item["status"] not in {"observed", "unavailable"}
            or not isinstance(item["reason"], str) or len(item["reason"]) > 128
        ):
            return invalid
    if value["status"] == "observed" and (
        len(value["observations"]) != 2 or any(
            item["status"] != "observed" or item["parser"] not in {"pyyaml", "fallback"}
            or item["reason"] for item in value["observations"]
        )
    ):
        return invalid
    return value


def read_map(source_root: str | Path, registry_root: str | Path) -> MapRead:
    """Unavailable is distinct from a complete, deliberately empty inventory."""
    try:
        path = map_path(source_root, registry_root)
        validate_directory(path.parent)
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
        postures = data.get("postures", [])
        posture_errors = data.get("posture_errors", [])
        if not isinstance(postures, list) or len(data["roots"]) + len(postures) > MAX_ROOTS:
            raise ValueError("invalid repository posture inventory")
        if (
            data.get("posture_authority", "complete") not in {"complete", "partial"}
            or not isinstance(posture_errors, list) or len(posture_errors) > MAX_ROOTS
            or any(not isinstance(name, str) for name in posture_errors)
        ):
            raise ValueError("invalid repository posture completeness")
        for index, row in enumerate(data["roots"] + postures):
            if (
                not isinstance(row, dict)
                or any(not isinstance(row.get(key), str) or not row[key]
                       for key in ("name", "delegate", "path"))
                or (row["delegate"] == "none" and index < len(data["roots"]))
                or not os.path.isabs(row["path"])
                or (
                    "registered_path" in row
                    and (
                        not isinstance(row["registered_path"], str)
                        or not os.path.isabs(row["registered_path"])
                    )
                )
                or not isinstance(row.get("locus"), (dict, type(None)))
                or not isinstance(row.get("identity"), dict)
                or any(not isinstance(row["identity"].get(key), str)
                       or not os.path.isabs(row["identity"][key])
                       for key in ("root", "git_dir", "common_dir"))
            ):
                raise ValueError("invalid guarded anchor")
            if row["identity"]["root"] != os.path.normcase(os.path.abspath(row["path"])):
                raise ValueError("guarded anchor path and identity disagree")
        for row in postures:
            if row.get("posture") not in {"", "reference", "worktree"} or type(row.get("elevated")) is not bool:
                raise ValueError("invalid repository posture")
        inventory = read_anchor_inventory(
            data["anchor_inventory"], row_limit=MAX_ROOTS - len(data["roots"]) - len(postures),
        ) if "anchor_inventory" in data else None
        return MapRead("ready", data["roots"], postures=postures,
                       posture_errors=posture_errors, anchor_inventory=inventory)
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
    if target and guarded.get("posture") == "worktree" and target["root"] != identity["root"]:
        return False
    return bool(target and target["common_dir"] == identity["common_dir"])
