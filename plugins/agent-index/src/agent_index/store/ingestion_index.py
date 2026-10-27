"""Point/file index upkeep within the existing source publication owner."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .scalar_index import (
    MAX_SEGMENTS,
    ScalarIndexPreparationRequired,
    consolidate_index,
    consolidation_rows,
    inspect_index,
    prepare_index,
    require_budget,
)

if TYPE_CHECKING:
    from .content_store import ContentStore
    from .multi_model_store import MultiModelStore
    from .vector_table import VectorTable


def _owners(store: MultiModelStore) -> list[tuple[ContentStore | VectorTable, str]]:
    owners: list[tuple[ContentStore | VectorTable, str]] = [
        (store.content_store, "chunk_id"), (store.content_store, "file_path"),
    ]
    owners.extend((store.get_vector_table(model), "chunk_id") for model in store.model_ids)
    return owners


def prepare_ingestion_indices(
    store: MultiModelStore, *, max_rows: int, reserve_segment: bool = False,
) -> None:
    """Cover only budgeted tails; refuse implicit corpus builds or consolidation."""
    if type(max_rows) is not int or max_rows < 0:
        raise ValueError("Ingestion preparation budget must be a nonnegative integer")
    domain = store._source_domain
    if domain is None:
        raise ScalarIndexPreparationRequired("Ingestion indices require a participating source store")
    with domain.operation(store.content_store.db):
        owners = _owners(store)
        for owner, column in owners:
            state, _ = inspect_index(owner._get_or_create_table(), column=column)
            require_budget(state, max_rows)
            if reserve_segment and state.segments >= MAX_SEGMENTS:
                raise ScalarIndexPreparationRequired(
                    "Ingestion index segment budget exceeded; explicit consolidation required"
                )
        for owner, column in owners:
            prepare_index(owner, column=column, max_rows=max_rows)


def consolidate_ingestion_indices(store: MultiModelStore, *, max_rows: int) -> None:
    """Explicit maintenance only: budget each index's whole physical table before effects."""
    if type(max_rows) is not int or max_rows < 0:
        raise ValueError("Ingestion consolidation budget must be a nonnegative integer")
    domain = store._source_domain
    if domain is None:
        raise ScalarIndexPreparationRequired("Consolidation requires a participating source store")
    with domain.operation(store.content_store.db):
        owners = _owners(store)
        for owner, column in owners:
            state, dataset = inspect_index(owner._get_or_create_table(), column=column)
            consolidation_rows(state, dataset, max_rows=max_rows)
        for owner, column in owners:
            consolidate_index(owner, column=column, max_rows=max_rows)
