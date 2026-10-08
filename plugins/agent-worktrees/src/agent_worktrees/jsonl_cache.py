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
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

_Stamp = tuple[int, int, int, int]
_cache_lock = threading.Lock()
_MAX_FILES = 64
log = logging.getLogger(__name__)


@dataclass
class _Entry:
    stamp: _Stamp
    offset: int
    complete: list[dict]
    visible: list[dict]


_cache: OrderedDict[tuple[str, str], _Entry] = OrderedDict()


def _stamp(stat: os.stat_result) -> _Stamp:
    return stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_size


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


def read_jsonl(path: Path, *, errors: str = "strict") -> list[dict]:
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
            observed = _stamp(path.stat())
            if previous is not None and previous.stamp == observed:
                _cache.move_to_end(key)
                return previous.visible
            with path.open("rb") as handle:
                stamp = _stamp(os.fstat(handle.fileno()))
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
        complete = previous.complete + decoded if append else decoded
        tail: list[dict] = []
        if boundary < len(raw):
            try:
                tail = _decode(raw[boundary:], errors)
            except UnicodeDecodeError as exc:
                if exc.reason != "unexpected end of data":
                    raise
        visible = complete + tail if tail else complete
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
