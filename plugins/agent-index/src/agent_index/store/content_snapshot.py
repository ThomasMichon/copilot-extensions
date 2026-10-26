"""Bounded retained-version reads; descriptors do not authorize cursor progress."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .source_layout import source_read

if TYPE_CHECKING:
    from .content_store import ChunkRecord


class ContentSnapshotUnavailable(RuntimeError):
    """Snapshot input cannot be proven complete; explicit repair is required."""


@dataclass(frozen=True)
class ContentSnapshot:
    """A local physical incarnation and immutable version, not a source lease."""

    root: str
    table_name: str
    storage: tuple[int, int, int]
    domain: str | None
    version: int
    rows: int

    def __post_init__(self) -> None:
        if (not isinstance(self.root, str) or not self.root
                or not isinstance(self.table_name, str) or not self.table_name
                or not isinstance(self.storage, tuple) or len(self.storage) != 3
                or any(type(v) is not int or v < 0 for v in self.storage)
                or not self.storage[1]
                or (self.domain is not None
                    and (not isinstance(self.domain, str) or not self.domain))
                or type(self.version) is not int or self.version < 1
                or type(self.rows) is not int or self.rows < 0):
            raise ValueError("Invalid content snapshot descriptor")


def _identity(owner) -> tuple[str, tuple[int, int, int], str | None]:
    if "://" in str(owner._db_path):
        raise ContentSnapshotUnavailable("Remote content snapshots are not qualified")
    root = Path(owner._db_path).resolve()
    try:
        value = (root / f"{owner._table_name}.lance").stat()
    except OSError as exc:
        raise ContentSnapshotUnavailable("Content snapshot storage is unavailable") from exc
    if not value.st_ino:
        raise ContentSnapshotUnavailable("Content storage lacks a stable local identity")
    domain = owner._source_domain
    domain_id = domain.receipt().get("domain") if domain is not None else None
    if domain is not None and (not isinstance(domain_id, str) or not domain_id):
        raise ContentSnapshotUnavailable("Content source-domain identity is unavailable")
    return str(root), (value.st_dev, value.st_ino, value.st_ctime_ns), domain_id


def _require_identity(owner, snapshot: ContentSnapshot) -> None:
    root, storage, domain = _identity(owner)
    if (root != snapshot.root or owner._table_name != snapshot.table_name
            or storage != snapshot.storage or domain != snapshot.domain):
        raise ContentSnapshotUnavailable("Content snapshot identity changed; repair required")


def _records(rows: list[dict[str, Any]]) -> list[ChunkRecord]:
    from .content_store import ChunkRecord

    result = []
    text = (
        "chunk_id", "source", "file_path", "chunk_type", "language", "content",
        "content_hash",
    )
    for row in rows:
        if (any(not isinstance(row.get(name), str) for name in text)
                or not row["chunk_id"] or not row["content_hash"]
                or any(type(row.get(name)) is not int for name in ("line_start", "line_end"))
                or not isinstance(row.get("metadata"), str)):
            raise ContentSnapshotUnavailable("Malformed canonical snapshot row")
        try:
            metadata = json.loads(row["metadata"]) if row["metadata"] else {}
        except ValueError as exc:
            raise ContentSnapshotUnavailable("Malformed canonical snapshot metadata") from exc
        if not isinstance(metadata, dict):
            raise ContentSnapshotUnavailable("Canonical snapshot metadata must be an object")
        result.append(ChunkRecord(
            **{name: row[name] for name in text},
            line_start=row["line_start"], line_end=row["line_end"], metadata=metadata,
        ))
    return result


class _ContentSnapshotMixin:
    if TYPE_CHECKING:
        _db: Any
        _db_path: str
        _table_name: str
        _source_domain: Any

    @source_read
    def capture_snapshot(self) -> ContentSnapshot:
        """Capture metadata only, without creating or migrating canonical storage."""
        root, storage, domain = _identity(self)
        try:
            table = self._db.open_table(self._table_name)
            table.checkout_latest()
            table.checkout(table.version)
            snapshot = ContentSnapshot(
                root, self._table_name, storage, domain, table.version, table.count_rows(),
            )
        except (OSError, RuntimeError, ValueError) as exc:
            raise ContentSnapshotUnavailable("Cannot capture content snapshot; repair required") from exc
        _require_identity(self, snapshot)
        return snapshot

    @source_read
    def read_snapshot(
        self, snapshot: ContentSnapshot, *, start: int, limit: int,
    ) -> list[ChunkRecord]:
        """Read at most limit version-relative offsets; returned order is unspecified."""
        if not isinstance(snapshot, ContentSnapshot):
            raise ValueError("A content snapshot descriptor is required")
        if (type(start) is not int or start < 0 or start > snapshot.rows
                or type(limit) is not int or limit < 1):
            raise ValueError("Invalid bounded content snapshot range")
        _require_identity(self, snapshot)
        try:
            table = self._db.open_table(self._table_name)
            table.checkout(snapshot.version)
            if table.version != snapshot.version or table.count_rows() != snapshot.rows:
                raise ContentSnapshotUnavailable("Content snapshot version or row count changed")
            count = min(limit, snapshot.rows - start)
            if count == 0:
                rows = []
            else:
                columns = [
                    "chunk_id", "source", "file_path", "chunk_type", "language",
                    "content", "content_hash", "line_start", "line_end", "metadata",
                ]
                rows = table.take_offsets(list(range(start, start + count))).select(
                    columns,
                ).to_arrow().to_pylist()
            if len(rows) != count:
                raise ContentSnapshotUnavailable("Incomplete bounded content snapshot read")
            result = _records(rows)
        except (OSError, RuntimeError, ValueError, KeyError, TypeError) as exc:
            raise ContentSnapshotUnavailable("Content snapshot read failed; repair required") from exc
        _require_identity(self, snapshot)
        return result
