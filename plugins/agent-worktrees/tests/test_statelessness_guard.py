"""Tests for the statelessness_guard preToolUse hook decision logic."""

from __future__ import annotations

import importlib.util
import json
import time
from pathlib import Path

import pytest

# The guard ships as a standalone script under scripts/ (deployed to
# ~/.agent-worktrees/bin/), not as a package module -- load it by path.
_GUARD_PATH = Path(__file__).resolve().parents[1] / "scripts" / "statelessness_guard.py"
_spec = importlib.util.spec_from_file_location("statelessness_guard", _GUARD_PATH)
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)


@pytest.fixture
def harness(tmp_path: Path) -> Path:
    """A stateless-harness checkout: .git + .agent-worktrees/config.yaml."""
    root = tmp_path / "citadel-harness"
    (root / ".git").mkdir(parents=True)
    (root / ".agent-worktrees").mkdir()
    (root / ".agent-worktrees" / "config.yaml").write_text(
        "default_branch: main\nstateless: true\n", encoding="utf-8"
    )
    return root


@pytest.fixture
def plain_repo(tmp_path: Path) -> Path:
    root = tmp_path / "normal-repo"
    (root / ".git").mkdir(parents=True)
    (root / ".agent-worktrees").mkdir()
    (root / ".agent-worktrees" / "config.yaml").write_text(
        "default_branch: main\n", encoding="utf-8"
    )
    return root


def _write(tool, path, cwd):
    return {"toolName": tool, "cwd": str(cwd), "toolArgs": {"path": str(path)}}


# --- allow paths --------------------------------------------------------------

def test_read_tool_allows(harness):
    p = {"toolName": "view", "cwd": str(harness), "toolArgs": {"path": "efforts/x.md"}}
    assert guard.decide(p, env={}, home=harness) is None


def test_write_into_harness_docs_allows(harness):
    # docs/ and visions/ are legit harness content, not personal state.
    for ok in ("docs/x.md", "visions/harbor.md", "AGENTS.md", ".github/x.json"):
        p = _write("create", harness / ok, harness)
        assert guard.decide(p, env={}, home=harness) is None, ok


def test_write_personal_state_in_plain_repo_allows(plain_repo):
    # Not a stateless harness -> guard is inert (backward compatible).
    p = _write("create", plain_repo / "efforts/active/x/README.md", plain_repo)
    assert guard.decide(p, env={}, home=plain_repo) is None


# --- deny paths ---------------------------------------------------------------

@pytest.mark.parametrize("rel", [
    "efforts/active/x/README.md",
    "logs/2026/x.md",
    "weekly-updates/2026/x.md",
    "icm/themes.md",
    "ownership.yml",
    "dev-assignments.yml",
])
def test_write_personal_state_into_harness_denies(harness, rel):
    p = _write("create", harness / rel, harness)
    d = guard.decide(p, env={}, home=harness)
    assert d is not None and d["permissionDecision"] == "deny"
    assert "state-root" in d["permissionDecisionReason"]


def test_relative_path_resolved_against_cwd(harness):
    # Agent cwd is the harness; a relative efforts/ path resolves into it.
    p = {"toolName": "create", "cwd": str(harness),
         "toolArgs": {"path": "efforts/active/x/README.md"}}
    d = guard.decide(p, env={}, home=harness)
    assert d and d["permissionDecision"] == "deny"


# --- escape hatches -----------------------------------------------------------

def test_env_off_allows(harness):
    p = _write("create", harness / "efforts/x.md", harness)
    assert guard.decide(p, env={"AGENT_WORKTREES_STATELESS_GUARD": "off"}, home=harness) is None
    assert guard.decide(p, env={"CROSS_REPO_GUARD": "off"}, home=harness) is None


def test_break_glass_allows(harness, tmp_path):
    home = tmp_path / "home"
    (home / ".agent-worktrees").mkdir(parents=True)
    (home / ".agent-worktrees" / "allow-edits.json").write_text(json.dumps({
        "grants": {"citadel-harness": {"expires_at_ms": (time.time() + 600) * 1000}}
    }), encoding="utf-8")
    p = _write("create", harness / "efforts/x.md", harness)
    # repo name comes from WORKTREE_PROJECT
    env = {"WORKTREE_PROJECT": "citadel-harness"}
    assert guard.decide(p, env=env, home=home) is None


def test_expired_break_glass_denies(harness, tmp_path):
    home = tmp_path / "home"
    (home / ".agent-worktrees").mkdir(parents=True)
    (home / ".agent-worktrees" / "allow-edits.json").write_text(json.dumps({
        "grants": {"citadel-harness": {"expires_at_ms": (time.time() - 10) * 1000}}
    }), encoding="utf-8")
    p = _write("create", harness / "efforts/x.md", harness)
    d = guard.decide(p, env={"WORKTREE_PROJECT": "citadel-harness"}, home=home)
    assert d and d["permissionDecision"] == "deny"


# --- shell ---------------------------------------------------------------------

def test_shell_write_into_personal_state_denies(harness):
    target = harness / "efforts" / "active" / "x" / "README.md"
    cmd = f'Set-Content "{target}" -Value "leak"'
    p = {"toolName": "powershell", "cwd": str(harness), "toolArgs": {"command": cmd}}
    d = guard.decide(p, env={}, home=harness)
    assert d and d["permissionDecision"] == "deny"


def test_shell_read_allows(harness):
    target = harness / "efforts" / "x.md"
    cmd = f'Get-Content "{target}"'
    p = {"toolName": "powershell", "cwd": str(harness), "toolArgs": {"command": cmd}}
    assert guard.decide(p, env={}, home=harness) is None


def test_shell_write_outside_harness_allows(harness, tmp_path):
    # Writing efforts/ into the KNOWLEDGE repo (a different root) is fine.
    other = tmp_path / "knowledge"
    (other / "efforts").mkdir(parents=True)
    cmd = f'Set-Content "{other / "efforts" / "x.md"}" -Value "ok"'
    p = {"toolName": "powershell", "cwd": str(harness), "toolArgs": {"command": cmd}}
    assert guard.decide(p, env={}, home=harness) is None


def test_malformed_payload_allows():
    # Fail-open on junk.
    assert guard.decide({}, env={}, home=Path(".")) is None
    assert guard.decide({"toolName": "create"}, env={}, home=Path(".")) is None
