"""Small cross-platform advisory file lock for agent-ssh state transactions."""

from __future__ import annotations

import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


def validate_lock_file(path: Path) -> None:
    """Check existing lock-file kinds without opening, creating or acquiring them."""
    if not os.path.lexists(path):
        return
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
        or getattr(info, "st_file_attributes", 0) & 0x400
        or getattr(info, "st_reparse_tag", 0)
    ):
        raise ValueError("SSH writer lock must be an ordinary file with one link")


@contextmanager
def exclusive_file_lock(path: Path) -> Iterator[None]:
    """Hold an OS-released exclusive lock for the duration of the context."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
