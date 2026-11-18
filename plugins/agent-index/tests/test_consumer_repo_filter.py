"""Consumers customize source naming without forking the guarded FTS path."""

from __future__ import annotations

import ntpath
import os
from collections.abc import Callable
from pathlib import Path
from typing import SupportsIndex
from unittest.mock import Mock

import numpy as np
import pytest

from agent_index.chunking.base import Chunk
from agent_index.index_config import IndexConfig, ModelProfile
from agent_index.search.engine import SearchEngine
from agent_index.store import multi_model_store
from agent_index.store.content_store import ContentStore
from agent_index.store.multi_model_store import MultiModelStore
from agent_index.store.repo_filter import _like_pattern, path_glob_sql
from agent_index.store.result_filters import SearchFilters
from agent_index.store.source_layout import LAYOUT, initialize_source_store


class CatalogStore(ContentStore):
    def repo_filter_sql(self, repo: str) -> str:
        return f"source LIKE '%:{_like_pattern(repo)}' ESCAPE '\\'"


def catalog_repo_matches(source: str, repo: str) -> bool:
    return bool(repo) and source.endswith(f":{repo}")


@pytest.mark.parametrize("source_aware", [False, True])
@pytest.mark.parametrize("model_count", [1, 2])
@pytest.mark.parametrize("with_fts", [False, True])
def test_instance_bonus_preserves_independent_native_rankings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source_aware: bool, model_count: int,
    with_fts: bool,
) -> None:
    profiles = {
        model: ModelProfile(model, model, table_name=f"vectors_{model}", dim=2)
        for model in ("first", "second")[:model_count]
    }
    config = IndexConfig(data_dir=tmp_path, model_profiles=profiles)
    if source_aware:
        monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", LAYOUT)
        initialize: Callable[[IndexConfig], MultiModelStore] = initialize_source_store
        initialize(config)
    else:
        monkeypatch.delenv("AGENT_INDEX_STORE_LAYOUT", raising=False)
    disabled = MultiModelStore(config.lance_dir, exact_match_bonus=0.0)
    chunks = [
        Chunk("backup py", "docs/overview.md", "text", "markdown", 1, 1, source="git:alpha"),
        Chunk(
            "implementation details", "src/backup.py", "code", "python", 1, 1,
            source="git:alpha",
        ),
    ]
    vectors = np.array([[1.0, 0.0], [0.8, 0.6]], dtype=np.float32)
    for profile in profiles.values():
        disabled.register_model(profile)
        disabled.upsert(profile.model_id, chunks, vectors)
    if with_fts:
        assert disabled.ensure_fts_index()
        assert [
            chunk_id for chunk_id, _ in disabled.content_store.fts_search("backup.py")
        ] == [chunks[0].chunk_id]
    else:
        assert not disabled.fts_available

    def search(store: MultiModelStore, query: str = "backup.py") -> list[tuple[str, float]]:
        if source_aware:
            inference = Mock()
            inference.embed_query.return_value = vectors[0]
            engine = SearchEngine(dict.fromkeys(profiles, inference), store)
            results = engine.search_authorized(
                query, families=("git:alpha",), mode="hybrid" if with_fts else "vector",
            )
        elif model_count == 1:
            results = store.hybrid_search("first", vectors[0], query)
        else:
            results = store.search_all(dict.fromkeys(profiles, vectors[0]), query)
        assert {hit.chunk_id for hit in results} == {chunk.chunk_id for chunk in chunks}
        return [(hit.chunk_id, hit.score) for hit in results]

    unboosted = search(disabled)
    assert unboosted[0][0] == chunks[0].chunk_id
    boosted = MultiModelStore(config.lance_dir, exact_match_bonus=2.0)
    default = MultiModelStore(config.lance_dir)
    explicit_default = MultiModelStore(
        config.lance_dir, exact_match_bonus=multi_model_store._EXACT_MATCH_BONUS,
    )
    for store in (boosted, default, explicit_default):
        for profile in profiles.values():
            store.register_model(profile)
        if with_fts:
            assert store.ensure_fts_index()
    domain = disabled._source_domain
    receipt = domain.receipt() if domain is not None else None
    expected_top = chunks[1] if with_fts or model_count > 1 else chunks[0]
    assert search(boosted)[0][0] == expected_top.chunk_id
    if not with_fts and model_count == 1:
        assert search(boosted) == unboosted
    assert search(disabled) == unboosted
    assert search(default) == search(explicit_default)
    assert search(disabled) == unboosted
    assert search(boosted, "backup py") == search(disabled, "backup py")
    if source_aware:
        assert domain is not None
        assert domain.receipt() == receipt


@pytest.mark.parametrize("source_aware", [True, False])
def test_facade_preserves_source_delete_return_and_remaining_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source_aware: bool,
) -> None:
    profile = ModelProfile("test", "test", table_name="vectors_test", dim=2)
    if source_aware:
        monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", LAYOUT)
        initialize: Callable[[IndexConfig], MultiModelStore] = initialize_source_store
        store = initialize(IndexConfig(data_dir=tmp_path, model_profiles={"test": profile}))
    else:
        monkeypatch.delenv("AGENT_INDEX_STORE_LAYOUT", raising=False)
        store = MultiModelStore(tmp_path / "lance")
        store.register_model(profile)
    chunks = [
        Chunk(f"document {index}", f"{index}.md", "text", "markdown", 1, 1, source=source)
        for index, source in enumerate(("git:alpha", "git:alpha:commits", "git:alphabet"))
    ]
    vectors = np.array([[1.0, 0.0]] * len(chunks), dtype=np.float32)
    store.upsert("test", chunks, vectors)
    assert store.delete_by_source("git:alpha") == (2 if source_aware else None)
    assert store.delete_by_source("git:alpha") == (0 if source_aware else None)
    assert [hit.chunk_id for hit in store.search("test", vectors[0])] == [chunks[2].chunk_id]


@pytest.mark.parametrize("repo", ["owner/repo_under", "owner/repo'quoted"])
def test_consumer_policy_scopes_authorized_native_queries_before_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, repo: str,
) -> None:
    monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", LAYOUT)
    profile = ModelProfile("test", "test", table_name="vectors_test", dim=2)
    config = IndexConfig(data_dir=tmp_path, model_profiles={"test": profile})
    initialize: Callable[[IndexConfig], MultiModelStore] = initialize_source_store
    initialize(config)
    store = MultiModelStore(
        config.lance_dir, content_store_type=CatalogStore, repo_matcher=catalog_repo_matches,
    )
    store.register_model(profile)
    sources = [
        f"catalog:issues:{repo}",
        *(["catalog:issues:owner/other"] * 51),
        f"catalog:issues-extra:{repo}",
        f"catalog:issues:{repo}",
    ]
    chunks = [
        Chunk(
            f"needle document {index}", f"docs/item-{index}.py", "code",
            "markdown" if index == len(sources) - 1 else "python", 1, 1, source=source,
        )
        for index, source in enumerate(sources)
    ]
    vectors = np.array([[0.1, 0.9], *([[1.0, 0.0]] * (len(chunks) - 1))], dtype=np.float32)
    store.upsert("test", chunks, vectors)
    assert store.ensure_fts_index()
    inference = Mock()
    inference.embed_query.return_value = np.array([1.0, 0.0], dtype=np.float32)
    engine = SearchEngine({"test": inference}, store)
    assert store._source_domain is not None
    before = store._source_domain.receipt()
    for mode in ("vector", "fts", "hybrid"):
        hits = engine.search_authorized(
            "needle", families=("catalog:issues",), repo=repo, language="python",
            mode=mode, limit=1,
        )
        assert [hit.chunk_id for hit in hits] == [chunks[0].chunk_id]
        assert engine.search_authorized(
            "needle", families=("catalog:issues",), repo="issues", mode=mode,
        ) == []
        assert engine.search_authorized("needle", families=(), repo=repo, mode=mode) == []
    assert store._source_domain.receipt() == before

    default = MultiModelStore(config.lance_dir)
    default.register_model(profile)
    default_engine = SearchEngine({"test": inference}, default)
    for mode in ("vector", "fts", "hybrid"):
        assert default_engine.search_authorized(
            "needle", families=("catalog:issues",), repo=repo, mode=mode,
        ) == []


@pytest.mark.parametrize("with_fts", [True, False])
@pytest.mark.parametrize("repo, sibling", [
    ("owner/repo_under", "owner/repoXunder"),
    ("owner/repo%percent", "owner/repoXpercent"),
    ("owner/repo'quoted", "owner/repoquoted"),
    ("owner/repo\\slash", "owner/reposlash"),
])
def test_multi_model_consumer_policy_scopes_all_retrieval_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    with_fts: bool, repo: str, sibling: str,
) -> None:
    monkeypatch.delenv("AGENT_INDEX_STORE_LAYOUT", raising=False)
    store = MultiModelStore(
        tmp_path / "lance", content_table="articles",
        content_store_type=CatalogStore, repo_matcher=catalog_repo_matches,
    )
    profile = ModelProfile("test", "test", table_name="vectors_test", dim=2)
    store.register_model(profile)
    store.register_model(ModelProfile("other", "other", table_name="vectors_other", dim=2))
    chunks = [
        Chunk(
            f"searchable phrase unique item {index}", f"docs/item-{index}.py",
            "code", "python", 1, 1, source=f"catalog:issues:{name}",
            metadata={"labels": ["kept"]},
        )
        for index, name in enumerate((repo, repo, sibling))
    ]
    vectors = np.array([[1.0, 0.0], [0.8, 0.6], [0.99, 0.1]], dtype=np.float32)
    store.upsert("test", chunks, vectors)
    store.upsert_vectors("other", chunks, vectors[::-1].copy())
    if with_fts:
        assert store.ensure_fts_index()
    expected = {chunk.chunk_id for chunk in chunks[:2]}
    filters: SearchFilters = {
        "repo": repo, "source": "catalog:issues", "language": "python",
        "file_path_glob": "docs/item-*.py", "labels": ["kept"],
    }
    fusion = Mock(wraps=multi_model_store._reciprocal_rank_fusion)
    multi_fusion = Mock(wraps=multi_model_store._multi_list_rrf)
    monkeypatch.setattr(multi_model_store, "_reciprocal_rank_fusion", fusion)
    monkeypatch.setattr(multi_model_store, "_multi_list_rrf", multi_fusion)
    assert {hit.chunk_id for hit in store.search("test", vectors[0], **filters)} == expected
    for query in ("searchable phrase", "absentlexicaltoken"):
        fusion.reset_mock()
        multi_fusion.reset_mock()
        has_fts_hits = with_fts and query == "searchable phrase"
        assert {
            hit.chunk_id for hit in store.hybrid_search("test", vectors[0], query, **filters)
        } == expected
        if has_fts_hits:
            fusion.assert_called_once()
            assert fusion.call_args is not None
            assert {hit.chunk_id for hit in fusion.call_args.args[1]} == expected
        else:
            fusion.assert_not_called()
        assert {
            hit.chunk_id
            for hit in store.search_all({"test": vectors[0], "other": vectors[0]}, query, **filters)
        } == expected
        multi_fusion.assert_called_once()
        assert multi_fusion.call_args is not None
        lists = multi_fusion.call_args.args[0]
        assert len(lists) == (3 if has_fts_hits else 2)
        if has_fts_hits:
            assert {hit.chunk_id for hit in lists[-1]} == expected
    assert {
        hit.chunk_id for hit in store.find_similar(chunks[0].chunk_id, limit=1, **filters)
    } == {chunks[1].chunk_id}
    if with_fts:
        assert {hit.chunk_id for hit in store.fts_search("searchable phrase", **filters)} == expected
        assert {
            hit.chunk_id for hit in store.search_all({}, "searchable phrase", **filters)
        } == expected
    assert store.search("test", vectors[0], repo=repo, labels=["missing"]) == []

    # A default facade over the same rows must retain its independent policy.
    default = MultiModelStore(tmp_path / "lance", content_table="articles")
    default.register_model(profile)
    assert default.search("test", vectors[0], repo=repo) == []
    assert default.hybrid_search("test", vectors[0], "searchable phrase", repo=repo) == []


@pytest.mark.parametrize("repo, sibling", [
    ("owner/repo_under", "owner/repoXunder"),
    ("owner/repo%percent", "owner/repoXpercent"),
    ("owner/repo'quoted", "owner/repoquoted"),
    ("owner/repo\\slash", "owner/reposlash"),
])
def test_custom_repo_filter_is_applied_to_real_fts_candidates_before_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, repo: str, sibling: str,
) -> None:
    monkeypatch.delenv("AGENT_INDEX_STORE_LAYOUT", raising=False)
    store = CatalogStore(tmp_path / "lance")
    sources = [f"catalog:issues:{repo}", f"catalog:issues:{sibling}"]
    texts = ["shared searchable phrase primary", "shared searchable phrase sibling different"]
    chunks = [
        Chunk(texts[index], f"item-{index}.md", "text", "text", 1, 1,
              source=source)
        for index, source in enumerate(sources)
    ]
    store.upsert(chunks)
    store.ensure_fts_index()
    for selected, expected in zip((repo, sibling), chunks, strict=True):
        for limit in (1, 10):
            hits = store.fts_search("searchable phrase", repo=selected, limit=limit)
            assert [chunk_id for chunk_id, _score in hits] == [expected.chunk_id]
    assert len(store.fts_search("searchable phrase", limit=10)) == 2


def test_default_repo_filter_retains_generic_source_identity(tmp_path: Path) -> None:
    store = ContentStore(tmp_path / "lance")
    predicate = store.repo_filter_sql("owner/repo")
    assert predicate == "split_part(source, ':', 2) = 'owner/repo'"


@pytest.mark.parametrize("pattern, selected, sibling", [
    ("*src/*_adapter.py", "src/code_adapter.py", "src/codeXadapter.py"),
    ("docs/report?.[mt][dx]", "docs/report1.md", "docs/report12.md"),
    ("docs/[!a-z].md", "docs/7.md", "docs/a.md"),
    ("docs/[(?>].md", "docs/>.md", "docs/:.md"),
    ("docs/[[]?.md", "docs/[a.md", "docs/a.md"),
    ("docs/[]]?.md", "docs/]a.md", "docs/a.md"),
    ("docs/[[]unfinished[.md", "docs/[unfinished[.md", "docs/unfinished.md"),
    ("docs/100%_ready.md", "docs/100%_ready.md", "docs/100X_ready.md"),
    ("docs/a'b.md", "docs/a'b.md", "nested/docs/a'b.md"),
    (r"docs/end\Z.md", r"docs/end\Z.md", r"docs/end\z.md"),
    ("*\u03b4\u03bf\u03ba\u03b9\u03bc\u03ae*.md",
     "docs/\u03b4\u03bf\u03ba\u03b9\u03bc\u03ae.md", "docs/other.md"),
])
def test_path_globs_filter_real_fts_candidates_before_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    pattern: str, selected: str, sibling: str,
) -> None:
    monkeypatch.delenv("AGENT_INDEX_STORE_LAYOUT", raising=False)
    multi = MultiModelStore(tmp_path / "lance")
    store = multi.content_store
    chunks = [
        Chunk("searchable phrase primary", selected, "text", "text", 1, 1, source="git:repo"),
        Chunk("searchable phrase sibling different", sibling, "text", "text", 1, 1,
              source="git:repo"),
    ]
    store.upsert(chunks)
    assert store.ensure_fts_index()
    for limit in (1, 10):
        hits = store.fts_search(
            "searchable phrase", source="git:repo", file_path_glob=pattern, limit=limit,
        )
        assert [chunk_id for chunk_id, _score in hits] == [chunks[0].chunk_id]
    multi.register_model(ModelProfile("test", "test", table_name="vectors_test", dim=2))
    vectors = np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
    multi.upsert_vectors("test", chunks, vectors)
    assert multi.fts_available
    for limit in (1, 10):
        hits = multi.hybrid_search(
            "test", vectors[0], "searchable phrase", source="git:repo", repo="repo",
            file_path_glob=pattern, limit=limit,
        )
        assert [hit.chunk_id for hit in hits] == [chunks[0].chunk_id]


@pytest.mark.parametrize("prefix", ["", "[]", "[!]", "[a]"])
def test_unmatched_bracket_globs_use_linear_character_scans(prefix: str) -> None:
    class CountedPattern(str):
        reads = 0

        def __getitem__(self, key: SupportsIndex | slice) -> str:
            self.reads += 1
            return super().__getitem__(key)

    pattern = CountedPattern(prefix + "[" * 1024)
    predicate = path_glob_sql(pattern)
    assert predicate.startswith("regexp_like(file_path, ")
    assert pattern.reads < 10 * len(pattern)


@pytest.mark.parametrize("with_fts", [True, False])
def test_globs_preserve_source_repo_and_metadata_exclusions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, with_fts: bool,
) -> None:
    monkeypatch.delenv("AGENT_INDEX_STORE_LAYOUT", raising=False)
    store = MultiModelStore(tmp_path / "lance")
    store.register_model(ModelProfile("test", "test", table_name="vectors_test", dim=2))
    sources = ["other:owner/repo", "git:owner/other", *["git:owner/repo"] * 4]
    chunks = [
        Chunk(
            f"searchable phrase item {index}",
            f"{'other' if index == 4 else 'docs'}/keep-{index}.py",
            "code", "markdown" if index == 2 else "python", 1, 1,
            source=source, metadata={"labels": ["other" if index == 3 else "kept"]},
        )
        for index, source in enumerate(sources)
    ]
    angles = np.linspace(0.0, 1.0, len(chunks), dtype=np.float32)
    vectors = np.column_stack((np.cos(angles), np.sin(angles)))
    store.upsert("test", chunks, vectors)
    if with_fts:
        assert store.ensure_fts_index()
        fts_hits = store.content_store.fts_search(
            "searchable phrase", source="git", repo="owner/repo", language="python",
            file_path_glob="docs/keep-*.py", limit=10,
        )
        assert {chunk_id for chunk_id, _ in fts_hits} == {
            chunks[3].chunk_id, chunks[5].chunk_id,
        }
        faceted_hits = store.fts_search(
            "searchable phrase", source="git", repo="owner/repo", language="python",
            file_path_glob="docs/keep-*.py", labels=["kept"], limit=10,
        )
        assert {hit.chunk_id for hit in faceted_hits} == {chunks[5].chunk_id}
    assert store.fts_available is with_fts
    fusion = Mock(wraps=multi_model_store._reciprocal_rank_fusion)
    monkeypatch.setattr(multi_model_store, "_reciprocal_rank_fusion", fusion)

    def retrieve(omit: str = "", limit: int = 10) -> set[str]:
        fusion.reset_mock()
        hits = store.hybrid_search(
            "test", vectors[0], "searchable phrase", limit=limit,
            source=None if omit == "source" else "git",
            repo=None if omit == "repo" else "owner/repo",
            language=None if omit == "language" else "python",
            labels=None if omit == "labels" else ["kept"],
            file_path_glob=None if omit == "glob" else "docs/keep-*.py",
        )
        result_ids = {hit.chunk_id for hit in hits}
        if with_fts:
            fusion.assert_called_once()
            assert fusion.call_args is not None
            fts_results = fusion.call_args.args[1]
            assert fts_results
            assert {hit.chunk_id for hit in fts_results} == result_ids
        else:
            fusion.assert_not_called()
        return result_ids

    for limit in (1, 10):
        assert retrieve(limit=limit) == {chunks[5].chunk_id}
    for index, omitted in enumerate(("source", "repo", "language", "labels", "glob")):
        assert retrieve(omitted) == {chunks[index].chunk_id, chunks[5].chunk_id}


@pytest.mark.parametrize("limit", [1, 10])
@pytest.mark.parametrize("inside_window", [True, False])
def test_hybrid_glob_limits_preserve_the_finite_vector_window(
    tmp_path: Path, limit: int, inside_window: bool,
) -> None:
    store = MultiModelStore(tmp_path / "lance")
    store.register_model(ModelProfile("test", "test", table_name="vectors_test", dim=2))
    first_match = 50 - limit if inside_window else 50
    chunks = [
        Chunk(
            f"unique document {index}",
            f"{'selected' if first_match <= index < first_match + limit else 'other'}/{index}.md",
            "text", "text", 1, 1, source="git:repo",
        )
        for index in range(60)
    ]
    angles = np.linspace(0.0, 1.0, len(chunks), dtype=np.float32)
    vectors = np.column_stack((np.cos(angles), np.sin(angles)))
    store.upsert("test", chunks, vectors)
    assert not store.fts_available
    hits = store.hybrid_search(
        "test", np.array([1.0, 0.0], dtype=np.float32), "absent",
        file_path_glob="selected/*.md", limit=limit,
    )
    expected = chunks[first_match:first_match + limit] if inside_window else []
    assert {hit.chunk_id for hit in hits} == {chunk.chunk_id for chunk in expected}


def test_hybrid_glob_does_not_apply_windows_path_normalization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = MultiModelStore(tmp_path / "lance")
    store.register_model(ModelProfile("test", "test", table_name="vectors_test", dim=2))
    chunks = [
        Chunk(f"searchable phrase {index}", path, "text", "text", 1, 1, source="git:repo")
        for index, path in enumerate([r"docs/end\Z.md", r"docs/end\z.md"])
    ]
    vectors = np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
    store.upsert("test", chunks, vectors)
    assert store.ensure_fts_index()
    monkeypatch.setattr(os.path, "normcase", ntpath.normcase)
    pattern = r"docs/end\Z.md"
    fts_hits = store.content_store.fts_search("searchable phrase", file_path_glob=pattern)
    assert [chunk_id for chunk_id, _ in fts_hits] == [chunks[0].chunk_id]
    hybrid_hits = store.hybrid_search(
        "test", vectors[0], "searchable phrase", file_path_glob=pattern, limit=10,
    )
    assert [hit.chunk_id for hit in hybrid_hits] == [chunks[0].chunk_id]
