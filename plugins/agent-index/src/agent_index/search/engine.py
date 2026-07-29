"""Search engine - query-time semantic search.

Provides CLI-oriented search using MultiModelStore and per-model
engine clients.  For API search, see ``agent_index.server.app.search``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_index.index_config import IndexConfig
    from agent_index.embedding.query_embedder import InProcessQueryEmbedder
    from agent_index.engine.client import EngineClient
    from agent_index.store.multi_model_store import MultiModelStore
    from agent_index.store.store import SearchResult

logger = logging.getLogger(__name__)


class SearchEngine:
    """Query-time semantic search over the agent-index index.

    Holds references to per-model query embedders (in-process CPU embedders by
    default, or GPU ``EngineClient``s when ``search_in_process`` is off) and the
    MultiModelStore.  Supports hybrid search across all models.
    """

    def __init__(
        self,
        engine_clients: dict[str, EngineClient | InProcessQueryEmbedder],
        store: MultiModelStore,
    ) -> None:
        self._engine_clients = engine_clients
        self._store = store

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        source: str | None = None,
        language: str | None = None,
        file_path_glob: str | None = None,
        repo: str | None = None,
        labels: list[str] | None = None,
        camera: list[str] | None = None,
        voice: list[str] | None = None,
        model: str | None = None,
    ) -> list[SearchResult]:
        """Run hybrid search across all models (or a specific model).

        Falls back to pure vector search if FTS is unavailable.
        """
        from agent_index.engine.client import EngineUnavailableError

        filter_kwargs = {
            "source": source,
            "language": language,
            "file_path_glob": file_path_glob,
            "repo": repo,
            "labels": labels,
            "camera": camera,
            "voice": voice,
        }

        target_models = [model] if model else list(self._engine_clients)

        vectors = {}
        for model_id in target_models:
            client = self._engine_clients.get(model_id)
            if client is None:
                continue
            try:
                vectors[model_id] = client.embed_query(query)
            except EngineUnavailableError:
                logger.warning("Engine for model '%s' unavailable", model_id)

        if not vectors:
            return []

        return self._store.search_all(
            vectors, query, limit=limit, **filter_kwargs,
        )

    def find_similar(
        self,
        chunk_id: str,
        *,
        limit: int = 10,
        min_score: float = 0.0,
        source: str | None = None,
        language: str | None = None,
        file_path_glob: str | None = None,
        repo: str | None = None,
        labels: list[str] | None = None,
        camera: list[str] | None = None,
        voice: list[str] | None = None,
    ) -> list[SearchResult]:
        """Find items similar to an already-indexed chunk.

        Reuses the chunk's stored embedding (no query embedding needed), so
        this works even when the embedding engines are offline.  Delegates
        to ``MultiModelStore.find_similar``, which keeps the comparison in
        the chunk's own embedding space and excludes the chunk itself.
        """
        return self._store.find_similar(
            chunk_id,
            limit=limit,
            min_score=min_score,
            source=source,
            language=language,
            file_path_glob=file_path_glob,
            repo=repo,
            labels=labels,
            camera=camera,
            voice=voice,
        )


def create_search_engine(config: IndexConfig | None = None) -> SearchEngine:
    """Create a search engine from config.

    By default (``search_in_process``) the query embedders run in-process on the
    CPU, so CLI/API search is decoupled from the GPU engine; set
    ``AGENT_INDEX_SEARCH_IN_PROCESS=0`` to embed queries via the GPU engine subprocess.
    """
    if config is None:
        from agent_index.index_config import IndexConfig

        config = IndexConfig()

    from agent_index.embedding.query_embedder import InProcessQueryEmbedder
    from agent_index.engine.client import EngineClient
    from agent_index.store.multi_model_store import MultiModelStore

    multi_store = MultiModelStore(
        config.lance_dir, content_table=config.content_table,
    )
    engine_clients: dict[str, EngineClient | InProcessQueryEmbedder] = {}
    for profile in config.model_profiles.values():
        multi_store.register_model(profile)
        if config.search_in_process:
            engine_clients[profile.model_id] = InProcessQueryEmbedder(
                profile, device=config.query_device, config=config,
            )
        else:
            engine_clients[profile.model_id] = EngineClient(
                base_url=profile.engine_url,
                query_prefix=profile.query_prefix,
                model_id=profile.model_id,
            )

    return SearchEngine(engine_clients, multi_store)


def run_search(
    *,
    query: str,
    limit: int = 10,
    source: str | None = None,
    language: str | None = None,
    repo: str | None = None,
) -> None:
    """Run a search and print results to stdout (CLI entry point)."""
    from agent_index.index_config import IndexConfig

    config = IndexConfig()
    engine = create_search_engine(config)

    print(f"Searching: {query}")
    if source:
        print(f"  Source: {source}")
    if repo:
        print(f"  Repo: {repo}")
    if language:
        print(f"  Language: {language}")
    print()

    results = engine.search(
        query,
        limit=limit,
        source=source,
        language=language,
        repo=repo,
    )

    if not results:
        print("No results found.")
        return

    for i, r in enumerate(results, 1):
        score_pct = r.score * 100
        print(f"--- Result {i} ({score_pct:.0f}% relevance) ---")
        print(f"  File: {r.file_path}:{r.line_start}-{r.line_end}")
        print(f"  Source: {r.source} | Type: {r.chunk_type} | Lang: {r.language}")
        # Show first 3 lines of content
        lines = r.content.split("\n")
        preview = "\n    ".join(lines[:3])
        if len(lines) > 3:
            preview += f"\n    ... ({len(lines) - 3} more lines)"
        print(f"    {preview}")
        print()
