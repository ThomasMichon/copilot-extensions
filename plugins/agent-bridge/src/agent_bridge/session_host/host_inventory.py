"""Strict host-index storage observations; no native custody or liveness claims."""

from __future__ import annotations

from dataclasses import dataclass, field, fields
import json
import math
import os
from pathlib import Path
import stat
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .host_index import HostRecord

MAX_INDEX_BYTES = 16 * 1024 * 1024


class HostInventoryError(ValueError):
    """The existing host index cannot be completely and safely observed."""


@dataclass(frozen=True)
class HostInventory:
    present: bool
    records: tuple[HostRecord, ...] = field(repr=False)
    file_identity: tuple[int, ...] | None
    _wire: bytes | None = field(repr=False)
    native_custody_verified: bool = field(default=False, init=False)

    @property
    def storage_complete(self) -> bool:
        return self.present


def plain_parents(path: Path) -> None:
    for parent in (path.parent, *path.parent.parents):
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise HostInventoryError("Host inventory parent is not an ordinary directory")


def _identity(info: os.stat_result) -> tuple[int, ...]:
    return info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise HostInventoryError("Duplicate host inventory field")
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    raise HostInventoryError(f"Nonfinite host inventory value: {value}")


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise HostInventoryError("Nonfinite host inventory float")
    return number


def read_inventory(path: Path) -> HostInventory:
    from .host_index import HostRecord

    plain_parents(path)
    try:
        named = path.lstat()
    except FileNotFoundError:
        return HostInventory(False, (), None, None)
    if (
        not stat.S_ISREG(named.st_mode) or named.st_nlink != 1
        or getattr(named, "st_file_attributes", 0) & 0x400
    ):
        raise HostInventoryError("Host inventory must be a single-link ordinary file")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(fd, "rb") as stream:
        opened = os.fstat(stream.fileno())
        if _identity(opened) != _identity(named) or opened.st_nlink != 1:
            raise HostInventoryError("Host inventory changed before reading")
        wire = stream.read(MAX_INDEX_BYTES + 1)
        final = os.fstat(stream.fileno())
        if (
            len(wire) > MAX_INDEX_BYTES or len(wire) != opened.st_size
            or _identity(final) != _identity(opened)
            or _identity(path.lstat()) != _identity(opened)
        ):
            raise HostInventoryError("Host inventory changed or exceeds its bound")
    try:
        raw = json.loads(
            wire.decode("utf-8"), object_pairs_hook=_unique,
            parse_constant=_invalid_constant, parse_float=_finite_float,
        )
    except (ValueError, UnicodeError) as exc:
        raise HostInventoryError("Host inventory is not valid strict JSON") from exc
    if (
        type(raw) is not dict or set(raw) != {"version", "hosts"}
        or type(raw["version"]) is not int or raw["version"] != 1
        or type(raw["hosts"]) is not dict
    ):
        raise HostInventoryError("Unsupported host inventory schema")
    expected = {item.name for item in fields(HostRecord)}
    records = []
    for sid, row in sorted(raw["hosts"].items()):
        if (
            not sid or "\0" in sid or type(row) is not dict or set(row) != expected
            or row["session_id"] != sid
            or any(type(row[key]) is not int or row[key] < 1
                   for key in ("port", "host_pid", "child_pid", "protocol_version"))
            or row["port"] > 65535 or row["protocol_version"] != 1
            or type(row["owner_pid"]) is not int or row["owner_pid"] < 0
            or type(row["created_at"]) not in (int, float)
            or not math.isfinite(row["created_at"]) or row["created_at"] < 0
            or type(row["resume_on_reattach"]) is not bool
            or any(type(row[key]) is not dict for key in ("endpoint", "extra"))
            or any(type(row[key]) is not str or "\0" in row[key] for key in (
                "session_id", "host_version", "state_file", "nonce", "boundary", "owner_generation",
            ))
        ):
            raise HostInventoryError("Incomplete, unsupported or inconsistent host record")
        records.append(HostRecord(**row))
    return HostInventory(True, tuple(records), _identity(opened), wire)
