"""Shared pytest fixtures for agent-logger's test suite."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _trust_repo_config_by_default(request: pytest.FixtureRequest, monkeypatch):
    """Default every test's repo-local config discovery to "trusted".

    The registered-project trust gate (``repo_trust.repo_config_is_trusted``)
    exists to stop repo-local config from being honored for a checkout of an
    unregistered/non-default-branch repo -- see that function's docstring
    for the full threat model. Almost every existing repo-config test
    predates that gate and uses a bare fake ``.git`` directory (no real
    remote, no registry entry), so without this default they would all
    start seeing repo-local config as silently absent -- not because the
    behavior they're testing changed, but because the fixture repo looks
    exactly like an untrusted one to the gate.

    Tests that specifically exercise the trust gate itself opt out with
    ``@pytest.mark.no_autotrust`` to see the real function.
    """
    if request.node.get_closest_marker("no_autotrust"):
        return
    monkeypatch.setattr(
        "agent_logger.config.repo_config_is_trusted", lambda root: True
    )
