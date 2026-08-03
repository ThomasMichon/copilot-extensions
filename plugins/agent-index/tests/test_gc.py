from __future__ import annotations

from agent_index.indexing.gc import is_live_source
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
