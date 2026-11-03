"""Fresh private-process reads cannot provision, adopt or enable source hosting."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import pytest
from native_shell_support import run_headless

pytestmark = [
    pytest.mark.portfolio_tier("T3"),
    pytest.mark.effect("process"),
    pytest.mark.effect("filesystem"),
]


def tree(root: Path) -> dict[str, str | None]:
    return {
        str(path.relative_to(root)): (
            hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        )
        for path in root.rglob("*")
    }


@pytest.mark.parametrize("history", [False, True])
@pytest.mark.parametrize("explicit_project", [False, True])
def test_actual_fresh_main_read_is_structured_and_write_free(
    tmp_path: Path, history: bool, explicit_project: bool
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    policy = tmp_path / ".copilot-extensions" / "agent-worktrees" / "config.yaml"
    policy.parent.mkdir(parents=True)
    policy.write_text("expose_agent: false\n", encoding="utf-8")
    env = {
        **os.environ,
        "HOME": str(home),
        "USERPROFILE": str(home),
        "AGENT_HOME": str(home),
        "PYTHONDONTWRITEBYTECODE": "1",
        "AGENT_WORKTREES_NO_SELFPROVISION": "1",
    }
    argv = [sys.executable, "-m", "agent_worktrees"]
    if explicit_project:
        argv.extend(["--project", "unregistered-project"])
    argv.extend([
        "worktree-observation-history" if history else "worktree-observations",
        "--worktree", "full-worktree", "--json",
    ])
    if history:
        argv.extend(["--source-generation", "a" * 32])
    before = tree(tmp_path)
    result = run_headless(
        argv, cwd=tmp_path, env=env, capture_output=True, text=True, timeout=20
    )
    assert result.returncode == 1, result.stderr
    value = json.loads(result.stdout)
    expected = "unavailable" if explicit_project else "project-unavailable"
    assert value["type"] == "error" and value["error"]["code"] == expected, (
        result.stdout, result.stderr
    )
    assert "Traceback" not in result.stderr and "::agent-provisioning::" not in result.stderr
    assert tree(tmp_path) == before
