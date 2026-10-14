"""Native prefiltered reads for a complete, independently owned cold domain."""

from __future__ import annotations

from .content_store import sanitize_fts_query
from .repo_filter import _like_pattern, _sql_str, repo_filter_sql
from .source_layout import SourceLayoutError


def source_predicate(families: tuple[str, ...], language=None, repo=None) -> str:
    if not families:
        return "FALSE"
    if len(families) > 32:
        raise ValueError("too many authorized families")
    for selector in (language, repo):
        if selector is not None and (
            not isinstance(selector, str) or not selector or len(selector.encode()) > 256
        ):
            raise ValueError("invalid authorized selector")
    clauses = []
    for family in families:
        if (not isinstance(family, str) or not family or len(family.encode()) > 256
                or any(ord(char) < 32 for char in family)):
            raise ValueError("invalid authorized family")
        clauses.append(
            f"(source = '{_sql_str(family)}' OR "
            f"source LIKE '{_like_pattern(family)}:%' ESCAPE '\\')"
        )
    result = "(" + " OR ".join(clauses) + ")"
    if language is not None:
        result += f" AND language = '{_sql_str(language)}'"
    if repo is not None:
        result += f" AND ({repo_filter_sql(repo)})"
    return result


def search_source_store(store, vectors: dict, query: str, *, families: tuple[str, ...],
                        limit: int = 10, language=None, repo=None, include_fts=True):
    """Caller supplies verified canonical families; this API does not authenticate grants."""
    from .multi_model_store import (
        _DEFAULT_FTS_WEIGHT,
        _DEFAULT_VECTOR_WEIGHT,
        _deduplicate_by_content,
        _fts_to_results,
        _hits_to_results,
        _multi_list_rrf,
        _normalize_scores,
    )
    from .vector_table import VectorHit

    domain = store._source_domain
    if domain is None:
        raise SourceLayoutError("source-aware retrieval requires selected cold layout")
    if not families:
        return []
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("invalid search limit")
    predicate = source_predicate(families, language, repo)
    window = max(50, limit * 5)
    with domain.read(store.content_store.db):
        content = store.content_store._get_or_create_table()
        fts_ready = domain.require_fts(store.content_store) if include_fts else False
        branches = []
        for model, vector in vectors.items():
            table = store.get_vector_table(model)._get_or_create_table()
            if not {"source", "language"} <= set(table.schema.names):
                raise SourceLayoutError("source projection incomplete")
            rows = (
                table.search(vector.tolist()).metric("cosine").bypass_vector_index()
                .where(predicate, prefilter=True).limit(window).to_list()
            )
            branches.append(sorted([
                VectorHit(r["chunk_id"], 1.0 - r["_distance"]) for r in rows
            ], key=lambda hit: (-hit.score, hit.chunk_id)))
        lexical = []
        if fts_ready:
            sanitized = sanitize_fts_query(query)
            if sanitized:
                rows = (
                    content.search(sanitized, query_type="fts")
                    .where(predicate, prefilter=True)
                    .select(["chunk_id", "_score"]).limit(window).to_list()
                )
                lexical = sorted(
                    [(r["chunk_id"], r["_score"]) for r in rows],
                    key=lambda hit: (-hit[1], hit[0]),
                )
        ids = {h.chunk_id for branch in branches for h in branch} | {cid for cid, _ in lexical}
        if not ids:
            return []
        records = {}
        for start in range(0, len(ids), 512):
            selected = sorted(ids)[start:start + 512]
            sql = ", ".join(f"'{_sql_str(cid)}'" for cid in selected)
            rows = (
                content.search().where(
                    f"({predicate}) AND chunk_id IN ({sql})", prefilter=True,
                ).limit(len(selected)).to_list()
            )
            # Native hydration helper receives only prefiltered canonical rows.
            from .content_store import ChunkRecord, _decode_metadata

            for row in rows:
                records[row["chunk_id"]] = ChunkRecord(
                    chunk_id=row["chunk_id"], source=row["source"], file_path=row["file_path"],
                    chunk_type=row["chunk_type"], language=row["language"], content=row["content"],
                    content_hash=row.get("content_hash", ""), line_start=row["line_start"],
                    line_end=row["line_end"], metadata=_decode_metadata(row.get("metadata")),
                )
        lists = [_hits_to_results(branch, records) for branch in branches]
        weights = [_DEFAULT_VECTOR_WEIGHT] * len(lists)
        if lexical:
            lists.append(_fts_to_results(lexical, records))
            weights.append(_DEFAULT_FTS_WEIGHT)
        if not lists:
            return []
        merged = lists[0] if len(lists) == 1 else _multi_list_rrf(
            lists, weights=weights, query=query,
        )
        merged.sort(key=lambda hit: (-hit.score, hit.chunk_id))
        return _normalize_scores(_deduplicate_by_content(merged))[:limit]
