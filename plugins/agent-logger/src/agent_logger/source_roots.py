"""Common discovery of flat and provider-qualified archive source roots."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from agent_logger import sessions
from agent_logger.process_logs import ProcessLogRef, iter_process_log_refs
from agent_logger.sync.provenance import (
    existing_real_directory,
    is_link_or_reparse,
    open_regular_no_follow,
)

SOURCE_METADATA_MEMBER = ".archive-source.json"
MAX_SOURCE_METADATA_BYTES = 1024 * 1024
MAX_SOURCE_ENTRIES = 10_000
_CASE_INSENSITIVE = os.name == "nt"
_COMPONENT = re.compile(r"[A-Za-z0-9_.-]+\Z")
_RESERVED = re.compile(r"(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])\Z", re.IGNORECASE)
_LEGACY_CODESPACE_GROUPS = frozenset({".codespaces", ".codespaces-live"})
VenueKind = Literal["machine", "container", "codespace"]
LayoutKind = Literal["flat", "container", "codespace"]
MarkerStamp = tuple[int, int, int, int]


class SourceLayoutError(ValueError):
    """A source namespace, identity, or directory cannot be admitted safely."""


def _component(value: str) -> str:
    if (
        not isinstance(value, str)
        or not _COMPONENT.fullmatch(value)
        or value.endswith(".")
        or len(value) > 255
        or _RESERVED.fullmatch(value.split(".")[0])
    ):
        raise SourceLayoutError(f"unsupported archive path component: {value!r}")
    return value


def _repository(value: str) -> str:
    if not isinstance(value, str) or len(value.split("/")) != 2:
        raise SourceLayoutError("provisioning repository must be owner/name")
    for part in value.split("/"):
        _component(part)
    return value


def validate_source_key(value: str) -> str:
    """Admit a flat key or exactly one declared provider group and leaf."""
    if not isinstance(value, str) or "\\" in value:
        raise SourceLayoutError(f"invalid archive source key: {value!r}")
    parts = value.split("/")
    if len(parts) == 1:
        _component(parts[0])
        if parts[0].startswith("."):
            raise SourceLayoutError("hidden flat keys are reserved for housekeeping")
        if parts[0].endswith((".containers", ".codespaces")):
            raise SourceLayoutError("a provider group requires a venue leaf")
    elif len(parts) == 2:
        group, leaf = parts
        _component(leaf)
        if group not in _LEGACY_CODESPACE_GROUPS:
            _component(group)
            suffix = next(
                (s for s in (".containers", ".codespaces") if group.endswith(s)),
                None,
            )
            if suffix is None:
                raise SourceLayoutError("nested sources require a declared provider group")
            _component(group.removesuffix(suffix))
    else:
        raise SourceLayoutError("archive source keys have at most two components")
    return value


@dataclass(frozen=True)
class SourceIdentity:
    """Producer-recorded identity, independent of its current physical alias."""

    venue_kind: VenueKind
    provider: str
    host: str | None = None
    repository: str | None = None
    venue_name: str | None = None

    def __post_init__(self) -> None:
        _component(self.provider)
        if self.venue_kind == "machine":
            if self.host is None or self.repository is not None or self.venue_name is not None:
                raise SourceLayoutError("machine identity requires only a host")
            _component(self.host)
        elif self.venue_kind == "container":
            if self.host is None or self.venue_name is None or self.repository is not None:
                raise SourceLayoutError("container identity requires host and venue_name")
            _component(self.host)
            _component(self.venue_name)
        elif self.venue_kind == "codespace":
            if self.repository is None or self.venue_name is None or self.host is not None:
                raise SourceLayoutError("codespace identity requires repository and venue_name")
            _repository(self.repository)
            _component(self.venue_name)
        else:
            raise SourceLayoutError(f"unsupported venue_kind: {self.venue_kind!r}")
        validate_source_key(self.namespace)

    @property
    def namespace(self) -> str:
        if self.venue_kind == "machine":
            return str(self.host)
        if self.venue_kind == "container":
            return f"{self.host}.containers/{self.venue_name}"
        return f"{str(self.repository).split('/')[-1]}.codespaces/{self.venue_name}"

    def to_dict(self) -> dict[str, str | None]:
        return {
            "venue_kind": self.venue_kind,
            "provider": self.provider,
            "host": self.host,
            "repository": self.repository,
            "venue_name": self.venue_name,
        }

    @property
    def source_id(self) -> str:
        encoded = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()


def _directory(path: Path) -> Path:
    result = existing_real_directory(path)
    if result is None:
        raise SourceLayoutError(f"missing or unsafe archive directory: {path}")
    return result


def _optional_directory(path: Path) -> Path | None:
    try:
        path.lstat()
    except FileNotFoundError:
        return None
    return _directory(path)


def _regular_member(path: Path, *, optional: bool = False) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        if optional:
            return
        raise
    if is_link_or_reparse(path, info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise SourceLayoutError(f"session observation is not regular: {path}")


def validate_session_ref(ref: sessions.SessionRef) -> None:
    """Reject linked or non-regular session files, including optional sidecars."""
    if ref.kind == "live":
        _directory(ref.path)
        member = ref.path / sessions.EVENTS_MEMBER
        sidecars = (ref.path / name for name in sessions.SIDECAR_MEMBERS)
    else:
        _directory(ref.path.parent)
        member = ref.path
        sidecars = (ref.path.parent / f"{ref.id}.{name}" for name in sessions.SIDECAR_MEMBERS)
    _regular_member(member)
    for sidecar in sidecars:
        _regular_member(sidecar, optional=True)


def _validate_session_entries(path: Path, *, archived: bool) -> None:
    """Check candidates the legacy reader's existence predicates would omit."""
    for candidate in path.iterdir():
        if archived:
            if sessions.archive_stem(candidate) is not None:
                _regular_member(candidate)
            continue
        info = candidate.lstat()
        if is_link_or_reparse(candidate, info.st_mode):
            raise SourceLayoutError(f"linked session directory: {candidate}")
        if stat.S_ISDIR(info.st_mode):
            _directory(candidate)
            _regular_member(candidate / sessions.EVENTS_MEMBER, optional=True)


@dataclass(frozen=True)
class ArchiveSource:
    """One physical source observation; aliases are never silently coalesced."""

    key: str
    path: Path
    layout: LayoutKind
    identity: SourceIdentity | None = None
    legacy_aliases: tuple[str, ...] = ()
    _directory_id: tuple[int, int] = field(default=(0, 0), repr=False, compare=False)
    _marker_stamp: MarkerStamp | None = field(default=None, repr=False, compare=False)

    def validate(self) -> None:
        path = _directory(self.path)
        info = path.stat()
        if (info.st_dev, info.st_ino) != self._directory_id:
            raise SourceLayoutError(f"archive source changed since discovery: {self.path}")
        try:
            current = _stamp((path / SOURCE_METADATA_MEMBER).lstat())
        except FileNotFoundError:
            current = None
        if current != self._marker_stamp:
            raise SourceLayoutError(f"archive source metadata changed: {self.path}")

    def iter_sessions(self) -> Iterator[sessions.SessionRef]:
        """Use the shared live-preferred session/archive reader."""
        self.validate()
        live = _optional_directory(self.path / "session-state")
        archived = _optional_directory(self.path / "archived")
        if live is not None:
            _validate_session_entries(live, archived=False)
        if archived is not None:
            _validate_session_entries(archived, archived=True)
        for ref in sessions.iter_session_refs(
            live,
            *((archived,) if archived is not None else ()),
        ):
            self.validate()
            validate_session_ref(ref)
            yield ref

    def iter_process_logs(self) -> Iterator[ProcessLogRef]:
        """Preserve physical raw/gzip/ZIP observations through the existing reader."""
        self.validate()
        logs = _optional_directory(self.path / "logs")
        if logs is not None:
            yield from iter_process_log_refs(logs)


def _entries(path: Path) -> list[Path]:
    out: list[Path] = []
    names: set[str] = set()
    for child in path.iterdir():
        if len(out) >= MAX_SOURCE_ENTRIES:
            raise SourceLayoutError(f"archive directory exceeds entry budget: {path}")
        key = child.name.casefold() if _CASE_INSENSITIVE else child.name
        if key in names:
            raise SourceLayoutError(f"case-folded archive path collision: {child}")
        names.add(key)
        out.append(child)
    return sorted(out)


def _stamp(info: os.stat_result) -> MarkerStamp:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _metadata(
    path: Path,
) -> tuple[SourceIdentity | None, tuple[str, ...], MarkerStamp | None]:
    marker = path / SOURCE_METADATA_MEMBER
    try:
        marker.lstat()
    except FileNotFoundError:
        return None, (), None
    return read_source_metadata(marker)


def read_source_metadata(
    marker: Path,
) -> tuple[SourceIdentity, tuple[str, ...], MarkerStamp]:
    """Read required identity metadata through the common bounded, no-link reader."""
    _directory(marker.parent)
    info = marker.lstat()
    if is_link_or_reparse(marker, info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise SourceLayoutError(f"archive source metadata is not regular: {marker}")
    with open_regular_no_follow(marker) as stream:
        if _stamp(os.fstat(stream.fileno())) != _stamp(info):
            raise SourceLayoutError(f"archive source metadata changed while opening: {marker}")
        encoded = stream.read(MAX_SOURCE_METADATA_BYTES + 1)
        if _stamp(os.fstat(stream.fileno())) != _stamp(info):
            raise SourceLayoutError(f"archive source metadata changed while reading: {marker}")
    if len(encoded) > MAX_SOURCE_METADATA_BYTES:
        raise SourceLayoutError(f"archive source metadata exceeds byte budget: {marker}")
    try:
        data = json.loads(encoded)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise SourceLayoutError(f"invalid archive source metadata: {marker}") from exc
    if not isinstance(data, dict) or type(data.get("schema_version")) is not int:
        raise SourceLayoutError(f"invalid archive source metadata schema: {marker}")
    if data["schema_version"] != 1:
        raise SourceLayoutError(f"unsupported archive source metadata schema: {marker}")
    identity = SourceIdentity(
        venue_kind=data.get("venue_kind"),
        provider=data.get("provider"),
        host=data.get("host"),
        repository=data.get("repository"),
        venue_name=data.get("venue_name"),
    )
    aliases = data.get("legacy_aliases", [])
    if not isinstance(aliases, list) or any(not isinstance(a, str) for a in aliases):
        raise SourceLayoutError(f"invalid archive source aliases: {marker}")
    for alias in aliases:
        validate_source_key(alias)
    alias_keys = [alias.casefold() if _CASE_INSENSITIVE else alias for alias in aliases]
    if len(set(alias_keys)) != len(aliases):
        raise SourceLayoutError(f"duplicate archive source aliases: {marker}")
    return identity, tuple(aliases), _stamp(info)


def _source(root: Path, path: Path, layout: LayoutKind) -> ArchiveSource:
    path = _directory(path)
    before = path.stat()
    key = validate_source_key(path.relative_to(root).as_posix())
    identity, aliases, marker_stamp = _metadata(path)
    if identity is not None:
        if layout != "flat" and identity.venue_kind != layout:
            raise SourceLayoutError(f"source identity disagrees with provider group: {key}")
        physical = key.casefold() if _CASE_INSENSITIVE else key
        declared = (identity.namespace, *aliases)
        allowed = {k.casefold() if _CASE_INSENSITIVE else k for k in declared}
        if physical not in allowed:
            raise SourceLayoutError(f"source identity disagrees with namespace: {key}")
    source = ArchiveSource(
        key, path, layout, identity, aliases, (before.st_dev, before.st_ino), marker_stamp
    )
    source.validate()
    return source


def iter_archive_sources(root: Path) -> Iterator[ArchiveSource]:
    """Discover flat roots, legacy CodeSpaces, and qualified groups, without recursion.

    Missing, inaccessible, malformed and unsafe evidence raises explicitly.
    Source records without a producer marker retain unknown identity.
    """
    root = _directory(root)
    count = 0
    for entry in _entries(root):
        grouped = entry.name in _LEGACY_CODESPACE_GROUPS or entry.name.endswith(
            (".containers", ".codespaces")
        )
        if entry.name.startswith(".") and not grouped:
            continue
        mode = entry.lstat().st_mode
        if stat.S_ISREG(mode):
            continue
        layout: LayoutKind = (
            "container"
            if entry.name.endswith(".containers")
            else "codespace"
            if entry.name in _LEGACY_CODESPACE_GROUPS or entry.name.endswith(".codespaces")
            else "flat"
        )
        if layout != "flat" and entry.name not in _LEGACY_CODESPACE_GROUPS:
            suffix = ".containers" if layout == "container" else ".codespaces"
            _component(entry.name.removesuffix(suffix))
        candidates = _entries(_directory(entry)) if layout != "flat" else [entry]
        for candidate in candidates:
            if stat.S_ISREG(candidate.lstat().st_mode):
                continue
            count += 1
            if count > MAX_SOURCE_ENTRIES:
                raise SourceLayoutError("archive corpus exceeds source budget")
            yield _source(root, candidate, layout)
