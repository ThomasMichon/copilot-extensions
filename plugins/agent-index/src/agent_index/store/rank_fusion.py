"""Weighted rank fusion and identifier-only exact-match rescue."""

from __future__ import annotations

import os
import re
from collections import defaultdict

from agent_index.store.store import SearchResult

_RRF_K = 60
# Meaning outweighs lexical query echoes; exact identifiers can still be rescued.
_DEFAULT_VECTOR_WEIGHT = float(os.environ.get("AGENT_INDEX_VECTOR_WEIGHT", "2.0"))
_DEFAULT_FTS_WEIGHT = float(os.environ.get("AGENT_INDEX_FTS_WEIGHT", "1.0"))
_EXACT_MATCH_BONUS = float(os.environ.get("AGENT_INDEX_EXACT_MATCH_BONUS", "1.0"))
_EXACT_MATCH_MAX_QUERY_LEN = 80


def _exact_phrase_bonus(query: str, result: SearchResult, bonus: float) -> float:
    """Return a top-rank RRF bonus for a token-bounded identifier match.

    Multi-word queries are excluded so quoted natural-language queries do not
    outrank their semantic matches merely by echoing the question.
    """
    if bonus <= 0:
        return 0.0
    needle = query.strip().lower()
    if len(needle) < 3 or len(needle) > _EXACT_MATCH_MAX_QUERY_LEN:
        return 0.0
    if len(needle.split()) != 1:
        return 0.0
    haystack = f"{result.file_path}\n{result.content}".lower()
    if re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", haystack):
        return bonus / (_RRF_K + 1)
    return 0.0


def _reciprocal_rank_fusion(
    vector_results: list[SearchResult],
    fts_results: list[SearchResult],
    *,
    vector_weight: float = _DEFAULT_VECTOR_WEIGHT,
    fts_weight: float = _DEFAULT_FTS_WEIGHT,
    query: str | None = None,
    exact_match_bonus: float = _EXACT_MATCH_BONUS,
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

    if query and exact_match_bonus > 0:
        for cid, r in results_by_id.items():
            scores[cid] += _exact_phrase_bonus(query, r, exact_match_bonus)

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
    exact_match_bonus: float = _EXACT_MATCH_BONUS,
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

    if query and exact_match_bonus > 0:
        for cid, r in results_by_id.items():
            scores[cid] += _exact_phrase_bonus(query, r, exact_match_bonus)

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
