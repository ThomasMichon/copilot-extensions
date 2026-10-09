"""Session archive containers and their shared member-path boundary."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import struct
import tarfile
import tempfile
import zipfile
from abc import ABC, abstractmethod
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import BinaryIO

MAX_ARCHIVE_MEMBERS = 10_000
MAX_ARCHIVE_MEMBER_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024
MAX_ZIP_DIRECTORY_BYTES = 16 * 1024 * 1024
_CASE_INSENSITIVE = os.name == "nt"
_RESERVED = re.compile(r"(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])\Z", re.IGNORECASE)


@dataclass(frozen=True)
class ArchiveMemberDigest:
    size: int
    sha256: str


def _copy_and_digest(
    source: BinaryIO,
    destination: BinaryIO | None,
    maximum_bytes: int,
) -> ArchiveMemberDigest:
    digest = hashlib.sha256()
    size = 0
    while chunk := source.read(min(1024 * 1024, maximum_bytes - size + 1)):
        size += len(chunk)
        if size > maximum_bytes:
            raise ValueError("session archive member exceeds its byte budget")
        digest.update(chunk)
        if destination is not None:
            destination.write(chunk)
    return ArchiveMemberDigest(size, digest.hexdigest())


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

    @abstractmethod
    def archive_dir(self, src_dir: Path, dest: Path) -> None:
        """Bundle ``src_dir``'s contents into a single archive at ``dest``.

        Members are stored *relative to ``src_dir``* (no leading session-id
        component) so extraction reproduces the session directory directly.
        """

    @abstractmethod
    def read_member(self, archive: Path, member: str) -> bytes | None:
        """Return the bytes of ``member`` from ``archive``, or ``None``."""

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
            or ":" in part
        ):
            raise ValueError(f"unsafe archive member path: {name!r}")
        parts.append(part)
    return "/".join(parts)


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
                    arcname = path.relative_to(src_dir).as_posix()
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
        result: dict[str, ArchiveMemberDigest] = {}
        total = 0
        with tarfile.open(archive, "r:gz") as tar:
            for info in tar.getmembers():
                if info.isdir():
                    continue
                if not info.isfile():
                    raise ValueError(f"non-regular session archive member: {info.name!r}")
                name = _validate_member_name(info.name)
                if not name or name in result:
                    raise ValueError(f"duplicate or empty session archive member: {info.name!r}")
                if name != info.name:
                    raise ValueError(f"noncanonical session archive member: {info.name!r}")
                if len(result) >= MAX_ARCHIVE_MEMBERS:
                    raise ValueError("session archive exceeds its member budget")
                if info.size < 0 or info.size > MAX_ARCHIVE_MEMBER_BYTES:
                    raise ValueError("session archive member exceeds its byte budget")
                source = tar.extractfile(info)
                if source is None:
                    raise ValueError(f"missing session archive member: {info.name!r}")
                with source:
                    result[name] = _copy_and_digest(
                        source, None, min(MAX_ARCHIVE_MEMBER_BYTES, MAX_ARCHIVE_BYTES - total)
                    )
                if result[name].size != info.size:
                    raise ValueError(f"truncated session archive member: {info.name!r}")
                total += result[name].size
        return result


def _zip_members(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    result: dict[str, zipfile.ZipInfo] = {}
    names: set[str] = set()
    total = 0
    for index, info in enumerate(archive.infolist()):
        if index >= MAX_ARCHIVE_MEMBERS:
            raise ValueError("session ZIP exceeds its member budget")
        name = _validate_member_name(info.filename)
        if any(_RESERVED.fullmatch(part.split(".")[0]) for part in name.split("/")):
            raise ValueError(f"unsafe session ZIP member: {info.filename!r}")
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
        result[name] = info
    file_keys = {name.casefold() if _CASE_INSENSITIVE else name for name in result}
    for name in result:
        for parent in PurePosixPath(name).parents:
            key = str(parent).casefold() if _CASE_INSENSITIVE else str(parent)
            if key in file_keys:
                raise ValueError(f"session ZIP file shadows a directory: {name!r}")
    return result


def _preflight_zip(raw: BinaryIO) -> None:
    """Bound central-directory allocation before zipfile constructs its index."""
    raw.seek(0, os.SEEK_END)
    size = raw.tell()
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
    elif count == 0xFFFF or directory_size == 0xFFFFFFFF or directory_offset == 0xFFFFFFFF:
        raise zipfile.BadZipFile("missing session ZIP64 locator")
    if disk or directory_disk or local_count != count:
        raise ValueError("unsupported multi-volume session ZIP")
    if count > MAX_ARCHIVE_MEMBERS:
        raise ValueError("session ZIP exceeds its member budget")
    if directory_size > MAX_ZIP_DIRECTORY_BYTES:
        raise ValueError("session ZIP exceeds its central-directory byte budget")
    raw.seek(0)


@contextmanager
def _open_zip(path: Path) -> Iterator[zipfile.ZipFile]:
    from agent_logger.sync.provenance import existing_real_directory, open_regular_no_follow

    parent = existing_real_directory(path.parent)
    if parent is None:
        raise ValueError(f"unsafe session ZIP directory: {path.parent}")
    path = parent / path.name
    with open_regular_no_follow(path) as raw:
        before = os.fstat(raw.fileno())
        _preflight_zip(raw)
        with zipfile.ZipFile(raw) as archive:
            yield archive
        after = os.fstat(raw.fileno())
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError(f"session ZIP changed during reading: {path}")


def _source_files(root: Path) -> Iterator[Path]:
    from agent_logger.sync.provenance import existing_real_directory, is_link_or_reparse

    def walk_error(error: OSError) -> None:
        raise error

    count = 0
    for directory, directories, files in os.walk(root, onerror=walk_error, followlinks=False):
        parent = Path(directory)
        if existing_real_directory(parent) is None:
            raise ValueError(f"unsafe session ZIP source directory: {parent}")
        count += len(directories) + len(files)
        if count > MAX_ARCHIVE_MEMBERS:
            raise ValueError("session ZIP source exceeds its entry budget")
        directories[:] = [
            name
            for name in sorted(directories)
            if not is_link_or_reparse(parent / name, (parent / name).lstat().st_mode)
        ]
        for name in sorted(files):
            path = parent / name
            mode = path.lstat().st_mode
            if stat.S_ISREG(mode) and not is_link_or_reparse(path, mode):
                yield path


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
                        name = _validate_member_name(path.relative_to(src_dir).as_posix())
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

    def extract_all(self, archive: Path, dest_dir: Path) -> None:
        from agent_logger.sync.provenance import ensure_real_directory

        with _open_zip(archive) as opened:
            members = _zip_members(opened)
            dest_dir = ensure_real_directory(dest_dir)
            for name, info in members.items():
                out = dest_dir / name
                ensure_real_directory(out.parent)
                with opened.open(info) as source, out.open("xb") as target:
                    copied = _copy_and_digest(source, target, info.file_size)
                if copied.size != info.file_size:
                    raise ValueError(f"truncated session ZIP member: {info.filename!r}")

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
