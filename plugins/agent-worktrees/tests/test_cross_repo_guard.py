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


# -- false-positive regressions (dotfiles#1144), mirrored from anchor guard ----

def test_shell_readonly_git_with_fd_redirect_and_path_in_var_allows(
    tmp_path, guarded
):
    gp = guarded[0]["path"]
    cmd = f'$a="{gp}"; cd $a; git fetch origin --quiet 2>&1 | Out-Null; git log'
    assert guard.decide(_shell(cmd, tmp_path), env={},
                        home=tmp_path, guarded_roots=guarded) is None


def test_shell_guarded_path_in_quoted_body_allows(tmp_path, guarded):
    gp = guarded[0]["path"]
    body = f'mentions `{gp}` and `Set-Content` and `git commit` as prose'
    cmd = f'gh issue create --repo o/r --body "{body}"'
    assert guard.decide(_shell(cmd, tmp_path), env={},
                        home=tmp_path, guarded_roots=guarded) is None


def test_shell_fd_dup_redirect_is_not_a_write(tmp_path, guarded):
    gp = guarded[0]["path"]
    assert guard.decide(_shell(f'cat "{gp}\\x" 2>&1', tmp_path), env={},
                        home=tmp_path, guarded_roots=guarded) is None


def test_shell_git_read_dashC_guarded_allows(tmp_path, guarded):
    gp = guarded[0]["path"]
    assert guard.decide(_shell(f'git -C "{gp}" log --oneline', tmp_path),
                        env={}, home=tmp_path, guarded_roots=guarded) is None


def test_shell_git_commit_from_guarded_cwd_denies(tmp_path, guarded):
    # A repo-scoped git write with cwd inside the guarded repo (no path named).
    gp = guarded[0]["path"]
    d = guard.decide(_shell("git commit -m x", gp), env={},
                     home=tmp_path, guarded_roots=guarded)
    assert d and d["permissionDecision"] == "deny"


def test_shell_cd_into_guarded_then_git_commit_denies(tmp_path, guarded):
    # `cd <guarded>; git commit` from an unrelated tool cwd must be caught.
    gp = guarded[0]["path"]
    d = guard.decide(_shell(f'cd "{gp}"; git commit -m x', tmp_path),
                     env={}, home=tmp_path, guarded_roots=guarded)
    assert d and d["permissionDecision"] == "deny"


def test_shell_cd_variable_target_does_not_move_cwd(tmp_path, guarded):
    gp = guarded[0]["path"]
    cmd = f'$a="{gp}"; cd $a; git commit -m x'
    assert guard.decide(_shell(cmd, tmp_path), env={},
                        home=tmp_path, guarded_roots=guarded) is None


def test_shell_redirect_into_guarded_still_denies(tmp_path, guarded):
    gp = guarded[0]["path"]
    d = guard.decide(_shell(f'echo hi > "{gp}\\note.txt"', tmp_path),
                     env={}, home=tmp_path, guarded_roots=guarded)
    assert d and d["permissionDecision"] == "deny"


def test_shell_sudo_prefixed_write_into_guarded_denies(tmp_path, guarded):
    gp = guarded[0]["path"]
    d = guard.decide(_shell(f'sudo rm -rf "{gp}/src"', tmp_path),
                     env={}, home=tmp_path, guarded_roots=guarded)
    assert d and d["permissionDecision"] == "deny"


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


# --- runtime resolution (#1089: no dependence on the fragile PATH binstub) -----

def test_strip_nt_prefix():
    assert guard._strip_nt_prefix("\\??\\C:\\slot") == "C:\\slot"
    assert guard._strip_nt_prefix("\\\\?\\C:\\slot") == "C:\\slot"
    assert guard._strip_nt_prefix("C:\\slot") == "C:\\slot"


def _make_slot(root: Path, ver: str) -> Path:
    """Create versions/<ver> with a runtime python and return that python path."""
    slot = root / "versions" / ver
    py = guard._slot_python(slot)
    py.parent.mkdir(parents=True, exist_ok=True)
    py.write_text("", encoding="utf-8")
    (slot / ".install-complete.json").write_text(
        json.dumps({
            "version": ver,
            "completed_at": "2026-08-27T00:00:00Z",
            "pid": 1,
        }),
        encoding="utf-8",
    )
    return py


def test_runtime_argv_prefers_current_version(tmp_path):
    root = tmp_path / ".agent-worktrees"
    _make_slot(root, "1.5.3-dev100")  # older slot present too
    py = _make_slot(root, "1.5.3-dev200")
    (root / "current-version").write_text("1.5.3-dev200", encoding="utf-8")
    assert guard._runtime_argv(root) == [str(py), "-m", "agent_worktrees"]


def test_runtime_argv_falls_back_to_newest_slot_without_marker(tmp_path):
    root = tmp_path / ".agent-worktrees"
    _make_slot(root, "1.5.3-dev9")
    py = _make_slot(root, "1.5.3-dev10")
    # No current-version marker -> newest slot wins.
    assert guard._runtime_argv(root) == [str(py), "-m", "agent_worktrees"]


def test_runtime_argv_ignores_stale_marker_pointing_at_missing_slot(tmp_path):
    root = tmp_path / ".agent-worktrees"
    py = _make_slot(root, "1.5.3-dev100")
    (root / "current-version").write_text("1.5.3-dev999", encoding="utf-8")  # gone
    # Marker slot has no python -> fall through to the newest present slot.
    assert guard._runtime_argv(root) == [str(py), "-m", "agent_worktrees"]


def test_runtime_argv_none_when_no_runtime(tmp_path, monkeypatch):
    root = tmp_path / ".agent-worktrees"
    root.mkdir()
    monkeypatch.setattr(guard.shutil, "which", lambda _n: None)
    assert guard._runtime_argv(root) is None


def test_run_related_warns_once_when_runtime_unresolved(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(guard, "_runtime_argv", lambda: None)
    guard._RUNTIME_UNRESOLVED_WARNED = False
    assert guard._run_related(["list", "--json"], str(tmp_path)) is None
    first = capsys.readouterr().err
    assert "INACTIVE" in first and "cross-repo-guard" in first
    # A second call does not re-warn (one-time).
    assert guard._run_related(["list", "--json"], str(tmp_path)) is None
    assert capsys.readouterr().err == ""


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


def test_deadline_discovery_does_not_publish_partial_cache(monkeypatch, tmp_path):
    home = tmp_path / "home"
    monkeypatch.setattr(
        guard,
        "_discover_guarded_roots",
        lambda root, deadline: [],
    )
    assert guard.load_guarded_roots(
        str(tmp_path), home, deadline=guard.time.monotonic() + 1
    ) == []
    assert not guard._cache_path(str(tmp_path), home).exists()
