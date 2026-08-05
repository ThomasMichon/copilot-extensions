"""Shared test fixtures for agent-codespaces."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _disable_codespace_claim(monkeypatch):
    """Disable the #897 exclusive-claim enforcement by default in unit tests.

    The auto-claim in ``agent-codespaces ssh`` shells out to ``agent-worktrees``
    (to resolve the calling worktree + enumerate active worktrees) and writes the
    real host lease file. Unit tests that exercise the ``ssh`` CLI must not do
    real subprocess I/O or touch host state, so claiming is off by default. Tests
    that specifically cover claim enforcement opt back in by deleting the env var
    and mocking the ``lease`` seam.
    """
    monkeypatch.setenv("AGENT_CODESPACES_DISABLE_CLAIM", "1")
