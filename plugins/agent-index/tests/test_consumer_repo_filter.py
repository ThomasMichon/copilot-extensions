"""Consumers customize source naming without forking the guarded FTS path."""

from __future__ import annotations

import ntpath
import os
from pathlib import Path
from typing import SupportsIndex

import numpy as np
import pytest

from agent_index.chunking.base import Chunk
from agent_index.index_config import ModelProfile
from agent_index.store.content_store import ContentStore
from agent_index.store.multi_model_store import MultiModelStore
from agent_index.store.repo_filter import _like_pattern, path_glob_sql


class CatalogStore(ContentStore):
    def repo_filter_sql(self, repo: str) -> str:
        return f"source LIKE '%:{_like_pattern(repo)}' ESCAPE '\\'"


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

    def retrieve(omit: str = "", limit: int = 10) -> set[str]:
        hits = store.hybrid_search(
            "test", vectors[0], "searchable phrase", limit=limit,
            source=None if omit == "source" else "git",
            repo=None if omit == "repo" else "owner/repo",
            language=None if omit == "language" else "python",
            labels=None if omit == "labels" else ["kept"],
            file_path_glob=None if omit == "glob" else "docs/keep-*.py",
        )
        return {hit.chunk_id for hit in hits}

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
