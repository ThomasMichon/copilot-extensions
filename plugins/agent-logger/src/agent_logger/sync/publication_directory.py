"""Directory-bound process-log payload publication, including directory creation."""

from __future__ import annotations

import os
import stat
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from agent_logger.sync.archive_directory import (
    ArchiveDirectory, _posix_flags, _windows_directory,
)
from agent_logger.sync.provenance import (
    durable_replace, open_regular_no_follow, windows_extended_path,
)


@dataclass(frozen=True)
class PublicationDirectory(ArchiveDirectory):
    """Write only bare filenames beneath one live destination boundary."""

    private: bool = False

    def open(self, name: str, *, protected: bool = False) -> BinaryIO:
        stream = super().open(name, protected=protected)
        try:
            if self.private and self.fd is None:
                from agent_logger.sync.publication_windows import check_private

                check_private(self.path / self._name(name))
            return stream
        except BaseException:
            stream.close()
            raise

    def stat(self, name: str) -> os.stat_result:
        if self.fd is None:
            return os.stat(
                windows_extended_path(self.path / self._name(name)), follow_symlinks=False,
            )
        return super().stat(name)

    def create(self, name: str, *, mode: int = 0o666) -> BinaryIO:
        name = self._name(name)
        if self.fd is None:
            if mode == 0o666:
                return open(windows_extended_path(self.path / name), "xb")
            if mode != 0o600:
                raise ValueError("private publication requires mode 0600")
            from agent_logger.sync.publication_windows import create_private

            fd = create_private(self.path / name)
        else:
            fd = os.open(
                name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW"),
                mode, dir_fd=self.fd,
            )
        try:
            return os.fdopen(fd, "wb")
        except BaseException:
            os.close(fd)
            raise

    def info(self) -> os.stat_result:
        return self.path.stat() if self.fd is None else os.fstat(self.fd)

    def check_private(self) -> None:
        if self.fd is None:
            from agent_logger.sync.publication_windows import check_private

            check_private(self.path)
        else:
            info = self.info()
            if info.st_uid != getattr(os, "geteuid")() or stat.S_IMODE(info.st_mode) & 0o077:
                raise PermissionError("publication directory must be private and owner-controlled")

    def sync(self) -> None:
        if self.fd is None:
            from agent_logger.sync.publication_windows import sync_directory

            sync_directory(self.path)
        else:
            os.fsync(self.fd)

    def mkdir(self, name: str, *, private: bool = False) -> None:
        name = self._name(name)
        if self.fd is None:
            if private:
                from agent_logger.sync.publication_windows import mkdir_private

                mkdir_private(self.path / name)
            else:
                os.mkdir(windows_extended_path(self.path / name))
        else:
            os.mkdir(name, mode=0o700 if private else 0o777, dir_fd=self.fd)

    def rmdir(self, name: str) -> None:
        name = self._name(name)
        if self.fd is None:
            os.rmdir(windows_extended_path(self.path / name))
        else:
            os.rmdir(name, dir_fd=self.fd)

    def metadata(self, stream: BinaryIO, name: str, source: os.stat_result) -> None:
        if self.fd is not None:
            os.utime(stream.fileno(), ns=(source.st_atime_ns, source.st_mtime_ns))
            getattr(os, "fchmod")(stream.fileno(), stat.S_IMODE(source.st_mode))
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
def publication_directory(
    path: Path, *, create: bool = True,
    inspect: Callable[[PublicationDirectory], None] | None = None,
    durable: bool = False, private: bool = False,
    durable_root: Path | None = None,
) -> Iterator[PublicationDirectory]:
    """Create and pin a lexical directory chain without following redirects.

    POSIX child creation and all later I/O are descriptor-relative. Windows
    keeps each parent unrenamable before creating/opening the next child.
    No unchecked pathname mkdir precedes this boundary. Readback uses
    ``create=False``: missing children fail without mkdir or durability writes,
    and successful exit requires the pinned ancestry to retain its lexical names.
    An optional inspector examines each admitted child through that same pin.
    Explicit durable/private publication qualifies its terminal directory and
    newly created entries; ordinary callers retain their original semantics.
    Native durable writes require an explicit owner root with an existing
    parent. Every entry at/below it is reflushed, including on an exact retry.
    """
    absolute = path.expanduser().absolute()
    if ".." in absolute.parts:
        raise OSError(f"unsafe publication directory: {path}")
    floor = None if durable_root is None else durable_root.expanduser().absolute()
    if os.name == "nt" and durable and create:
        if (
            floor is None or floor == floor.parent or ".." in floor.parts
            or (absolute != floor and floor not in absolute.parents)
        ):
            raise ValueError("native durable publication requires an explicit containing owner root")
    with ExitStack() as stack:
        current = Path(absolute.anchor)
        fd: int | None = None
        readback_edges: list[tuple[int, str, int]] = []
        if os.name == "nt":
            stack.enter_context(_windows_directory(current))
            for component in absolute.parts[1:]:
                parent = PublicationDirectory(current, None)
                current /= component
                owned = floor is not None and (current == floor or floor in current.parents)
                if create and (not durable or owned):
                    if durable:
                        parent.sync()
                    try:
                        parent.mkdir(component, private=private or durable)
                    except FileExistsError:
                        pass
                    if durable:
                        parent.sync()
                stack.enter_context(_windows_directory(current))
                if inspect is not None:
                    inspect(PublicationDirectory(current, None))
        else:
            flags = _posix_flags("O_DIRECTORY", "O_NOFOLLOW")
            fd = os.open(current, flags)
            stack.callback(os.close, fd)
            for component in absolute.parts[1:]:
                current /= component
                if create:
                    try:
                        os.mkdir(component, mode=0o700 if private else 0o777, dir_fd=fd)
                    except FileExistsError:
                        pass
                    os.fsync(fd)
                parent_fd = fd
                fd = os.open(component, flags, dir_fd=parent_fd)
                stack.callback(os.close, fd)
                if not create:
                    readback_edges.append((parent_fd, component, fd))
                if inspect is not None:
                    inspect(PublicationDirectory(current, fd))
        directory = PublicationDirectory(absolute, fd, private)
        if private:
            directory.check_private()
        if durable and create:
            directory.sync()
        yield directory
        for parent_fd, component, child_fd in readback_edges:
            named = os.stat(component, dir_fd=parent_fd, follow_symlinks=False)
            held = os.fstat(child_fd)
            if (named.st_dev, named.st_ino) != (held.st_dev, held.st_ino):
                raise ValueError("process-log readback directory changed during verification")
