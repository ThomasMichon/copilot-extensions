"""Bounded, archive-transparent reads of Copilot process-log evidence."""

from __future__ import annotations

import gzip
import os
import stat
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

DEFAULT_MAX_LINE_BYTES = 1024 * 1024


def _is_log_name(name: str) -> bool:
    return (
        name.startswith("process-")
        and name.endswith(".log")
        and len(name) > len("process-.log")
        and "/" not in name
        and "\\" not in name
    )


@contextmanager
def _open_regular(path: Path) -> Iterator[BinaryIO]:
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise ValueError(f"process-log evidence is not a regular file: {path}")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        with os.fdopen(fd, "rb") as stream:
            fd = -1
            opened = os.fstat(stream.fileno())
            if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
                raise ValueError(f"process-log evidence changed while opening: {path}")
            yield stream
    finally:
        if fd != -1:
            os.close(fd)


def _zip_logs(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    logs: list[zipfile.ZipInfo] = []
    names: set[str] = set()
    for info in archive.infolist():
        if info.is_dir():
            continue
        if not _is_log_name(info.filename):
            if info.filename.endswith(".log"):
                raise ValueError(f"process-log ZIP members must be flat: {info.filename!r}")
            continue
        if stat.S_ISLNK(info.external_attr >> 16):
            raise ValueError(f"process-log ZIP member is a symlink: {info.filename!r}")
        if info.filename in names:
            raise ValueError(f"duplicate process-log ZIP member: {info.filename!r}")
        names.add(info.filename)
        logs.append(info)
    return sorted(logs, key=lambda info: info.filename)


@dataclass(frozen=True)
class ProcessLogRef:
    """One physical observation; storage aliases retain the same logical name."""

    path: Path
    member: str | None = None

    def __post_init__(self) -> None:
        if self.member is not None:
            if self.path.suffix != ".zip" or not _is_log_name(self.member):
                raise ValueError("a process-log member requires a ZIP and a flat log name")
        elif not _is_log_name(self.path.name.removesuffix(".gz")):
            raise ValueError(f"unsupported process-log evidence path: {self.path}")

    @property
    def logical_name(self) -> str:
        return self.member if self.member is not None else self.path.name.removesuffix(".gz")

    def iter_lines(self, *, max_line_bytes: int = DEFAULT_MAX_LINE_BYTES) -> Iterator[str]:
        """Read UTF-8 lines without extracting archives or allocating an entire log."""
        if isinstance(max_line_bytes, bool) or not isinstance(max_line_bytes, int):
            raise ValueError("max_line_bytes must be a positive integer")
        if max_line_bytes <= 0:
            raise ValueError("max_line_bytes must be a positive integer")
        with _open_regular(self.path) as raw:
            if self.member is not None:
                with zipfile.ZipFile(raw) as archive:
                    info = next(
                        (info for info in _zip_logs(archive) if info.filename == self.member),
                        None,
                    )
                    if info is None:
                        raise ValueError(f"process-log ZIP member is missing: {self.member!r}")
                    with archive.open(info) as stream:
                        yield from _lines(stream, max_line_bytes)
            elif self.path.suffix == ".gz":
                with gzip.GzipFile(fileobj=raw, mode="rb") as stream:
                    yield from _lines(stream, max_line_bytes)
            else:
                yield from _lines(raw, max_line_bytes)


def _lines(stream: BinaryIO, max_line_bytes: int) -> Iterator[str]:
    while True:
        line = stream.readline(max_line_bytes + 1)
        if not line:
            return
        if len(line) > max_line_bytes:
            raise ValueError(f"process-log line exceeds {max_line_bytes} bytes")
        yield line.decode("utf-8")


def iter_process_log_refs(log_root: Path) -> Iterator[ProcessLogRef]:
    """Enumerate live, gzip, and flat ZIP observations without silently deduplicating."""
    root_stat = log_root.lstat()
    if not stat.S_ISDIR(root_stat.st_mode):
        raise ValueError(f"process-log source is not a directory: {log_root}")
    for path in sorted(log_root.iterdir()):
        if _is_log_name(path.name.removesuffix(".gz")):
            yield ProcessLogRef(path)
        elif path.suffix == ".zip":
            with _open_regular(path) as raw, zipfile.ZipFile(raw) as archive:
                for info in _zip_logs(archive):
                    yield ProcessLogRef(path, info.filename)
