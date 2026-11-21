"""Process-owned writer serialization for the shared data-directory checkpoint."""

from __future__ import annotations

import errno
import os
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
from typing import ParamSpec, Protocol, TypeVar, cast

P = ParamSpec("P")
R = TypeVar("R")


class _WindowsFileLock(Protocol):
    """The msvcrt surface absent from non-Windows checker stubs."""

    LK_NBLCK: int
    LK_UNLCK: int

    def locking(self, fd: int, mode: int, count: int, /) -> None: ...


class _PosixFileLock(Protocol):
    """The fcntl surface absent from Windows checker stubs."""

    LOCK_EX: int
    LOCK_NB: int
    LOCK_UN: int

    def flock(self, fd: int, operation: int, /) -> None: ...

_guard = threading.Lock()
_threads: dict[Path, threading.RLock] = {}
_local = threading.local()
_owned_fds: set[int] = set()


class SourcePipelineBusy(RuntimeError):
    """Another participating writer owns this data directory."""


def _before_fork() -> None:
    _guard.acquire()


def _after_fork_parent() -> None:
    _guard.release()


def _after_fork() -> None:
    global _guard, _threads, _local, _owned_fds
    # Close inherited descriptions without unlocking the parent's shared flock.
    for fd in _owned_fds:
        os.close(fd)
    _guard = threading.Lock()
    _threads = {}
    _local = threading.local()
    _owned_fds = set()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(
        before=_before_fork, after_in_parent=_after_fork_parent, after_in_child=_after_fork,
    )


def _try_lock(fd: int) -> bool:
    try:
        if os.name == "nt":
            import msvcrt

            windows_lock = cast(_WindowsFileLock, msvcrt)
            os.lseek(fd, 0, os.SEEK_SET)
            windows_lock.locking(fd, windows_lock.LK_NBLCK, 1)
        else:
            import fcntl

            posix_lock = cast(_PosixFileLock, fcntl)
            posix_lock.flock(fd, posix_lock.LOCK_EX | posix_lock.LOCK_NB)
    except OSError as error:
        busy = {errno.EACCES, errno.EAGAIN} if os.name == "nt" else {
            errno.EAGAIN, errno.EWOULDBLOCK,
        }
        if error.errno in busy:
            return False
        raise
    return True


def _unlock(fd: int) -> None:
    if os.name == "nt":
        import msvcrt

        windows_lock = cast(_WindowsFileLock, msvcrt)
        os.lseek(fd, 0, os.SEEK_SET)
        windows_lock.locking(fd, windows_lock.LK_UNLCK, 1)
    else:
        import fcntl

        posix_lock = cast(_PosixFileLock, fcntl)
        posix_lock.flock(fd, posix_lock.LOCK_UN)


@contextmanager
def source_writer(
    data_dir: Path, *, wait: bool = False, cancel_check: Callable[[], None] | None = None,
    allow_reentry: bool = True,
) -> Iterator[None]:
    """Own acquisition through checkpoint publication; readers do not participate.

    All writers using one JSON state file must join, including hosted ingest and
    retirement callers. This cannot fence older, nonparticipating writers.
    ``allow_reentry=False`` refuses even this thread's existing ownership,
    independently of ``wait``, for operations that must not revoke active work.
    """
    owner_pid = os.getpid()
    root = Path(data_dir).resolve()
    with _guard:
        mutex = _threads.setdefault(root, threading.RLock())

    def retry() -> None:
        if not wait:
            raise SourcePipelineBusy(f"Source writer already active for {root}")
        if cancel_check:
            cancel_check()
        time.sleep(0.1)

    while not mutex.acquire(blocking=False):
        retry()
    try:
        if cancel_check:
            cancel_check()
        active = getattr(_local, "active", None)
        if active is None:
            active = _local.active = set()
        if root in active:
            if not allow_reentry:
                raise SourcePipelineBusy(f"Source writer already active for {root}")
            yield
            return
        root.mkdir(parents=True, exist_ok=True)
        # A fork must see either no descriptor or a registered descriptor to close.
        with _guard:
            fd = os.open(root / ".source-writer-v1.lock", os.O_CREAT | os.O_RDWR, 0o600)
            _owned_fds.add(fd)
        locked = False
        try:
            while not _try_lock(fd):
                retry()
            locked = True
            if cancel_check:
                cancel_check()
            active.add(root)
            try:
                yield
            finally:
                if os.getpid() == owner_pid:
                    active.remove(root)
        finally:
            if os.getpid() == owner_pid:
                try:
                    if locked:
                        _unlock(fd)
                finally:
                    with _guard:
                        _owned_fds.remove(fd)
                        os.close(fd)
    finally:
        if os.getpid() == owner_pid:
            mutex.release()


def own_reindex(function: Callable[P, R]) -> Callable[P, R]:
    """Acquire before even resolving connectors or loading the shared state."""
    @wraps(function)
    def call(*args: P.args, **kwargs: P.kwargs) -> R:
        from agent_index.index_config import IndexConfig

        config = kwargs.get("config")
        if config is None:
            config = kwargs["config"] = IndexConfig()
        with source_writer(cast(IndexConfig, config).data_dir):
            return function(*args, **kwargs)
    return call
