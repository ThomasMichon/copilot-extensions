"""Directory-bound process-log payload publication, including directory creation."""

from __future__ import annotations

import os
import stat
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import BinaryIO

from agent_logger.sync.archive_directory import (
    ArchiveDirectory, _posix_flags, _windows_directory,
)
from agent_logger.sync.provenance import (
    durable_replace, open_regular_no_follow, windows_extended_path,
)


class PublicationDirectory(ArchiveDirectory):
    """Write only bare filenames beneath one live destination boundary."""

    def stat(self, name: str) -> os.stat_result:
        if self.fd is None:
            return os.stat(
                windows_extended_path(self.path / self._name(name)), follow_symlinks=False,
            )
        return super().stat(name)

    def create(self, name: str) -> BinaryIO:
        name = self._name(name)
        if self.fd is None:
            return open(windows_extended_path(self.path / name), "xb")
        fd = os.open(
            name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o666, dir_fd=self.fd,
        )
        try:
            return os.fdopen(fd, "wb")
        except BaseException:
            os.close(fd)
            raise

    def metadata(self, stream: BinaryIO, name: str, source: os.stat_result) -> None:
        if self.fd is not None:
            os.utime(stream.fileno(), ns=(source.st_atime_ns, source.st_mtime_ns))
            os.fchmod(stream.fileno(), stat.S_IMODE(source.st_mode))
        else:
            path = windows_extended_path(self.path / self._name(name))
            os.utime(path, ns=(source.st_atime_ns, source.st_mtime_ns))
            os.chmod(path, stat.S_IMODE(source.st_mode))

    def replace(self, temporary: str, destination: str) -> None:
        temporary, destination = self._name(temporary), self._name(destination)
        if self.fd is None:
            durable_replace(self.path / temporary, self.path / destination)
        else:
            os.replace(temporary, destination, src_dir_fd=self.fd, dst_dir_fd=self.fd)
            os.fsync(self.fd)

    def unlink(self, name: str) -> None:
        name = self._name(name)
        try:
            if self.fd is None:
                path = self.path / name
                try:
                    os.unlink(windows_extended_path(path))
                except PermissionError:
                    # A copied read-only mode must not strand a rejected temp.
                    with open_regular_no_follow(path, allow_writes=False):
                        os.chmod(windows_extended_path(path), stat.S_IREAD | stat.S_IWRITE)
                    os.unlink(windows_extended_path(path))
            else:
                os.unlink(name, dir_fd=self.fd)
        except FileNotFoundError:
            pass


@contextmanager
def publication_directory(path: Path) -> Iterator[PublicationDirectory]:
    """Create and pin a lexical directory chain without following redirects.

    POSIX child creation and all later I/O are descriptor-relative. Windows
    keeps each parent unrenamable before creating/opening the next child.
    No unchecked pathname mkdir precedes this boundary.
    """
    absolute = path.expanduser().absolute()
    if ".." in absolute.parts:
        raise OSError(f"unsafe publication directory: {path}")
    with ExitStack() as stack:
        current = Path(absolute.anchor)
        fd: int | None = None
        if os.name == "nt":
            stack.enter_context(_windows_directory(current))
            for component in absolute.parts[1:]:
                current /= component
                try:
                    os.mkdir(windows_extended_path(current))
                except FileExistsError:
                    pass
                stack.enter_context(_windows_directory(current))
        else:
            flags = _posix_flags("O_DIRECTORY", "O_NOFOLLOW")
            fd = os.open(current, flags)
            stack.callback(os.close, fd)
            for component in absolute.parts[1:]:
                try:
                    os.mkdir(component, dir_fd=fd)
                except FileExistsError:
                    pass
                os.fsync(fd)
                fd = os.open(component, flags, dir_fd=fd)
                stack.callback(os.close, fd)
        yield PublicationDirectory(absolute, fd)
