"""Tests for the process-boundary engine client (Phase 6b).

The Worktree Manager reaches the agent-worktrees engine ONLY by shelling out to
its ``--json`` verbs (never importing it). These tests drive that seam with a
faked ``subprocess.run`` + ``engine_path`` so no real engine is required, and
assert the parsing, the version-skew retry, and the error paths.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from worktree_manager import engine_client as ec


def _fake_completed(cmd, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr=stderr)


def _install_fake(monkeypatch, handler):
    """Point the client at a fake engine binstub + a scripted ``subprocess.run``."""
    monkeypatch.setattr(ec, "engine_path", lambda: "/fake/agent-worktrees")
    monkeypatch.setattr(ec.subprocess, "run",
                        lambda cmd, **kw: handler(cmd, kw))


_ONE_WT = {
    "version": 1,
    "worktrees": [
        {
            "id": "tmichon-cloud1-win-20260813-1200-ab12",
            "repo": "dotfiles",
            "machine": "cloud1",
            "branch": "worktree/x",
            "title": "fix the thing",
            "state": "wip",
            "ahead": 2,
            "behind": 1,
            "dirty": True,
            "status": "active",
            "path": "/w/x",
        }
    ],
}


def test_list_worktrees_parses_rows(monkeypatch):
    def handler(cmd, kw):
        assert "--project" in cmd and "dotfiles" in cmd
        assert "list" in cmd and "--json" in cmd and "--classify" in cmd
        return _fake_completed(cmd, stdout=json.dumps(_ONE_WT))

    _install_fake(monkeypatch, handler)
    wts = ec.list_worktrees("dotfiles")
    assert len(wts) == 1
    w = wts[0]
    assert w.repo == "dotfiles" and w.machine == "cloud1"
    assert w.state == "wip" and w.ahead == 2 and w.behind == 1 and w.dirty
    assert w.id4 == "ab12"
    assert w.sync_tag == "\u21912\u21931"
    assert w.title == "fix the thing"


def test_title_null_is_none(monkeypatch):
    payload = {"version": 1, "worktrees": [{"id": "aaaa", "title": "null"}]}
    _install_fake(monkeypatch, lambda cmd, kw: _fake_completed(cmd, stdout=json.dumps(payload)))
    (w,) = ec.list_worktrees("dotfiles")
    assert w.title is None
    assert w.id4 == "aaaa"


def test_engine_absent_raises_install_hint(monkeypatch):
    monkeypatch.setattr(ec, "engine_path", lambda: None)
    assert ec.engine_available() is False
    with pytest.raises(ec.EngineError) as ei:
        ec.list_worktrees("dotfiles")
    assert ei.value.install_hint is True


def test_classify_rejection_retries_without(monkeypatch):
    calls = []

    def handler(cmd, kw):
        calls.append(list(cmd))
        if "--classify" in cmd:
            # An older engine rejects the unknown flag.
            return _fake_completed(cmd, returncode=2, stderr="unrecognized arguments: --classify")
        return _fake_completed(cmd, stdout=json.dumps(_ONE_WT))

    _install_fake(monkeypatch, handler)
    wts = ec.list_worktrees("dotfiles")
    assert len(wts) == 1
    # First attempt carried --classify; the retry dropped it.
    assert any("--classify" in c for c in calls)
    assert any("--classify" not in c for c in calls)


def test_error_envelope_is_surfaced(monkeypatch):
    payload = json.dumps({"version": 1, "error": "no such project"})
    _install_fake(monkeypatch, lambda cmd, kw: _fake_completed(cmd, returncode=1, stdout=payload))
    with pytest.raises(ec.EngineError) as ei:
        ec.list_worktrees("dotfiles", classify=False)
    assert "no such project" in str(ei.value)


def test_invalid_json_raises(monkeypatch):
    _install_fake(monkeypatch, lambda cmd, kw: _fake_completed(cmd, stdout="not json"))
    with pytest.raises(ec.EngineError):
        ec.list_worktrees("dotfiles", classify=False)


def test_timeout_raises_engine_error(monkeypatch):
    monkeypatch.setattr(ec, "engine_path", lambda: "/fake/agent-worktrees")

    def boom(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 1)

    monkeypatch.setattr(ec.subprocess, "run", boom)
    with pytest.raises(ec.EngineError) as ei:
        ec.list_worktrees("dotfiles", classify=False)
    assert "timed out" in str(ei.value)


def test_empty_worktrees_list(monkeypatch):
    _install_fake(monkeypatch, lambda cmd, kw: _fake_completed(
        cmd, stdout=json.dumps({"version": 1, "worktrees": []})))
    assert ec.list_worktrees("dotfiles") == []


# ── resolve_launch_plan (slice 3) ─────────────────────────────────────────────

_RESUME_PLAN = {
    "action": "exec",
    "work_dir": "/w/x",
    "status_path": "/w/x",
    "cmd": ["copilot", "--resume=sess123"],
    "env": {"COPILOT_CUSTOM_INSTRUCTIONS_DIRS": "/home/u/.dotfiles"},
    "worktree_id": "m-win-1200-ab12",
    "post_exit": True,
    "no_mux": True,
}


def test_resolve_resume_parses_plan(monkeypatch):
    def handler(cmd, kw):
        assert "resolve" in cmd and "--json" in cmd
        assert "--worktree-id" in cmd and "m-win-1200-ab12" in cmd
        assert "--new" not in cmd
        return _fake_completed(cmd, stdout=json.dumps(_RESUME_PLAN))

    _install_fake(monkeypatch, handler)
    plan = ec.resolve_launch_plan("dotfiles", worktree_id="m-win-1200-ab12")
    assert plan.is_exec and plan.no_mux is True
    assert plan.cmd == ["copilot", "--resume=sess123"]
    assert plan.work_dir == "/w/x" and plan.worktree_id == "m-win-1200-ab12"
    assert plan.post_exit is True


def test_resolve_new_sends_new_flag(monkeypatch):
    def handler(cmd, kw):
        assert "--new" in cmd and "--worktree-id" not in cmd
        return _fake_completed(cmd, stdout=json.dumps(_RESUME_PLAN))

    _install_fake(monkeypatch, handler)
    plan = ec.resolve_launch_plan("dotfiles", new=True)
    assert plan.is_exec


def test_resolve_requires_a_target(monkeypatch):
    _install_fake(monkeypatch, lambda cmd, kw: _fake_completed(cmd))
    with pytest.raises(ec.EngineError):
        ec.resolve_launch_plan("dotfiles")


def test_resolve_worktree_and_new_are_exclusive(monkeypatch):
    _install_fake(monkeypatch, lambda cmd, kw: _fake_completed(cmd))
    with pytest.raises(ec.EngineError):
        ec.resolve_launch_plan("dotfiles", worktree_id="x", new=True)


def test_resolve_none_action(monkeypatch):
    payload = {"action": "none", "exit_code": 0}
    _install_fake(monkeypatch, lambda cmd, kw: _fake_completed(cmd, stdout=json.dumps(payload)))
    plan = ec.resolve_launch_plan("dotfiles", worktree_id="x")
    assert plan.action == "none" and not plan.is_exec and plan.exit_code == 0


def test_resolve_unwraps_nested_launch(monkeypatch):
    nested = {"worktree": {"id": "x"}, "launch": _RESUME_PLAN}
    _install_fake(monkeypatch, lambda cmd, kw: _fake_completed(cmd, stdout=json.dumps(nested)))
    plan = ec.resolve_launch_plan("dotfiles", worktree_id="m-win-1200-ab12")
    assert plan.cmd == ["copilot", "--resume=sess123"]


def test_resolve_bare_resume_skew_retries_without_flag(monkeypatch):
    calls = []

    def handler(cmd, kw):
        calls.append(list(cmd))
        if "--bare-resume" in cmd:
            return _fake_completed(cmd, returncode=2,
                                   stderr="unrecognized arguments: --bare-resume")
        return _fake_completed(cmd, stdout=json.dumps(_RESUME_PLAN))

    _install_fake(monkeypatch, handler)
    plan = ec.resolve_launch_plan("dotfiles", worktree_id="x", bare_resume=True)
    assert plan.is_exec
    assert any("--bare-resume" in c for c in calls)
    assert any("--bare-resume" not in c for c in calls)


def test_resolve_error_envelope_surfaced(monkeypatch):
    payload = json.dumps({"version": 1, "error": "no such worktree"})
    _install_fake(monkeypatch, lambda cmd, kw: _fake_completed(cmd, returncode=1, stdout=payload))
    with pytest.raises(ec.EngineError) as ei:
        ec.resolve_launch_plan("dotfiles", worktree_id="nope")
    assert "no such worktree" in str(ei.value)
