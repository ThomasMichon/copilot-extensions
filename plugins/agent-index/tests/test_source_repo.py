"""Native repository predicates must agree with repository semantics before top-k."""

import numpy as np
from test_source_aware_store import chunk, cold as cold

from agent_index.search.engine import SearchEngine
from agent_index.store.repo_filter import repo_matches
from agent_index.store.source_search import source_predicate


def test_native_ado_repo_prefilter_parity_all_query_modes(cold, monkeypatch):
    _config, store = cold
    sources = [
        "ado:org/project", "ado:org/project:workitems",
        "azure-devops:org/project:pulls", "ado:host/org/project:workitems",
        "git:project:commits", "github:owner/project:issues",
        "ado:org/project-extra:workitems", "ado:org/Project:workitems",
        "ado:org/all:workitems", "ado:org/weird%_name:workitems",
        "ado:org/weirdXXname:workitems",
    ]
    forbidden = "ado:org/projectlookalike:workitems"
    items = [chunk(source, f"needle document {i}", path=f"{i}.md")
             for i, source in enumerate([*sources, forbidden])]
    # Highest similarity belongs to disallowed/lookalike rows; selection must precede top-k.
    store.upsert("small", items, np.array([
        [0.1, 0.9] if i < 6 else [1, 0] for i in range(len(items))
    ], dtype=np.float32))
    assert store.ensure_fts_index()

    class Inference:
        def embed_query(self, query):
            return np.array([1, 0], dtype=np.float32)

    engine = SearchEngine({"small": Inference()}, store)
    families = tuple(sources)
    tables = [store.content_store._get_or_create_table(),
              store.get_vector_table("small")._get_or_create_table()]
    native = []
    for table in tables:
        original = table.search

        def observed(*args, _original=original, **kwargs):
            query = _original(*args, **kwargs)
            where = query.where

            def scoped(predicate, **options):
                native.append((predicate, options.get("prefilter")))
                return where(predicate, **options)

            monkeypatch.setattr(query, "where", scoped)
            return query

        monkeypatch.setattr(table, "search", observed)
    for repo in (None, "project", "org/project", "host/org/project", "all", "weird%_name"):
        expected = {source for source in sources if repo is None or repo_matches(source, repo)}
        for mode in ("vector", "fts", "hybrid"):
            native.clear()
            hits = engine.search_authorized("needle", families=families, repo=repo,
                                            mode=mode, limit=100)
            assert {hit.source for hit in hits} == expected
            assert native and len(native) <= 3
            assert all(prefilter is True for _predicate, prefilter in native)
            assert all(source_predicate(families, repo=repo) in predicate
                       for predicate, _prefilter in native)
    for mode in ("vector", "fts", "hybrid"):
        hits = engine.search_authorized(
            "needle", families=("ado:org/project",), repo="project", mode=mode, limit=1,
        )
        assert len(hits) == 1 and hits[0].source in {"ado:org/project", "ado:org/project:workitems"}
