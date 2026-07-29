"""Multi-model store - facade over ContentStore + N VectorTables.

Provides a unified interface for indexing (upsert) and search (vector,
FTS, hybrid) across multiple embedding models. Each model gets its own
``VectorTable``; content and BM25 FTS are shared globally via
``ContentStore``.

Search flow:
1. Embed query with the specified model's engine client
2. Vector search on that model's VectorTable → ranked chunk_ids
3. FTS search on global ContentStore → ranked chunk_ids
4. RRF fusion across all result lists
5. Hydrate results from ContentStore

For A/B evaluation, ``compare_search`` runs the query against multiple
models and returns per-model ranked results plus a fused ranking.
"""

from __future__ import annotations

import logging
import os
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import numpy as np

from agent_index.store.content_store import ChunkRecord, ContentStore
from agent_index.store.item_repr import (
    ItemRepresentation,
    item_content_hash,
    pool_vectors,
)
from agent_index.store.store import SearchResult
from agent_index.store.vector_table import VectorHit, VectorTable

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from agent_index.chunking.base import Chunk
    from agent_index.index_config import ModelProfile

logger = logging.getLogger(__name__)

# RRF constant - higher values flatten rank differences
_RRF_K = 60

# Default weights for hybrid search fusion.
#
# agent-index's primary goal is semantic ("search by meaning") search, supplemented
# by keyword. The vector signal must therefore outweigh BM25/FTS: otherwise a
# document that merely contains the query string verbatim (e.g. a session log
# that quotes the query) outranks the authoritative semantic match (#880).
# Vector is weighted above FTS so meaning wins and lexical matches only break
# ties / surface exact-token hits. Tunable via env for ops experimentation.
_DEFAULT_VECTOR_WEIGHT = float(os.environ.get("AGENT_INDEX_VECTOR_WEIGHT", "2.0"))
_DEFAULT_FTS_WEIGHT = float(os.environ.get("AGENT_INDEX_FTS_WEIGHT", "1.0"))

# Exact-keyword rescue (#890). The blanket vector>FTS weight flip (#880) fixed
# query-echo but can over-correct: a vector candidate at rank ~50
# (2/(60+51) = 0.018) can out-rank a *top* FTS-only exact hit at rank 1
# (1/(60+1) = 0.016), burying exact lexical matches that have no strong semantic
# signal -- issue IDs, filenames, symbols, config keys. When such an
# identifier-like query appears verbatim (token-bounded) in a result, add a
# bonus in units of one top-rank RRF position so the hit isn't out-ranked by a
# merely mid-rank vector candidate. Deliberately scoped to *single-token*
# queries: multi-word phrases are where query-echo session logs live (#880), so
# boosting a verbatim phrase there would re-break that fix. `0` disables.
_EXACT_MATCH_BONUS = float(os.environ.get("AGENT_INDEX_EXACT_MATCH_BONUS", "1.0"))
# Skip the verbatim check for long queries -- a whole-phrase match by a long NL
# question is vanishingly rare and would only add cost/noise.
_EXACT_MATCH_MAX_QUERY_LEN = 80


def _exact_phrase_bonus(query: str, result: SearchResult, bonus: float) -> float:
    """RRF score bonus when an *identifier-like* query appears verbatim
    (token-bounded) in a result's file path or content -- the exact-keyword
    rescue for #890.

    Only **single-token** queries are rescued (issue IDs, filenames, symbols,
    config keys, dotted/underscored identifiers). Multi-word queries are
    deliberately excluded: a natural-language phrase quoted verbatim is exactly
    the query-echo session log #880 pushed *down*, so boosting it here would
    re-introduce that bug. Returns one top-rank RRF unit
    (``bonus / (_RRF_K + 1)``) on a match, so an exact hit gains roughly the
    ground a rank-1 position would; ``0.0`` otherwise.
    """
    if bonus <= 0:
        return 0.0
    needle = query.strip().lower()
    if len(needle) < 3 or len(needle) > _EXACT_MATCH_MAX_QUERY_LEN:
        return 0.0
    if len(needle.split()) != 1:  # multi-word -> query-echo territory, skip
        return 0.0
    haystack = f"{result.file_path}\n{result.content}".lower()
    if re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", haystack):
        return bonus / (_RRF_K + 1)
    return 0.0

# Vector results are post-filtered by source/repo, so when such a filter is
# set we widen the candidate pool to avoid a small repo's matches never
# entering the globally-ranked top-N.
_FILTER_CANDIDATE_BOOST = 4


@dataclass(frozen=True)
class ModelSearchResult:
    """Results from a single model's search, before cross-model fusion."""

    model_id: str
    results: list[SearchResult]
    vector_hits: list[VectorHit]


class MultiModelStore:
    """Facade over ContentStore + per-model VectorTables.

    Provides a unified API for multi-model indexing and search.
    """

    def __init__(
        self,
        db_path: str | Path,
        *,
        content_table: str = "chunks",
    ) -> None:
        self._content = ContentStore(db_path, table_name=content_table)
        self._vector_tables: dict[str, VectorTable] = {}
        self._db_path = db_path

    @property
    def content_store(self) -> ContentStore:
        return self._content

    def register_model(self, profile: ModelProfile) -> VectorTable:
        """Register a model and create its VectorTable.

        Returns the VectorTable instance for direct access if needed.
        """
        if profile.model_id in self._vector_tables:
            return self._vector_tables[profile.model_id]

        vt = VectorTable(
            self._content.db,
            table_name=profile.table_name,
            dim=profile.dim,
        )
        self._vector_tables[profile.model_id] = vt
        logger.info(
            "Registered model '%s' → table '%s' (dim=%d)",
            profile.model_id,
            profile.table_name,
            profile.dim,
        )
        return vt

    def get_vector_table(self, model_id: str) -> VectorTable:
        """Get the VectorTable for a registered model."""
        if model_id not in self._vector_tables:
            raise KeyError(
                f"Model '{model_id}' not registered. "
                f"Available: {list(self._vector_tables)}"
            )
        return self._vector_tables[model_id]

    @property
    def model_ids(self) -> list[str]:
        return list(self._vector_tables)

    # -- write ---------------------------------------------------------------

    def upsert_content(self, chunks: list[Chunk]) -> int:
        """Store chunk content in the global content table."""
        return self._content.upsert(chunks)

    def upsert_vectors(
        self,
        model_id: str,
        chunks: list[Chunk],
        vectors: np.ndarray,
    ) -> int:
        """Store vectors for a specific model."""
        vt = self.get_vector_table(model_id)
        return vt.upsert(chunks, vectors)

    def upsert(
        self,
        model_id: str,
        chunks: list[Chunk],
        vectors: np.ndarray,
    ) -> int:
        """Store content + vectors in one call (convenience)."""
        self.upsert_content(chunks)
        return self.upsert_vectors(model_id, chunks, vectors)

    def delete_by_source(self, source: str) -> None:
        """Remove all data for a source from content and all vector tables."""
        # Get chunk_ids before deleting content
        chunk_ids = self._get_chunk_ids_for_source(source)
        self._content.delete_by_source(source)
        # Delete vectors by chunk_id from all model tables
        for vt in self._vector_tables.values():
            if chunk_ids:
                vt.delete_by_chunk_ids(chunk_ids)

    def delete_by_source_exact(self, source: str) -> int:
        """Remove data for an EXACT source value (no prefix matching).

        Deletes content + vectors for chunks whose ``source`` equals
        *source* exactly. Returns the number of content chunks removed.
        Used by source garbage collection (#879) so purging a stale generic
        source name never touches a live per-repo source that shares its
        prefix.
        """
        chunk_ids = self._content.get_chunk_ids_by_source_exact(source)
        self._content.delete_by_source_exact(source)
        for vt in self._vector_tables.values():
            if chunk_ids:
                vt.delete_by_chunk_ids(chunk_ids)
        return len(chunk_ids)

    def distinct_sources(self) -> set[str]:
        """Return the set of distinct ``source`` values present in the store."""
        return self._content.distinct_sources()

    def source_counts(self) -> dict[str, int]:
        """Return ``{source: chunk_count}`` actually stored (truthful status)."""
        return self._content.source_counts()

    def get_document(self, source: str, file_path: str) -> ChunkRecord | None:
        """Reconstruct a file's full indexed text (delegates to ContentStore)."""
        return self._content.get_document(source, file_path)

    def distinct_items(self) -> list[tuple[str, str]]:
        """Distinct ``(source, file_path)`` content items in the store.

        The unit the similarity-cluster pass groups by — one issue, one quip,
        one doc, one source file.  Delegates to ContentStore.
        """
        return self._content.distinct_items()

    def item_representation(
        self, source: str, file_path: str,
    ) -> ItemRepresentation | None:
        """Build a content item's per-model centroids + exact-dup key.

        Groups the item's chunks, pools each model's chunk vectors (length-
        weighted by line span, L2-normalized) into a per-space centroid, and
        hashes the line-ordered chunk content hashes into an exact-duplicate
        key.  An item whose chunks span both the code and prose tables gets a
        centroid per space (``ItemRepresentation.is_mixed``); the spaces are
        never blended together.

        Returns None if the item has no indexed chunks.
        """
        records = self._content.get_chunks_for_file(source, file_path)
        if not records:
            return None

        content_hash = item_content_hash([r.content_hash for r in records])

        centroids: dict[str, np.ndarray] = {}
        chunk_ids = [r.chunk_id for r in records]
        for model_id, vt in self._vector_tables.items():
            vec_map = vt.get_vectors(chunk_ids)
            if not vec_map:
                continue
            # Preserve line order; weight each chunk by its line span so longer
            # chunks pull the centroid proportionally.
            vlist: list[np.ndarray] = []
            wlist: list[float] = []
            for r in records:
                v = vec_map.get(r.chunk_id)
                if v is None:
                    continue
                vlist.append(v)
                wlist.append(float(max(r.line_end - r.line_start + 1, 1)))
            if not vlist:
                continue
            centroids[model_id] = pool_vectors(
                np.vstack(vlist), np.asarray(wlist, dtype=np.float32),
            )

        return ItemRepresentation(
            source=source,
            file_path=file_path,
            centroids=centroids,
            content_hash=content_hash,
            chunk_count=len(records),
        )

    def iter_item_representations(self) -> Iterator[ItemRepresentation]:
        """Yield an ``ItemRepresentation`` for every distinct content item.

        Drives the offline clustering pass.  Items with no pooled centroid
        (no vectors yet) are skipped.
        """
        for source, file_path in self._content.distinct_items():
            rep = self.item_representation(source, file_path)
            if rep is not None and rep.centroids:
                yield rep

    def delete_by_file(self, source: str, file_path: str) -> list[str]:
        """Remove content + vectors for a file. Returns deleted chunk_ids.

        Queries ContentStore for chunk_ids first, then deletes from content
        and all VectorTables - no orphaned vectors.
        """
        chunk_ids = self._content.delete_by_file(source, file_path)
        for vt in self._vector_tables.values():
            if chunk_ids:
                vt.delete_by_chunk_ids(chunk_ids)
        return chunk_ids

    def delete_stale_by_file(
        self,
        source: str,
        file_path: str,
        *,
        before: float,
    ) -> int:
        """Remove stale chunks for a specific file (older than *before*).

        Used after re-indexing a modified file: new chunks have a fresh
        ``indexed_at`` timestamp, old chunks are cleaned up here.
        """
        # Get stale chunk_ids from content store
        stale_ids = self._content.get_stale_chunk_ids_by_file(
            source, file_path, before=before,
        )
        if not stale_ids:
            return 0
        # Delete from content store
        self._content.delete_stale_by_file(source, file_path, before=before)
        # Delete from all vector tables
        for vt in self._vector_tables.values():
            vt.delete_by_chunk_ids(stale_ids)
        return len(stale_ids)

    def delete_stale(self, source: str, *, before: float) -> int:
        """Remove stale chunks from content store and all vector tables."""
        removed = self._content.delete_stale(source, before=before)
        for vt in self._vector_tables.values():
            vt.delete_stale(before=before)
        return removed

    # -- maintenance ---------------------------------------------------------

    def compact(self, *, older_than_days: int = 2) -> dict[str, Any]:
        """Compact all LanceDB tables (content + vector tables).

        Merges small data fragments and cleans up old versions to
        reclaim disk space and improve query performance.

        Returns:
            Dict mapping table names to their compaction stats.
        """
        results: dict[str, Any] = {}

        logger.info("Starting LanceDB compaction across all tables")
        results["content"] = self._content.compact(
            older_than_days=older_than_days,
        )

        for model_id, vt in self._vector_tables.items():
            results[f"vectors_{model_id}"] = vt.compact(
                older_than_days=older_than_days,
            )

        total_before = sum(r.get("fragments_before", 0) for r in results.values())
        total_after = sum(r.get("fragments_after", 0) for r in results.values())
        logger.info(
            "LanceDB compaction complete: %d -> %d fragments across %d tables",
            total_before, total_after, len(results),
        )

        return results

    # -- FTS -----------------------------------------------------------------

    def ensure_fts_index(self) -> bool:
        return self._content.ensure_fts_index()

    def mark_fts_dirty(self) -> None:
        self._content.mark_fts_dirty()

    @property
    def fts_available(self) -> bool:
        return self._content.fts_available

    @property
    def fts_dirty(self) -> bool:
        return self._content.fts_dirty

    def fts_rebuild_due(self) -> bool:
        return self._content.fts_rebuild_due()

    @property
    def fts_consecutive_failures(self) -> int:
        return self._content.fts_consecutive_failures

    # -- search --------------------------------------------------------------

    def search(
        self,
        model_id: str,
        vector: np.ndarray,
        *,
        limit: int = 10,
        source: str | None = None,
        language: str | None = None,
        file_path_glob: str | None = None,
        repo: str | None = None,
        labels: list[str] | None = None,
        camera: list[str] | None = None,
        voice: list[str] | None = None,
    ) -> list[SearchResult]:
        """Pure vector search using one model, hydrated from content store."""
        vt = self.get_vector_table(model_id)
        candidate_limit = max(limit * 3, 30)
        if source or repo or labels or camera or voice:
            candidate_limit *= _FILTER_CANDIDATE_BOOST
        hits = vt.search(vector, limit=candidate_limit)

        if not hits:
            return []

        results = self._hydrate_hits(hits)
        results = _apply_filters(
            results, source=source, language=language,
            file_path_glob=file_path_glob, repo=repo, labels=labels,
            camera=camera, voice=voice,
        )
        return results[:limit]

    def _vector_table_for_chunk(
        self, chunk_id: str,
    ) -> tuple[str, VectorTable, np.ndarray] | None:
        """Locate the model table that holds *chunk_id* and its vector.

        A chunk lives in exactly one model table (code vs prose), chosen at
        index time by ``chunk_type``.  Probing the tables for the stored
        vector keeps similarity comparisons inside a single embedding space
        without the store needing to know the content-type routing rules.
        """
        for model_id, vt in self._vector_tables.items():
            vec = vt.get_vector(chunk_id)
            if vec is not None:
                return model_id, vt, vec
        return None

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
        """Find chunks most similar to an already-indexed chunk.

        Reuses the chunk's stored embedding (no re-embedding) and searches
        the *same* model table it lives in, so comparisons never cross the
        code/prose embedding-space boundary.  The chunk itself is excluded
        from the results.

        Args:
            chunk_id: The reference chunk to find neighbours for.
            limit: Maximum neighbours to return.
            min_score: Drop neighbours with cosine score below this.
            source/language/file_path_glob/repo/labels: Optional metadata
                filters applied to the neighbours (same semantics as
                ``search``).

        Returns:
            Ranked neighbours (highest cosine first), excluding *chunk_id*.
            Empty if the chunk has no stored vector.
        """
        located = self._vector_table_for_chunk(chunk_id)
        if located is None:
            return []
        _model_id, vt, vector = located

        # Over-fetch: +1 covers the guaranteed self-match, the multiplier
        # leaves headroom for post-hydration metadata filtering.
        candidate_limit = max((limit + 1) * 3, 30)
        if source or repo or labels or camera or voice:
            candidate_limit *= _FILTER_CANDIDATE_BOOST
        hits = vt.search(vector, limit=candidate_limit)

        hits = [
            h for h in hits
            if h.chunk_id != chunk_id and h.score >= min_score
        ]
        if not hits:
            return []

        results = self._hydrate_hits(hits)
        results = _apply_filters(
            results, source=source, language=language,
            file_path_glob=file_path_glob, repo=repo, labels=labels,
            camera=camera, voice=voice,
        )
        return results[:limit]

    def hybrid_search(
        self,
        model_id: str,
        vector: np.ndarray,
        query_text: str,
        *,
        limit: int = 10,
        source: str | None = None,
        language: str | None = None,
        file_path_glob: str | None = None,
        repo: str | None = None,
        labels: list[str] | None = None,
        camera: list[str] | None = None,
        voice: list[str] | None = None,
        vector_weight: float = _DEFAULT_VECTOR_WEIGHT,
        fts_weight: float = _DEFAULT_FTS_WEIGHT,
    ) -> list[SearchResult]:
        """Hybrid (vector + BM25) search using one model.

        Falls back to pure vector search if FTS is unavailable.
        """
        candidate_limit = max(limit * 5, 50)
        if source or repo or labels or camera or voice:
            candidate_limit *= _FILTER_CANDIDATE_BOOST
        filter_kwargs = {
            "source": source,
            "language": language,
            "file_path_glob": file_path_glob,
            "repo": repo,
            "labels": labels,
            "camera": camera,
            "voice": voice,
        }

        # Vector search via model's table
        vt = self.get_vector_table(model_id)
        vector_hits = vt.search(vector, limit=candidate_limit)

        if not self.fts_available:
            results = self._hydrate_hits(vector_hits)
            results = _apply_filters(results, **filter_kwargs)
            deduped = _deduplicate_by_content(results)
            return deduped[:limit]

        # FTS search via global content store
        _fts_excluded = set(_FACET_KEYS)
        fts_filter_kwargs = {
            k: v for k, v in filter_kwargs.items() if k not in _fts_excluded
        }
        fts_hits = self._content.fts_search(
            query_text, limit=candidate_limit, **fts_filter_kwargs
        )

        if not fts_hits:
            results = self._hydrate_hits(vector_hits)
            results = _apply_filters(results, **filter_kwargs)
            deduped = _deduplicate_by_content(results)
            return deduped[:limit]

        # Collect all chunk_ids for hydration
        all_ids = list({
            h.chunk_id for h in vector_hits
        } | {
            cid for cid, _ in fts_hits
        })

        records = self._content.get_by_ids(all_ids)
        if not records:
            return []

        # Build SearchResult lists for RRF
        vector_results = _apply_filters(
            _hits_to_results(vector_hits, records), **filter_kwargs
        )
        # FTS SQL filters don't cover post-filter-only facets (e.g. labels),
        # so re-apply the full filter set to the FTS branch too.
        fts_results = _apply_filters(
            _fts_to_results(fts_hits, records), **filter_kwargs
        )

        merged = _reciprocal_rank_fusion(
            vector_results,
            fts_results,
            vector_weight=vector_weight,
            fts_weight=fts_weight,
            query=query_text,
        )
        deduped = _deduplicate_by_content(merged)
        normalized = _normalize_scores(deduped)
        return normalized[:limit]

    def search_all(
        self,
        vectors_by_model: dict[str, np.ndarray],
        query_text: str,
        *,
        limit: int = 10,
        source: str | None = None,
        language: str | None = None,
        file_path_glob: str | None = None,
        repo: str | None = None,
        labels: list[str] | None = None,
        camera: list[str] | None = None,
        voice: list[str] | None = None,
        vector_weight: float = _DEFAULT_VECTOR_WEIGHT,
        fts_weight: float = _DEFAULT_FTS_WEIGHT,
    ) -> list[SearchResult]:
        """Cross-model search: fan out to all models, fuse with RRF.

        Embeds the query with each model's engine (done upstream), runs
        vector search on each model's table, adds FTS if available, and
        fuses all ranked lists with weighted RRF.
        """
        candidate_limit = max(limit * 5, 50)
        if source or repo or labels or camera or voice:
            candidate_limit *= _FILTER_CANDIDATE_BOOST
        result_lists: list[list[SearchResult]] = []
        weights: list[float] = []

        # Vector search per model
        for model_id, vector in vectors_by_model.items():
            vt = self.get_vector_table(model_id)
            hits = vt.search(vector, limit=candidate_limit)
            results = self._hydrate_hits(hits)
            # Post-filter by metadata if requested
            results = _apply_filters(
                results, source=source, language=language,
                file_path_glob=file_path_glob, repo=repo, labels=labels,
            camera=camera, voice=voice,
            )
            result_lists.append(results)
            weights.append(vector_weight)

        # FTS if available
        if self.fts_available:
            fts_hits = self._content.fts_search(
                query_text, limit=candidate_limit,
                source=source, language=language,
                file_path_glob=file_path_glob, repo=repo,
            )
            if fts_hits:
                records = self._content.get_by_ids(
                    [cid for cid, _ in fts_hits]
                )
                # FTS SQL filters don't cover post-filter-only facets
                # (e.g. labels), so re-apply the full filter set here too.
                fts_results = _apply_filters(
                    _fts_to_results(fts_hits, records),
                    source=source, language=language,
                    file_path_glob=file_path_glob, repo=repo, labels=labels,
            camera=camera, voice=voice,
                )
                result_lists.append(fts_results)
                weights.append(fts_weight)

        if not result_lists:
            return []

        # Single-list shortcut - skip RRF overhead
        if len(result_lists) == 1:
            deduped = _deduplicate_by_content(result_lists[0])
            normalized = _normalize_scores(deduped)
            return normalized[:limit]

        fused = _multi_list_rrf(result_lists, weights=weights, query=query_text)
        deduped = _deduplicate_by_content(fused)
        normalized = _normalize_scores(deduped)
        return normalized[:limit]

    def fts_search(
        self,
        query_text: str,
        *,
        limit: int = 10,
        source: str | None = None,
        language: str | None = None,
        file_path_glob: str | None = None,
        repo: str | None = None,
        labels: list[str] | None = None,
        camera: list[str] | None = None,
        voice: list[str] | None = None,
    ) -> list[SearchResult]:
        """Pure BM25 full-text search via the shared content store."""
        fts_hits = self._content.fts_search(
            query_text, limit=limit,
            source=source, language=language,
            file_path_glob=file_path_glob, repo=repo,
        )
        if not fts_hits:
            return []
        records = self._content.get_by_ids([cid for cid, _ in fts_hits])
        results = _fts_to_results(fts_hits, records)
        if labels or camera or voice:
            results = _apply_filters(
                results, labels=labels, camera=camera, voice=voice
            )
        return results

    def compare_search(
        self,
        vectors_by_model: dict[str, np.ndarray],
        query_text: str,
        *,
        limit: int = 10,
    ) -> dict[str, list[SearchResult]]:
        """A/B evaluation: search each model and return per-model results.

        Args:
            vectors_by_model: {model_id: query_vector} from each engine.
            query_text: Raw query text for FTS component.
            limit: Max results per model.

        Returns:
            {model_id: ranked_results} for side-by-side comparison.
        """
        results: dict[str, list[SearchResult]] = {}
        for model_id, vector in vectors_by_model.items():
            results[model_id] = self.hybrid_search(
                model_id,
                vector,
                query_text,
                limit=limit,
            )
        return results

    # -- stats ---------------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        """Combined statistics across content and vector tables."""
        content_stats = self._content.stats()
        vector_stats = {}
        for model_id, vt in self._vector_tables.items():
            vector_stats[model_id] = {
                "table_name": vt.table_name,
                "vector_count": vt.count(),
                "dim": vt.dim,
            }
        content_stats["models"] = vector_stats
        return content_stats

    def distinct_forge_repos(self) -> list[str]:
        """Return sorted distinct ``owner/repo`` names from indexed Forge sources.

        Forge sources are named ``forge:<subtype>:<owner>/<repo>``.  This
        parses the trailing ``owner/repo`` from every such source so the
        search UI can offer a repo sub-filter.
        """
        repos: set[str] = set()
        for src in self._content.distinct_sources():
            parts = src.split(":", 2)
            if len(parts) == 3 and parts[0] == "forge" and "/" in parts[2]:
                repos.add(parts[2])
        return sorted(repos)

    # -- internal helpers ----------------------------------------------------

    def _hydrate_hits(
        self,
        hits: list[VectorHit],
        *,
        limit: int | None = None,
    ) -> list[SearchResult]:
        """Join VectorHits with content from ContentStore."""
        if not hits:
            return []

        chunk_ids = [h.chunk_id for h in hits]
        records = self._content.get_by_ids(chunk_ids)

        results = []
        for h in hits:
            rec = records.get(h.chunk_id)
            if rec is None:
                continue
            results.append(SearchResult(
                chunk_id=h.chunk_id,
                score=h.score,
                source=rec.source,
                file_path=rec.file_path,
                chunk_type=rec.chunk_type,
                language=rec.language,
                content=rec.content,
                content_hash=rec.content_hash,
                line_start=rec.line_start,
                line_end=rec.line_end,
                metadata=rec.metadata,
            ))

        if limit is not None:
            results = results[:limit]
        return results

    def _get_chunk_ids_for_source(self, source: str) -> list[str]:
        """Get all chunk_ids for a source (exact + prefix match)."""
        table = self._content._get_or_create_table()
        try:
            rows = (
                table.search()
                .where(f"source = '{source}' OR source LIKE '{source}:%'")
                .select(["chunk_id"])
                .limit(100000)
                .to_list()
            )
            return [r["chunk_id"] for r in rows]
        except Exception:
            return []


# -- fusion helpers (factored from store.py) ---------------------------------


def _hits_to_results(
    hits: list[VectorHit],
    records: dict[str, Any],
) -> list[SearchResult]:
    """Convert VectorHits + ChunkRecords → SearchResults."""
    results = []
    for h in hits:
        rec = records.get(h.chunk_id)
        if rec is None:
            continue
        results.append(SearchResult(
            chunk_id=h.chunk_id,
            score=h.score,
            source=rec.source,
            file_path=rec.file_path,
            chunk_type=rec.chunk_type,
            language=rec.language,
            content=rec.content,
            content_hash=rec.content_hash,
            line_start=rec.line_start,
            line_end=rec.line_end,
            metadata=getattr(rec, "metadata", {}),
        ))
    return results


def _fts_to_results(
    fts_hits: list[tuple[str, float]],
    records: dict[str, Any],
) -> list[SearchResult]:
    """Convert FTS (chunk_id, score) pairs + ChunkRecords → SearchResults."""
    results = []
    for chunk_id, score in fts_hits:
        rec = records.get(chunk_id)
        if rec is None:
            continue
        results.append(SearchResult(
            chunk_id=chunk_id,
            score=score,
            source=rec.source,
            file_path=rec.file_path,
            chunk_type=rec.chunk_type,
            language=rec.language,
            content=rec.content,
            content_hash=rec.content_hash,
            line_start=rec.line_start,
            line_end=rec.line_end,
            metadata=getattr(rec, "metadata", {}),
        ))
    return results


def _reciprocal_rank_fusion(
    vector_results: list[SearchResult],
    fts_results: list[SearchResult],
    *,
    vector_weight: float = _DEFAULT_VECTOR_WEIGHT,
    fts_weight: float = _DEFAULT_FTS_WEIGHT,
    query: str | None = None,
) -> list[SearchResult]:
    """Merge two ranked lists using weighted Reciprocal Rank Fusion."""
    scores: dict[str, float] = defaultdict(float)
    results_by_id: dict[str, SearchResult] = {}

    for rank, r in enumerate(vector_results):
        scores[r.chunk_id] += vector_weight / (_RRF_K + rank + 1)
        results_by_id[r.chunk_id] = r

    for rank, r in enumerate(fts_results):
        scores[r.chunk_id] += fts_weight / (_RRF_K + rank + 1)
        if r.chunk_id not in results_by_id:
            results_by_id[r.chunk_id] = r

    if query and _EXACT_MATCH_BONUS > 0:
        for cid, r in results_by_id.items():
            scores[cid] += _exact_phrase_bonus(query, r, _EXACT_MATCH_BONUS)

    ranked_ids = sorted(scores, key=scores.__getitem__, reverse=True)

    return [
        SearchResult(
            chunk_id=r.chunk_id,
            score=scores[r.chunk_id],
            source=r.source,
            file_path=r.file_path,
            chunk_type=r.chunk_type,
            language=r.language,
            content=r.content,
            content_hash=r.content_hash,
            line_start=r.line_start,
            line_end=r.line_end,
            metadata=r.metadata,
        )
        for cid in ranked_ids
        if (r := results_by_id[cid])
    ]


def _multi_list_rrf(
    result_lists: list[list[SearchResult]],
    *,
    weights: list[float] | None = None,
    query: str | None = None,
) -> list[SearchResult]:
    """Weighted Reciprocal Rank Fusion across N ranked lists."""
    if weights is None:
        weights = [1.0] * len(result_lists)

    scores: dict[str, float] = defaultdict(float)
    results_by_id: dict[str, SearchResult] = {}

    for result_list, weight in zip(result_lists, weights, strict=False):
        for rank, r in enumerate(result_list):
            scores[r.chunk_id] += weight / (_RRF_K + rank + 1)
            if r.chunk_id not in results_by_id:
                results_by_id[r.chunk_id] = r

    if query and _EXACT_MATCH_BONUS > 0:
        for cid, r in results_by_id.items():
            scores[cid] += _exact_phrase_bonus(query, r, _EXACT_MATCH_BONUS)

    ranked_ids = sorted(scores, key=scores.__getitem__, reverse=True)

    return [
        SearchResult(
            chunk_id=r.chunk_id,
            score=scores[r.chunk_id],
            source=r.source,
            file_path=r.file_path,
            chunk_type=r.chunk_type,
            language=r.language,
            content=r.content,
            content_hash=r.content_hash,
            line_start=r.line_start,
            line_end=r.line_end,
            metadata=r.metadata,
        )
        for cid in ranked_ids
        if (r := results_by_id[cid])
    ]


def _facet_values(metadata: dict | None, key: str) -> set[str]:
    """Return a metadata facet's value(s) as a string set.

    Facets are stored either as a scalar (``camera``) or a list (``labels``,
    ``voice``, ``tags``); normalize both to a set so matching is uniform.
    """
    if not metadata:
        return set()
    val = metadata.get(key)
    if val is None:
        return set()
    if isinstance(val, list):
        return {str(v) for v in val}
    return {str(val)}


# Metadata facets filtered with OR semantics, keyed by request param name.
# ``camera`` / ``voice`` are treated exactly like ``labels`` — a multi-valued
# facet match — they just read a different metadata key.
_FACET_KEYS = ("labels", "camera", "voice")


def _apply_filters(
    results: list[SearchResult],
    *,
    source: str | None = None,
    language: str | None = None,
    file_path_glob: str | None = None,
    repo: str | None = None,
    labels: list[str] | None = None,
    camera: list[str] | None = None,
    voice: list[str] | None = None,
) -> list[SearchResult]:
    """Post-filter search results by metadata fields.

    ``source`` matches a source exactly or as a subtype prefix
    (``forge:issues`` matches ``forge:issues:owner/repo``).  ``repo`` is an
    orthogonal sub-filter that matches the trailing ``owner/repo`` of any
    Forge source regardless of subtype (``forge:*:owner/repo``).
    ``labels`` / ``camera`` / ``voice`` are multi-valued metadata facets that
    each use OR semantics — a result matches if its corresponding
    ``metadata`` facet contains ANY of the requested values.
    """
    facets = {"labels": labels, "camera": camera, "voice": voice}
    if not any((source, language, file_path_glob, repo, *facets.values())):
        return results

    filtered = results
    if source:
        filtered = [r for r in filtered if r.source == source or r.source.startswith(f"{source}:")]
    if language:
        filtered = [r for r in filtered if r.language == language]
    if file_path_glob:
        import fnmatch
        filtered = [r for r in filtered if fnmatch.fnmatch(r.file_path, file_path_glob)]
    if repo:
        filtered = [r for r in filtered if r.source.endswith(f":{repo}")]
    for key in _FACET_KEYS:
        wanted = facets[key]
        if wanted:
            wanted_set = set(wanted)
            filtered = [
                r for r in filtered if wanted_set & _facet_values(r.metadata, key)
            ]
    return filtered


def _deduplicate_by_content(results: list[SearchResult]) -> list[SearchResult]:
    """Two-phase dedup: content-hash then file-path overlap."""
    # Phase 1: content_hash dedup
    seen_hash: dict[str, int] = {}
    phase1: list[SearchResult] = []

    for r in results:
        key = r.content_hash or r.chunk_id
        if key not in seen_hash:
            seen_hash[key] = len(phase1)
            phase1.append(r)
        else:
            existing_idx = seen_hash[key]
            if r.score > phase1[existing_idx].score:
                phase1[existing_idx] = r

    # Phase 2: file-path + line-overlap dedup (cross-source only)
    deduped: list[SearchResult] = []
    for r in phase1:
        overlap_idx = _find_overlapping(deduped, r)
        if overlap_idx is None:
            deduped.append(r)
        elif r.score > deduped[overlap_idx].score:
            deduped[overlap_idx] = r

    return deduped


def _find_overlapping(
    existing: list[SearchResult], candidate: SearchResult
) -> int | None:
    for i, e in enumerate(existing):
        if e.source == candidate.source:
            continue
        if not _paths_match(e.file_path, candidate.file_path):
            continue
        overlap = _line_overlap_ratio(e, candidate)
        if overlap >= 0.5:
            return i
    return None


def _paths_match(path_a: str, path_b: str) -> bool:
    if path_a == path_b:
        return True
    return path_a.endswith("/" + path_b) or path_b.endswith("/" + path_a)


def _line_overlap_ratio(a: SearchResult, b: SearchResult) -> float:
    overlap_start = max(a.line_start, b.line_start)
    overlap_end = min(a.line_end, b.line_end)
    overlap_lines = max(0, overlap_end - overlap_start + 1)
    if overlap_lines == 0:
        return 0.0
    span_a = a.line_end - a.line_start + 1
    span_b = b.line_end - b.line_start + 1
    return overlap_lines / min(span_a, span_b)


def _normalize_scores(results: list[SearchResult]) -> list[SearchResult]:
    """Normalize scores to 0-1 range (top result = 1.0)."""
    if not results:
        return results
    max_score = results[0].score
    if max_score <= 0:
        return results
    return [
        SearchResult(
            chunk_id=r.chunk_id,
            score=r.score / max_score,
            source=r.source,
            file_path=r.file_path,
            chunk_type=r.chunk_type,
            language=r.language,
            content=r.content,
            content_hash=r.content_hash,
            line_start=r.line_start,
            line_end=r.line_end,
            metadata=r.metadata,
        )
        for r in results
    ]
