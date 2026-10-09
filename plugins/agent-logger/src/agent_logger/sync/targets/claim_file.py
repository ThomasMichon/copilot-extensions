"""Kernel-owned claim files: no pathname-based temporary-file deletion."""

from __future__ import annotations

import ctypes
import errno
import os
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import BinaryIO, Protocol

class ClaimFile(Protocol):
    stream: BinaryIO

    @property
    def file_id(self) -> tuple[int, int]: ...

    def publish(self, destination: Path) -> None: ...


class LinuxClaimFile:
    """Publish an O_TMPFILE inode while keeping its descriptor alive."""

    def __init__(self, directory: Path) -> None:
        temporary_flag = getattr(os, "O_TMPFILE", 0)
        if not temporary_flag:
            raise OSError(errno.ENOTSUP, "anonymous claim files are unsupported")
        self.directory = directory
        self._published = False
        with ExitStack() as stack:
            self.directory_fd = os.open(
                directory,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
            )
            stack.callback(os.close, self.directory_fd)
            fd = os.open(
                ".", os.O_RDWR | temporary_flag | os.O_CLOEXEC,
                0o600, dir_fd=self.directory_fd,
            )
            try:
                self.stream = os.fdopen(fd, "w+b")
            except BaseException:
                os.close(fd)
                raise
            stack.enter_context(self.stream)
            self._resources = stack.pop_all()

    @property
    def file_id(self) -> tuple[int, int]:
        info = os.fstat(self.stream.fileno())
        return info.st_dev, info.st_ino

    def publish(self, destination: Path) -> None:
        if self._published:
            raise RuntimeError("claim file has already been published")
        if destination.parent != self.directory:
            raise ValueError("claim publication must stay in its opened directory")
        self.stream.flush()
        os.fchmod(self.stream.fileno(), 0o644)
        os.fsync(self.stream.fileno())
        library = ctypes.CDLL(None, use_errno=True)
        link_at = getattr(library, "linkat", None)
        if link_at is None:
            raise OSError(errno.ENOTSUP, "descriptor-based claim linking is unsupported")
        link_at.argtypes = [
            ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
        ]
        link_at.restype = ctypes.c_int
        source = os.fsencode(f"/proc/self/fd/{self.stream.fileno()}")
        if link_at(-100, source, self.directory_fd, os.fsencode(destination.name), 0x400):
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error), str(destination))
        self._published = True
        os.fsync(self.directory_fd)

    def close(self) -> None:
        self._resources.close()


@contextmanager
def create_claim_file(directory: Path) -> Iterator[ClaimFile]:
    if os.name == "nt":
        from agent_logger.sync.targets.windows_claim_file import WindowsClaimFile

        with WindowsClaimFile(directory) as claim:
            yield claim
    else:
        claim = LinuxClaimFile(directory)
        try:
            yield claim
        finally:
            claim.close()
