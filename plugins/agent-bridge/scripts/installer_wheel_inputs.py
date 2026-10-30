"""Bounded, stdlib-only wheel inspection. Byte evidence never grants admission."""
from __future__ import annotations

import base64
import csv
from dataclasses import dataclass
from email import policy
from email.message import Message
from email.parser import Parser
import hashlib
import io
import math
import os
from pathlib import Path
import re
import stat
import struct
import time
from types import MappingProxyType
from typing import BinaryIO, Mapping
import zipfile
import zlib


class WheelError(ValueError):
    """An unreadable, inconsistent, unsafe or over-budget wheel."""


class WheelDeadlineError(WheelError):
    """The selected input-reading deadline is invalid or exhausted."""


_WINDOWS_DEVICES = {
    "con", "prn", "aux", "nul", "conin$", "conout$",
    *(f"{kind}{number}" for kind in ("com", "lpt") for number in "123456789\u00b9\u00b2\u00b3"),
}


def _unsafe_windows_component(part: str) -> bool:
    return (
        part.partition(".")[0].rstrip(" ").casefold() in _WINDOWS_DEVICES
        or any(ord(char) < 32 or char in '<>"|?*' for char in part)
    )


@dataclass(frozen=True)
class WheelLimits:
    max_archive_bytes: int
    max_expanded_bytes: int
    max_member_bytes: int
    max_metadata_bytes: int
    max_members: int
    max_tags: int
    max_metadata_member_bytes: int

    def __post_init__(self):
        if any(type(value) is not int or value <= 0 for value in vars(self).values()):
            raise WheelError("wheel limits must be explicit positive integers")


@dataclass(frozen=True)
class WheelFacts:
    name: str
    version: str
    requires_python: str
    provides_extras: tuple[str, ...]
    requires_dist: tuple[str, ...]
    tags: tuple[str, ...]
    member_hashes: Mapping[str, str]
    prefix: str
    metadata: Message
    wheel_metadata: Message
    unpacked_size: int


@dataclass(frozen=True)
class InspectedWheel:
    facts: WheelFacts
    sha256: str
    size: int

    def __getattr__(self, name):
        return getattr(self.facts, name)


def _check_deadline(deadline: float) -> None:
    if (
        type(deadline) not in (int, float) or not math.isfinite(deadline)
        or time.monotonic() >= deadline
    ):
        raise WheelDeadlineError("wheel inspection deadline exhausted or invalid")


def _single(metadata: Message, name: str, *, optional: bool = False) -> str:
    values = metadata.get_all(name, [])
    if optional and not values:
        return ""
    if len(values) != 1 or not str(values[0]).strip():
        raise WheelError(f"ambiguous or missing wheel metadata: {name}")
    return str(values[0]).strip()


def _headers(data: bytes) -> Message:
    # Core metadata uses compat32; default silently decodes RFC 2047 words.
    message = Parser(policy=policy.compat32).parsestr(data.decode("utf-8"))
    if message.defects:
        raise WheelError("malformed wheel metadata headers")
    return message


def bounded_tag(text: str, *, limit: int, deadline: float) -> tuple[str, int]:
    """Canonicalize compressed tags without allocating their Cartesian product."""
    _check_deadline(deadline)
    fields = text.split("-")
    if len(fields) != 3:
        raise WheelError("invalid wheel tag")
    canonical = []
    count = 1
    for field in fields:
        _check_deadline(deadline)
        alternatives = set(field.split("."))
        if any(not re.fullmatch(r"[A-Za-z0-9_]+", item) for item in alternatives):
            raise WheelError("invalid wheel tag alternatives")
        count *= len(alternatives)
        if count > limit:
            raise WheelError("wheel tag expansion limit exceeded")
        canonical.append(".".join(sorted(alternatives)))
    _check_deadline(deadline)
    return "-".join(canonical), count


def preflight_archive(archive: BinaryIO, *, limits: WheelLimits, deadline: float) -> None:
    """Bound the actual central directory before ZipFile allocates its index."""
    def read_at(offset: int, size: int) -> bytes:
        _check_deadline(deadline)
        if offset < 0:
            raise WheelError("invalid wheel directory offset")
        archive.seek(offset)
        data = archive.read(size)
        if len(data) != size:
            raise WheelError("truncated wheel directory")
        return data

    _check_deadline(deadline)
    archive.seek(0, os.SEEK_END)
    size = archive.tell()
    if size > limits.max_archive_bytes:
        raise WheelError("wheel archive limit exceeded")
    tail_size = min(size, 22 + 65535)
    tail = read_at(size - tail_size, tail_size)
    index = tail.rfind(b"PK\x05\x06")
    if index < 0 or len(tail) - index < 22:
        raise WheelError("missing wheel directory footer")
    _, disk, start_disk, disk_count, count, directory_size, offset, comment_size = struct.unpack(
        "<4s4H2LH", tail[index:index + 22],
    )
    end = size - tail_size + index
    if disk or start_disk or disk_count != count or len(tail) - index != 22 + comment_size:
        raise WheelError("ambiguous or multi-disk wheel directory")
    locator = read_at(end - 20, 20) if end >= 20 else b""
    if locator.startswith(b"PK\x06\x07"):
        _, disk64, record_offset, disks = struct.unpack("<4sLQL", locator)
        if disk64 or disks != 1 or record_offset + 56 != end - 20:
            raise WheelError("invalid wheel ZIP64 directory locator")
        record = struct.unpack("<4sQ2H2L4Q", read_at(record_offset, 56))
        signature, length, _, _, disk64, start64, disk_count64, count64, size64, offset64 = record
        if (
            signature != b"PK\x06\x06" or length != 44 or disk64 or start64
            or disk_count64 != count64
            or any(old != maximum and old != new for old, maximum, new in (
                (count, 0xffff, count64), (directory_size, 0xffffffff, size64),
                (offset, 0xffffffff, offset64),
            ))
        ):
            raise WheelError("ambiguous wheel ZIP64 directory")
        count, directory_size, offset, end = count64, size64, offset64, record_offset
    # ZIP32 permits exactly 0xffff entries without a ZIP64 locator.
    elif directory_size == 0xffffffff or offset == 0xffffffff:
        raise WheelError("missing wheel ZIP64 directory locator")
    if count > limits.max_members:
        raise WheelError("wheel member count limit exceeded")
    if directory_size > limits.max_metadata_bytes:
        raise WheelError("wheel directory metadata limit exceeded")
    if offset + directory_size != end:
        raise WheelError("invalid wheel directory extent")
    actual = 0
    position = offset
    while position < end:
        _check_deadline(deadline)
        if actual >= limits.max_members:
            raise WheelError("wheel actual member count limit exceeded")
        if position + 46 > end:
            raise WheelError("truncated wheel directory entry")
        header = read_at(position, 46)
        if header[:4] != b"PK\x01\x02" or struct.unpack_from("<H", header, 34)[0]:
            raise WheelError("invalid or multi-disk wheel directory entry")
        lengths = struct.unpack_from("<3H", header, 28)
        position += 46 + sum(lengths)
        if position > end:
            raise WheelError("wheel directory entry exceeds its extent")
        actual += 1
    if actual != count:
        raise WheelError("wheel directory entry count mismatch")
    _check_deadline(deadline)
    archive.seek(0)


def _member_chunks(
    archive: BinaryIO, bundle: zipfile.ZipFile, member: zipfile.ZipInfo, *,
    limit: int, deadline: float,
):
    _check_deadline(deadline)
    archive.seek(0, os.SEEK_END)
    archive_size = archive.tell()
    if not 0 <= member.header_offset <= archive_size - 30:
        raise WheelError("wheel local-header offset is outside the archive")
    # ZipFile checks local names and overlap, but ZipExtFile can truncate output
    # to a forged file_size before reaching the actual DEFLATE end marker.
    with bundle.open(member):
        pass
    archive.seek(member.header_offset)
    header = archive.read(30)
    if len(header) != 30 or header[:4] != b"PK\x03\x04":
        raise WheelError("invalid wheel local header")
    if struct.unpack_from("<H", header, 8)[0] != member.compress_type:
        raise WheelError("ambiguous wheel compression")
    start = member.header_offset + 30 + sum(struct.unpack_from("<2H", header, 26))
    if start > archive_size or member.compress_size > archive_size - start:
        raise WheelError("wheel compressed member extent is outside the archive")
    archive.seek(start)
    if member.compress_type == zipfile.ZIP_STORED and member.compress_size != member.file_size:
        raise WheelError("wheel stored member size mismatch")
    decoder = zlib.decompressobj(-15) if member.compress_type == zipfile.ZIP_DEFLATED else None
    remaining = member.compress_size
    output_limit = min(limit, member.file_size)
    total = 0
    crc = 0

    def measured(chunk: bytes) -> bytes:
        nonlocal total, crc
        total += len(chunk)
        if total > output_limit:
            raise WheelError("wheel member output limit or declared size exceeded")
        crc = zlib.crc32(chunk, crc)
        return chunk

    while remaining:
        _check_deadline(deadline)
        data = archive.read(min(1 << 16, remaining))
        if not data:
            raise WheelError("truncated wheel compressed member")
        remaining -= len(data)
        if decoder is None:
            yield measured(data)
            continue
        while data:
            _check_deadline(deadline)
            length = len(data)
            output = decoder.decompress(data, min(1 << 16, output_limit - total + 1))
            data = decoder.unconsumed_tail
            if decoder.unused_data or (decoder.eof and remaining):
                raise WheelError("trailing wheel compressed data")
            if output:
                yield measured(output)
            if data and len(data) == length and not output:
                raise WheelError("wheel decompression made no progress")
    if decoder is not None:
        while not decoder.eof:
            _check_deadline(deadline)
            output = decoder.decompress(b"", min(1 << 16, output_limit - total + 1))
            if not output:
                raise WheelError("incomplete wheel DEFLATE stream")
            yield measured(output)
        if decoder.unused_data or decoder.unconsumed_tail:
            raise WheelError("trailing wheel compressed data")
    if total != member.file_size or crc != member.CRC:
        raise WheelError("wheel member length or CRC mismatch")


def inspect_archive(
    archive: BinaryIO, *, limits: WheelLimits, deadline: float,
    record_algorithms: frozenset[str] = frozenset({"sha256"}),
) -> WheelFacts:
    """Inspect a held archive without extraction; callers retain source custody."""
    _check_deadline(deadline)
    if not record_algorithms or not record_algorithms <= {"sha256", "sha384", "sha512"}:
        raise WheelError("unsupported wheel RECORD digest policy")
    try:
        preflight_archive(archive, limits=limits, deadline=deadline)
        with zipfile.ZipFile(archive) as bundle:
            members = bundle.infolist()
            if len(members) > limits.max_members:
                raise WheelError("wheel member count limit exceeded")
            names = [member.filename for member in members]
            seen = set()
            tree = {}
            prefixes = set()
            expanded = 0
            for member in members:
                _check_deadline(deadline)
                name = member.orig_filename
                parts = (name[:-1] if member.is_dir() else name).split("/")
                kind = stat.S_IFMT(member.external_attr >> 16)
                if (
                    member.flag_bits & 1 or "\\" in name or "\0" in name
                    or member.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                    or name.startswith("/") or ":" in name
                    or any(part in ("", ".", "..") for part in parts)
                    or any(part.endswith((".", " ")) for part in parts)
                    or (os.name == "nt" and any(_unsafe_windows_component(part) for part in parts))
                    or kind not in (0, stat.S_IFREG, stat.S_IFDIR)
                    or (kind == stat.S_IFDIR and not member.is_dir())
                    or (member.is_dir() and member.file_size)
                ):
                    raise WheelError("unsafe wheel member")
                folded = name.rstrip("/").casefold()
                if folded in seen:
                    raise WheelError("duplicate or case-ambiguous wheel members")
                seen.add(folded)
                node = tree
                for index, part in enumerate(parts):
                    _check_deadline(deadline)
                    if None in node:
                        raise WheelError("ambiguous wheel file/directory members")
                    node = node.setdefault(part.casefold(), {})
                    if part.casefold().endswith(".dist-info"):
                        if index != 0:
                            raise WheelError("ambiguous wheel dist-info")
                        prefixes.add(part + "/")
                if not member.is_dir():
                    if node:
                        raise WheelError("ambiguous wheel file/directory members")
                    node[None] = True
                expanded += member.file_size
                if (
                    expanded > limits.max_expanded_bytes
                    or member.file_size > limits.max_member_bytes
                ):
                    raise WheelError("wheel expanded/member limit exceeded")
            for member in members:
                if member.is_dir():
                    for _ in _member_chunks(archive, bundle, member, limit=0, deadline=deadline):
                        pass
            if len(prefixes) != 1:
                raise WheelError("ambiguous or missing wheel dist-info")
            prefix = prefixes.pop()
            metadata_names = [prefix + name for name in ("METADATA", "WHEEL", "RECORD")]
            if not set(metadata_names) <= set(names):
                raise WheelError("missing wheel metadata")
            metadata = {}
            remaining = limits.max_metadata_bytes
            for name in metadata_names:
                member_limit = min(remaining, limits.max_metadata_member_bytes)
                if bundle.getinfo(name).file_size > member_limit:
                    raise WheelError("wheel metadata limit exceeded")
                metadata[name] = b"".join(_member_chunks(
                    archive, bundle, bundle.getinfo(name), limit=member_limit, deadline=deadline,
                ))
                remaining -= len(metadata[name])
            package = _headers(metadata[prefix + "METADATA"])
            wheel = _headers(metadata[prefix + "WHEEL"])
            if (
                not re.fullmatch(r"2\.[0-9]+", _single(package, "Metadata-Version"))
                or _single(wheel, "Wheel-Version") != "1.0"
                or _single(wheel, "Root-Is-Purelib") not in ("true", "false")
            ):
                raise WheelError("unsupported wheel metadata version or layout")
            tags = tuple(str(tag).strip() for tag in wheel.get_all("Tag", []))
            if (
                not tags or len(set(tags)) != len(tags)
                or any(not re.fullmatch(r"[A-Za-z0-9_.]+-[A-Za-z0-9_.]+-[A-Za-z0-9_.]+", tag)
                       for tag in tags)
            ):
                raise WheelError("invalid wheel tags")
            tag_count = 0
            for tag in tags:
                _, count = bounded_tag(tag, limit=limits.max_tags - tag_count, deadline=deadline)
                tag_count += count
            rows = []
            for row in csv.reader(
                io.StringIO(metadata[prefix + "RECORD"].decode("utf-8")), strict=True,
            ):
                _check_deadline(deadline)
                if len(rows) >= len(members):
                    raise WheelError("wheel RECORD row limit exceeded")
                rows.append(row)
            if (
                any(len(row) != 3 for row in rows)
                or len(rows) != len({row[0] for row in rows})
                or {row[0] for row in rows} != {
                    member.filename for member in members if not member.is_dir()
                }
            ):
                raise WheelError("wheel RECORD member set mismatch")
            hashes = {}
            for name, recorded_hash, recorded_size in rows:
                _check_deadline(deadline)
                if name == prefix + "RECORD":
                    if recorded_hash or recorded_size:
                        raise WheelError("wheel RECORD must not hash itself")
                    continue
                algorithm, separator, _ = recorded_hash.partition("=")
                if not separator or algorithm not in record_algorithms:
                    raise WheelError("unsupported wheel RECORD digest")
                source_digest = hashlib.sha256()
                digest = source_digest if algorithm == "sha256" else hashlib.new(algorithm)
                size = 0
                for chunk in _member_chunks(
                    archive, bundle, bundle.getinfo(name),
                    limit=limits.max_member_bytes, deadline=deadline,
                ):
                    size += len(chunk)
                    digest.update(chunk)
                    if digest is not source_digest:
                        source_digest.update(chunk)
                encoded = base64.urlsafe_b64encode(digest.digest()).rstrip(b"=").decode("ascii")
                if (
                    recorded_hash != f"{algorithm}={encoded}" or recorded_size != str(size)
                    or size != bundle.getinfo(name).file_size
                ):
                    raise WheelError("wheel RECORD digest or size mismatch")
                hashes[name] = source_digest.hexdigest()
            facts = WheelFacts(
                _single(package, "Name"), _single(package, "Version"),
                _single(package, "Requires-Python", optional=True),
                tuple(str(value).strip() for value in package.get_all("Provides-Extra", [])),
                tuple(str(value).strip() for value in package.get_all("Requires-Dist", [])),
                tags, MappingProxyType(hashes), prefix, package, wheel, expanded,
            )
            _check_deadline(deadline)
            return facts
    except (
        OSError, UnicodeError, zipfile.BadZipFile, RuntimeError, NotImplementedError,
        csv.Error, EOFError, zlib.error, OverflowError,
    ) as exc:
        raise WheelError(f"invalid wheel archive: {exc}") from exc


def _ordinary(path: Path) -> os.stat_result:
    for item in (*reversed(path.parents), path):
        info = item.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise WheelError("linked wheel input refused")
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise WheelError("wheel input must be an ordinary unlinked file")
    return info


def _identity(info: os.stat_result) -> tuple:
    # Windows 3.12 path stat and fstat disagree on ctime's birth/change meaning.
    timestamp = getattr(info, "st_birthtime_ns", info.st_ctime_ns) if os.name == "nt" else info.st_ctime_ns
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, timestamp


def inspect_wheel(
    path: Path, *, sha256: str, limits: WheelLimits, deadline: float,
) -> InspectedWheel:
    """Compare streamed bytes with an asserted digest, not an approval decision."""
    _check_deadline(deadline)
    if not isinstance(sha256, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", sha256):
        raise WheelError("invalid wheel digest")
    path = Path(os.path.abspath(path))
    try:
        before = _ordinary(path)
        if before.st_size > limits.max_archive_bytes:
            raise WheelError("wheel archive limit exceeded")
        flags = os.O_RDONLY
        for name in ("O_BINARY", "O_NOINHERIT", "O_NOFOLLOW", "O_NONBLOCK"):
            flags |= getattr(os, name, 0)
        with os.fdopen(os.open(path, flags), "rb") as stream:
            opened = os.fstat(stream.fileno())
            if _identity(opened) != _identity(before):
                raise WheelError("wheel input changed before inspection")
            digest = hashlib.sha256()
            size = 0
            while True:
                _check_deadline(deadline)
                chunk = stream.read(min(1 << 20, limits.max_archive_bytes - size + 1))
                if not chunk:
                    break
                size += len(chunk)
                if size > limits.max_archive_bytes:
                    raise WheelError("wheel archive limit exceeded")
                digest.update(chunk)
            if "sha256:" + digest.hexdigest() != sha256:
                raise WheelError("wheel archive digest mismatch")
            facts = inspect_archive(stream, limits=limits, deadline=deadline)
            after = os.fstat(stream.fileno())
            if (
                _identity(after) != _identity(opened)
                or after.st_ctime_ns != opened.st_ctime_ns
                or _identity(_ordinary(path)) != _identity(before)
            ):
                raise WheelError("wheel input changed during inspection")
            _check_deadline(deadline)
            return InspectedWheel(facts, sha256, size)
    except OSError as exc:
        raise WheelError(f"cannot read wheel input: {exc}") from exc
