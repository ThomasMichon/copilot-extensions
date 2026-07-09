"""Tests for connection emit-back (_connection_identity / _print)."""

from __future__ import annotations

from agent_bridge.__main__ import (
    _connection_identity,
    _print_connection_identity,
)


class _FakeClient:
    def __init__(self, session: dict | None, raise_exc: bool = False):
        self._session = session
        self._raise = raise_exc

    def get_session(self, session_id: str) -> dict:
        if self._raise:
            raise RuntimeError("boom")
        return self._session or {}


class TestConnectionIdentity:
    def test_ssh_venue_with_worktree(self):
        client = _FakeClient({
            "agent_name": "SPO.Core@cloud1",
            "target_type": "ssh",
            "target_host": "tmichon-cloud1",
            "worktree_id": "wt-abc123",
        })
        ident = _connection_identity(client, "sess-1")
        assert ident["session_id"] == "sess-1"
        assert ident["agent"] == "SPO.Core@cloud1"
        assert ident["venue"] == "ssh:tmichon-cloud1"
        assert ident["worktree_id"] == "wt-abc123"

    def test_command_venue_codespace(self):
        client = _FakeClient({
            "agent_name": "codespace:type-filters",
            "target_type": "command",
            "target_host": "",
            "worktree_id": None,
        })
        ident = _connection_identity(client, "sess-2")
        # No host -> venue is just the type.
        assert ident["venue"] == "command"
        assert ident["worktree_id"] is None

    def test_local_loopback(self):
        client = _FakeClient({
            "agent_name": "dev6",
            "target_type": "local",
            "target_host": None,
            "worktree_id": None,
        })
        ident = _connection_identity(client, "sess-3")
        assert ident["venue"] == "local"
        assert ident["agent"] == "dev6"

    def test_get_session_failure_is_best_effort(self):
        client = _FakeClient(None, raise_exc=True)
        ident = _connection_identity(client, "sess-4")
        # Degrades gracefully to just the session id.
        assert ident == {"session_id": "sess-4"}

    def test_print_line(self, capsys):
        _print_connection_identity({
            "session_id": "sess-5",
            "agent": "dotfiles@cloud1",
            "venue": "ssh:tmichon-cloud1",
            "worktree_id": "wt-9",
        })
        out = capsys.readouterr().out
        assert "session=sess-5" in out
        assert "agent=dotfiles@cloud1" in out
        assert "venue=ssh:tmichon-cloud1" in out
        assert "worktree=wt-9" in out

    def test_print_omits_missing_fields(self, capsys):
        _print_connection_identity({"session_id": "sess-6"})
        out = capsys.readouterr().out
        assert "session=sess-6" in out
        assert "agent=" not in out
        assert "worktree=" not in out


class TestWorktreesGet:
    """CLI caller/sender identity from `agent-worktrees get` (replaces WORKTREE_ID)."""

    def _fake_run(self, stdout="", rc=0):
        import subprocess
        class _R:
            returncode = rc
            def __init__(self, out): self.stdout = out; self.stderr = ""
        def run(cmd, **kw):
            return _R(stdout)
        return run

    def test_worktrees_get_returns_value(self, monkeypatch):
        import agent_bridge.__main__ as m
        monkeypatch.setattr(m.shutil, "which", lambda n: "agent-worktrees")
        monkeypatch.setattr(m.subprocess, "run", self._fake_run("dotfiles\n"))
        assert m._worktrees_get("project") == "dotfiles"

    def test_sender_repo(self, monkeypatch):
        import agent_bridge.__main__ as m
        monkeypatch.setattr(m.shutil, "which", lambda n: "agent-worktrees")
        monkeypatch.setattr(m.subprocess, "run", self._fake_run("SPO.Core\n"))
        assert m._sender_repo() == "SPO.Core"

    def test_caller_id_uses_worktree_dir(self, monkeypatch):
        import agent_bridge.__main__ as m
        monkeypatch.setattr(m.shutil, "which", lambda n: "agent-worktrees")
        monkeypatch.setattr(m.subprocess, "run", self._fake_run("D:/wt/x\n"))
        assert m._get_caller_id() == "D:/wt/x"

    def test_empty_output_is_none(self, monkeypatch):
        import agent_bridge.__main__ as m
        monkeypatch.setattr(m.shutil, "which", lambda n: "agent-worktrees")
        monkeypatch.setattr(m.subprocess, "run", self._fake_run("\n"))
        assert m._worktrees_get("project") is None

    def test_binary_missing_is_none(self, monkeypatch):
        import agent_bridge.__main__ as m
        monkeypatch.setattr(m.shutil, "which", lambda n: None)
        assert m._worktrees_get("project") is None

    def test_nonzero_rc_is_none(self, monkeypatch):
        import agent_bridge.__main__ as m
        monkeypatch.setattr(m.shutil, "which", lambda n: "agent-worktrees")
        monkeypatch.setattr(m.subprocess, "run", self._fake_run("boom", rc=1))
        assert m._worktrees_get("bogus") is None
