"""Incremental FTS maintenance without unrelated data or scalar-index rewrites."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lancedb.table import LanceTable


def update_fts_index(table: LanceTable) -> None:
    """Append the FTS delta; consolidation and data cleanup remain explicit work."""
    from .content_store import ContentStore

    indices = [
        index for index in table.list_indices()
        if ContentStore._index_type(index) in {"FTS", "INVERTED"}
        and ContentStore._index_columns(index) == ["content"]
    ]
    if len(indices) != 1:
        raise RuntimeError("Incremental FTS requires exactly one content index")
    table.to_lance().optimize.optimize_indices(
        index_names=[indices[0].name], num_indices_to_merge=0, retrain=False,
    )
    table.checkout_latest()
