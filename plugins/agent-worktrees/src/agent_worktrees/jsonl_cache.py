"""Bounded-snapshot, incremental reads of append-only lifecycle logs.

Appends decode only new bytes, not the entire history. Atomic replacements,
truncation, and same-size modifications restart the reader. Writers must append
or replace atomically; arbitrary in-place edits followed by growth are not an
append-only log operation.

Results are read-only cache values. Public readers copy their final selected
records before returning them. Each file read stops at its opening size, even
when another process keeps appending. Unterminated lines remain provisional and
are revisited on the next append, preserving visibility of valid EOF records
without losing records completed across multiple writes.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
from collections import OrderedDict
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from itertools import islice
from pathlib import Path
from typing import BinaryIO, overload

_Stamp = tuple[int, int, int, int]
_cache_lock = threading.Lock()
_MAX_FILES = 64
log = logging.getLogger(__name__)


@dataclass
class _Entry:
    stamp: _Stamp
    offset: int
    complete: list[dict]
    visible: "_Snapshot"


@dataclass(frozen=True)
class _Snapshot(Sequence[dict]):
    """A fixed-length view of appendable history, with a provisional EOF tail."""

    records: list[dict]
    length: int
    tail: tuple[dict, ...] = ()

    def __len__(self) -> int:
        return self.length + len(self.tail)

    @overload
    def __getitem__(self, index: int) -> dict: ...

    @overload
    def __getitem__(self, index: slice) -> list[dict]: ...

    def __getitem__(self, index: int | slice) -> dict | list[dict]:
        if isinstance(index, slice):
            return [self[i] for i in range(*index.indices(len(self)))]
        if index < 0:
            index += len(self)
        if index < 0 or index >= len(self):
            raise IndexError(index)
        return self.records[index] if index < self.length else self.tail[index - self.length]

    def __iter__(self) -> Iterator[dict]:
        yield from islice(self.records, self.length)
        yield from self.tail


_cache: OrderedDict[tuple[str, str], _Entry] = OrderedDict()


def _stamp(handle: BinaryIO) -> _Stamp:
    stat = os.fstat(handle.fileno())
    identity = stat.st_dev, stat.st_ino
    if sys.platform == "win32":
        # Python 3.10 fstat reports zero device/inode on Windows. Obtain file
        # identity from the actual open handle, never a potentially replaced path.
        import ctypes
        import msvcrt
        from ctypes import wintypes

        class FileInformation(ctypes.Structure):
            _fields_ = [
                ("attributes", wintypes.DWORD),
                ("created", wintypes.FILETIME),
                ("accessed", wintypes.FILETIME),
                ("written", wintypes.FILETIME),
                ("volume", wintypes.DWORD),
                ("size_high", wintypes.DWORD),
                ("size_low", wintypes.DWORD),
                ("links", wintypes.DWORD),
                ("index_high", wintypes.DWORD),
                ("index_low", wintypes.DWORD),
            ]

        api = ctypes.WinDLL("kernel32", use_last_error=True).GetFileInformationByHandle
        api.argtypes = [wintypes.HANDLE, ctypes.POINTER(FileInformation)]
        api.restype = wintypes.BOOL
        info = FileInformation()
        if not api(msvcrt.get_osfhandle(handle.fileno()), ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        identity = info.volume, (info.index_high << 32) | info.index_low
    return identity[0], identity[1], stat.st_mtime_ns, stat.st_size


def _read_snapshot(handle: BinaryIO, offset: int, size: int) -> bytes:
    handle.seek(offset)
    return handle.read(max(0, size - offset))


def _decode(raw: bytes, errors: str) -> list[dict]:
    out: list[dict] = []
    for raw_line in raw.split(b"\n"):
        line = raw_line.decode("utf-8", errors=errors)
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def read_jsonl(path: Path, *, errors: str = "strict") -> Sequence[dict]:
    """Return a read-only parsed snapshot, reusing complete lines on append.

    File identity is observed on the opened handle, not inferred from a path
    stat preceding open. A replaced path therefore never labels old bytes with
    the new file's identity. Serializing cache fills also prevents an older
    concurrent reader from overwriting a newer cursor.
    """
    key = str(path), errors
    with _cache_lock:
        previous = _cache.get(key)
        try:
            with path.open("rb") as handle:
                stamp = _stamp(handle)
                if previous is not None and previous.stamp == stamp:
                    _cache.move_to_end(key)
                    return previous.visible
                append = (
                    previous is not None
                    and previous.stamp[:2] == stamp[:2]
                    and stamp[3] > previous.stamp[3]
                )
                offset = previous.offset if append else 0
                raw = _read_snapshot(handle, offset, stamp[3])
        except FileNotFoundError:
            _cache.pop(key, None)
            return []
        except OSError:
            _cache.pop(key, None)
            log.warning("Unable to read lifecycle log %s", path, exc_info=True)
            return []
        # A truncation racing the bounded read cannot publish a misleading
        # cursor; let the next call retry against a fresh opening snapshot.
        if len(raw) != stamp[3] - offset:
            _cache.pop(key, None)
            log.warning("Lifecycle log changed size while reading %s", path)
            return []
        boundary = raw.rfind(b"\n") + 1
        decoded = _decode(raw[:boundary], errors)
        if append:
            complete = previous.complete
            complete.extend(decoded)
        else:
            complete = decoded
        tail: list[dict] = []
        if boundary < len(raw):
            try:
                tail = _decode(raw[boundary:], errors)
            except UnicodeDecodeError as exc:
                if exc.reason != "unexpected end of data":
                    raise
        visible = _Snapshot(complete, len(complete), tuple(tail))
        entry = _Entry(stamp, offset + boundary, complete, visible)
        _cache[key] = entry
        _cache.move_to_end(key)
        while len(_cache) > _MAX_FILES:
            _cache.popitem(last=False)
        return visible


def clear() -> None:
    """Drop all process-local reader state."""
    with _cache_lock:
        _cache.clear()


def invalidate(path: Path) -> None:
    """Drop this process's cursors for a path; other processes stat identity."""
    with _cache_lock:
        for key in tuple(_cache):
            if key[0] == str(path):
                _cache.pop(key, None)
