"""Execution-owned, crash-releasing custody for one local operation."""

from __future__ import annotations

import errno
import os
import stat
import sys
import threading
from pathlib import Path
from typing import BinaryIO

from .lease import _acquire_os_lock

_guard = threading.Lock()
_holders: dict[tuple[int, int, int], OperationLock] = {}


def _before_fork() -> None:
    _guard.acquire()


def _after_fork_parent() -> None:
    _guard.release()


def _after_fork() -> None:
    global _guard, _holders
    first_error: OSError | None = None
    try:
        for holder in _holders.values():
            if holder._file is not None:
                try:
                    holder._file.close()
                except OSError as error:
                    if first_error is None:
                        first_error = error
    finally:
        _guard = threading.Lock()
        _holders = {}
    if first_error is not None:
        raise first_error


if hasattr(os, "register_at_fork"):
    os.register_at_fork(
        before=_before_fork, after_in_parent=_after_fork_parent, after_in_child=_after_fork,
    )


class OperationLockedError(RuntimeError):
    """Another execution owns this operation's lock."""


class OperationOwnershipError(RuntimeError):
    """Operation custody or its lock-file identity cannot be verified."""


def _plain_file(path: Path) -> os.stat_result:
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode) or path.is_symlink()
        or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    ):
        raise OperationOwnershipError(f"operation lock is not a plain file: {path}")
    return info


def _assert_linux_flock(stream: BinaryIO) -> None:
    if sys.platform != "linux":
        raise OperationOwnershipError("inherited native custody requires Linux descriptor evidence")
    try:
        with open(f"/proc/self/fdinfo/{stream.fileno()}", encoding="ascii") as source:
            document = source.read(8193)
        if len(document) > 8192:
            raise OperationOwnershipError("inherited operation descriptor evidence exceeds its bound")
        records = [line.split() for line in document.splitlines() if line.startswith("lock:")]
        info = os.fstat(stream.fileno())
        if (
            len(records) != 1 or len(records[0]) != 9
            or records[0][2:5] != ["FLOCK", "ADVISORY", "WRITE"]
            or records[0][7:] != ["0", "EOF"]
        ):
            raise OperationOwnershipError("inherited descriptor does not hold an exclusive whole-file flock")
        major, minor, inode = records[0][6].split(":")
        if (
            int(major, 16) != os.major(info.st_dev) or int(minor, 16) != os.minor(info.st_dev)
            or int(inode) != info.st_ino
        ):
            raise OperationOwnershipError("inherited descriptor lock identity differs")
    except (OSError, ValueError, UnicodeError) as error:
        raise OperationOwnershipError("inherited operation descriptor evidence unavailable") from error


class OperationLock:
    """Serialize one operation, not a daemon lifetime or a runtime installation.

    The kernel owns exclusion across processes. A process-local reservation also
    excludes separate threads/handles on Windows, whose byte locks are per
    process. The file stays in place; neither age nor a PID authorizes reclaim.
    Closing the owning descriptor releases custody, including on process death.
    Callers must separately journal any work that outlives that custody.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._file: BinaryIO | None = None
        self._owner: tuple[int, threading.Thread] | None = None
        self._key: tuple[int, int, int] | None = None
        self._inherited = False

    @classmethod
    def from_inherited(cls, path: Path, fd: int) -> OperationLock:
        """Consume a positively flock-owned Linux descriptor without releasing custody."""
        if sys.platform != "linux" or type(fd) is not int or fd < 3:
            raise OperationOwnershipError("native custody requires a non-stdio Linux descriptor")
        holder = cls(path)
        with _guard:
            if not holder.path.is_absolute() or holder.path.parent.resolve(strict=True) != holder.path.parent:
                raise OperationOwnershipError("operation lock requires an absolute, unlinked parent")
            duplicate = os.dup(fd)
            try:
                stream = os.fdopen(duplicate, "r+b", buffering=0)
            except BaseException:
                os.close(duplicate)
                raise
            try:
                info = holder._assert_file(stream)
                _assert_linux_flock(stream)
                key = (os.getpid(), info.st_dev, info.st_ino)
                if key in _holders:
                    raise OperationLockedError(f"another execution holds {holder.path}")
                os.close(fd)
            except BaseException:
                stream.close()
                raise
            holder._file = stream
            holder._key = key
            holder._owner = (os.getpid(), threading.current_thread())
            holder._inherited = True
            _holders[key] = holder
        return holder

    def acquire(self) -> None:
        with _guard:
            if self._file is not None:
                raise OperationOwnershipError("operation custody is already acquired")
            if not self.path.is_absolute() or self.path.parent.resolve(strict=True) != self.path.parent:
                raise OperationOwnershipError("operation lock requires an absolute, unlinked parent")
            try:
                existing = _plain_file(self.path)
            except FileNotFoundError:
                pass
            else:
                if (os.getpid(), existing.st_dev, existing.st_ino) in _holders:
                    raise OperationLockedError(f"another execution holds {self.path}")
            fd = os.open(self.path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
            stream = os.fdopen(fd, "r+b", buffering=0)
            key = None
            reserved = False
            try:
                info = self._assert_file(stream)
                key = (os.getpid(), info.st_dev, info.st_ino)
                if key in _holders:
                    raise OperationLockedError(f"another execution holds {self.path}")
                _holders[key] = self
                reserved = True
                self._file = stream
                self._key = key
                self._owner = (os.getpid(), threading.current_thread())
                try:
                    _acquire_os_lock(stream)
                except OSError as error:
                    if error.errno in (errno.EACCES, errno.EAGAIN):
                        raise OperationLockedError(f"another process holds {self.path}") from error
                    raise
                self._assert_file(stream)
            except BaseException:
                stream.close()
                if reserved and key is not None:
                    _holders.pop(key, None)
                    self._file = None
                    self._key = None
                    self._owner = None
                    self._inherited = False
                raise

    def _owned_file(self) -> BinaryIO:
        stream = self._file
        if self._owner != (os.getpid(), threading.current_thread()) or stream is None:
            raise OperationOwnershipError("operation belongs to a different execution or is not held")
        return stream

    def _assert_file(self, stream: BinaryIO) -> os.stat_result:
        if self.path.parent.resolve(strict=True) != self.path.parent:
            raise OperationOwnershipError("operation lock parent changed")
        info = os.fstat(stream.fileno())
        if not os.path.samestat(info, _plain_file(self.path)):
            raise OperationOwnershipError("operation lock file was replaced")
        return info

    def assert_owned(self) -> None:
        with _guard:
            self._assert_file(self._owned_file())
            key = self._key
            if key is None or _holders.get(key) is not self:
                raise OperationOwnershipError("operation's local reservation changed")
            if self._inherited:
                _assert_linux_flock(self._owned_file())

    def release(self) -> None:
        with _guard:
            if self._file is None:
                return
            stream = self._owned_file()
            stream.close()
            key = self._key
            if key is not None and _holders.get(key) is self:
                del _holders[key]
            self._file = None
            self._key = None
            self._owner = None
            self._inherited = False

    def __enter__(self) -> OperationLock:
        self.acquire()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.release()
