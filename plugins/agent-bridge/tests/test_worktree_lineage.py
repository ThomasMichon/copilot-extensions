"""Tests for the write-side session-lineage helper (worktree-self-knowledge
Phase 4).

agent-bridge contributes the ACP session's lifecycle *facts* to the
agent-worktrees ground layer (register + link-succession + note-handoff) so the
derived head is correct on the ACP path, and reads the successor's role + the
worktree history digest to seed its lineage awareness (the ``sessionStart``
role/digest hook cannot fire under ``copilot --acp``). Every path is fail-open.

These tests cover the pure seed-header composition and the fail-open subprocess
wrappers (argv shape + degrade-to-no-op), without spawning the real binstub.
"""

from __future__ import annotations

import subprocess


from agent_bridge import worktree_lineage as wl


# --- pure seed-header composition -------------------------------------------

def test_header_head_role_names_predecessor():
    out = wl.build_succession_seed_header(
        {"role": "head", "head_session": "S2"}, None, predecessor="S1"
    )
    assert "current head" in out
    assert "S1" in out
    assert out.startswith("## Your place in this worktree's lineage")


def test_header_successor_elect_without_role_predecessor():
    # After link-succession the role is head; but if we ever seed pre-link the
    # incoming role still names the predecessor we pass explicitly.
    out = wl.build_succession_seed_header(
        {"role": "successor-elect", "pending_handoff_predecessor": "S1"}, None
    )
    assert "incoming head" in out
    assert "S1" in out


def test_header_includes_digest():
    out = wl.build_succession_seed_header(
        {"role": "head"}, "focus: build the thing\nhandoff: S1 -> S2", predecessor="S1"
    )
    assert "Recent worktree history" in out
    assert "build the thing" in out


def test_header_empty_when_nothing_to_say():
    assert wl.build_succession_seed_header(None, None) == ""
    assert wl.build_succession_seed_header({}, None) == ""


def test_header_digest_only_no_role():
    out = wl.build_succession_seed_header(None, "focus: x", predecessor=None)
    assert "Recent worktree history" in out
    assert "focus: x" in out


# --- fail-open wrappers ------------------------------------------------------

class _FakeProc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _patch_run(monkeypatch, *, captured, returncode=0, stdout="", raise_exc=None):
    monkeypatch.setattr(wl, "_agent_worktrees_bin", lambda: "/usr/bin/agent-worktrees")

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        if raise_exc:
            raise raise_exc
        return _FakeProc(returncode=returncode, stdout=stdout)

    monkeypatch.setattr(subprocess, "run", fake_run)


def test_register_session_argv(monkeypatch):
    captured: dict = {}
    _patch_run(monkeypatch, captured=captured)
    ok = wl.register_session("wt-a", "acp-1", pid=42, worktree_dir="/wt/a")
    assert ok is True
    argv = captured["argv"]
    assert argv[1:] == [
        "register-session", "--session-id", "acp-1", "--cwd", "/wt/a", "--pid", "42",
    ]


def test_register_session_argv_no_dir_fallback(monkeypatch):
    captured: dict = {}
    _patch_run(monkeypatch, captured=captured)
    wl.register_session("wt-a", "acp-1")
    assert captured["argv"][1:] == [
        "register-session", "--session-id", "acp-1", "--worktree-id", "wt-a",
    ]


def test_link_succession_argv(monkeypatch):
    captured: dict = {}
    _patch_run(monkeypatch, captured=captured, stdout="{}")
    ok = wl.link_succession("wt-a", "acp-1", "acp-2", worktree_dir="/wt/a")
    assert ok is True
    assert captured["argv"][1:] == [
        "link-succession", "--worktree", "wt-a",
        "--predecessor", "acp-1", "--successor", "acp-2", "--json",
    ]


def test_note_handoff_argv(monkeypatch):
    captured: dict = {}
    _patch_run(monkeypatch, captured=captured)
    ok = wl.note_handoff("wt-a", "acp-1", title="context-pressure", worktree_dir="/wt/a")
    assert ok is True
    assert captured["argv"][1:] == [
        "note-handoff", "--session-id", "acp-1", "--worktree-dir", "/wt/a",
        "--title", "context-pressure",
    ]


def test_session_role_uses_worktree_dir(monkeypatch):
    captured: dict = {}
    _patch_run(
        monkeypatch, captured=captured,
        stdout='{"role": "head", "head_session": "acp-2", "is_head": true}',
    )
    role = wl.session_role("wt-a", "acp-2", worktree_dir="/wt/a")
    assert role == {"role": "head", "head_session": "acp-2", "is_head": True}
    assert "--worktree-dir" in captured["argv"] and "/wt/a" in captured["argv"]
    assert "--worktree-id" not in captured["argv"]


def test_history_digest_returns_text(monkeypatch):
    captured: dict = {}
    _patch_run(monkeypatch, captured=captured, stdout="  focus: x\n")
    assert wl.history_digest("wt-a", "acp-2", worktree_dir="/wt/a") == "focus: x"
    assert "--worktree-dir" in captured["argv"]


def test_missing_binstub_fails_open(monkeypatch):
    monkeypatch.setattr(wl, "_agent_worktrees_bin", lambda: None)
    assert wl.register_session("wt-a", "acp-1") is False
    assert wl.link_succession("wt-a", "acp-1", "acp-2") is False
    assert wl.note_handoff("wt-a", "acp-1") is False
    assert wl.session_role("wt-a", "acp-1") is None
    assert wl.history_digest("wt-a") is None


def test_subprocess_raise_fails_open(monkeypatch):
    captured: dict = {}
    _patch_run(monkeypatch, captured=captured, raise_exc=OSError("boom"))
    assert wl.register_session("wt-a", "acp-1") is False
    assert wl.session_role("wt-a", "acp-1") is None


def test_nonzero_exit_is_false(monkeypatch):
    captured: dict = {}
    _patch_run(monkeypatch, captured=captured, returncode=3)
    assert wl.link_succession("wt-a", "acp-1", "acp-2") is False


def test_empty_args_short_circuit(monkeypatch):
    # Never shell out on missing ids.
    called = {"n": 0}
    monkeypatch.setattr(wl, "_agent_worktrees_bin", lambda: (_ for _ in ()).throw(AssertionError))
    # _agent_worktrees_bin must not even be consulted when ids are empty.
    monkeypatch.setattr(wl, "_run", lambda *a, **k: called.__setitem__("n", called["n"] + 1) or None)
    assert wl.register_session("", "acp-1") is False
    assert wl.link_succession("wt", "", "acp-2") is False
    assert wl.note_handoff("wt", "") is False
    assert wl.session_role("", "acp") is None
    assert wl.history_digest("") is None
    assert called["n"] == 0
