"""Real policy backing for literal-path command-serialization fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_worktrees import agent_policy


@pytest.fixture(autouse=True)
def synthetic_workspace_policy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = tmp_path / "synthetic-command-workspace"
    workspace.mkdir()
    (workspace / ".git").mkdir()
    require = agent_policy.require
    aliases = {"/a", "/w/wt", "/selected", "/selected worktree", r"C:\w\wt"}

    def require_fixture(path: str | Path, *, operation: str = "launch") -> None:
        # Keep exact command-serialization strings without writing to OS roots.
        # Real test-owned paths still undergo their own actual policy check.
        require(workspace if str(path) in aliases else path, operation=operation)

    monkeypatch.setattr(agent_policy, "require", require_fixture)
