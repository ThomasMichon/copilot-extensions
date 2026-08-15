from __future__ import annotations

import pytest

from agent_index.indexing.engine import configured_sources


@pytest.fixture(autouse=True)
def _isolate_registry(monkeypatch, tmp_path):
    """Isolate the corpus sweep from the host's real adopted-projects registry.

    ``configured_sources()`` sweeps ``corpus.sources`` from the agent-worktrees
    registry (``~/.agent-worktrees``) and the machine-local config
    (``~/.agent-index``). On a box that has actually adopted projects (a real
    indexer host) those leak in and defeat the ``["git"]`` default these tests
    assert. Point both homes at empty temp dirs so the sweep finds nothing.
    """
    monkeypatch.setenv("AGENT_WORKTREES_HOME", str(tmp_path / "worktrees"))
    monkeypatch.setenv("AGENT_INDEX_HOME", str(tmp_path / "index"))


def test_configured_sources_from_env(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_INDEX_SOURCES", " git:repo, github:owner/repo ,, ")

    assert configured_sources() == ["git:repo", "github:owner/repo"]


def test_configured_sources_defaults_to_git(monkeypatch) -> None:
    monkeypatch.delenv("AGENT_INDEX_SOURCES", raising=False)

    assert configured_sources() == ["git"]


def test_configured_sources_blank_env_defaults_to_git(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_INDEX_SOURCES", " , ")

    assert configured_sources() == ["git"]
