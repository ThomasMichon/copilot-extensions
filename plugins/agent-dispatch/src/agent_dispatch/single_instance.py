"""A crash-safe, cross-platform **single-instance lock** for the supervisor daemon.

The singleton supervisor must be *exactly one process per machine-and-environment*,
and -- critically -- a daemon that **crashes without cleaning up must not
permanently block its own restart**. A store lease (pin-not-failover) can't give
that: a random per-process holder that never releases would lock the scope
forever. An **OS advisory lock on a lock file** does exactly the right thing: the
kernel releases it automatically when the holding process dies, so a restart
reacquires cleanly, while a *live* second daemon is refused.

``SingleInstance.acquire`` takes a non-blocking exclusive lock (``fcntl.flock`` on
POSIX, ``msvcrt.locking`` on Windows). :func:`is_locked` is a cheap liveness probe
-- it tries to acquire and immediately releases, so ``supervise daemon-status`` /
``--ensure`` can ask "is a daemon already holding this scope?" without racing.
"""

from __future__ import annotations

import os
import re
from pathlib import Path


def lock_path_for(run_dir: Path, scope: str) -> Path:
    """The lock-file path for a supervisor ``scope`` under ``run_dir``."""
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", scope).strip("-") or "supervisor"
    return Path(run_dir) / f"{slug}.lock"


class SingleInstance:
    """A non-blocking exclusive lock over a lock file (OS-released on crash)."""

    def __init__(self, lock_path: str | Path):
        self.lock_path = Path(lock_path)
        self._fd: int | None = None

    def acquire(self) -> bool:
        """Try to take the lock. Returns ``True`` if acquired, ``False`` if another
        live process already holds it. Idempotent for the holding instance."""
        if self._fd is not None:
            return True
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(self.lock_path), os.O_CREAT | os.O_RDWR, 0o644)
        try:
            _lock(fd)
        except OSError:
            os.close(fd)
            return False
        self._fd = fd
        try:
            os.ftruncate(fd, 0)
            os.write(fd, str(os.getpid()).encode("ascii"))
            os.fsync(fd)
        except OSError:
            pass
        return True

    def release(self) -> None:
        """Release the lock (best-effort). The OS would release it on process exit
        anyway; this makes a clean shutdown immediate."""
        fd = self._fd
        if fd is None:
            return
        try:
            _unlock(fd)
        except OSError:
            pass
        finally:
            try:
                os.close(fd)
            except OSError:
                pass
            self._fd = None

    def __enter__(self) -> bool:
        return self.acquire()

    def __exit__(self, *exc: object) -> None:
        self.release()


def is_locked(lock_path: str | Path) -> bool:
    """Whether a live process currently holds the lock at ``lock_path``.

    Probes by attempting a non-blocking acquire: success means *nobody* held it
    (we release immediately and report ``False``); failure means a live holder
    exists (``True``). A missing file means unlocked.
    """
    probe = SingleInstance(lock_path)
    if probe.acquire():
        probe.release()
        return False
    return True


if os.name == "nt":  # pragma: no cover -- exercised on Windows only
    import msvcrt

    def _lock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

    def _unlock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _lock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)
