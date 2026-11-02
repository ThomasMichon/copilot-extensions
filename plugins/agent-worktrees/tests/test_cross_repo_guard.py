"""Cross-repository guard decisions retain their existing write/shell policy."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import time

import pytest

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "cross_repo_guard.py"
_spec = importlib.util.spec_from_file_location("cross_repo_guard", _PATH)
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)


@pytest.fixture
def guarded(tmp_path):
    repo = tmp_path / "target"
    repo.mkdir()
    return [{
        "name": "target", "delegate": "agent-bridge", "path": str(repo),
        "locus": {"machines": ["worker"]},
    }]


@pytest.mark.parametrize(("tool", "target", "denied"), [
    ("create", "inside", True), ("edit", "inside", True),
    ("create", "outside", False), ("view", "inside", False),
])
def test_write_and_read_targets(tmp_path, guarded, tool, target, denied):
    root = Path(guarded[0]["path"]) if target == "inside" else tmp_path / "outside"
    payload = {"toolName": tool, "cwd": str(tmp_path), "toolArgs": {"path": str(root / "new")}}
    decision = guard.decide(payload, env={}, home=tmp_path, guarded_roots=guarded)
    assert bool(decision) is denied
    if denied:
        assert decision["permissionDecision"] == "deny"
        assert "agent-bridge" in decision["permissionDecisionReason"]


def test_relative_target(tmp_path, guarded):
    payload = {"toolName": "edit", "cwd": guarded[0]["path"], "toolArgs": {"path": "new"}}
    assert guard.decide(payload, env={}, home=tmp_path, guarded_roots=guarded)["permissionDecision"] == "deny"


@pytest.mark.parametrize(("command", "denied"), [
    ('Set-Content "{root}/new" "hi"', True),
    ('git -C "{root}" commit -m x', True),
    ('cat "{root}/README.md"', False),
    ('$a="{root}"; cd $a; git fetch origin --quiet 2>&1 | Out-Null; git log', False),
    ('gh issue create --body "mentions {root} and Set-Content and git commit as prose"', False),
    ('cat "{root}/x" 2>&1', False),
    ('git -C "{root}" log --oneline', False),
    ('cd "{root}"; git commit -m x', True),
    ('$a="{root}"; cd $a; git commit -m x', False),
    ('echo hi > "{root}/new"', True),
    ('sudo rm -rf "{root}/src"', True),
])
def test_shell_targets_not_path_mentions(tmp_path, guarded, command, denied):
    payload = {
        "toolName": "bash", "cwd": str(tmp_path),
        "toolArgs": {"command": command.format(root=guarded[0]["path"])},
    }
    assert bool(guard.decide(payload, env={}, home=tmp_path, guarded_roots=guarded)) is denied


def test_shell_git_write_from_target_cwd(tmp_path, guarded):
    payload = {"toolName": "bash", "cwd": guarded[0]["path"], "toolArgs": {"command": "git commit -m x"}}
    assert guard.decide(payload, env={}, home=tmp_path, guarded_roots=guarded)["permissionDecision"] == "deny"


@pytest.mark.parametrize(("env", "field", "value"), [
    ({"CROSS_REPO_GUARD": "off"}, None, None),
    ({"CROSS_REPO_GUARD_MODE": "off"}, None, None),
    ({"CROSS_REPO_GUARD_MODE": "warn"}, "additionalContext", None),
    ({"CROSS_REPO_GUARD_MODE": "ask"}, "permissionDecision", "ask"),
    ({}, "permissionDecision", "deny"),
])
def test_live_modes(tmp_path, guarded, env, field, value):
    payload = {"toolName": "edit", "cwd": guarded[0]["path"], "toolArgs": {"path": "new"}}
    decision = guard.decide(payload, env=env, home=tmp_path, guarded_roots=guarded)
    if field is None:
        assert decision is None
    elif value is None:
        assert decision[field] and "permissionDecision" not in decision
    else:
        assert decision[field] == value


@pytest.mark.parametrize("expired", [False, True])
def test_break_glass_is_live(tmp_path, guarded, expired):
    registry = tmp_path / ".agent-worktrees"
    registry.mkdir()
    (registry / "allow-edits.json").write_text(json.dumps({
        "grants": {"target": {"expires_at_ms": (time.time() + (-10 if expired else 600)) * 1000}},
    }))
    payload = {"toolName": "edit", "cwd": guarded[0]["path"], "toolArgs": {"path": "new"}}
    assert bool(guard.decide(payload, env={}, home=tmp_path, guarded_roots=guarded)) is expired


@pytest.mark.parametrize("command", ["Get-Item README.md", "Get-Date", "git log --oneline", ""])
def test_readonly_shell_skips_map(monkeypatch, tmp_path, command):
    monkeypatch.setattr(guard, "load_guarded_roots", lambda *a, **k: pytest.fail("read acquired facts"))
    assert guard.decide({
        "toolName": "powershell", "cwd": str(tmp_path), "toolArgs": {"command": command},
    }, env={}, home=tmp_path) is None
