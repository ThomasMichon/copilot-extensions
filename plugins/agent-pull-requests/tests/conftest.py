"""Shared test fixtures for agent-pull-requests."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_state_dir(tmp_path, monkeypatch):
    """Every test gets its own throwaway ``AGENT_PULL_REQUESTS_HOME`` so a
    ``WatchDaemon(persist=True)`` (the default) never touches a real
    operator's ``~/.agent-pull-requests`` during the test suite."""
    monkeypatch.setenv("AGENT_PULL_REQUESTS_HOME", str(tmp_path))
