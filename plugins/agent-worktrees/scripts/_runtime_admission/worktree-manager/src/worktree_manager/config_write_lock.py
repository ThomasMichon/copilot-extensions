"""Bounded interprocess serialization for every Manager configuration writer."""

from __future__ import annotations

from contextlib import contextmanager
from collections.abc import Iterator
import time
from pathlib import Path

from .mux_file_lock import _try_lock_file_once, _unlock_file


@contextmanager
def config_write_lock(path: Path, *, timeout: float = 5.0) -> Iterator[None]:
    """Keep the retained sibling inode locked across read, modification and publish."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise OSError("Manager configuration is linked; edit its owning source instead")
    lock = path.with_name("." + path.name + ".lock")
    if lock.is_symlink():
        raise OSError("Manager configuration writer lock is linked")
    with lock.open("a+b") as stream:
        deadline = time.monotonic() + timeout
        while not _try_lock_file_once(stream):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Manager configuration writer lock unavailable; refresh and retry")
            time.sleep(min(0.025, remaining))
        try:
            yield
        finally:
            _unlock_file(stream)
