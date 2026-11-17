"""Exclusive same-owner attachment files with descriptor-pinned custody."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import stat
import sys
from types import TracebackType

from .attachment_import import MAX_ATTACHMENT_BYTES


class ExportFileError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass
class _CreatedFile:
    name: str
    descriptor: int | None
    identity: tuple[int, int] | None = None
    size: int = 0


def _identity(info: os.stat_result) -> tuple[int, int]:
    return info.st_dev, info.st_ino


def _uid() -> int:
    if sys.platform == "win32":
        raise ExportFileError("output_custody_unsupported")
    return os.getuid()


class ExclusivePair:
    """Retain every created file on failure; never overwrite, unlink or retry."""

    def __init__(self, directory: str, name: str) -> None:
        if (
            os.name != "posix"
            or not all(hasattr(os, flag) for flag in ("O_DIRECTORY", "O_NOFOLLOW"))
            or os.open not in os.supports_dir_fd
            or os.stat not in os.supports_dir_fd
            or os.stat not in os.supports_follow_symlinks
        ):
            raise ExportFileError("output_custody_unsupported")
        self.uid = _uid()
        self.names = (name, name + ".pub")
        self.effect_possible = False
        self._closed = False
        self._directories: list[int] = []
        self._links: list[tuple[int, str, int]] = []
        self._files: list[_CreatedFile] = []
        path = Path(directory).expanduser()
        if not path.is_absolute() or ".." in path.parts:
            raise ExportFileError("output_custody_invalid")
        if (
            not name or name in (".", "..") or "/" in name or "\\" in name
            or "\x00" in name
        ):
            raise ExportFileError("output_custody_invalid")
        flags = os.O_RDONLY | int(getattr(os, "O_DIRECTORY")) | int(getattr(os, "O_NOFOLLOW"))
        flags |= int(getattr(os, "O_CLOEXEC", 0))
        try:
            root = os.open(path.anchor, flags)
            self._directories.append(root)
            for component in path.parts[1:]:
                child = os.open(component, flags, dir_fd=self._directories[-1])
                self._links.append((self._directories[-1], component, child))
                self._directories.append(child)
            self.preflight()
        except BaseException:
            self._release()
            raise

    def __enter__(self) -> ExclusivePair:
        return self

    def _verify_directories(self) -> None:
        if self._closed or not self._directories:
            raise ExportFileError("output_custody_invalid")
        if _identity(os.fstat(self._directories[0])) != _identity(
            os.stat("/", follow_symlinks=False)
        ):
            raise ExportFileError("output_custody_invalid")
        for descriptor in self._directories:
            info = os.fstat(descriptor)
            sticky_root = info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)
            if (
                not stat.S_ISDIR(info.st_mode)
                or info.st_uid not in (0, self.uid)
                or info.st_mode & 0o022 and not sticky_root
            ):
                raise ExportFileError("output_custody_invalid")
        for parent, name, child in self._links:
            named = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if _identity(named) != _identity(os.fstat(child)):
                raise ExportFileError("output_custody_invalid")
        final = os.fstat(self._directories[-1])
        if final.st_uid != self.uid or stat.S_IMODE(final.st_mode) != 0o700:
            raise ExportFileError("output_custody_invalid")

    def _verify_files(self) -> None:
        for created in self._files:
            named = os.stat(
                created.name, dir_fd=self._directories[-1], follow_symlinks=False,
            )
            opened = (
                os.fstat(created.descriptor) if created.descriptor is not None else named
            )
            if any(
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or info.st_uid != self.uid
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_size != created.size
                or _identity(info) != created.identity
                for info in (named, opened)
            ):
                raise ExportFileError("output_custody_invalid")

    def verify(self) -> None:
        self._verify_directories()
        self._verify_files()

    def preflight(self) -> None:
        self.verify()
        for name in self.names:
            try:
                os.stat(name, dir_fd=self._directories[-1], follow_symlinks=False)
            except FileNotFoundError:
                continue
            raise ExportFileError("destination_exists")

    def materialize(self, private: bytes, public: bytes) -> None:
        if self.effect_possible or any(
            not isinstance(value, bytes) or not 1 <= len(value) <= MAX_ATTACHMENT_BYTES
            for value in (private, public)
        ):
            raise ExportFileError("invalid_export")
        self.preflight()
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | int(getattr(os, "O_NOFOLLOW"))
        flags |= int(getattr(os, "O_CLOEXEC", 0)) | int(getattr(os, "O_NONBLOCK", 0))
        for name in self.names:
            self.effect_possible = True
            descriptor = os.open(name, flags, 0o600, dir_fd=self._directories[-1])
            created = _CreatedFile(name, descriptor)
            self._files.append(created)
            created.identity = _identity(os.fstat(descriptor))
            self.verify()
        # Acquire both empty files before writing either attachment.
        for created, data in zip(self._files, (private, public)):
            while created.size < len(data):
                self.verify()
                assert created.descriptor is not None
                count = os.write(created.descriptor, data[created.size:])
                if not 0 < count <= len(data) - created.size:
                    raise ExportFileError("export_unconfirmed")
                created.size += count
            self.verify()
            assert created.descriptor is not None
            os.fsync(created.descriptor)
        os.fsync(self._directories[-1])
        self.verify()

    def _release(self) -> BaseException | None:
        failure: BaseException | None = None
        for created in self._files:
            descriptor, created.descriptor = created.descriptor, None
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except BaseException as error:
                    failure = failure or error
        for descriptor in reversed(self._directories):
            try:
                os.close(descriptor)
            except BaseException as error:
                failure = failure or error
        self._directories.clear()
        self._closed = True
        return failure

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        failure: BaseException | None = None
        for created in self._files:
            descriptor, created.descriptor = created.descriptor, None
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except BaseException as error:
                    failure = failure or error
        if exc_type is None and failure is None:
            try:
                self.verify()
            except BaseException as error:
                failure = error
        release_failure = self._release()
        failure = failure or release_failure
        if exc_type is None and failure is not None:
            raise failure
