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


@pytest.mark.parametrize("directive", ["Add File", "Update File", "Delete File", "Move to"])
def test_native_patch_targets_are_guided(tmp_path, guarded, directive):
    patch = f"*** Begin Patch\n*** {directive}: {guarded[0]['path']}/new.py\n*** End Patch\n"
    decision = guard.decide({
        "toolName": "apply_patch", "cwd": str(tmp_path), "toolArgs": patch,
    }, env={}, home=tmp_path, guarded_roots=guarded)
    assert decision["permissionDecision"] == "deny"
    assert "allow-edits target" in decision["permissionDecisionReason"]


def test_patch_content_is_not_a_write_target(tmp_path, guarded):
    patch = (
        f"*** Begin Patch\n*** Add File: {tmp_path}/outside.py\n"
        f"+*** Update File: {guarded[0]['path']}/new.py\n*** End Patch\n"
    )
    assert guard.decide({
        "toolName": "apply_patch", "cwd": str(tmp_path), "toolArgs": patch,
    }, env={}, home=tmp_path, guarded_roots=guarded) is None


def test_grant_for_first_patch_target_does_not_allow_second_repo(tmp_path, guarded):
    other = tmp_path / "other"
    other.mkdir()
    roots = [*guarded, {**guarded[0], "name": "other", "path": str(other)}]
    registry = tmp_path / ".agent-worktrees"
    registry.mkdir()
    (registry / "allow-edits.json").write_text(json.dumps({
        "grants": {"target": {"expires_at_ms": (time.time() + 600) * 1000}},
    }))
    patch = (
        f"*** Begin Patch\n*** Update File: {guarded[0]['path']}/new.py\n"
        f"*** Move to: {other}/new.py\n*** End Patch\n"
    )
    decision = guard.decide({
        "toolName": "apply_patch", "cwd": str(tmp_path), "toolArgs": patch,
    }, env={}, home=tmp_path, guarded_roots=roots)
    assert "'other'" in decision["permissionDecisionReason"]


@pytest.mark.parametrize("tool", ["apply_patch", "powershell"])
@pytest.mark.parametrize("weak_mode", ["warn", "ask"])
@pytest.mark.parametrize("reverse", [False, True])
def test_strongest_target_policy_wins_across_target_order(tmp_path, guarded, tool, weak_mode, reverse):
    other = tmp_path / "read-only"
    other.mkdir()
    roots = [
        {**guarded[0], "delegate": "none", "posture": "worktree"},
        {**guarded[0], "name": "read-only", "path": str(other),
         "delegate": "none", "posture": "reference"},
    ]
    paths = [Path(roots[0]["path"]) / "one.py", other / "two.py"]
    if reverse:
        paths.reverse()
    args = (
        "*** Begin Patch\n" + "".join(f"*** Update File: {path}\n" for path in paths)
        + "*** End Patch\n"
        if tool == "apply_patch" else
        {"command": "; ".join(f'Set-Content "{path}" "value"' for path in paths)}
    )
    decision = guard.decide(
        {"toolName": tool, "cwd": str(tmp_path), "toolArgs": args},
        env={"ANCHOR_WRITE_GUARD_MODE": weak_mode}, home=tmp_path, guarded_roots=roots,
    )
    assert decision["permissionDecision"] == "deny"
    assert "'read-only'" in decision["permissionDecisionReason"]


@pytest.mark.parametrize(("posture", "phrase"), [
    ("reference", "read-only"), ("worktree", "linked worktree"),
    ("elevated", "elevated execution venue"),
])
def test_prepared_posture_guidance(tmp_path, guarded, posture, phrase):
    roots = [{**guarded[0], "delegate": "none", "posture": posture}]
    decision = guard.decide({
        "toolName": "edit", "cwd": str(tmp_path),
        "toolArgs": {"path": str(Path(roots[0]["path"]) / "new.py")},
    }, env={}, home=tmp_path, guarded_roots=roots)
    assert decision["permissionDecision"] == "deny"
    assert phrase in decision["permissionDecisionReason"]
    assert "does not grant access or elevation" in decision["permissionDecisionReason"]


@pytest.mark.parametrize(("env", "expected"), [
    ({"ANCHOR_WRITE_GUARD": "off"}, None),
    ({"ANCHOR_WRITE_GUARD_MODE": "off"}, None),
    ({"ANCHOR_WRITE_GUARD_MODE": "ask"}, "ask"),
])
def test_prepared_worktree_posture_preserves_anchor_controls(tmp_path, guarded, env, expected):
    roots = [{**guarded[0], "delegate": "none", "posture": "worktree"}]
    decision = guard.decide({
        "toolName": "edit", "cwd": str(tmp_path),
        "toolArgs": {"path": str(Path(roots[0]["path"]) / "new.py")},
    }, env=env, home=tmp_path, guarded_roots=roots)
    assert (decision["permissionDecision"] if decision else None) == expected


@pytest.mark.parametrize("command", ['echo value > "{target}"', 'Set-Content "{target}" value'])
@pytest.mark.parametrize("delegated", [False, True])
@pytest.mark.parametrize("linked", [False, True])
def test_nested_linked_shell_destination_preserves_posture_and_delegation(
    tmp_path, command, delegated, linked,
):
    root = tmp_path / "anchor space"
    (root / ".git").mkdir(parents=True)
    target = root / ".worktrees" / "feature space"
    target.mkdir(parents=True)
    if linked:
        gitdir = root / ".git" / "worktrees" / "feature"
        gitdir.mkdir(parents=True)
        (gitdir / "commondir").write_text("../..\n")
        (target / ".git").write_text(f"gitdir: {gitdir}\n")
    row = {
        "name": "target", "path": str(root),
        "delegate": "agent-bridge" if delegated else "none",
        "identity": guard._guard_map.repository_identity(root),
    }
    if not delegated:
        row["posture"] = "worktree"
    result = guard.decide({
        "toolName": "powershell", "cwd": str(tmp_path),
        "toolArgs": {"command": command.format(target=target / "new.py")},
    }, env={}, home=tmp_path, guarded_roots=[row])
    assert bool(result) is (delegated or not linked)
