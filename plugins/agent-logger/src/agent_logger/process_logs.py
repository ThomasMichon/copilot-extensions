"""Bounded, archive-transparent reads of Copilot process-log evidence."""

from __future__ import annotations

import errno
import gzip
import os
import stat
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO

DEFAULT_MAX_LINE_BYTES = 1024 * 1024


def _is_log_name(name: str) -> bool:
    return (
        name.startswith("process-")
        and name.endswith(".log")
        and len(name) > len("process-.log")
        and "/" not in name
        and "\\" not in name
    )


@contextmanager
def _open_regular(path: Path) -> Iterator[BinaryIO]:
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise ValueError(f"process-log evidence is not a regular file: {path}")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        with os.fdopen(fd, "rb") as stream:
            fd = -1
            opened = os.fstat(stream.fileno())
            if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
                raise ValueError(f"process-log evidence changed while opening: {path}")
            yield stream
    finally:
        if fd != -1:
            os.close(fd)


def _zip_logs(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    logs: list[zipfile.ZipInfo] = []
    names: set[str] = set()
    for info in archive.infolist():
        if info.is_dir():
            continue
        if not _is_log_name(info.filename):
            leaf = info.filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
            if _is_log_name(leaf):
                raise ValueError(f"process-log ZIP members must be flat: {info.filename!r}")
            continue
        if stat.S_ISLNK(info.external_attr >> 16):
            raise ValueError(f"process-log ZIP member is a symlink: {info.filename!r}")
        if info.filename in names:
            raise ValueError(f"duplicate process-log ZIP member: {info.filename!r}")
        names.add(info.filename)
        logs.append(info)
    return sorted(logs, key=lambda info: info.filename)


@dataclass(frozen=True)
class ProcessLogRef:
    """One physical observation; storage aliases retain the same logical name."""

    path: Path
    member: str | None = None
    # Set only by `iter_process_log_refs` on POSIX: the directory this ref's
    # `path` was discovered directly under, re-opened with O_NOFOLLOW at read
    # time instead of trusting `path`'s parent component by name. Excluded
    # from equality/repr so refs built directly (as every existing caller and
    # test does) keep comparing solely on `(path, member)`.
    verified_root: Path | None = field(default=None, compare=False, repr=False)

    def __post_init__(self) -> None:
        if self.member is not None:
            if self.path.suffix != ".zip" or not _is_log_name(self.member):
                raise ValueError("a process-log member requires a ZIP and a flat log name")
        elif not _is_log_name(self.path.name.removesuffix(".gz")):
            raise ValueError(f"unsupported process-log evidence path: {self.path}")

    @property
    def logical_name(self) -> str:
        return self.member if self.member is not None else self.path.name.removesuffix(".gz")

    def iter_lines(self, *, max_line_bytes: int = DEFAULT_MAX_LINE_BYTES) -> Iterator[str]:
        """Read UTF-8 lines without extracting archives or allocating an entire log."""
        if isinstance(max_line_bytes, bool) or not isinstance(max_line_bytes, int):
            raise ValueError("max_line_bytes must be a positive integer")
        if max_line_bytes <= 0:
            raise ValueError("max_line_bytes must be a positive integer")
        if self.verified_root is not None and _supports_dir_fd():
            with (
                _open_root_dir(self.verified_root) as root_fd,
                _open_regular_at(root_fd, self.path.name) as raw,
            ):
                yield from self._read_opened(raw, max_line_bytes)
        else:
            with _open_regular(self.path) as raw:
                yield from self._read_opened(raw, max_line_bytes)

    def _read_opened(self, raw: BinaryIO, max_line_bytes: int) -> Iterator[str]:
        if self.member is not None:
            with zipfile.ZipFile(raw) as archive:
                info = next(
                    (info for info in _zip_logs(archive) if info.filename == self.member),
                    None,
                )
                if info is None:
                    raise ValueError(f"process-log ZIP member is missing: {self.member!r}")
                with archive.open(info) as stream:
                    yield from _lines(stream, max_line_bytes)
        elif self.path.suffix == ".gz":
            with gzip.GzipFile(fileobj=raw, mode="rb") as stream:
                yield from _lines(stream, max_line_bytes)
        else:
            yield from _lines(raw, max_line_bytes)


def _lines(stream: BinaryIO, max_line_bytes: int) -> Iterator[str]:
    while True:
        line = stream.readline(max_line_bytes + 1)
        if not line:
            return
        if len(line) > max_line_bytes:
            raise ValueError(f"process-log line exceeds {max_line_bytes} bytes")
        yield line.decode("utf-8")


def _supports_dir_fd() -> bool:
    # POSIX only -- mirrors the directory-fd gate `_fsync_directory` already
    # uses elsewhere in this plugin's sync targets. Windows has no equivalent
    # "openat" primitive, so the root here is re-resolved by path instead; see
    # `iter_process_log_refs`'s docstring for the resulting platform gap.
    return os.name != "nt"


@contextmanager
def _open_root_dir(log_root: Path) -> Iterator[int]:
    """Open ``log_root`` once and verify its identity, so later per-entry
    traversal is bound to this directory handle rather than re-resolving the
    root path -- a swap of the final root component (e.g. onto a symlink)
    between the initial check and later entry opens would otherwise let an
    attacker redirect enumeration/reads outside the configured root."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_DIRECTORY", 0)
    try:
        fd = os.open(log_root, flags)
    except FileNotFoundError:
        raise
    except NotADirectoryError as exc:
        raise ValueError(f"process-log source is not a directory: {log_root}") from exc
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            # A symlinked root rejected by O_NOFOLLOW: no dedicated subclass
            # exists for this errno, so it only arrives as a bare OSError.
            raise ValueError(f"process-log source is not a directory: {log_root}") from exc
        # Permission errors, I/O errors, descriptor exhaustion, overlong
        # paths, etc. are genuine, actionable failures -- never relabel them
        # as "not a directory".
        raise
    try:
        if not stat.S_ISDIR(os.fstat(fd).st_mode):
            raise ValueError(f"process-log source is not a directory: {log_root}")
        yield fd
    finally:
        os.close(fd)


@contextmanager
def _open_regular_at(dir_fd: int, name: str) -> Iterator[BinaryIO]:
    """Open a regular file by name within a pinned, already-verified directory
    handle, refusing to follow a symlinked entry."""
    before = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    if not stat.S_ISREG(before.st_mode):
        raise ValueError(f"process-log evidence is not a regular file: {name}")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(name, flags, dir_fd=dir_fd)
    try:
        with os.fdopen(fd, "rb") as stream:
            fd = -1
            opened = os.fstat(stream.fileno())
            if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
                raise ValueError(f"process-log evidence changed while opening: {name}")
            yield stream
    finally:
        if fd != -1:
            os.close(fd)


def iter_process_log_refs(log_root: Path) -> Iterator[ProcessLogRef]:
    """Enumerate live, gzip, and flat ZIP observations without silently
    deduplicating.

    On POSIX, directory listing, per-ZIP header reads, and every later
    ``ProcessLogRef.iter_lines()`` call on a returned ref all reopen
    ``log_root`` itself with ``O_NOFOLLOW`` rather than trusting a path
    component by name -- so a swap of ``log_root`` onto a symlink, whether
    before enumeration or any time after (including well after a ref was
    returned), cannot redirect traversal or a later read outside the
    configured root. Windows has no "openat" equivalent, so there the root
    is re-resolved by path for each access; this is a known, platform-
    specific gap rather than an equivalent guarantee.

    A directory with no raw/gzip/ZIP process-log evidence -- including one
    containing only unrelated files, or a ZIP with no supported members --
    yields no refs. This is ordinary, not an error: absence of evidence is
    not evidence of a missing or misconfigured source.
    """
    if _supports_dir_fd():
        with _open_root_dir(log_root) as root_fd:
            entries = sorted(os.scandir(root_fd), key=lambda entry: entry.name)
            for entry in entries:
                name = entry.name
                if _is_log_name(name.removesuffix(".gz")):
                    yield ProcessLogRef(log_root / name, verified_root=log_root)
                elif name.endswith(".zip"):
                    with _open_regular_at(root_fd, name) as raw, zipfile.ZipFile(raw) as archive:
                        # Resolve member names while the archive is still
                        # open, then close both the ZIP and its file
                        # descriptor before yielding -- yielding mid-`with`
                        # would otherwise pin the archive open for as long as
                        # the caller takes to consume (or abandon) the
                        # generator, which can block rotation/compaction.
                        names = [info.filename for info in _zip_logs(archive)]
                    for member in names:
                        yield ProcessLogRef(log_root / name, member, verified_root=log_root)
        return
    root_stat = log_root.lstat()
    if not stat.S_ISDIR(root_stat.st_mode):
        raise ValueError(f"process-log source is not a directory: {log_root}")
    for path in sorted(log_root.iterdir()):
        if _is_log_name(path.name.removesuffix(".gz")):
            yield ProcessLogRef(path)
        elif path.suffix == ".zip":
            with _open_regular(path) as raw, zipfile.ZipFile(raw) as archive:
                names = [info.filename for info in _zip_logs(archive)]
            for name in names:
                yield ProcessLogRef(path, name)
