"""Content-item reads, with complete failure-propagating reads for derived artifacts."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from .repo_filter import _sql_str
from .source_layout import source_read

if TYPE_CHECKING:
    from .content_store import ChunkRecord

log = logging.getLogger(__name__)


class _ContentItemReadsMixin:
    if TYPE_CHECKING:
        def _get_or_create_table(self) -> Any: ...

    @source_read
    def get_chunks_for_file(
        self, source: str, file_path: str, *, strict: bool = False,
    ) -> list[ChunkRecord]:
        """Read line-ordered chunks; strict reads require the complete item."""
        from .content_store import ChunkRecord, _decode_metadata

        table = self._get_or_create_table()
        predicate = (
            f"source = '{_sql_str(source)}' AND file_path = '{_sql_str(file_path)}'"
        )
        try:
            count = table.count_rows(predicate) if strict else 10000
            if count == 0:
                return []
            rows = table.search().where(predicate).limit(count).to_list()
            if strict and len(rows) != count:
                raise RuntimeError("Incomplete file chunk read")
        except Exception:
            if strict:
                raise
            log.warning("File chunk read failed", exc_info=True)
            return []
        rows.sort(key=lambda r: (r.get("line_start", 0), r.get("line_end", 0)))
        return [
            ChunkRecord(
                chunk_id=r["chunk_id"],
                source=r["source"],
                file_path=r["file_path"],
                chunk_type=r.get("chunk_type", ""),
                language=r.get("language", ""),
                content=r.get("content", ""),
                content_hash=r.get("content_hash", ""),
                line_start=r.get("line_start", 0),
                line_end=r.get("line_end", 0),
                metadata=_decode_metadata(r.get("metadata")),
            )
            for r in rows
        ]

    @source_read
    def distinct_items(self, *, strict: bool = False) -> list[tuple[str, str]]:
        """Read distinct item identities; strict reads never mask partial input."""
        table = self._get_or_create_table()
        try:
            total = table.count_rows()
            if total == 0:
                return []
            rows = table.search().select(["source", "file_path"]).limit(total).to_list()
            if strict and (len(rows) != total or any(
                row.get("source") is None or row.get("file_path") is None for row in rows
            )):
                raise RuntimeError("Incomplete content item enumeration")
        except Exception:
            if strict:
                raise
            log.warning("Distinct item scan failed", exc_info=True)
            return []
        return sorted({
            (row["source"], row["file_path"]) for row in rows
            if row.get("source") is not None and row.get("file_path") is not None
        })
