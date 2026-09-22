from __future__ import annotations

from agent_index.indexing.gc import is_configured_source, is_live_source
from agent_index.sources import registered_source_prefixes


def test_registered_prefixes_cover_generic_connectors() -> None:
    prefixes = registered_source_prefixes()
    assert {"git", "github", "ado", "azure-devops"} <= prefixes


def test_git_sources_are_live() -> None:
    # #116: the generic git scheme the engine's own GitRepoConnector emits must
    # be live, or a full-reindex GC wipes the whole freshly-built index.
    assert is_live_source("git:dotfiles")
    assert is_live_source("git:dotfiles:commits")
    assert is_live_source("git")  # bare crawl marker


def test_github_and_ado_sources_are_live() -> None:
    assert is_live_source("github:owner/repo")
    assert is_live_source("github:owner/repo:issues")
    assert is_live_source("github:owner/repo:pulls")
    assert is_live_source("ado:proj:workitems")
    assert is_live_source("azure-devops:proj:pulls")


def test_service_and_extra_keep_are_live() -> None:
    assert is_live_source("service-feed:clips")
    assert is_live_source("analysis-feed:videos")
    assert is_live_source("host:worktree", extra_keep=frozenset({"host:worktree"}))


def test_legacy_forge_repo_qualified_live_bare_stale() -> None:
    # Back-compat: repo-qualified forge stays live; the old bare generation is stale.
    assert is_live_source("forge:code:owner/repo")
    assert not is_live_source("forge:code")
    assert not is_live_source("forge:issues")


def test_unregistered_and_abandoned_schemes_are_stale() -> None:
    assert not is_live_source("")
    assert not is_live_source("monorepo")           # abandoned old scheme
    assert not is_live_source("gitlab:owner/repo")  # no registered connector
    # "gitlab" must not be caught by the "git" prefix (git: != gitlab:)
    assert not is_live_source("gitlab")


def test_is_configured_source_exact_and_subsource_match() -> None:
    configured = frozenset({"github:owner/repo", "git:dotfiles"})
    # Exact matches.
    assert is_configured_source("github:owner/repo", configured)
    assert is_configured_source("git:dotfiles", configured)
    # Hierarchical sub-sources of a configured spec are configured too.
    assert is_configured_source("github:owner/repo:issues", configured)
    assert is_configured_source("github:owner/repo:pulls", configured)
    assert is_configured_source("git:dotfiles:commits", configured)


def test_is_configured_source_removed_source_is_not_configured() -> None:
    # A source removed from corpus.sources sometime after it was indexed is
    # no longer configured, even though it still matches a LIVE naming scheme
    # (is_live_source alone can't tell "removed from config" apart from
    # "abandoned generation" -- that's exactly the gap gc_unconfigured_sources
    # closes).
    configured = frozenset({"github:owner/repo"})
    assert not is_configured_source("github:owner/other-repo", configured)
    assert not is_configured_source("git:some-other-repo", configured)


def test_is_configured_source_empty_and_extra_keep() -> None:
    assert not is_configured_source("", frozenset())
    assert not is_configured_source("git:x", frozenset())
    assert is_configured_source(
        "host:worktree", frozenset(), extra_keep=frozenset({"host:worktree"})
    )


class _FakeMultiStore:
    def __init__(self, counts: dict[str, int]) -> None:
        self._counts = dict(counts)
        self.deleted: list[str] = []

    def source_counts(self) -> dict[str, int]:
        return dict(self._counts)

    def delete_by_source_exact(self, source: str) -> int:
        self.deleted.append(source)
        return self._counts.get(source, 0)


class _FakePathIndex:
    def __init__(self) -> None:
        self.deleted_sources: list[str] = []

    def delete_source(self, source: str) -> None:
        self.deleted_sources.append(source)


def test_gc_unconfigured_sources_purges_only_removed_sources() -> None:
    from agent_index.indexing.gc import gc_unconfigured_sources
    from agent_index.indexing.state import IndexState, SourceState

    multi_store = _FakeMultiStore(
        {
            "github:owner/repo": 10,
            "github:owner/repo:issues": 3,
            "git:removed-repo": 7,
        }
    )
    path_index = _FakePathIndex()
    state = IndexState(
        sources={
            "github:owner/repo": SourceState(chunk_count=10),
            "github:owner/repo:issues": SourceState(chunk_count=3),
            "git:removed-repo": SourceState(chunk_count=7),
        },
        total_chunks=20,
    )

    summary = gc_unconfigured_sources(
        multi_store, path_index, state,
        configured_names=frozenset({"github:owner/repo"}),
    )

    assert summary["purged"] == {"git:removed-repo": 7}
    assert summary["kept"] == ["github:owner/repo", "github:owner/repo:issues"]
    assert summary["chunks_deleted"] == 7
    assert multi_store.deleted == ["git:removed-repo"]
    assert path_index.deleted_sources == ["git:removed-repo"]
    assert "git:removed-repo" not in state.sources
    assert state.total_chunks == 13


def test_gc_unconfigured_sources_dry_run_does_not_mutate() -> None:
    from agent_index.indexing.gc import gc_unconfigured_sources
    from agent_index.indexing.state import IndexState, SourceState

    multi_store = _FakeMultiStore({"git:removed-repo": 5})
    path_index = _FakePathIndex()
    state = IndexState(
        sources={"git:removed-repo": SourceState(chunk_count=5)},
        total_chunks=5,
    )

    summary = gc_unconfigured_sources(
        multi_store, path_index, state,
        configured_names=frozenset(),
        dry_run=True,
    )

    assert summary["purged"] == {"git:removed-repo": 5}
    assert summary["dry_run"] is True
    assert multi_store.deleted == []
    assert path_index.deleted_sources == []
    assert "git:removed-repo" in state.sources
