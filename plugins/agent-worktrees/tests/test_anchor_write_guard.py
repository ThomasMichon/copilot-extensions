"""Tests for the anchor_write_guard preToolUse hook decision logic.

The guard blocks writes into the ANCHOR (main checkout, ``.git`` is a directory)
of a ``class: worktree`` repo, while always allowing writes into a linked
worktree (``.git`` is a file) and into singleton/unregistered checkouts.
"""

from __future__ import annotations

import importlib.util
import json
import time
from pathlib import Path

import pytest

# The guard ships as a standalone script under scripts/ (deployed to
# ~/.agent-worktrees/bin/), not as a package module -- load it by path.
_GUARD_PATH = Path(__file__).resolve().parents[1] / "scripts" / "anchor_write_guard.py"
_spec = importlib.util.spec_from_file_location("anchor_write_guard", _GUARD_PATH)
assert _spec and _spec.loader, f"cannot load guard script at {_GUARD_PATH}"
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)


def _main_checkout(base: Path, name: str) -> Path:
    """A main checkout: ``.git`` is a DIRECTORY (the anchor)."""
    root = base / name
    (root / ".git").mkdir(parents=True)
    return root


def _linked_worktree(path: Path) -> Path:
    """A linked worktree: ``.git`` is a FILE (a gitdir pointer)."""
    path.mkdir(parents=True)
    (path / ".git").write_text("gitdir: /somewhere/.git/worktrees/x\n",
                               encoding="utf-8")
    return path


@pytest.fixture
def anchor(tmp_path: Path) -> list[dict]:
    """One worktree-class repo whose anchor is a real main checkout on disk."""
    root = _main_checkout(tmp_path, "myrepo")
    return [{"name": "myrepo", "path": str(root)}]


def _write(tool, path, cwd):
    return {"toolName": tool, "cwd": str(cwd), "toolArgs": {"path": str(path)}}


def _shell(cmd, cwd):
    return {"toolName": "bash", "cwd": str(cwd), "toolArgs": {"command": cmd}}


# --- write-tool blocking ------------------------------------------------------

def test_write_into_anchor_denies(tmp_path, anchor):
    target = Path(anchor[0]["path"]) / "src" / "x.py"
    d = guard.decide(_write("create", target, tmp_path), env={},
                     home=tmp_path, anchors=anchor)
    assert d and d["permissionDecision"] == "deny"
    assert "myrepo" in d["permissionDecisionReason"]
    assert "worktree" in d["permissionDecisionReason"].lower()


def test_write_into_linked_worktree_allows(tmp_path, anchor):
    # A sibling worktree sharing the anchor's name PREFIX (myrepo.worktrees/...);
    # its .git is a file, so it must always pass.
    wt = _linked_worktree(tmp_path / "myrepo.worktrees" / "wt1")
    target = wt / "src" / "x.py"
    assert guard.decide(_write("edit", target, wt), env={}, home=tmp_path,
                        anchors=anchor) is None


def test_write_into_nested_linked_worktree_allows(tmp_path, anchor):
    # Even a worktree nested INSIDE the anchor path is fine (.git file wins).
    wt = _linked_worktree(Path(anchor[0]["path"]) / ".worktrees" / "wt1")
    target = wt / "x.py"
    assert guard.decide(_write("create", target, wt), env={}, home=tmp_path,
                        anchors=anchor) is None


def test_write_into_unregistered_main_checkout_allows(tmp_path, anchor):
    # A different main checkout not registered as worktree-class (e.g. a
    # singleton like SPO.Core) must not be blocked.
    other = _main_checkout(tmp_path, "singleton-repo")
    target = other / "x.py"
    assert guard.decide(_write("create", target, tmp_path), env={},
                        home=tmp_path, anchors=anchor) is None


def test_write_outside_any_repo_allows(tmp_path, anchor):
    target = tmp_path / "loose" / "x.py"
    assert guard.decide(_write("create", target, tmp_path), env={},
                        home=tmp_path, anchors=anchor) is None


def test_read_tool_into_anchor_allows(tmp_path, anchor):
    target = Path(anchor[0]["path"]) / "README.md"
    p = {"toolName": "view", "cwd": str(tmp_path), "toolArgs": {"path": str(target)}}
    assert guard.decide(p, env={}, home=tmp_path, anchors=anchor) is None


def test_relative_write_path_resolves_against_cwd(tmp_path, anchor):
    cwd = Path(anchor[0]["path"]) / "src"
    p = {"toolName": "edit", "cwd": str(cwd), "toolArgs": {"path": "x.py"}}
    d = guard.decide(p, env={}, home=tmp_path, anchors=anchor)
    assert d and d["permissionDecision"] == "deny"


# --- shell blocking -----------------------------------------------------------

def test_shell_write_into_anchor_denies(tmp_path, anchor):
    gp = anchor[0]["path"]
    d = guard.decide(_shell(f'Set-Content "{gp}\\notes.md" "hi"', tmp_path),
                     env={}, home=tmp_path, anchors=anchor)
    assert d and d["permissionDecision"] == "deny"


def test_shell_git_commit_into_anchor_denies(tmp_path, anchor):
    gp = anchor[0]["path"]
    d = guard.decide(_shell(f'git -C "{gp}" commit -m x', tmp_path),
                     env={}, home=tmp_path, anchors=anchor)
    assert d and d["permissionDecision"] == "deny"


def test_shell_read_into_anchor_allows(tmp_path, anchor):
    gp = anchor[0]["path"]
    assert guard.decide(_shell(f'cat "{gp}/README.md"', tmp_path),
                        env={}, home=tmp_path, anchors=anchor) is None


# -- cwd-scoped git mutation (no path named) -- the incident-class case --------

def test_shell_git_commit_from_anchor_cwd_denies(tmp_path, anchor):
    # ``git commit`` with cwd INSIDE the anchor mutates it without naming a path.
    gp = anchor[0]["path"]
    d = guard.decide(_shell("git commit -m x", gp), env={}, home=tmp_path,
                     anchors=anchor)
    assert d and d["permissionDecision"] == "deny"


def test_shell_git_add_from_anchor_subdir_denies(tmp_path, anchor):
    cwd = Path(anchor[0]["path"]) / "src"
    d = guard.decide(_shell("git add -A", cwd), env={}, home=tmp_path,
                     anchors=anchor)
    assert d and d["permissionDecision"] == "deny"


def test_shell_git_commit_from_worktree_cwd_allows(tmp_path, anchor):
    # Same command from a LINKED worktree cwd (.git file) is fine.
    wt = _linked_worktree(tmp_path / "myrepo.worktrees" / "wt1")
    assert guard.decide(_shell("git commit -m x", wt), env={}, home=tmp_path,
                        anchors=anchor) is None


def test_shell_git_dashC_worktree_from_anchor_cwd_allows(tmp_path, anchor):
    # ``git -C <worktree>`` run FROM the anchor cwd targets the worktree, not the
    # anchor -- the ``-C`` redirect is left to the (path-literal) scan.
    wt = _linked_worktree(tmp_path / "myrepo.worktrees" / "wt2")
    d = guard.decide(_shell(f'git -C "{wt}" commit -m x', anchor[0]["path"]),
                     env={}, home=tmp_path, anchors=anchor)
    assert d is None


def test_shell_git_read_from_anchor_cwd_allows(tmp_path, anchor):
    # A read-only git command from the anchor cwd is fine (no write verb).
    assert guard.decide(_shell("git status", anchor[0]["path"]),
                        env={}, home=tmp_path, anchors=anchor) is None


def test_shell_git_commit_from_unrelated_cwd_allows(tmp_path, anchor):
    # cwd is not a worktree-class anchor -> not our business.
    other = _main_checkout(tmp_path, "singleton-repo")
    assert guard.decide(_shell("git commit -m x", other),
                        env={}, home=tmp_path, anchors=anchor) is None


def test_shell_write_into_sibling_worktree_allows(tmp_path, anchor):
    # A write into ``<anchor>.worktrees\...`` shares the anchor's string prefix
    # but NOT the anchor+separator boundary, so it must not be flagged.
    gp = anchor[0]["path"]
    sib = f"{gp}.worktrees\\wt1\\x.py"
    assert guard.decide(_shell(f'Set-Content "{sib}" "hi"', tmp_path),
                        env={}, home=tmp_path, anchors=anchor) is None


# -- false-positive regressions (dotfiles#1144) --------------------------------
# The anchor path merely *appearing* in a command (an assignment, a cd, a quoted
# data payload) alongside a write-ish token must NOT be denied -- only a real
# write *target* is.

def test_shell_readonly_git_with_fd_redirect_and_anchor_in_var_allows(
    tmp_path, anchor
):
    # Repro 1: a read-only `git fetch`/`git log` where the anchor path is only in
    # a `$var=`/`cd`, and the sole "write" token is the `>` of a `2>&1` fd dup.
    gp = anchor[0]["path"]
    cmd = (f'$a="{gp}"; cd $a; git fetch origin --quiet 2>&1 | Out-Null; '
           f'git --no-pager log origin/main --oneline -5')
    assert guard.decide(_shell(cmd, tmp_path), env={}, home=tmp_path,
                        anchors=anchor) is None


def test_shell_fd_dup_redirect_is_not_a_write(tmp_path, anchor):
    # `2>&1` / `1>&2` are fd dups, not file writes -- even with the anchor named
    # in an inert position.
    gp = anchor[0]["path"]
    assert guard.decide(_shell(f'cat "{gp}\\README.md" 2>&1', tmp_path),
                        env={}, home=tmp_path, anchors=anchor) is None


def test_shell_anchor_in_quoted_body_payload_allows(tmp_path, anchor):
    # Repro 2: `gh issue create` whose --body PROSE mentions the anchor path and
    # write verbs (Set-Content, git commit) as data, not commands.
    gp = anchor[0]["path"]
    body = (f'A read-only `git fetch ... 2>&1` in `{gp}` was denied. '
            f'A genuine `Set-Content "{gp}\\x"` / `git commit` must still deny.')
    cmd = f'gh issue create --repo o/r --title "bug" --body "{body}"'
    assert guard.decide(_shell(cmd, tmp_path), env={}, home=tmp_path,
                        anchors=anchor) is None


def test_shell_cd_into_anchor_then_read_allows(tmp_path, anchor):
    gp = anchor[0]["path"]
    assert guard.decide(_shell(f'cd "{gp}"; git status', tmp_path),
                        env={}, home=tmp_path, anchors=anchor) is None


def test_shell_stderr_redirect_to_anchor_file_still_denies(tmp_path, anchor):
    # A real fd-to-FILE redirect INTO the anchor (`2> <anchor>\err.log`) is a
    # write and must still be denied (only fd-dup `>&` is exempt).
    gp = anchor[0]["path"]
    d = guard.decide(_shell(f'some-tool 2> "{gp}\\err.log"', tmp_path),
                     env={}, home=tmp_path, anchors=anchor)
    assert d and d["permissionDecision"] == "deny"


def test_shell_redirect_into_anchor_still_denies(tmp_path, anchor):
    gp = anchor[0]["path"]
    d = guard.decide(_shell(f'echo hi > "{gp}\\note.txt"', tmp_path),
                     env={}, home=tmp_path, anchors=anchor)
    assert d and d["permissionDecision"] == "deny"


def test_shell_write_verb_not_at_command_position_allows(tmp_path, anchor):
    # A write cmdlet name appearing mid-segment as an argument value (not at
    # command position) with the anchor in a quoted arg must not trigger.
    gp = anchor[0]["path"]
    cmd = f'echo "run Set-Content on {gp} later"'
    assert guard.decide(_shell(cmd, tmp_path), env={}, home=tmp_path,
                        anchors=anchor) is None


# --- modes + kill switches ----------------------------------------------------

def test_kill_switch_env_allows(tmp_path, anchor):
    target = Path(anchor[0]["path"]) / "x.py"
    p = _write("create", target, tmp_path)
    assert guard.decide(p, env={"ANCHOR_WRITE_GUARD": "off"}, home=tmp_path,
                        anchors=anchor) is None
    assert guard.decide(p, env={"CROSS_REPO_GUARD": "off"}, home=tmp_path,
                        anchors=anchor) is None
    assert guard.decide(p, env={"ANCHOR_WRITE_GUARD_MODE": "off"}, home=tmp_path,
                        anchors=anchor) is None


def test_mode_warn_returns_additional_context(tmp_path, anchor):
    target = Path(anchor[0]["path"]) / "x.py"
    d = guard.decide(_write("create", target, tmp_path),
                     env={"ANCHOR_WRITE_GUARD_MODE": "warn"}, home=tmp_path,
                     anchors=anchor)
    assert d and "additionalContext" in d and "permissionDecision" not in d


def test_mode_ask_returns_ask(tmp_path, anchor):
    target = Path(anchor[0]["path"]) / "x.py"
    d = guard.decide(_write("create", target, tmp_path),
                     env={"ANCHOR_WRITE_GUARD_MODE": "ask"}, home=tmp_path,
                     anchors=anchor)
    assert d and d["permissionDecision"] == "ask"


# --- break-glass --------------------------------------------------------------

def test_active_break_glass_allows(tmp_path, anchor):
    home = tmp_path / "home"
    (home / ".agent-worktrees").mkdir(parents=True)
    (home / ".agent-worktrees" / "allow-edits.json").write_text(json.dumps({
        "grants": {"myrepo": {"expires_at_ms": (time.time() + 600) * 1000}}
    }), encoding="utf-8")
    target = Path(anchor[0]["path"]) / "x.py"
    assert guard.decide(_write("create", target, tmp_path), env={},
                        home=home, anchors=anchor) is None


def test_expired_break_glass_still_denies(tmp_path, anchor):
    home = tmp_path / "home"
    (home / ".agent-worktrees").mkdir(parents=True)
    (home / ".agent-worktrees" / "allow-edits.json").write_text(json.dumps({
        "grants": {"myrepo": {"expires_at_ms": (time.time() - 60) * 1000}}
    }), encoding="utf-8")
    target = Path(anchor[0]["path"]) / "x.py"
    d = guard.decide(_write("create", target, tmp_path), env={},
                     home=home, anchors=anchor)
    assert d and d["permissionDecision"] == "deny"


# --- empty set / fail-open ----------------------------------------------------

def test_no_anchors_allows(tmp_path):
    target = tmp_path / "anything" / "x.py"
    assert guard.decide(_write("create", target, tmp_path), env={},
                        home=tmp_path, anchors=[]) is None


# --- repos.yaml discovery (stdlib mini-parser) --------------------------------

def test_load_worktree_anchors_filters_by_class(tmp_path):
    home = tmp_path / "home"
    (home / ".agent-worktrees").mkdir(parents=True)
    (home / ".agent-worktrees" / "repos.yaml").write_text(
        "schema_version: 1\n"
        "srcroot:\n"
        "  windows: \"C:\\\\Data\\\\Src\"\n"
        "repos:\n"
        "  SPO.Core:\n"
        "    class: singleton\n"
        "    windows: \"C:\\\\Core\\\\SPO\"\n"
        "  copilot-extensions:\n"
        "    class: worktree\n"
        "    windows: \"C:\\\\Data\\\\Src\\\\copilot-extensions\"\n"
        "    linux: \"/home/u/copilot-extensions\"\n"
        "  other-wt:\n"
        "    class: worktree\n"
        "    windows: \"C:\\\\Data\\\\Src\\\\other\"\n",
        encoding="utf-8")
    anchors = guard.load_worktree_anchors(home)
    names = {a["name"] for a in anchors}
    assert names == {"copilot-extensions", "other-wt"}  # singleton excluded
    paths = {a["path"] for a in anchors}
    # Double-backslash unescaped to single; both platform paths surfaced.
    assert "C:\\Data\\Src\\copilot-extensions" in paths
    assert "/home/u/copilot-extensions" in paths


def test_load_worktree_anchors_missing_file_is_empty(tmp_path):
    assert guard.load_worktree_anchors(tmp_path / "nope") == []
