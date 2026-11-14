"""Consumers customize source naming without forking the guarded FTS path."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_index.chunking.base import Chunk
from agent_index.store.content_store import ContentStore
from agent_index.store.repo_filter import _like_pattern


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
    store = ContentStore(tmp_path / "lance")
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
