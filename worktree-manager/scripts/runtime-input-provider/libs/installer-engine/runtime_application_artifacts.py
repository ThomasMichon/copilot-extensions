"""Original ordinary-application artifacts and finite, receipted reclamation.

The application installer owns admission, quiescence and runtime/publication
custody. These callbacks neither acquire that authority nor stop any process.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import time
from typing import Any

from installer_dependency_config import native_retained_path

INVENTORY = ".application-artifacts.json"
_CONTROLS = (INVENTORY, ".install-complete.json", "install-success.json")
_MAX_ENTRIES = 65536
_MAX_RECORD_BYTES = 32 << 20
_MAX_FILE_BYTES = 512 << 20
_MAX_TREE_BYTES = 4 << 30


def _deadline(deadline: float) -> None:
    if not math.isfinite(deadline) or time.monotonic() >= deadline:
        raise TimeoutError("application artifact deadline expired")


def _native(path: Path) -> Path:
    return Path(native_retained_path(path))


def _plain(path: Path) -> None:
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("application artifact paths must be absolute and ordinary")
    for parent in reversed((path, *path.parents)):
        info = _native(parent).lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & 0x400
        ):
            raise ValueError("application artifact ancestry is indirect or not a directory")


def _slot(slot: Path) -> None:
    if (
        slot.parent.name != "versions"
        or re.fullmatch(r"[0-9][A-Za-z0-9._+-]*", slot.name) is None
        or slot.name.endswith(".")
    ):
        raise ValueError("application artifacts require a numbered version slot")
    _plain(slot.parent)


def _binding(binding: dict[str, str]) -> None:
    if (
        set(binding) != {"component", "payload_hash", "profile_lock_id"}
        or not isinstance(binding["component"], str)
        or re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", binding["component"]) is None
        or not isinstance(binding["payload_hash"], str)
        or re.fullmatch(r"[a-f0-9]{64}", binding["payload_hash"]) is None
        or not isinstance(binding["profile_lock_id"], str)
        or re.fullmatch(r"sha256:[a-f0-9]{64}", binding["profile_lock_id"]) is None
    ):
        raise ValueError("application artifact Source/profile binding is invalid")


def _state(path: Path, deadline: float) -> dict[str, Any]:
    _deadline(deadline)
    _plain(path.parent)
    native = _native(path)
    before = native.lstat()
    indirect = stat.S_ISLNK(before.st_mode) or getattr(before, "st_file_attributes", 0) & 0x400
    digest = ""
    if indirect:
        if (
            path.name != "lib64" or path.parent.name != ".venv"
            or os.readlink(native) != "lib"
        ):
            raise ValueError("application artifact is an unsupported link")
        kind, digest = "link", hashlib.sha256(b"lib").hexdigest()
    elif stat.S_ISDIR(before.st_mode):
        kind = "directory"
    elif stat.S_ISREG(before.st_mode) and before.st_size <= _MAX_FILE_BYTES:
        kind = "file"
        hasher = hashlib.sha256()
        with native.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                raise RuntimeError("application artifact changed before reading")
            count = 0
            while block := stream.read(1 << 20):
                _deadline(deadline)
                count += len(block)
                if count > _MAX_FILE_BYTES:
                    raise ValueError("application artifact exceeds its file bound")
                hasher.update(block)
        digest = hasher.hexdigest()
    else:
        raise ValueError("application artifact is unsupported or exceeds its file bound")
    after = native.lstat()
    if any(
        getattr(before, field) != getattr(after, field)
        for field in ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns")
    ):
        raise RuntimeError("application artifact changed during inspection")
    _deadline(deadline)
    return {
        "kind": kind, "device": after.st_dev, "inode": after.st_ino,
        "mode": after.st_mode, "digest": digest,
        "size": after.st_size if kind == "file" else 0,
    }


def _tree(slot: Path, deadline: float, *, include_controls: bool = False) -> dict[str, Any]:
    entries: dict[str, Any] = {}
    total = 0

    def walk(directory: Path) -> None:
        nonlocal total
        _plain(directory)
        with os.scandir(_native(directory)) as children:
            for entry in children:
                _deadline(deadline)
                if directory == slot and entry.name in _CONTROLS and not include_controls:
                    continue
                if len(entries) >= _MAX_ENTRIES:
                    raise ValueError("application artifact inventory exceeds its entry bound")
                path = directory / entry.name
                value = _state(path, deadline)
                entries[path.relative_to(slot).as_posix()] = value
                total += value["size"]
                if total > _MAX_TREE_BYTES:
                    raise ValueError("application artifact inventory exceeds its byte bound")
                if value["kind"] == "directory":
                    walk(path)

    walk(slot)
    return entries


def _write_once(path: Path, record: dict[str, Any], deadline: float) -> None:
    _deadline(deadline)
    _plain(path.parent)
    content = json.dumps(record, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    if len(content) > _MAX_RECORD_BYTES:
        raise ValueError("application artifact record exceeds its byte bound")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(_native(path), flags, 0o600), "wb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    _deadline(deadline)


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("application artifact record has duplicate fields")
        result[key] = value
    return result


def _record(path: Path, deadline: float) -> dict[str, Any]:
    before = _state(path, deadline)
    if (
        before["kind"] != "file" or before["size"] > _MAX_RECORD_BYTES
        or _native(path).lstat().st_nlink != 1
    ):
        raise ValueError("application artifact record is not a bounded ordinary file")
    with _native(path).open("rb") as stream:
        content = stream.read(_MAX_RECORD_BYTES + 1)
    if len(content) > _MAX_RECORD_BYTES or _state(path, deadline) != before:
        raise RuntimeError("application artifact record changed while reading")
    result = json.loads(content, object_pairs_hook=_unique)
    if not isinstance(result, dict):
        raise ValueError("application artifact record must be an object")
    return result


def _base(slot: Path, binding: dict[str, str], deadline: float) -> dict[str, Any]:
    _slot(slot)
    _binding(binding)
    return {
        "version": 1, "slot": str(slot), "binding": binding,
        "parents": [
            _state(slot.parent.parent, deadline), _state(slot.parent, deadline),
        ],
    }


def seal_inventory(slot: Path, binding: dict[str, str], *, deadline: float) -> None:
    """Seal only unfinished construction, while its installer holds construction custody."""
    record = _base(slot, binding, deadline)
    _plain(slot)
    if any(os.path.lexists(_native(slot / name)) for name in _CONTROLS):
        raise RuntimeError("application inventory requires unfinished, unsealed construction")
    record.update({
        "schema": "installer-engine.application-artifacts",
        "directory": _state(slot, deadline), "entries": _tree(slot, deadline),
    })
    _write_once(slot / INVENTORY, record, deadline)


def _pending(slot: Path, paths: tuple[Path, ...], deadline: float) -> dict[str, Any]:
    if len(paths) > 128 or len(set(paths)) != len(paths):
        raise ValueError("application pending artifacts must be finite and distinct")
    pending = {}
    for path in paths:
        if (
            not path.is_absolute() or ".." in path.parts or _native(path) == _native(slot)
            or not _native(path).is_relative_to(_native(slot))
        ):
            raise ValueError("application pending artifact must be inside its original slot")
        state = _state(path, deadline)
        if state["kind"] != "file":
            raise ValueError("application pending artifact must be an ordinary file")
        name = _native(path).relative_to(_native(slot)).as_posix()
        if name in pending:
            raise ValueError("application pending artifacts must be physically distinct")
        pending[name] = state
    return pending


def prepare_cleanup(
    slot: Path, binding: dict[str, str], receipt: Path, *, deadline: float,
    pending_artifacts: tuple[Path, ...] = (),
) -> None:
    """Persist original physical objects before deletion, never adopting pending artifacts.

    The application owner must independently settle its explicitly named transient
    files. They are excluded from this receipt, so cleanup refuses while any remain.
    """
    expected = _base(slot, binding, deadline)
    _plain(slot)
    inventory = _record(slot / INVENTORY, deadline)
    pending = _pending(slot, pending_artifacts, deadline)
    if (
        set(inventory) != {*expected, "schema", "directory", "entries"}
        or inventory.get("schema") != "installer-engine.application-artifacts"
        or any(inventory.get(key) != value for key, value in expected.items())
        or inventory.get("directory") != _state(slot, deadline)
        or not isinstance(inventory.get("entries"), dict)
        or pending.keys() & inventory["entries"].keys()
        or {**inventory["entries"], **pending} != _tree(slot, deadline)
    ):
        raise RuntimeError("application construction inventory changed or belongs to another Source")
    entries = dict(inventory["entries"])
    for name in _CONTROLS:
        path = slot / name
        if os.path.lexists(_native(path)):
            if name == "install-success.json" and _record(path, deadline) != {
                "state": "installed", "version": slot.name,
            }:
                raise RuntimeError("application install-success publication changed")
            entries[name] = _state(path, deadline)
    if not receipt.is_absolute() or ".." in receipt.parts or receipt.is_relative_to(slot):
        raise ValueError("application cleanup receipt must be outside its candidate")
    receipt.parent.mkdir(parents=True, exist_ok=True)
    _plain(receipt.parent)
    _write_once(receipt, {
        **expected, "schema": "installer-engine.application-cleanup",
        "directory": inventory["directory"], "entries": entries,
    }, deadline)


def remove_from_receipt(
    slot: Path, binding: dict[str, str], receipt: Path, *, deadline: float,
) -> None:
    """Finite cleanup callback under canonical custody; missing originals permit partial retry."""
    if any(Path(value).resolve().is_relative_to(slot.resolve()) for value in (
        __file__, sys.executable, sys.prefix, receipt,
    )):
        raise ValueError("application cleanup owner and receipt must be outside its candidate")
    expected = _base(slot, binding, deadline)
    record = _record(receipt, deadline)
    if (
        set(record) != {*expected, "schema", "directory", "entries"}
        or record.get("schema") != "installer-engine.application-cleanup"
        or any(record.get(key) != value for key, value in expected.items())
        or not isinstance(record.get("entries"), dict)
        or len(record["entries"]) > _MAX_ENTRIES + len(_CONTROLS)
    ):
        raise RuntimeError("application cleanup receipt belongs to another installation")
    if not os.path.lexists(_native(slot)):
        return
    _plain(slot)
    if _state(slot, deadline) != record["directory"]:
        raise RuntimeError("application cleanup candidate directory changed")
    current = _tree(slot, deadline, include_controls=True)
    if any(value != record["entries"].get(name) for name, value in current.items()):
        raise RuntimeError("application cleanup found changed or unattributed artifacts")
    for name in sorted(current, key=lambda value: (-value.count("/"), value)):
        path = slot / name
        if _state(path, deadline) != record["entries"][name]:
            raise RuntimeError("application artifact changed before deletion")
        if current[name]["kind"] == "directory":
            _native(path).rmdir()
        else:
            _native(path).unlink()
        _deadline(deadline)
    if _state(slot, deadline) != record["directory"]:
        raise RuntimeError("application cleanup candidate changed before final removal")
    _native(slot).rmdir()
    _deadline(deadline)
