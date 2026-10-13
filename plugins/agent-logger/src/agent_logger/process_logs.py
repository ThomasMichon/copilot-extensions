"""Bounded, archive-transparent reads of Copilot process-log evidence."""

from __future__ import annotations

import errno
import gzip
import os
import stat
import zipfile
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO

if TYPE_CHECKING:
    from agent_logger.sync.archive_directory import ArchiveDirectory

DEFAULT_MAX_LINE_BYTES = 1024 * 1024


def _is_log_name(name: str) -> bool:
    return (
        name.startswith("process-")
        and name.endswith(".log")
        and len(name) > len("process-.log")
        and "/" not in name
        and "\\" not in name
    )


def is_process_log_candidate(name: str) -> bool:
    """Whether *name* (a bare filename, not a path) is worth transferring as
    process-log evidence: a live or gzip ``process-*.log`` file, or a
    ``.zip`` archive that may contain flat ``process-*.log`` members.

    A transfer-time predicate, not a validity check: unlike
    :func:`iter_process_log_refs`, this never opens the file, so a ``.zip``
    with no supported members (or none at all) still passes here -- the
    reader's own enumeration is what yields zero refs for it, not this.

    Rejects any ``name`` containing a path separator (POSIX ``/`` or
    Windows ``\\``) outright -- a bare filename is the documented contract,
    and without this check a path-like value such as ``../outside.zip``
    would still satisfy the ``.zip`` branch's plain ``str.endswith`` test.
    """
    if "/" in name or "\\" in name:
        return False
    return _is_log_name(name.removesuffix(".gz")) or name.endswith(".zip")


@contextmanager
def _open_regular(path: Path) -> Iterator[BinaryIO]:
    with _process_log_directory(path.parent) as directory:
        with _open_regular_in(directory, path.name) as stream:
            yield stream


def _open_process_log_entry(
    directory: ArchiveDirectory, name: str, *, protected: bool = False,
) -> BinaryIO:
    try:
        return directory.open(name, protected=protected)
    except ValueError as exc:
        raise ValueError(
            f"process-log evidence is not a regular file: {directory.path / name}",
        ) from exc


@contextmanager
def _open_regular_in(
    directory: ArchiveDirectory, name: str, *, protected: bool = False,
) -> Iterator[BinaryIO]:
    from agent_logger.sync.provenance import is_link_or_reparse

    path = directory.path / name
    before = directory.stat(name)
    if not stat.S_ISREG(before.st_mode) or is_link_or_reparse(path, before.st_mode):
        raise ValueError(f"process-log evidence is not a regular file: {path}")
    with _open_process_log_entry(directory, name, protected=protected) as stream:
        opened = os.fstat(stream.fileno())
        if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise ValueError(f"process-log evidence changed while opening: {path}")
        yield stream


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
    # Reader root, excluded from reference equality/repr. POSIX retains the
    # configured root; Windows retains its canonical I/O root independently
    # of the configured path used for display and reference identity.
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
        if self.verified_root is not None and supports_dir_fd():
            with (
                open_root_dir(self.verified_root) as root_fd,
                open_regular_at(root_fd, self.path.name) as raw,
            ):
                yield from self._read_opened(raw, max_line_bytes)
        else:
            path = self.verified_root / self.path.name if self.verified_root else self.path
            with _open_regular(path) as raw:
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


def supports_dir_fd() -> bool:
    """Whether directory-fd-pinned traversal (``open_root_dir``/
    ``open_regular_at``) is available on this platform.

    Windows uses canonical ancestor handles denying directory renames instead.
    """
    return os.name != "nt"


@contextmanager
def _process_log_directory(log_root: Path) -> Iterator[ArchiveDirectory]:
    """Normalize opening failures without relabelling errors in the consumer."""
    from agent_logger.sync.archive_directory import ArchiveRootChangedError, archive_directory

    with ExitStack() as stack:
        try:
            directory = stack.enter_context(archive_directory(log_root))
        except ArchiveRootChangedError as exc:
            raise ArchiveRootChangedError(f"{exc}: {log_root}") from exc
        except (NotADirectoryError, ValueError) as exc:
            raise ValueError(f"process-log source is not a directory: {log_root}") from exc
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise ValueError(f"process-log source is not a directory: {log_root}") from exc
            raise
        yield directory


@contextmanager
def open_root_dir(log_root: Path) -> Iterator[int]:
    """Pin canonical ancestors before exposing a POSIX directory fd."""
    with _process_log_directory(log_root) as directory:
        if directory.fd is None:
            raise OSError(errno.ENOTSUP, "process-log directory fd requires POSIX")
        yield directory.fd


@contextmanager
def open_regular_at(dir_fd: int, name: str) -> Iterator[BinaryIO]:
    """Open a regular file by name within a pinned, already-verified directory
    handle, refusing to follow a symlinked entry.

    *name* must be a bare filename -- no path separator, and never
    absolute. This is enforced here, not only documented: ``os.stat``/
    ``os.open`` with ``dir_fd`` silently ignore ``dir_fd`` entirely when
    *name* is itself absolute (POSIX semantics), and a ``../``-containing
    value can still traverse outside the pinned directory even when
    relative -- either would let a public caller escape the pinned root
    this function exists to enforce, rather than relying on every caller
    to prefilter the value correctly.
    """
    from agent_logger.sync.archive_directory import ArchiveDirectory

    if name in ("", ".", "..") or "/" in name or "\\" in name or Path(name).is_absolute():
        raise ValueError(f"process-log evidence name must be a bare filename: {name!r}")
    if not supports_dir_fd():
        raise OSError(errno.ENOTSUP, "process-log directory fd requires POSIX")
    before = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    if not stat.S_ISREG(before.st_mode):
        raise ValueError(f"process-log evidence is not a regular file: {name}")
    with _open_process_log_entry(ArchiveDirectory(Path("."), dir_fd), name) as stream:
        opened = os.fstat(stream.fileno())
        if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise ValueError(f"process-log evidence changed while opening: {name}")
        yield stream


def iter_process_log_refs(log_root: Path) -> Iterator[ProcessLogRef]:
    """Enumerate live, gzip, and flat ZIP observations without silently
    deduplicating.

    Each enumeration/read pins canonical ancestors: POSIX uses directory fds,
    Windows holds native handles denying directory renames during pathname I/O.
    Intentional ancestor aliases are resolved once at the start of each
    operation; this is containment during an operation, not a snapshot across
    separate enumeration and read calls.

    A directory with no raw/gzip/ZIP process-log evidence -- including one
    containing only unrelated files, or a ZIP with no supported members --
    yields no refs. This is ordinary, not an error: absence of evidence is
    not evidence of a missing or misconfigured source.

    ``log_root`` is converted to an absolute path (without resolving
    symlinks) up front, so a relative root is pinned to the working
    directory at enumeration time -- a later ``iter_lines()`` call is
    unaffected by an intervening ``chdir()``.
    """
    log_root = log_root.absolute()
    if supports_dir_fd():
        with open_root_dir(log_root) as root_fd:
            entries = sorted(os.scandir(root_fd), key=lambda entry: entry.name)
            for entry in entries:
                name = entry.name
                if _is_log_name(name.removesuffix(".gz")):
                    yield ProcessLogRef(log_root / name, verified_root=log_root)
                elif name.endswith(".zip"):
                    with open_regular_at(root_fd, name) as raw, zipfile.ZipFile(raw) as archive:
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
    refs: list[ProcessLogRef] = []
    with _process_log_directory(log_root) as directory:
        for name in directory.names():
            path = log_root / name
            if _is_log_name(name.removesuffix(".gz")):
                refs.append(ProcessLogRef(path, verified_root=directory.path))
            elif path.suffix == ".zip":
                with (
                    _open_regular_in(directory, name, protected=True) as raw,
                    zipfile.ZipFile(raw) as archive,
                ):
                    refs.extend(
                        ProcessLogRef(path, info.filename, verified_root=directory.path)
                        for info in _zip_logs(archive)
                    )
    yield from refs
