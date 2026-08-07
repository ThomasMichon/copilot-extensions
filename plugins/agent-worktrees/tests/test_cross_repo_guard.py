"""Tests for the cross_repo_guard preToolUse hook decision logic (E1c, #878)."""

from __future__ import annotations

import importlib.util
import json
import time
from pathlib import Path

import pytest

# The guard ships as a standalone script under scripts/ (deployed to
# ~/.agent-worktrees/bin/), not as a package module -- load it by path.
_GUARD_PATH = Path(__file__).resolve().parents[1] / "scripts" / "cross_repo_guard.py"
_spec = importlib.util.spec_from_file_location("cross_repo_guard", _GUARD_PATH)
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)


@pytest.fixture
def guarded(tmp_path: Path) -> list[dict]:
    """One agent-guarded related repo with a real local checkout path."""
    repo = tmp_path / "SPO.Core"
    repo.mkdir()
    return [{"name": "SPO.Core", "delegate": "agent-bridge",
             "path": str(repo), "locus": {"machines": ["dev6"]}}]


def _write(tool, path, cwd):
    return {"toolName": tool, "cwd": str(cwd), "toolArgs": {"path": str(path)}}


def _shell(cmd, cwd):
    return {"toolName": "bash", "cwd": str(cwd), "toolArgs": {"command": cmd}}


# --- write-tool blocking ------------------------------------------------------

def test_write_into_guarded_repo_denies(tmp_path, guarded):
    target = Path(guarded[0]["path"]) / "src" / "x.ts"
    p = _write("create", target, tmp_path)
    d = guard.decide(p, env={}, home=tmp_path, guarded_roots=guarded)
    assert d["permissionDecision"] == "deny"
    assert "SPO.Core" in d["permissionDecisionReason"]
    assert "agent-bridge" in d["permissionDecisionReason"]


def test_write_outside_guarded_repo_allows(tmp_path, guarded):
    p = _write("create", tmp_path / "elsewhere" / "y.ts", tmp_path)
    assert guard.decide(p, env={}, home=tmp_path, guarded_roots=guarded) is None


def test_read_tool_into_guarded_allows(tmp_path, guarded):
    target = Path(guarded[0]["path"]) / "src" / "x.ts"
    p = {"toolName": "view", "cwd": str(tmp_path), "toolArgs": {"path": str(target)}}
    assert guard.decide(p, env={}, home=tmp_path, guarded_roots=guarded) is None


def test_relative_write_path_resolves_against_cwd(tmp_path, guarded):
    # cwd INSIDE the guarded repo + a relative path -> resolves into it -> deny.
    cwd = Path(guarded[0]["path"]) / "src"
    p = {"toolName": "edit", "cwd": str(cwd), "toolArgs": {"path": "x.ts"}}
    d = guard.decide(p, env={}, home=tmp_path, guarded_roots=guarded)
    assert d and d["permissionDecision"] == "deny"


# --- shell blocking -----------------------------------------------------------

def test_shell_write_into_guarded_denies(tmp_path, guarded):
    gp = guarded[0]["path"]
    p = _shell(f'Set-Content "{gp}\\notes.md" "hi"', tmp_path)
    d = guard.decide(p, env={}, home=tmp_path, guarded_roots=guarded)
    assert d and d["permissionDecision"] == "deny"


def test_shell_git_commit_into_guarded_denies(tmp_path, guarded):
    gp = guarded[0]["path"]
    p = _shell(f'git -C "{gp}" commit -m x', tmp_path)
    d = guard.decide(p, env={}, home=tmp_path, guarded_roots=guarded)
    assert d and d["permissionDecision"] == "deny"


def test_shell_read_into_guarded_allows(tmp_path, guarded):
    gp = guarded[0]["path"]
    p = _shell(f'cat "{gp}\\README.md"', tmp_path)
    assert guard.decide(p, env={}, home=tmp_path, guarded_roots=guarded) is None


# --- modes + kill switches ----------------------------------------------------

def test_mode_off_env_allows(tmp_path, guarded):
    target = Path(guarded[0]["path"]) / "x.ts"
    p = _write("create", target, tmp_path)
    assert guard.decide(p, env={"CROSS_REPO_GUARD": "off"}, home=tmp_path,
                        guarded_roots=guarded) is None
    assert guard.decide(p, env={"CROSS_REPO_GUARD_MODE": "off"}, home=tmp_path,
                        guarded_roots=guarded) is None


def test_mode_warn_returns_additional_context(tmp_path, guarded):
    target = Path(guarded[0]["path"]) / "x.ts"
    p = _write("create", target, tmp_path)
    d = guard.decide(p, env={"CROSS_REPO_GUARD_MODE": "warn"}, home=tmp_path,
                     guarded_roots=guarded)
    assert d and "additionalContext" in d and "permissionDecision" not in d


def test_mode_ask_returns_ask(tmp_path, guarded):
    target = Path(guarded[0]["path"]) / "x.ts"
    p = _write("create", target, tmp_path)
    d = guard.decide(p, env={"CROSS_REPO_GUARD_MODE": "ask"}, home=tmp_path,
                     guarded_roots=guarded)
    assert d and d["permissionDecision"] == "ask"


# --- break-glass --------------------------------------------------------------

def test_active_break_glass_allows(tmp_path, guarded):
    home = tmp_path / "home"
    (home / ".agent-worktrees").mkdir(parents=True)
    (home / ".agent-worktrees" / "allow-edits.json").write_text(json.dumps({
        "grants": {"SPO.Core": {"expires_at_ms": (time.time() + 600) * 1000}}
    }), encoding="utf-8")
    target = Path(guarded[0]["path"]) / "x.ts"
    p = _write("create", target, tmp_path)
    assert guard.decide(p, env={}, home=home, guarded_roots=guarded) is None


def test_expired_break_glass_still_denies(tmp_path, guarded):
    home = tmp_path / "home"
    (home / ".agent-worktrees").mkdir(parents=True)
    (home / ".agent-worktrees" / "allow-edits.json").write_text(json.dumps({
        "grants": {"SPO.Core": {"expires_at_ms": (time.time() - 60) * 1000}}
    }), encoding="utf-8")
    target = Path(guarded[0]["path"]) / "x.ts"
    p = _write("create", target, tmp_path)
    d = guard.decide(p, env={}, home=home, guarded_roots=guarded)
    assert d and d["permissionDecision"] == "deny"


# --- empty guarded set / fail-open --------------------------------------------

def test_no_guarded_repos_allows(tmp_path):
    target = tmp_path / "anything" / "x.ts"
    p = _write("create", target, tmp_path)
    assert guard.decide(p, env={}, home=tmp_path, guarded_roots=[]) is None


# --- guarded-root discovery (injected runner) ---------------------------------

def test_discover_filters_non_delegated_and_pathless(monkeypatch, tmp_path):
    real = tmp_path / "GuardedRepo"
    real.mkdir()

    def fake_related(args, cwd):
        if args[0] == "list":
            return json.dumps({"related": [
                {"name": "GuardedRepo", "delegate": "agent-bridge"},
                {"name": "PlainRepo", "delegate": "none"},
                {"name": "RemoteOnly", "delegate": "agent-codespaces"},
            ]})
        if args[0] == "show":
            name = args[1]
            if name == "GuardedRepo":
                return json.dumps({"registry": {"path": str(real)},
                                   "locus": {"machines": ["dev6"]}})
            return json.dumps({"registry": None})  # no local checkout
        return None

    monkeypatch.setattr(guard, "_run_related", fake_related)
    roots = guard._discover_guarded_roots(str(tmp_path))
    names = {g["name"] for g in roots}
    assert names == {"GuardedRepo"}  # plain (none) + pathless (RemoteOnly) dropped
    assert roots[0]["path"] == str(real)


def test_load_guarded_roots_cache_roundtrip(monkeypatch, tmp_path):
    home = tmp_path / "home"
    calls = {"n": 0}

    def fake_discover(root):
        calls["n"] += 1
        return [{"name": "X", "delegate": "agent-bridge", "path": str(tmp_path)}]

    monkeypatch.setattr(guard, "_discover_guarded_roots", fake_discover)
    r1 = guard.load_guarded_roots(str(tmp_path), home)
    r2 = guard.load_guarded_roots(str(tmp_path), home)
    assert r1 == r2 and calls["n"] == 1  # second call served from cache
