"""Tests for CWD-based worker-identity resolution (via agent-worktrees)."""

from __future__ import annotations

import subprocess

from agent_dispatch import identity


def test_resolve_identity_via_agent_worktrees(monkeypatch):
    monkeypatch.setattr(identity.shutil, "which", lambda _n: "/usr/bin/agent-worktrees")

    def fake_run(cmd, **_kw):
        key = cmd[-1]
        out = {
            "machine": "host-a",
            "worktree-dir": "/home/u/src/x.worktrees/host-a-wt-123",
        }[key]
        return subprocess.CompletedProcess(cmd, 0, out + "\n", "")

    monkeypatch.setattr(identity.subprocess, "run", fake_run)
    machine, worktree = identity.resolve_identity()
    assert machine == "host-a"
    assert worktree == "host-a-wt-123"


def test_resolve_identity_absent_agent_worktrees(monkeypatch):
    monkeypatch.setattr(identity.shutil, "which", lambda _n: None)
    assert identity.resolve_identity() == (None, None)


def test_resolve_identity_not_in_worktree(monkeypatch):
    monkeypatch.setattr(identity.shutil, "which", lambda _n: "/usr/bin/agent-worktrees")

    def fake_run(cmd, **_kw):
        # machine resolves, but worktree-dir is empty (not inside a worktree)
        out = "host-a" if cmd[-1] == "machine" else ""
        return subprocess.CompletedProcess(cmd, 0, out + "\n", "")

    monkeypatch.setattr(identity.subprocess, "run", fake_run)
    machine, worktree = identity.resolve_identity()
    assert machine == "host-a"
    assert worktree is None
