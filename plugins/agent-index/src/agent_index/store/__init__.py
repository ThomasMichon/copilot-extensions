"""Vector store — LanceDB wrapper for chunk storage and search.

Three-tier storage:
- ``ContentStore``: chunk text, metadata, global BM25 FTS index
- ``VectorTable``: per-model embedding vectors keyed by chunk_id
- ``MultiModelStore``: unified facade over ContentStore + N VectorTables
"""

from __future__ import annotations

from agent_index.store.content_store import ChunkRecord, ContentStore
from agent_index.store.multi_model_store import MultiModelStore
from agent_index.store.store import SearchResult
from agent_index.store.vector_table import VectorHit, VectorTable

__all__ = [
    "ChunkRecord",
    "ContentStore",
    "MultiModelStore",
    "SearchResult",
    "VectorHit",
    "VectorTable",
]
