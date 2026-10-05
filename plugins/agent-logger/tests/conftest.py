"""Shared pytest fixtures for agent-logger's test suite."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


_GIT_TIMEOUT_SECONDS = 20
_SCRUB_GIT_ENV_NAMES = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_CONFIG",
    "GIT_CONFIG_PARAMETERS",
)


def git_test_env(*, extra: dict[str, str] | None = None) -> dict[str, str]:
    """Return a git subprocess environment pinned to the fixture checkout."""
    env = dict(os.environ)
    for name in tuple(env):
        if name in _SCRUB_GIT_ENV_NAMES or name.startswith("GIT_CONFIG_"):
            env.pop(name, None)
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    if extra:
        env.update(extra)
    return env


@pytest.fixture(autouse=True)
def _isolate_git_config_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep ambient Git config from bleeding into direct test runs."""
    for name in tuple(os.environ):
        if name in _SCRUB_GIT_ENV_NAMES or name.startswith("GIT_CONFIG_"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


def init_git_repo(
    path: Path,
    *,
    remote: str | None = "https://example.test/example-owner/demo.git",
    branch: str = "main",
    remote_name: str = "origin",
) -> None:
    """Create a real (throwaway) git repo for trust-gate tests.

    Unlike the bare ``(repo / ".git").mkdir()`` fixture pattern used
    elsewhere in this suite (fine for tests that bypass the trust gate via
    the autouse fixture below), the trust gate itself shells out to real
    ``git`` commands, so exercising it honestly needs a real checkout.
    ``remote_name`` defaults to ``origin`` but may be set to any other local
    remote name (e.g. ``upstream``) to exercise that the trust gate matches
    on URL, not on a hardcoded local remote name.
    """
    path.mkdir(parents=True, exist_ok=True)
    env = git_test_env()
    subprocess.run(
        ["git", "init", "-q", "-b", branch, str(path)],
        check=True,
        env=env,
        timeout=_GIT_TIMEOUT_SECONDS,
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "test@example.test"],
        check=True,
        env=env,
        timeout=_GIT_TIMEOUT_SECONDS,
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.name", "Test"],
        check=True,
        env=env,
        timeout=_GIT_TIMEOUT_SECONDS,
    )
    if remote is not None:
        subprocess.run(
            ["git", "-C", str(path), "remote", "add", remote_name, remote],
            check=True,
            env=env,
            timeout=_GIT_TIMEOUT_SECONDS,
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
