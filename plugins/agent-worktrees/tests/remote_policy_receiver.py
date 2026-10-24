"""A policy-backed read-only receiver for structured-protocol transport tests."""

from __future__ import annotations

import base64
import json
from pathlib import Path
import subprocess

import pytest
from repository_policy import require_agent_hosting

from agent_worktrees import remote_seed_launch


@pytest.fixture(autouse=True)
def accepted_remote_owner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = tmp_path / "policy-backed-receiver"
    (workspace / ".git").mkdir(parents=True)
    invoke = remote_seed_launch._run_ssh

    def request(alias: str, command: str, *, timeout: int) -> subprocess.CompletedProcess[str]:
        script = (
            base64.b64decode(command.split("-EncodedCommand ", 1)[1]).decode("utf-16le")
            if "-EncodedCommand " in command else command
        )
        if "agent-policy" not in script:
            return invoke(alias, command, timeout=timeout)
        assert "check" in script and "--launch-request-b64" not in script
        report = {**require_agent_hosting(workspace).to_dict(), "allowed": True}
        return subprocess.CompletedProcess(["ssh", alias, command], 0, json.dumps(report), "")

    monkeypatch.setattr(remote_seed_launch, "_run_ssh", request)
