"""Index inventory measurement; the caller owns any selected-domain read fence."""

from __future__ import annotations

import logging
from typing import Any

from .store.source_layout import SourceLayoutError

log = logging.getLogger("agent-index.server")


def measure_index(db: Any, content_table: str, include_sources: bool) -> dict[str, Any]:
    try:
        names = sorted(db.table_names())
    except SourceLayoutError:
        raise
    except Exception:
        log.debug("Index status unavailable", exc_info=True)
        return {"chunks": None, "available": None, "tables": {}, "sources": {}}
    tables: dict[str, int | None] = {}
    for table_name in names:
        try:
            tables[table_name] = int(db.open_table(table_name).count_rows())
        except SourceLayoutError:
            raise
        except Exception:
            log.debug("Failed to count index table %s", table_name, exc_info=True)
            tables[table_name] = None

    # An absent content table is measured empty; a failed count remains unknown.
    chunks = tables.get(content_table, 0)
    available = chunks > 0 if isinstance(chunks, int) else None
    sources: dict[str, int] = {}
    if include_sources and isinstance(chunks, int) and chunks > 0:
        try:
            rows = db.open_table(content_table).search().select(["source"]).limit(chunks).to_list()
            for row in rows:
                source = row.get("source")
                if source:
                    sources[source] = sources.get(source, 0) + 1
        except SourceLayoutError:
            raise
        except Exception:
            # Histogram failure must not discard a separately measured count.
            log.debug("Failed to build source histogram", exc_info=True)
    return {"chunks": chunks, "available": available, "tables": tables, "sources": sources}
