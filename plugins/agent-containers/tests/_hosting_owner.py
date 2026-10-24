"""Read-only guest-owner replies for isolated launch orchestration fixtures."""

from __future__ import annotations

import json
import subprocess
import shlex


def policy_reply(argv, *, container_id: str, workspace: str):
    assert argv[:5] == ["docker", "exec", "-u", "vscode", container_id]
    command = argv[-1]
    if "agent-policy check --help" in command:
        data = None
    elif '"$WORKTREES" resolve --json' in command:
        words = shlex.split(command)
        worktree_id = words[words.index("--worktree-id") + 1]
        data = {"action": "exec", "worktree_id": worktree_id, "work_dir": workspace}
    else:
        assert 'agent-policy check --path "$PWD"' in command
        assert f"cd -- {workspace}" in command
        data = {
            "version": 1, "allowed": True, "repository": workspace,
            "expose_agent": None, "sources": [], "denied_by": [],
        }
    return subprocess.CompletedProcess(argv, 0, "" if data is None else json.dumps(data), "")
