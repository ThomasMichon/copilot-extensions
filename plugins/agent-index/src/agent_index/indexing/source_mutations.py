"""Attach one diagnostic summary after the existing source publication succeeds."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING

from agent_index.indexing.work_counts import current_counts
from agent_index.store.mutation_result import observe_mutations

if TYPE_CHECKING:
    from agent_index.store.multi_model_store import MultiModelStore


@contextmanager
def source_publication(store: MultiModelStore, source: str) -> Iterator[None]:
    """Failed application, cursor save or journal completion emits no success result."""
    counts = current_counts()
    if counts is None:
        yield
        return
    with observe_mutations(getattr(store, "_source_domain", None)) as result:
        yield
    counts.source_mutations.setdefault(source, []).append(result)
