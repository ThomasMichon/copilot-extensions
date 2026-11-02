"""Verify existing FTS coverage without scheduling an index-maintenance pass."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from .source_layout import SourceDomain, source_read

log = logging.getLogger(__name__)


class _ContentFtsCoverageMixin:
    _source_domain: SourceDomain | None
    _table_name: str
    _fts_available: bool

    if TYPE_CHECKING:
        def _get_or_create_table(self) -> Any: ...
        def _durable_fts_index_exists(self, table: Any) -> bool: ...
        def _index_type(self, index: object) -> str: ...
        def _index_columns(self, index: object) -> list[str]: ...

    @source_read
    def fts_current(self) -> bool:
        """Use the owning receipt or Lance's actual unindexed-row metadata."""
        table = self._get_or_create_table()
        table.checkout_latest()
        if self._source_domain is not None:
            coverage = self._source_domain.receipt().get("fts_coverage", {})
            if coverage.get(self._table_name) != table.version:
                return False
            if self._durable_fts_index_exists(table):
                self._fts_available = True
                return True
            return table.count_rows() == 0
        try:
            for index in table.list_indices():
                if self._index_type(index) not in {"FTS", "INVERTED"} or (
                    "content" not in self._index_columns(index)
                ):
                    continue
                name = index.get("name") if isinstance(index, dict) else getattr(index, "name", None)
                if not isinstance(name, str) or not name:
                    log.warning("FTS coverage metadata has no index name; maintenance remains due")
                    return False
                stats = table.index_stats(name)
                remaining = (
                    stats.get("num_unindexed_rows") if isinstance(stats, dict)
                    else getattr(stats, "num_unindexed_rows", None)
                )
                if type(remaining) is not int or remaining < 0:
                    log.warning("FTS coverage metadata is unavailable; maintenance remains due")
                    return False
                self._fts_available = True
                return remaining == 0
        except (AttributeError, OSError, RuntimeError, ValueError):
            log.warning("Unable to verify FTS coverage; maintenance remains due", exc_info=True)
        return False
