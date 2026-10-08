"""Reusable fixtures for the two documented container-testing workarounds when
a repository carries no ``indexer:``/``indexers:`` designation in its own
checked-in ``.agent-index/config.yaml`` -- the normal, intentional shape for a
stateless, shareable harness repo (e.g. ``example-harness``), which never
bakes a machine-specific designation into shared, forkable config. A real
operator's bound knowledge repo supplies that designation at runtime; a
container has neither a real knowledge repo nor a second real machine to
designate, so a test needs one of these two stand-ins instead:

1. :func:`paired_knowledge_repo_indexer` -- a temporary, local-only
   "knowledge repo" carrying the ``indexers:`` designation. This fixture
   works ONLY because it monkeypatches ``config._external_state_root``
   directly -- it mirrors the *shape* of what a real bound knowledge repo
   supplies, not the real resolution mechanism (a genuine run needs an
   actual ``agent-worktrees state-root --json`` response reporting
   ``bound: true`` / ``source: "knowledge_repo"``; see
   ``config._knowledge_root_for_repo`` / ``_knowledge_overlay.py``). A
   bare, unregistered directory is not independently discoverable outside
   this mocked test setup.
2. :func:`machine_local_indexer_overlay` -- a machine-local overlay
   (``<repo>/.copilot-extensions/agent-index/config.yaml``) declaring the
   current machine as the indexer directly, with no knowledge-repo/
   external-state resolution involved at all. This path is NOT
   automatically gitignored by anything in agent-index itself -- a real
   repo using this workaround must add it to its own ignore rules, or the
   machine-specific designation is left available to commit.

See ``plugins/agent-index/README.md`` ("Testing without an assigned indexer
machine") for the developer-facing explanation these fixtures back.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_index import config as agent_index_config

TEST_MACHINE = "test-machine"


def _write_base_config(repo: Path, *, sources_yaml: str = "corpus:\n  sources: []\n") -> None:
    base = repo / ".agent-index"
    base.mkdir(parents=True, exist_ok=True)
    (base / "config.yaml").write_text(sources_yaml, encoding="utf-8")


@pytest.fixture
def paired_knowledge_repo_indexer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Workaround 1: pair a temporary, local-only knowledge repo.

    Returns the subject repo's root. ``AGENT_INDEX_MACHINE`` is set to
    :data:`TEST_MACHINE`, which the paired knowledge repo's ``indexers:``
    designates as the (sole) indexer, so ``transport.plan_route()`` resolves
    ``role == "host"`` for it -- exactly the resolution a real paired
    knowledge repo would produce, without needing a real second machine.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_base_config(repo)
    # requires_external_state_root: true is what makes config.py actually
    # consult the knowledge-repo layer at all (see config._knowledge_root_for_repo).
    worktrees_dir = repo / ".agent-worktrees"
    worktrees_dir.mkdir()
    (worktrees_dir / "config.yaml").write_text(
        "requires_external_state_root: true\n", encoding="utf-8",
    )

    knowledge = tmp_path / "knowledge"
    (knowledge / ".agent-index").mkdir(parents=True)
    (knowledge / ".agent-index" / "config.yaml").write_text(
        f"indexers:\n  - machine: {TEST_MACHINE}\n", encoding="utf-8",
    )

    monkeypatch.setattr(agent_index_config, "repo_root", lambda explicit=None: repo)
    # Stands in for the real subprocess call to `agent-worktrees state-root
    # --json` (see _knowledge_overlay.external_state_root) -- a container has
    # no real agent-worktrees registration to resolve against.
    monkeypatch.setattr(agent_index_config, "_external_state_root", lambda _root: ("ready", knowledge))
    monkeypatch.setenv("AGENT_INDEX_MACHINE", TEST_MACHINE)
    return repo


@pytest.fixture
def machine_local_indexer_overlay(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Workaround 2: a machine-local overlay declaring this machine as the
    indexer directly -- no knowledge repo or external-state resolution at
    all. Returns the subject repo's root."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_base_config(repo)

    overlay = repo / ".copilot-extensions" / "agent-index" / "config.yaml"
    overlay.parent.mkdir(parents=True)
    overlay.write_text(f"indexer:\n  machine: {TEST_MACHINE}\n", encoding="utf-8")

    monkeypatch.setattr(agent_index_config, "repo_root", lambda explicit=None: repo)
    monkeypatch.setenv("AGENT_INDEX_MACHINE", TEST_MACHINE)
    return repo
