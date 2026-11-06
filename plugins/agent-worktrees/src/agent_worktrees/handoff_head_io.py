"""Finite, non-creating filesystem observations for handoff/head comparison."""

from __future__ import annotations

import json
import os
import stat
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

FILE_BYTES = 1024 * 1024
TOTAL_BYTES = 8 * FILE_BYTES
PATH_LIMIT = 512
ENTRY_LIMIT = 64
WORK_SECONDS = 1.0


class ObservationFailure(ValueError):
    """An observation cannot establish a comparison, without exposing content."""

    def __init__(self, reason: str, *, unavailable: bool = True) -> None:
        super().__init__(reason)
        self.reason = reason
        self.unavailable = unavailable


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ObservationFailure("duplicate-receipt-field")
        result[key] = value
    return result


def _signature(path: Path) -> tuple[int, int, int, int, int] | None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise ObservationFailure("linked-observation-path")
    return info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns


@dataclass
class Snapshot:
    """A cooperative work budget; the process caller owns a hard deadline."""

    started: float = field(default_factory=time.monotonic)
    bytes_read: int = 0
    file_reads: int = 0
    paths: dict[Path, tuple[int, int, int, int, int] | None] = field(default_factory=dict)
    presence_only: set[Path] = field(default_factory=set)

    def check(self) -> None:
        if time.monotonic() - self.started >= WORK_SECONDS:
            raise ObservationFailure("observation-time-budget")

    def _same(
        self,
        path: Path,
        first: tuple | None,
        second: tuple | None,
    ) -> bool:
        if first is None or second is None:
            return first is second
        return first[:3] == second[:3] if path in self.presence_only else first == second

    def inspect(
        self,
        path: Path,
        *,
        presence_only: bool = False,
    ) -> tuple[int, int, int, int, int] | None:
        self.check()
        if path not in self.paths and len(self.paths) >= PATH_LIMIT:
            raise ObservationFailure("observation-path-budget")
        for parent in reversed(path.parents):
            signature = _signature(parent)
            if signature is not None and not stat.S_ISDIR(signature[2]):
                raise ObservationFailure("invalid-observation-parent")
        signature = _signature(path)
        if presence_only and path not in self.paths:
            self.presence_only.add(path)
        if not presence_only:
            self.presence_only.discard(path)
        if path in self.paths and not self._same(path, self.paths[path], signature):
            raise ObservationFailure("source-changed", unavailable=False)
        self.paths[path] = signature
        return signature

    def read(self, path: Path, *, limit: int = FILE_BYTES) -> bytes | None:
        signature = self.inspect(path)
        if signature is None:
            return None
        if not stat.S_ISREG(signature[2]):
            raise ObservationFailure("nonregular-observation-file")
        if signature[3] > limit:
            raise ObservationFailure("observation-file-budget")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        fd = os.open(path, flags)
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns)
                != signature
            ):
                raise ObservationFailure("source-changed", unavailable=False)
            chunks: list[bytes] = []
            size = 0
            while size <= limit:
                self.check()
                chunk = os.read(fd, min(65536, limit + 1 - size))
                if not chunk:
                    break
                size += len(chunk)
                self.bytes_read += len(chunk)
                if size > limit or self.bytes_read > TOTAL_BYTES:
                    raise ObservationFailure("observation-byte-budget")
                chunks.append(chunk)
            self.file_reads += 1
            self.inspect(path)
            return b"".join(chunks)
        finally:
            os.close(fd)

    def json(self, path: Path) -> dict[str, Any] | None:
        raw = self.read(path)
        if raw is None:
            return None
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique)
        if not isinstance(value, dict):
            raise ObservationFailure("invalid-receipt")
        return value

    def entries(self, path: Path) -> list[str]:
        signature = self.inspect(path)
        if signature is None:
            return []
        if not stat.S_ISDIR(signature[2]):
            raise ObservationFailure("invalid-receipt-directory")
        names: list[str] = []
        with os.scandir(path) as iterator:
            for entry in iterator:
                self.check()
                if len(names) >= ENTRY_LIMIT:
                    raise ObservationFailure("observation-directory-budget")
                names.append(entry.name)
        return sorted(names)

    def verify(self) -> None:
        for path, signature in self.paths.items():
            self.check()
            if not self._same(path, _signature(path), signature):
                raise ObservationFailure("source-changed", unavailable=False)

    def work(self) -> dict[str, int | float]:
        return {
            "file_reads": self.file_reads,
            "bytes_read": self.bytes_read,
            "paths": len(self.paths),
            "elapsed_ms": round((time.monotonic() - self.started) * 1000, 3),
        }
