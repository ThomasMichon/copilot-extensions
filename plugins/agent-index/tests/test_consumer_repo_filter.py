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
