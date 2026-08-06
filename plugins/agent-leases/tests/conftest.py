from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from agent_leases.config import Settings


def git(
    *args: str,
    cwd: Path | None = None,
    input_text: str | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(
        {
            "GIT_AUTHOR_NAME": "lease-test",
            "GIT_AUTHOR_EMAIL": "lease-test@example.invalid",
            "GIT_COMMITTER_NAME": "lease-test",
            "GIT_COMMITTER_EMAIL": "lease-test@example.invalid",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        input=input_text,
        capture_output=True,
        text=True,
        check=check,
        env=env,
    )


@pytest.fixture
def remote(tmp_path: Path) -> Path:
    path = tmp_path / "coordination.git"
    git("init", "--bare", str(path))
    return path


@pytest.fixture
def settings(remote: Path) -> Settings:
    return Settings(
        origin=str(remote),
        default_ttl_seconds=60,
        max_ttl_seconds=3600,
        clock_skew_seconds=10,
        acquire_retries=2,
    )
