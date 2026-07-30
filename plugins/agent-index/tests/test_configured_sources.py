from __future__ import annotations

from agent_index.indexing.engine import configured_sources


def test_configured_sources_from_env(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_INDEX_SOURCES", " git:repo, github:owner/repo ,, ")

    assert configured_sources() == ["git:repo", "github:owner/repo"]


def test_configured_sources_defaults_to_git(monkeypatch) -> None:
    monkeypatch.delenv("AGENT_INDEX_SOURCES", raising=False)

    assert configured_sources() == ["git"]


def test_configured_sources_blank_env_defaults_to_git(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_INDEX_SOURCES", " , ")

    assert configured_sources() == ["git"]
