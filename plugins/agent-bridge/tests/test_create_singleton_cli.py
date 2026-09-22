"""Tests for singleton-repo create refusal on the CLI."""

from __future__ import annotations

import argparse

import pytest

from agent_bridge import __main__ as m
from agent_bridge.client import BridgeClientError


class _Client:
    def __init__(self):
        self.listed = 0

    def get_session(self, _target):
        raise BridgeClientError(404, "not found")

    def list_agents(self):
        self.listed += 1
        return [{"name": "llama.cpp@Lambda-Core", "project": "llama.cpp"}]


def test_create_refuses_singleton_repo_target(monkeypatch, capsys):
    monkeypatch.setattr(m, "_get_client", lambda: _Client())
    monkeypatch.setattr(
        "agent_bridge.resume_handoff_cli.find_singleton_repo",
        lambda target: (
            type("Repo", (), {"name": "llama.cpp", "path": "/repo/llama.cpp"})()
            if target == "llama.cpp"
            else None
        ),
    )
    args = argparse.Namespace(
        target="llama.cpp@Lambda-Core",
        prompt=None,
        caller=None,
        json=False,
        no_wait=False,
        model=None,
        effort=None,
        target_dir=None,
        worktree_id=None,
        session_id_file=None,
    )

    with pytest.raises(SystemExit) as exc:
        m._cmd_create(args)
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "[BLOCKED]" in err
    assert "singleton repo 'llama.cpp'" in err
    assert "agent-bridge resume llama.cpp@Lambda-Core" in err
