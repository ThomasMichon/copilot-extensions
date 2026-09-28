"""Tests for the read-only pending-handoff lookup (#3307 Phase 8,
visions/mux-companion). Split out of ``test_engine_client.py`` alongside the
``handoff_client`` module split (module-size cap) -- same faked-subprocess
pattern as ``engine_client``'s own tests, since ``worktree_state_dir`` runs
through the identical process-boundary ``_run`` helper.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from worktree_manager import engine_client as ec
from worktree_manager import handoff_client as hc


@pytest.fixture(autouse=True)
def _reset_engine_resolution(monkeypatch):
    ec.set_engine_command(None)
    monkeypatch.setattr(ec, "_INHERITED_ENGINE_COMMAND", None)
    monkeypatch.delenv(ec.ENGINE_ARGV_ENV, raising=False)
    monkeypatch.delenv(ec.ENGINE_CMD_ENV, raising=False)
    monkeypatch.delenv("AGENT_HOME", raising=False)


def _fake_completed(cmd, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr=stderr)


def _install_fake(monkeypatch, handler):
    """Point the client at a fake engine binstub + a scripted ``subprocess.run``."""
    monkeypatch.delenv(ec.ENGINE_ARGV_ENV, raising=False)
    monkeypatch.delenv(ec.ENGINE_CMD_ENV, raising=False)
    monkeypatch.setattr(ec, "_INHERITED_ENGINE_COMMAND", None)
    ec.set_engine_command(None)
    monkeypatch.setattr(
        ec, "installed_engine_command", lambda: ["/fake/agent-worktrees"]
    )
    monkeypatch.setattr(ec.subprocess, "run",
                        lambda cmd, **kw: handler(cmd, kw))


def test_worktree_state_dir_runs_from_the_worktree_cwd(monkeypatch, tmp_path):
    """``get worktree-state-dir`` infers its target from the process's own
    cwd, so the client must run the engine FROM the worktree path, not just
    pass it as an argument."""
    seen = {}

    def handler(cmd, kw):
        seen["cwd"] = kw.get("cwd")
        seen["cmd"] = cmd
        return _fake_completed(cmd, stdout=f"{tmp_path}\n")

    _install_fake(monkeypatch, handler)

    result = hc.worktree_state_dir("dotfiles", str(tmp_path))

    assert result == str(tmp_path)
    assert seen["cwd"] == str(tmp_path)
    assert seen["cmd"][-2:] == ["get", "worktree-state-dir"]


def test_worktree_state_dir_returns_none_on_engine_error(monkeypatch):
    monkeypatch.setattr(ec, "installed_engine_command", lambda: None)
    assert hc.worktree_state_dir("dotfiles", "/w") is None


def test_pending_handoff_reads_the_newest_unconsumed_baton(monkeypatch, tmp_path):
    """Reads context-handoff's own file-backed schema directly (kind ==
    "context-handoff", a ``consumed`` flag) -- picking the newest by
    ``createdAt`` among unconsumed candidates, ignoring a consumed one and
    anything that isn't this schema."""
    handoff_dir = tmp_path / "handoff"
    handoff_dir.mkdir()
    (handoff_dir / "handoff-old.json").write_text(json.dumps({
        "kind": "context-handoff", "title": "Old baton", "consumed": False,
        "createdAt": "2026-01-01T00:00:00.000Z",
    }), encoding="utf-8")
    (handoff_dir / "handoff-new.json").write_text(json.dumps({
        "kind": "context-handoff", "title": "Newest baton", "consumed": False,
        "createdAt": "2026-02-01T00:00:00.000Z", "sessionId": "sess-1",
    }), encoding="utf-8")
    (handoff_dir / "handoff-consumed.json").write_text(json.dumps({
        "kind": "context-handoff", "title": "Already consumed",
        "consumed": True, "createdAt": "2026-03-01T00:00:00.000Z",
    }), encoding="utf-8")
    (handoff_dir / "not-a-handoff.json").write_text(json.dumps({
        "kind": "something-else", "createdAt": "2026-04-01T00:00:00.000Z",
    }), encoding="utf-8")

    monkeypatch.setattr(hc, "worktree_state_dir", lambda *a, **k: str(tmp_path))

    result = hc.pending_handoff("dotfiles", "/w")

    assert result == {
        "title": "Newest baton",
        "createdAt": "2026-02-01T00:00:00.000Z",
        "sessionId": "sess-1",
    }


def test_pending_handoff_none_when_no_state_dir(monkeypatch):
    monkeypatch.setattr(hc, "worktree_state_dir", lambda *a, **k: None)
    assert hc.pending_handoff("dotfiles", "/w") is None


def test_pending_handoff_none_when_no_handoff_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(hc, "worktree_state_dir", lambda *a, **k: str(tmp_path))
    assert hc.pending_handoff("dotfiles", "/w") is None
