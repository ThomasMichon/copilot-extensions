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

import pytest

from agent_bridge import worktree_lineage as wl
from agent_bridge._peer_launch import ContextRefused

PREFIX = ["/owner/python", "-I", "-m", "agent_worktrees"]


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
    monkeypatch.setattr(wl, "_agent_worktrees_launch_prefix", lambda: PREFIX)

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
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
    # dotfiles#458: both --worktree-id and --cwd are always passed together
    # when known, so register-session can use the already-resolved id
    # directly instead of depending solely on fragile cwd-based inference.
    assert argv[:len(PREFIX)] == PREFIX
    assert argv[len(PREFIX):] == [
        "register-session", "--session-id", "acp-1", "--worktree-id", "wt-a",
        "--cwd", "/wt/a", "--pid", "42",
    ]


def test_register_session_argv_no_dir_fallback(monkeypatch):
    captured: dict = {}
    _patch_run(monkeypatch, captured=captured)
    wl.register_session("wt-a", "acp-1")
    assert captured["argv"][len(PREFIX):] == [
        "register-session", "--session-id", "acp-1", "--worktree-id", "wt-a",
    ]


def test_link_succession_argv(monkeypatch):
    captured: dict = {}
    _patch_run(monkeypatch, captured=captured, stdout="{}")
    ok = wl.link_succession("wt-a", "acp-1", "acp-2", worktree_dir="/wt/a")
    assert ok is True
    assert captured["argv"][len(PREFIX):] == [
        "link-succession", "--worktree", "wt-a",
        "--predecessor", "acp-1", "--successor", "acp-2", "--json",
    ]


def test_note_handoff_argv(monkeypatch):
    captured: dict = {}
    _patch_run(monkeypatch, captured=captured)
    ok = wl.note_handoff("wt-a", "acp-1", title="context-pressure", worktree_dir="/wt/a")
    assert ok is True
    assert captured["argv"][len(PREFIX):] == [
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
    monkeypatch.setattr(wl, "_agent_worktrees_launch_prefix", lambda: None)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("unselected owner spawned"))
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
    monkeypatch.setattr(wl, "_agent_worktrees_launch_prefix", lambda: (_ for _ in ()).throw(AssertionError))
    # Owner selection must not even be consulted when ids are empty.
    monkeypatch.setattr(wl, "_run", lambda *a, **k: called.__setitem__("n", called["n"] + 1) or None)
    assert wl.register_session("", "acp-1") is False
    assert wl.link_succession("wt", "", "acp-2") is False
    assert wl.note_handoff("wt", "") is False
    assert wl.session_role("", "acp") is None
    assert wl.history_digest("") is None
    assert called["n"] == 0


def test_refused_installation_fails_open_without_subprocess(monkeypatch):
    def refused():
        raise ContextRefused("selected installation refused")

    monkeypatch.setattr(wl, "_agent_worktrees_launch_prefix", refused)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("refused owner spawned"))
    assert wl.register_session("wt-a", "acp-1") is False
    assert wl.link_succession("wt-a", "acp-1", "acp-2") is False
    assert wl.note_handoff("wt-a", "acp-1") is False
    assert wl.session_role("wt-a", "acp-1") is None
    assert wl.history_digest("wt-a") is None


def test_lineage_scrubs_owner_routing_and_preserves_cwd(monkeypatch, tmp_path):
    inherited = {
        "AGENT_HOME", "AGENT_RT_ROOT", "AGENT_RT_PY", "COPILOT_PLUGIN_ROOT",
        "VIRTUAL_ENV", "PYTHONHOME", "PYTHONPATH", "__PYVENV_LAUNCHER__",
    }
    for key in inherited:
        monkeypatch.setenv(key, "/caller/override")
        monkeypatch.setenv(key.lower(), "/caller/override")
    monkeypatch.setenv("LINEAGE_UNRELATED", "retained")
    captured: dict = {}
    _patch_run(monkeypatch, captured=captured)
    assert wl.register_session("wt-a", "acp-1", worktree_dir=str(tmp_path))
    kwargs = captured["kwargs"]
    assert kwargs["cwd"] == str(tmp_path)
    assert kwargs["timeout"] == wl._LINEAGE_TIMEOUT_S
    assert kwargs["env"]["LINEAGE_UNRELATED"] == "retained"
    assert not any(key.upper() in inherited for key in kwargs["env"])
