"""Tests for the Manager's `update` command — the plugin updater/aligner seam.

`<project> update` hands off to `worktree-manager update`, which (1) self-updates
the Manager and (2) orchestrates the harness update by driving the engine's own
mechanics via `agent-worktrees update --no-manager` (the seam bypass). These pin
that sequencing, the flag forwarding, the bypass boundary, and self_update's
best-effort behavior — all without a real engine or network.
"""

from __future__ import annotations

import io
import subprocess
from contextlib import redirect_stdout

import pytest

from worktree_manager import __main__ as wm
from worktree_manager import engine_client as ec
from worktree_manager import self_install


def _run_update(rest, monkeypatch, *, su_action="already-current", su_kwargs=None):
    from worktree_manager.self_install import SelfUpdateResult
    calls = {}
    monkeypatch.setattr(
        wm, "_cmd_update", wm._cmd_update)  # ensure real function under test

    def fake_self_update(**kw):
        calls["self_update"] = kw
        return SelfUpdateResult(action=su_action, version="0.1.0-dev9",
                                previous="0.1.0-dev8", **(su_kwargs or {}))

    monkeypatch.setattr(self_install, "self_update", fake_self_update)

    def fake_passthrough(project, args, **kw):
        calls["passthrough"] = {"project": project, "args": args}
        return 0

    monkeypatch.setattr(ec, "run_engine_passthrough", fake_passthrough)
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = wm._cmd_update(rest)
    return rc, calls, buf.getvalue()


def test_update_self_updates_then_orchestrates_bypass(monkeypatch):
    rc, calls, out = _run_update([], monkeypatch)
    assert rc == 0
    # Self-update ran first…
    assert "self_update" in calls
    # …then the harness update was driven through the engine with the bypass flag.
    assert calls["passthrough"]["project"] is None
    assert calls["passthrough"]["args"] == ["update", "--no-manager"]


def test_update_forwards_flags_and_strips_project(monkeypatch):
    rc, calls, out = _run_update(
        ["--force", "--project", "dotfiles", "--skip-modules", "agent-bridge"],
        monkeypatch)
    assert rc == 0
    # --project is stripped (harness-wide); other flags forwarded after the bypass.
    assert calls["passthrough"]["args"] == [
        "update", "--no-manager", "--force", "--skip-modules", "agent-bridge"]


def test_update_reports_self_update_and_continues(monkeypatch):
    rc, calls, out = _run_update([], monkeypatch, su_action="updated")
    assert rc == 0
    assert "updated" in out and "active on next run" in out
    assert "passthrough" in calls  # continues to the harness update regardless


def test_update_engine_absent_hints_setup(monkeypatch):
    from worktree_manager.self_install import SelfUpdateResult
    monkeypatch.setattr(self_install, "self_update",
                        lambda **kw: SelfUpdateResult(action="skipped", reason="git not found"))

    def boom(project, args, **kw):
        raise ec.EngineError("not installed", install_hint=True)

    monkeypatch.setattr(ec, "run_engine_passthrough", boom)
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = wm._cmd_update([])
    assert rc == 1
    assert "worktree-manager setup" in buf.getvalue()


def test_strip_project_removes_pair():
    assert wm._strip_project(["--force", "--project", "x", "--skip-modules"]) == \
        ["--force", "--skip-modules"]


# ── run_engine_passthrough ────────────────────────────────────────────────────

def test_passthrough_builds_command_and_returns_code(monkeypatch):
    monkeypatch.setattr(ec, "engine_path", lambda: "/fake/agent-worktrees")
    ec.set_engine_command(None)
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(ec.subprocess, "run", fake_run)
    rc = ec.run_engine_passthrough(None, ["update", "--no-manager"])
    assert rc == 0
    assert seen["cmd"][-2:] == ["update", "--no-manager"]
    assert "/fake/agent-worktrees" in seen["cmd"][0]


def test_passthrough_absent_engine_hints_install(monkeypatch):
    monkeypatch.setattr(ec, "engine_path", lambda: None)
    ec.set_engine_command(None)
    with pytest.raises(ec.EngineError) as ei:
        ec.run_engine_passthrough(None, ["update"])
    assert ei.value.install_hint is True


# ── self_update (best-effort, git-fetch + version-install) ────────────────────

def test_self_update_skips_without_git(monkeypatch):
    monkeypatch.setattr(self_install.shutil, "which", lambda name: None)
    res = self_install.self_update(dry_run=True)
    assert res.action == "skipped"
    assert "git" in (res.reason or "")


def test_self_update_reports_updated(monkeypatch, tmp_path):
    from worktree_manager.self_install import SelfInstallResult
    monkeypatch.setattr(self_install.shutil, "which", lambda name: "/usr/bin/git")
    monkeypatch.setattr(self_install.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a[0], 0))
    # Pretend the fetched payload exists and self_install reports an install.
    monkeypatch.setattr(self_install.Path, "is_file", lambda self: True)
    monkeypatch.setattr(self_install, "self_install",
                        lambda **kw: SelfInstallResult(version="0.1.0-dev9",
                                                       action="installed", root=str(tmp_path)))
    res = self_install.self_update(root=tmp_path, dry_run=False)
    assert res.action == "updated"
    assert res.version == "0.1.0-dev9"
