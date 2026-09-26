"""Shared pytest fixtures for agent-logger's test suite."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


def init_git_repo(
    path: Path,
    *,
    remote: str | None = "https://example.test/tmichon/demo.git",
    branch: str = "main",
) -> None:
    """Create a real (throwaway) git repo for trust-gate tests.

    Unlike the bare ``(repo / ".git").mkdir()`` fixture pattern used
    elsewhere in this suite (fine for tests that bypass the trust gate via
    the autouse fixture below), the trust gate itself shells out to real
    ``git`` commands, so exercising it honestly needs a real checkout.
    """
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", branch, str(path)], check=True)
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "test@example.test"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.name", "Test"], check=True
    )
    if remote is not None:
        subprocess.run(
            ["git", "-C", str(path), "remote", "add", "origin", remote], check=True
        )


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
    monkeypatch.setattr(
        "agent_logger.tenancy.repo_config_is_trusted", lambda root: True
    )
