"""Search result types and hybrid-search utility functions.

Shared by ``MultiModelStore`` and legacy code paths.  The ``VectorStore``
class that previously lived here was removed as part of the multi-model
migration (#292).
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

EMBEDDING_DIM = 768

# RRF constant — higher values flatten rank differences
_RRF_K = 60

# Default weights for hybrid search fusion
_DEFAULT_VECTOR_WEIGHT = 1.0
_DEFAULT_FTS_WEIGHT = 2.0


@dataclass(frozen=True)
class SearchResult:
    """A single search result with score and metadata."""

    chunk_id: str
    score: float
    source: str
    file_path: str
    chunk_type: str
    language: str
    content: str
    content_hash: str
    line_start: int
    line_end: int
    metadata: dict[str, Any] = field(default_factory=dict, compare=False)




# -- hybrid search helpers ---------------------------------------------------


def _sanitize_fts_query(query: str) -> str:
    """Strip FTS operators and special chars that might confuse Tantivy."""
    # Remove Tantivy/Lucene-style operators
    cleaned = re.sub(r'[+\-!(){}[\]^"~*?:\\/<>]', " ", query)
    # Collapse whitespace
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _reciprocal_rank_fusion(
    vector_results: list[SearchResult],
    fts_results: list[SearchResult],
    *,
    vector_weight: float = _DEFAULT_VECTOR_WEIGHT,
    fts_weight: float = _DEFAULT_FTS_WEIGHT,
) -> list[SearchResult]:
    """Merge two ranked lists using weighted Reciprocal Rank Fusion.

    RRF score for each item = sum(weight / (k + rank)) across lists.
    """
    scores: dict[str, float] = defaultdict(float)
    results_by_id: dict[str, SearchResult] = {}

    for rank, r in enumerate(vector_results):
        scores[r.chunk_id] += vector_weight / (_RRF_K + rank + 1)
        results_by_id[r.chunk_id] = r

    for rank, r in enumerate(fts_results):
        scores[r.chunk_id] += fts_weight / (_RRF_K + rank + 1)
        if r.chunk_id not in results_by_id:
            results_by_id[r.chunk_id] = r

    # Sort by fused score descending
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


def _deduplicate_by_content(results: list[SearchResult]) -> list[SearchResult]:
    """Remove duplicate chunks (same content from different sources).

    Two-phase deduplication:
    1. Content-hash dedup — identical content from different sources collapses.
    2. File-path overlap dedup — same file path from different sources with
       significantly overlapping line ranges (≥50%) collapses.

    Keeps the highest-scored copy in each case.
    """
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
    """Find an existing result that overlaps the candidate (cross-source).

    Returns the index of the overlapping result, or None.
    Overlap must be ≥50% of the smaller chunk's line span.
    """
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
    """Check if two file paths refer to the same file.

    Handles cases where sources use different path prefixes
    (e.g., 'docs/foo.md' vs 'owner/repo/docs/foo.md').
    """
    if path_a == path_b:
        return True
    # Check if one is a suffix of the other
    return path_a.endswith("/" + path_b) or path_b.endswith("/" + path_a)


def _line_overlap_ratio(a: SearchResult, b: SearchResult) -> float:
    """Compute overlap ratio between two line ranges.

    Returns overlap_lines / min(span_a, span_b), or 0.0 if no overlap.
    """
    overlap_start = max(a.line_start, b.line_start)
    overlap_end = min(a.line_end, b.line_end)
    overlap_lines = max(0, overlap_end - overlap_start + 1)
    if overlap_lines == 0:
        return 0.0
    span_a = a.line_end - a.line_start + 1
    span_b = b.line_end - b.line_start + 1
    return overlap_lines / min(span_a, span_b)


def _normalize_scores(results: list[SearchResult]) -> list[SearchResult]:
    """Normalize RRF scores to 0-1 range using top-relative scaling.

    Divides all scores by the maximum score so the top result = 1.0
    and other results express their strength relative to the best match.
    """
    if not results:
        return results
    max_score = results[0].score  # results are already sorted descending
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
