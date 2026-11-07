"""Session archive containers and their shared member-path boundary."""

from __future__ import annotations

import gzip
import hashlib
import io
import os
import re
import shutil
import stat
import struct
import tarfile
import tempfile
import zipfile
from abc import ABC, abstractmethod
from bisect import bisect_left
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import BinaryIO, NoReturn

MAX_ARCHIVE_MEMBERS = 10_000
MAX_ARCHIVE_MEMBER_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024
MAX_ARCHIVE_INPUT_BYTES = MAX_ARCHIVE_BYTES + 64 * 1024 * 1024
MAX_TAR_CONTAINER_BYTES = MAX_ARCHIVE_BYTES + 64 * 1024 * 1024
MAX_ZIP_DIRECTORY_BYTES = 16 * 1024 * 1024
MAX_TAR_METADATA_BYTES = 16 * 1024 * 1024
_CASE_INSENSITIVE = os.name == "nt"
_RESERVED = re.compile(
    r"(CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[1-9\u00b9\u00b2\u00b3]|LPT[1-9\u00b9\u00b2\u00b3])\Z",
    re.IGNORECASE,
)
_INVALID_COMPONENT = re.compile(r'[\x00-\x1f\x7f-\x9f<>:"|?*]')


@dataclass(frozen=True)
class ArchiveMemberDigest:
    size: int
    sha256: str


class _LimitedTarInput(io.FileIO):
    """Cap compressed bytes consumed by tarfile's sequential stream reader."""

    def __init__(self, descriptor: int) -> None:
        super().__init__(descriptor, "rb", closefd=False)
        self.consumed = 0

    def read(self, size: int | None = -1) -> bytes:
        remaining = MAX_ARCHIVE_INPUT_BYTES - self.consumed + 1
        maximum = remaining if size is None or size < 0 else min(size, remaining)
        data = super().read(maximum)
        if data is None:
            raise OSError("session tar input unexpectedly unavailable")
        self.consumed += len(data)
        if self.consumed > MAX_ARCHIVE_INPUT_BYTES:
            raise ValueError("session archive exceeds its compressed-input byte budget")
        return data


class _LimitedTarDecoded:
    """Bound expanded container bytes before tarfile parses headers or metadata."""

    def __init__(self, source: BinaryIO) -> None:
        self.source = source
        self.consumed = 0

    def read(self, size: int = -1) -> bytes:
        remaining = MAX_TAR_CONTAINER_BYTES - self.consumed + 1
        maximum = remaining if size < 0 else min(size, remaining)
        data = self.source.read(maximum)
        self.consumed += len(data)
        if self.consumed > MAX_TAR_CONTAINER_BYTES:
            raise ValueError("session tar exceeds its decoded-container byte budget")
        return data


def _validate_read_limit(max_bytes: int) -> None:
    if type(max_bytes) is not int or max_bytes < 0:
        raise ValueError("member byte limit must be a nonnegative integer")


def _file_identity(info: os.stat_result) -> tuple[int, int, int, int]:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _file_revision(info: os.stat_result) -> tuple[int, int, int, int, int]:
    # Windows stat/fstat expose different ctime semantics; compare it API-to-API.
    return (*_file_identity(info), info.st_ctime_ns)


@contextmanager
def _open_stable_archive(path: Path) -> Iterator[BinaryIO]:
    from agent_logger.sync.publication_directory import publication_directory

    with publication_directory(path.parent, create=False) as directory:
        with directory.open(path.name) as raw:
            before = os.fstat(raw.fileno())
            named_before = directory.stat(path.name)
            if _file_identity(before) != _file_identity(named_before):
                raise ValueError("session archive changed while opening")
            yield raw
            if _file_revision(before) != _file_revision(os.fstat(raw.fileno())) or (
                _file_revision(named_before) != _file_revision(directory.stat(path.name))
            ):
                raise ValueError("session archive changed during reading")


def _copy_and_digest(
    source: BinaryIO,
    destination: BinaryIO | None,
    maximum_bytes: int,
    *,
    digest: bool = True,
) -> ArchiveMemberDigest:
    hasher = hashlib.sha256() if digest else None
    size = 0
    while chunk := source.read(min(1024 * 1024, maximum_bytes - size + 1)):
        size += len(chunk)
        if size > maximum_bytes:
            raise ValueError("session archive member exceeds its byte budget")
        if hasher is not None:
            hasher.update(chunk)
        if destination is not None:
            destination.write(chunk)
    return ArchiveMemberDigest(size, hasher.hexdigest() if hasher is not None else "")


class Codec(ABC):
    """A compression codec: bundle a directory into one archive and read back.

    A codec owns *both* the container (how a directory of files becomes one
    stream) and the compression. The default bundles with ``tar`` and is the
    only place tar/compression specifics live.
    """

    #: Registry name (config ``sync.compact.codec``).
    name: str = "base"
    #: File suffix for an archive produced by this codec (e.g. ``.tar.gz``).
    suffix: str = ""

    def archive_dir(self, src_dir: Path, dest: Path) -> None:
        """Bundle ``src_dir``'s contents into a single archive at ``dest``.

        Members are stored *relative to ``src_dir``* (no leading session-id
        component) so extraction reproduces the session directory directly.
        Read-only codecs may omit this method; writes then fail explicitly.
        """
        raise ValueError(f"codec {self.name!r} is read-only")

    @abstractmethod
    def read_member(self, archive: Path, member: str) -> bytes | None:
        """Return the bytes of ``member`` from ``archive``, or ``None``."""

    def read_member_bounded(
        self, archive: Path, member: str, *, max_bytes: int,
    ) -> bytes | None:
        """Read with a preallocation bound, or explicitly decline support."""
        raise ValueError(f"codec {self.name!r} does not support bounded member reads")

    @abstractmethod
    def extract_all(self, archive: Path, dest_dir: Path) -> None:
        """Safely extract every member of ``archive`` under ``dest_dir``."""

    @abstractmethod
    def list_members(self, archive: Path) -> list[str]:
        """Return the archive's member names (files only)."""

    def member_digests(self, archive: Path) -> dict[str, ArchiveMemberDigest]:
        """Prove equality of representations, or explicitly decline that proof."""
        raise ValueError(f"codec {self.name!r} cannot compare archive representations")


def _validate_member_name(name: str) -> str:
    """Reject absolute paths and ``..`` traversal; return a normalized name.

    Guards archive extraction against the ``tar`` path-traversal class of bug
    without relying on ``tarfile.extractall`` (which linters flag): callers
    read members explicitly and write them under a validated relative path.
    """
    windows = PureWindowsPath(name)
    norm = name.replace("\\", "/")
    posix = PurePosixPath(norm)
    if (
        windows.anchor
        or windows.drive
        or windows.root
        or posix.is_absolute()
        or norm.startswith("/")
    ):
        raise ValueError(f"unsafe archive member path: {name!r}")
    parts = []
    for part in norm.split("/"):
        if part in ("", "."):
            continue
        windows_normalized = part.rstrip(" .")
        if (
            part == ".."
            or windows_normalized != part
            or windows_normalized in ("", ".", "..")
            or _INVALID_COMPONENT.search(part)
            or _RESERVED.fullmatch(part.split(".")[0].rstrip(" "))
        ):
            raise ValueError(f"unsafe archive member path: {name!r}")
        parts.append(part)
    return "/".join(parts)


def _validate_source_member_name(name: str) -> str:
    normalized = _validate_member_name(name)
    if normalized != name:
        raise ValueError(f"noncanonical archive source member: {name!r}")
    return name


class TarGzCodec(Codec):
    """``tar`` + ``gzip`` bundling, standard-library only (default codec)."""

    name = "targz"
    suffix = ".tar.gz"

    def archive_dir(self, src_dir: Path, dest: Path) -> None:
        tmp = dest.with_name(dest.name + ".tmp")
        tmp.parent.mkdir(parents=True, exist_ok=True)
        try:
            with tarfile.open(tmp, "w:gz") as tar:
                for path in sorted(src_dir.rglob("*")):
                    if path.is_symlink() or not path.is_file():
                        continue
                    arcname = _validate_source_member_name(path.relative_to(src_dir).as_posix())
                    tar.add(path, arcname=arcname, recursive=False)
            os.replace(tmp, dest)
        finally:
            tmp.unlink(missing_ok=True)

    def read_member(self, archive: Path, member: str) -> bytes | None:
        target = _validate_member_name(member)
        with tarfile.open(archive, "r:gz") as tar:
            try:
                info = tar.getmember(target)
            except KeyError:
                return None
            if not info.isfile():
                return None
            fh = tar.extractfile(info)
            return fh.read() if fh is not None else None

    def extract_all(self, archive: Path, dest_dir: Path) -> None:
        dest_dir.mkdir(parents=True, exist_ok=True)
        with tarfile.open(archive, "r:gz") as tar:
            for info in tar.getmembers():
                if not info.isfile():
                    continue
                rel = _validate_member_name(info.name)
                out = dest_dir / rel
                try:
                    out.absolute().relative_to(dest_dir.absolute())
                except ValueError as exc:
                    raise ValueError(f"unsafe archive member path: {info.name!r}") from exc
                out.parent.mkdir(parents=True, exist_ok=True)
                fh = tar.extractfile(info)
                if fh is None:
                    continue
                with fh, open(out, "wb") as dst:
                    shutil.copyfileobj(fh, dst)

    def list_members(self, archive: Path) -> list[str]:
        with tarfile.open(archive, "r:gz") as tar:
            return [m.name for m in tar.getmembers() if m.isfile()]

    def member_digests(self, archive: Path) -> dict[str, ArchiveMemberDigest]:
        result, _ = self._scan_members(archive)
        return result

    def read_member_bounded(
        self, archive: Path, member: str, *, max_bytes: int,
    ) -> bytes | None:
        """Validate the whole bounded TAR stream while retaining only one member."""
        _validate_read_limit(max_bytes)
        target = _validate_source_member_name(member)
        _, data = self._scan_members(archive, target=target, max_bytes=max_bytes)
        return data

    def _scan_members(
        self, archive: Path, *, target: str | None = None, max_bytes: int = 0,
    ) -> tuple[dict[str, ArchiveMemberDigest], bytes | None]:
        result: dict[str, ArchiveMemberDigest] = {}
        names: set[str] = set()
        selected: bytes | None = None
        total = 0
        headers = 0
        metadata_bytes = 0

        class BoundedTarInfo(tarfile.TarInfo):
            def _reject_sparse(self, *args: object, **kwargs: object) -> NoReturn:
                raise ValueError("sparse session tar members are unsupported")

            # Sparse parsers consume extent metadata outside the header budgets.
            _proc_sparse = _reject_sparse
            _proc_gnusparse_00 = _reject_sparse
            _proc_gnusparse_01 = _reject_sparse
            _proc_gnusparse_10 = _reject_sparse

            @classmethod
            def frombuf(cls, buf: bytes, encoding: str, errors: str) -> tarfile.TarInfo:
                return cls._frombuf(buf, encoding, errors)

            @classmethod
            def _frombuf(
                cls, buf: bytes, encoding: str, errors: str, **kwargs: bool
            ) -> tarfile.TarInfo:
                nonlocal headers, metadata_bytes
                # Patched Python readers bypass frombuf, including for extended headers.
                parser = getattr(super(), "_frombuf", None)
                try:
                    info = (
                        parser(buf, encoding, errors, **kwargs)
                        if parser is not None
                        else super().frombuf(buf, encoding, errors)
                    )
                except (tarfile.InvalidHeaderError, tarfile.TruncatedHeaderError) as exc:
                    # TarFile.next otherwise treats a later corrupt header as EOF.
                    raise ValueError("invalid session tar member header") from exc
                headers += 1
                if headers > MAX_ARCHIVE_MEMBERS:
                    raise ValueError("session archive exceeds its member budget")
                if info.type in (
                    tarfile.XHDTYPE,
                    tarfile.XGLTYPE,
                    tarfile.SOLARIS_XHDTYPE,
                    tarfile.GNUTYPE_LONGNAME,
                    tarfile.GNUTYPE_LONGLINK,
                ):
                    metadata_bytes += info.size
                    if info.size < 0 or metadata_bytes > MAX_TAR_METADATA_BYTES:
                        raise ValueError("session tar exceeds its metadata byte budget")
                return info

        with _open_stable_archive(archive) as raw:
            if os.fstat(raw.fileno()).st_size > MAX_ARCHIVE_INPUT_BYTES:
                raise ValueError("session archive exceeds its compressed-input byte budget")
            with (
                _LimitedTarInput(raw.fileno()) as limited,
                gzip.GzipFile(fileobj=limited, mode="rb") as decoded,
                tarfile.open(
                    fileobj=_LimitedTarDecoded(decoded), mode="r|", tarinfo=BoundedTarInfo,
                ) as tar,
            ):
                for info in tar:
                    name = _validate_member_name(info.name)
                    key = name.casefold() if _CASE_INSENSITIVE else name
                    if not name or key in names:
                        raise ValueError(
                            f"duplicate or empty session archive member: {info.name!r}"
                        )
                    names.add(key)
                    if name != info.name:
                        raise ValueError(f"noncanonical session archive member: {info.name!r}")
                    if info.isdir():
                        if info.size:
                            raise ValueError(f"nonempty session tar directory: {info.name!r}")
                        continue
                    if not info.isfile():
                        raise ValueError(f"non-regular session archive member: {info.name!r}")
                    if info.size < 0 or info.size > MAX_ARCHIVE_MEMBER_BYTES:
                        raise ValueError("session archive member exceeds its byte budget")
                    if info.size > MAX_ARCHIVE_BYTES - total:
                        raise ValueError("session archive exceeds its total byte budget")
                    if name == target and info.size > max_bytes:
                        raise ValueError("session archive member exceeds its requested byte limit")
                    source = tar.extractfile(info)
                    if source is None:
                        raise ValueError(f"missing session archive member: {info.name!r}")
                    destination = io.BytesIO() if name == target else None
                    # Bounded single-member reads only need the selected member's
                    # bytes/digest; draining the rest without hashing avoids
                    # SHA-256 cost proportional to total archive content.
                    with source:
                        result[name] = _copy_and_digest(
                            source, destination, info.size,
                            digest=target is None or name == target,
                        )
                    if destination is not None:
                        selected = destination.getvalue()
                        destination.close()
                    if result[name].size != info.size:
                        raise ValueError(f"truncated session archive member: {info.name!r}")
                    total += result[name].size
                container_bytes = tar.fileobj.tell()
                if container_bytes > MAX_TAR_CONTAINER_BYTES:
                    raise ValueError("session tar exceeds its decoded-container byte budget")
                while trailing := tar.fileobj.read(
                    min(1024 * 1024, MAX_TAR_CONTAINER_BYTES - container_bytes + 1)
                ):
                    container_bytes += len(trailing)
                    if container_bytes > MAX_TAR_CONTAINER_BYTES:
                        raise ValueError("session tar exceeds its decoded-container byte budget")
                    if any(trailing):
                        raise ValueError("nonzero trailing data in session tar")
        return result, selected


def _zip_members(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    result: dict[str, zipfile.ZipInfo] = {}
    names: set[str] = set()
    total = 0
    compressed_total = 0
    for index, info in enumerate(archive.infolist()):
        if index >= MAX_ARCHIVE_MEMBERS:
            raise ValueError("session ZIP exceeds its member budget")
        name = _validate_member_name(info.orig_filename)
        key = name.casefold() if _CASE_INSENSITIVE else name
        if key in names:
            raise ValueError(f"duplicate session ZIP member: {info.filename!r}")
        names.add(key)
        kind = stat.S_IFMT(info.external_attr >> 16)
        if kind not in (0, stat.S_IFREG, stat.S_IFDIR):
            raise ValueError(f"non-regular session ZIP member: {info.filename!r}")
        if info.flag_bits & 1:
            raise ValueError(f"encrypted session ZIP member: {info.filename!r}")
        if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            raise ValueError(f"unsupported session ZIP compression: {info.compress_type}")
        if info.compress_size < 0 or info.compress_size > MAX_ARCHIVE_INPUT_BYTES:
            raise ValueError("session archive exceeds its compressed-input byte budget")
        if info.is_dir():
            if kind == stat.S_IFREG:
                raise ValueError(f"inconsistent session ZIP directory: {info.filename!r}")
            if info.file_size:
                raise ValueError(f"nonempty session ZIP directory: {info.filename!r}")
            continue
        if not name:
            raise ValueError(f"empty session ZIP member: {info.filename!r}")
        if kind == stat.S_IFDIR:
            raise ValueError(f"inconsistent session ZIP file: {info.filename!r}")
        if info.file_size < 0 or info.file_size > MAX_ARCHIVE_MEMBER_BYTES:
            raise ValueError("session ZIP member exceeds its byte budget")
        total += info.file_size
        if total > MAX_ARCHIVE_BYTES:
            raise ValueError("session ZIP exceeds its total byte budget")
        compressed_total += info.compress_size
        if compressed_total > MAX_ARCHIVE_INPUT_BYTES:
            raise ValueError("session archive exceeds its compressed-input byte budget")
        result[name] = info
    file_keys = sorted(name.casefold() if _CASE_INSENSITIVE else name for name in result)
    for index, name in enumerate(file_keys):
        prefix = name + "/"
        descendant = bisect_left(file_keys, prefix, lo=index + 1)
        if descendant < len(file_keys) and file_keys[descendant].startswith(prefix):
            raise ValueError(f"session ZIP file shadows a directory: {name!r}")
    return result


def _preflight_zip(raw: BinaryIO) -> None:
    """Bound central-directory allocation before zipfile constructs its index."""
    raw.seek(0, os.SEEK_END)
    size = raw.tell()
    if size > MAX_ARCHIVE_INPUT_BYTES:
        raise ValueError("session archive exceeds its compressed-input byte budget")
    raw.seek(max(0, size - 65_557))
    tail = raw.read(65_557)
    position = tail.rfind(b"PK\x05\x06")
    if position < 0 or len(tail) - position < 22:
        raise zipfile.BadZipFile("missing session ZIP end record")
    _, disk, directory_disk, local_count, count, directory_size, directory_offset, comment_size = (
        struct.unpack("<4s4H2LH", tail[position : position + 22])
    )
    if position + 22 + comment_size != len(tail):
        raise zipfile.BadZipFile("invalid session ZIP end record")
    end_offset = size - len(tail) + position
    directory_end = end_offset
    locator = b""
    if end_offset >= 20:
        raw.seek(end_offset - 20)
        locator = raw.read(20)
    if locator.startswith(b"PK\x06\x07"):
        signature, locator_disk, record_offset, disks = struct.unpack("<4sLQL", locator)
        if locator_disk or disks != 1 or record_offset != end_offset - 76:
            raise ValueError("unsupported session ZIP64 layout")
        raw.seek(record_offset)
        record = raw.read(56)
        if len(record) != 56:
            raise zipfile.BadZipFile("truncated session ZIP64 end record")
        (
            signature,
            record_size,
            _,
            _,
            disk,
            directory_disk,
            local_count,
            count,
            directory_size,
            _,
        ) = struct.unpack("<4sQ2H2L4Q", record)
        if signature != b"PK\x06\x06" or record_size != 44:
            raise zipfile.BadZipFile("invalid session ZIP64 end record")
        directory_end = record_offset
    elif count == 0xFFFF or directory_size == 0xFFFFFFFF or directory_offset == 0xFFFFFFFF:
        raise zipfile.BadZipFile("missing session ZIP64 locator")
    if disk or directory_disk or local_count != count:
        raise ValueError("unsupported multi-volume session ZIP")
    if count > MAX_ARCHIVE_MEMBERS:
        raise ValueError("session ZIP exceeds its member budget")
    if directory_size > MAX_ZIP_DIRECTORY_BYTES:
        raise ValueError("session ZIP exceeds its central-directory byte budget")
    directory_start = directory_end - directory_size
    if directory_start < 0:
        raise zipfile.BadZipFile("invalid session ZIP central-directory offset")
    raw.seek(directory_start)
    remaining = directory_size
    observed = 0
    while remaining:
        header = raw.read(min(46, remaining))
        if len(header) != 46 or header[:4] != b"PK\x01\x02":
            raise zipfile.BadZipFile("invalid session ZIP central-directory record")
        observed += 1
        if observed > MAX_ARCHIVE_MEMBERS:
            raise ValueError("session ZIP exceeds its member budget")
        name_size, extra_size, entry_comment_size = struct.unpack_from("<3H", header, 28)
        record_size = 46 + name_size + extra_size + entry_comment_size
        if record_size > remaining:
            raise zipfile.BadZipFile("truncated session ZIP central-directory record")
        raw.seek(record_size - 46, os.SEEK_CUR)
        remaining -= record_size
    if observed != count:
        raise zipfile.BadZipFile("inconsistent session ZIP central-directory entry count")
    raw.seek(0)


@contextmanager
def _open_zip(path: Path) -> Iterator[zipfile.ZipFile]:
    with _open_stable_archive(path) as raw:
        _preflight_zip(raw)
        with zipfile.ZipFile(raw) as archive:
            yield archive


def _source_files(root: Path) -> Iterator[Path]:
    from agent_logger.sync.provenance import existing_real_directory, is_link_or_reparse

    count = 0
    pending = [root]
    while pending:
        parent = pending.pop()
        if existing_real_directory(parent) is None:
            raise ValueError(f"unsafe session ZIP source directory: {parent}")
        directories: list[Path] = []
        files: list[Path] = []
        with os.scandir(parent) as entries:
            for entry in entries:
                count += 1
                if count > MAX_ARCHIVE_MEMBERS:
                    raise ValueError("session ZIP source exceeds its entry budget")
                path = parent / entry.name
                mode = entry.stat(follow_symlinks=False).st_mode
                if is_link_or_reparse(path, mode):
                    continue
                if stat.S_ISDIR(mode):
                    directories.append(path)
                elif stat.S_ISREG(mode):
                    files.append(path)
        yield from sorted(files, key=lambda path: path.name)
        pending.extend(sorted(directories, key=lambda path: path.name, reverse=True))


class ZipCodec(Codec):
    """Bounded standard-library ZIP, rooted at the session's contents."""

    name = "zip"
    suffix = ".zip"

    def archive_dir(self, src_dir: Path, dest: Path) -> None:
        from agent_logger.sync.provenance import (
            ensure_real_directory,
            existing_real_directory,
            open_regular_no_follow,
        )

        source_root = existing_real_directory(src_dir)
        if source_root is None:
            raise ValueError(f"unsafe session ZIP source: {src_dir}")
        src_dir = source_root
        dest = ensure_real_directory(dest.parent) / dest.name
        fd, temporary = tempfile.mkstemp(prefix=f".{dest.name}.", suffix=".tmp", dir=dest.parent)
        tmp = Path(temporary)
        try:
            total = 0
            count = 0
            with os.fdopen(fd, "w+b") as raw:
                with zipfile.ZipFile(raw, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                    for path in _source_files(src_dir):
                        count += 1
                        if count > MAX_ARCHIVE_MEMBERS:
                            raise ValueError("session ZIP exceeds its member budget")
                        name = _validate_source_member_name(path.relative_to(src_dir).as_posix())
                        with open_regular_no_follow(path) as source:
                            before = os.fstat(source.fileno())
                            if before.st_size > MAX_ARCHIVE_MEMBER_BYTES:
                                raise ValueError("session ZIP member exceeds its byte budget")
                            with archive.open(name, "w", force_zip64=True) as target:
                                copied = _copy_and_digest(
                                    source, target, min(before.st_size, MAX_ARCHIVE_BYTES - total)
                                )
                            after = os.fstat(source.fileno())
                            if copied.size != before.st_size or (
                                after.st_size,
                                after.st_mtime_ns,
                                after.st_ctime_ns,
                            ) != (before.st_size, before.st_mtime_ns, before.st_ctime_ns):
                                raise ValueError(
                                    f"session ZIP source changed during capture: {path}"
                                )
                            total += copied.size
                raw.flush()
                os.fsync(raw.fileno())
            self.member_digests(tmp)
            os.replace(tmp, dest)
        finally:
            tmp.unlink(missing_ok=True)

    def read_member(self, archive: Path, member: str) -> bytes | None:
        name = _validate_member_name(member)
        with _open_zip(archive) as opened:
            info = _zip_members(opened).get(name)
            if info is None:
                return None
            with opened.open(info) as source:
                data = source.read(info.file_size + 1)
            if len(data) != info.file_size:
                raise ValueError(f"truncated session ZIP member: {info.filename!r}")
            return data

    def read_member_bounded(
        self, archive: Path, member: str, *, max_bytes: int,
    ) -> bytes | None:
        """Bound the selected member before opening its decompression stream."""
        _validate_read_limit(max_bytes)
        name = _validate_source_member_name(member)
        with _open_zip(archive) as opened:
            info = _zip_members(opened).get(name)
            if info is None:
                return None
            if info.file_size > max_bytes:
                raise ValueError("session ZIP member exceeds its requested byte limit")
            with opened.open(info) as source:
                data = source.read(info.file_size + 1)
            if len(data) != info.file_size:
                raise ValueError(f"truncated session ZIP member: {info.filename!r}")
            return data

    def extract_all(self, archive: Path, dest_dir: Path) -> None:
        from agent_logger.sync.provenance import ensure_real_directory

        with _open_zip(archive) as opened:
            members = _zip_members(opened)
            dest_dir = ensure_real_directory(dest_dir)
            for name, info in members.items():
                out = dest_dir / name
                ensure_real_directory(out.parent)
                with tempfile.TemporaryDirectory(
                    prefix=".session-zip-", dir=out.parent
                ) as staging:
                    staged = Path(staging) / "member"
                    with staged.open("xb") as target:
                        with opened.open(info) as source:
                            copied = _copy_and_digest(source, target, info.file_size)
                        if copied.size != info.file_size:
                            raise ValueError(f"truncated session ZIP member: {info.filename!r}")
                    ensure_real_directory(out.parent)
                    # Publish without replacement; cleanup never unlinks the caller's path.
                    os.link(staged, out)

    def list_members(self, archive: Path) -> list[str]:
        with _open_zip(archive) as opened:
            return list(_zip_members(opened))

    def member_digests(self, archive: Path) -> dict[str, ArchiveMemberDigest]:
        with _open_zip(archive) as opened:
            result: dict[str, ArchiveMemberDigest] = {}
            for name, info in _zip_members(opened).items():
                with opened.open(info) as source:
                    result[name] = _copy_and_digest(source, None, info.file_size)
                if result[name].size != info.file_size:
                    raise ValueError(f"truncated session ZIP member: {info.filename!r}")
            return result
