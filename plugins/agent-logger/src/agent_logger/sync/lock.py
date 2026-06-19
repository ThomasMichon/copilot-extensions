"""Cross-platform advisory file lock for serialized syncs.

Ported from the facility session-sync engine: ``msvcrt`` on Windows,
``fcntl`` on POSIX, with a timeout. This is the same locking the
orchestrator's merge queue needs -- kept here so the sync engine never
runs two pushes against the same target concurrently.
"""

from __future__ import annotations

import contextlib
import os
import platform
import time
from collections.abc import Iterator
from pathlib import Path

IS_WINDOWS = platform.system() == "Windows"


@contextlib.contextmanager
def sync_lock(lock_file: Path, timeout: int = 10, wait: bool = True) -> Iterator[bool]:
    """Yield ``True`` if the lock was acquired, ``False`` otherwise.

    The lock is always released on exit. ``wait`` with a ``timeout`` retries
    a non-blocking acquire; ``wait=False`` tries once.
    """
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    fh = open(lock_file, "w")
    try:
        if IS_WINDOWS:
            import msvcrt

            deadline = time.monotonic() + (timeout if wait else 0)
            locked = False
            while True:
                try:
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                    locked = True
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        break
                    time.sleep(0.5)
            if not locked:
                fh.close()
                yield False
                return
        else:
            import fcntl

            deadline = time.monotonic() + (timeout if wait else 0)
            while True:
                try:
                    fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        fh.close()
                        yield False
                        return
                    time.sleep(0.5)
        fh.write(str(os.getpid()))
        fh.flush()
        yield True
    except OSError:
        fh.close()
        yield False
        return
    finally:
        try:
            if IS_WINDOWS:
                import msvcrt

                with contextlib.suppress(OSError):
                    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh, fcntl.LOCK_UN)
        except (OSError, ValueError):
            pass
        fh.close()
