"""Disposable advisory summaries; cleanup must read receipts under its owner lock."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path
import threading
import time

Signature = tuple[int, ...] | None
Result = tuple[bool, bool]
_cache: OrderedDict[Path, tuple[Signature, float, Result]] = OrderedDict()
_lock = threading.Lock()


def _signature(folder: Path) -> Signature:
    try:
        stat = folder.stat(follow_symlinks=False)
    except FileNotFoundError:
        return None
    return stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size


def invalidate(folder: Path) -> None:
    with _lock:
        _cache.pop(folder, None)


def read(folder: Path, fresh: Callable[[], Result]) -> Result:
    try:
        with _lock:
            signature = _signature(folder)
            now = time.monotonic()
            cached = _cache.get(folder)
            if cached is not None and cached[0] == signature and cached[1] > now:
                _cache.move_to_end(folder)
                return cached[2]
            result = fresh()
            if _signature(folder) != signature:
                _cache.pop(folder, None)
                return True, True
            # Atomic receipt replacement invalidates the directory signature.
            # Expiry also bounds stale display after an unsupported in-place edit.
            _cache[folder] = signature, now + 5, result
            _cache.move_to_end(folder)
            while len(_cache) > 1024:
                _cache.popitem(last=False)
            return result
    except (OSError, ValueError):
        return True, True
