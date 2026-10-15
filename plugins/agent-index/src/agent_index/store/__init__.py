"""Vector store — LanceDB wrapper for chunk storage and search.

Three-tier storage:
- ``ContentStore``: chunk text, metadata, global BM25 FTS index
- ``VectorTable``: per-model embedding vectors keyed by chunk_id
- ``MultiModelStore``: unified facade over ContentStore + N VectorTables
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_index.store.content_store import ChunkRecord, ContentStore
    from agent_index.store.multi_model_store import MultiModelStore
    from agent_index.store.store import SearchResult
    from agent_index.store.vector_table import VectorHit, VectorTable

_EXPORTS = {
    "ChunkRecord": ".content_store",
    "ContentStore": ".content_store",
    "MultiModelStore": ".multi_model_store",
    "SearchResult": ".store",
    "VectorHit": ".vector_table",
    "VectorTable": ".vector_table",
}

__all__ = [
    "ChunkRecord",
    "ContentStore",
    "MultiModelStore",
    "SearchResult",
    "VectorHit",
    "VectorTable",
]


def __getattr__(name: str) -> object:
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(_EXPORTS[name], __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
