"""Explicit fake Worktrees owner backed by an isolated real checkout policy."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from repository_policy import require_agent_hosting

from agent_dispatch import embody, hosting_policy


@pytest.fixture
def admission_owner(monkeypatch, tmp_path):
    workspace = tmp_path / "synthetic-worktree-a"
    (workspace / ".git").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.delenv("AGENT_DISPATCH_INSTALL_DIR", raising=False)
    calls = []

    def owner(argv, **kwargs):
        calls.append(argv)
        if argv[1:3] == ["repos", "find"]:
            data = {"path": str(workspace)}
        else:
            assert argv[1:3] == ["agent-policy", "check"]
            path = argv[argv.index("--path") + 1]
            data = {**require_agent_hosting(Path(path)).to_dict(), "allowed": True}
        return subprocess.CompletedProcess(argv, 0, json.dumps(data), "")

    monkeypatch.setattr(hosting_policy, "agent_worktrees_launch_prefix", lambda: ["admission-owner"])
    monkeypatch.setattr(hosting_policy, "subprocess", SimpleNamespace(
        run=owner, SubprocessError=subprocess.SubprocessError,
    ))
    monkeypatch.setattr(embody, "resolve_worktree", lambda worktree_id, **kwargs: {
        "worktree": worktree_id, "path": str(workspace), "project": kwargs.get("project"),
    })
    return SimpleNamespace(workspace=workspace, calls=calls)
