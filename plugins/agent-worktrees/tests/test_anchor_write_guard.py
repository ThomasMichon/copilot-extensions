"""Tests for the anchor_write_guard preToolUse hook decision logic.

The guard blocks writes into the ANCHOR (main checkout, ``.git`` is a directory)
of a ``class: worktree`` repo, while always allowing writes into a linked
worktree (``.git`` is a file) and into singleton/unregistered checkouts.
"""

from __future__ import annotations

import importlib.util
import json
import os
import time
from pathlib import Path

import sys

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


def test_explicit_invalid_context_never_reads_legacy_registry(
    tmp_path, monkeypatch
):
    invalid = tmp_path / "invalid" / "install.json"
    invalid.parent.mkdir()
    invalid.write_text("{", encoding="utf-8")
    monkeypatch.setattr(
        guard,
        "load_worktree_anchors",
        lambda _root: pytest.fail("legacy registry must not be read"),
    )
    env = {
        "COPILOT_EXTENSIONS_CONTEXT": str(invalid),
        "AGENT_WORKTREES_PAYLOAD_ROOT": str(
            Path(__file__).resolve().parents[1]
        ),
    }

    with pytest.raises(ValueError):
        guard.decide(
            _write("create", tmp_path / "x.py", tmp_path),
            env=env,
            home=tmp_path,
        )


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


def test_shell_git_pull_ff_only_with_dashC_into_anchor_allows(tmp_path, anchor):
    """``git pull --ff-only`` is exempt: git structurally refuses instead of
    ever creating a merge commit or applying ``pull.rebase``, so it can
    never introduce agent-authored content."""
    gp = anchor[0]["path"]
    assert guard.decide(
        _shell(f'git -C "{gp}" pull --ff-only origin main', tmp_path),
        env={}, home=tmp_path, anchors=anchor) is None


def test_shell_git_pull_ff_only_from_anchor_cwd_allows(tmp_path, anchor):
    gp = anchor[0]["path"]
    assert guard.decide(_shell("git pull origin main --ff-only", gp),
                        env={}, home=tmp_path, anchors=anchor) is None


def test_shell_git_fetch_from_anchor_cwd_allows(tmp_path, anchor):
    gp = anchor[0]["path"]
    assert guard.decide(_shell("git fetch origin", gp),
                        env={}, home=tmp_path, anchors=anchor) is None


def test_shell_git_merge_base_from_anchor_cwd_allows(tmp_path, anchor):
    gp = anchor[0]["path"]
    assert guard.decide(_shell("git merge-base main HEAD", gp),
                        env={}, home=tmp_path, anchors=anchor) is None


@pytest.mark.parametrize(
    "command",
    [
        "git merge-file ours base theirs",
        "git checkout-index --all",
    ],
)
def test_shell_mutating_dashed_git_command_from_anchor_denies(
    tmp_path, anchor, command,
):
    gp = anchor[0]["path"]
    d = guard.decide(_shell(command, gp), env={},
                     home=tmp_path, anchors=anchor)
    assert d and d["permissionDecision"] == "deny"


def test_shell_git_exe_commit_from_anchor_cwd_denies(tmp_path, anchor):
    gp = anchor[0]["path"]
    d = guard.decide(_shell("git.exe commit -m example", gp), env={},
                     home=tmp_path, anchors=anchor)
    assert d and d["permissionDecision"] == "deny"


def test_shell_git_exe_merge_base_from_anchor_cwd_allows(tmp_path, anchor):
    gp = anchor[0]["path"]
    assert guard.decide(_shell("git.exe merge-base main HEAD", gp),
                        env={}, home=tmp_path, anchors=anchor) is None


@pytest.mark.parametrize("executable", ["git-commit", "git-commit.exe"])
def test_shell_direct_git_commit_from_anchor_cwd_denies(
    tmp_path, anchor, executable,
):
    gp = anchor[0]["path"]
    d = guard.decide(_shell(f"{executable} -m example", gp), env={},
                     home=tmp_path, anchors=anchor)
    assert d and d["permissionDecision"] == "deny"


def test_shell_direct_git_merge_base_from_anchor_cwd_allows(tmp_path, anchor):
    gp = anchor[0]["path"]
    assert guard.decide(_shell("git-merge-base main HEAD", gp),
                        env={}, home=tmp_path, anchors=anchor) is None


@pytest.mark.parametrize("separator", ["\n", "; ", " && "])
def test_shell_git_readonly_batch_from_anchor_cwd_allows(
    tmp_path, anchor, separator,
):
    gp = anchor[0]["path"]
    command = separator.join([
        "git pull --ff-only origin main",
        "git fetch origin refs/pull/42/head",
        "git merge-base main HEAD",
    ])
    assert guard.decide(_shell(command, gp), env={}, home=tmp_path,
                        anchors=anchor) is None


def test_shell_git_fetch_and_powershell_merge_base_assignment_allows(
    tmp_path, anchor,
):
    gp = anchor[0]["path"]
    command = (
        "git fetch origin refs/pull/42/head\n"
        "$base = (git merge-base main HEAD).Trim()"
    )
    assert guard.decide(_shell(command, gp), env={}, home=tmp_path,
                        anchors=anchor) is None


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_bash_git_line_continuation_from_anchor_denies(
    tmp_path, anchor, newline,
):
    gp = anchor[0]["path"]
    command = f"git \\{newline}commit --allow-empty -m example"
    d = guard.decide(_shell(command, gp), env={},
                     home=tmp_path, anchors=anchor)
    assert d and d["permissionDecision"] == "deny"


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_powershell_git_line_continuation_from_anchor_denies(
    tmp_path, anchor, newline,
):
    gp = anchor[0]["path"]
    payload = {
        "toolName": "powershell",
        "cwd": str(gp),
        "toolArgs": {
            "command": f"git `{newline}commit --allow-empty -m example"
        },
    }
    d = guard.decide(payload, env={}, home=tmp_path, anchors=anchor)
    assert d and d["permissionDecision"] == "deny"


@pytest.mark.parametrize("separator", ["\n", "; ", " && "])
def test_shell_git_readonly_batch_with_dashC_allows(
    tmp_path, anchor, separator,
):
    gp = anchor[0]["path"]
    command = separator.join([
        f'git --no-pager -C "{gp}" pull --ff-only origin main',
        f'git --no-pager -C "{gp}" fetch origin refs/pull/42/head',
        f'git --no-pager -C "{gp}" merge-base main HEAD',
    ])
    assert guard.decide(_shell(command, tmp_path), env={}, home=tmp_path,
                        anchors=anchor) is None


def test_shell_git_write_with_global_options_still_denies(tmp_path, anchor):
    gp = anchor[0]["path"]
    command = (
        f'git --no-pager -c color.ui=false -C "{gp}" commit -m example'
    )
    d = guard.decide(_shell(command, tmp_path), env={}, home=tmp_path,
                     anchors=anchor)
    assert d and d["permissionDecision"] == "deny"


@pytest.mark.parametrize(
    "config",
    [
        "alias.save=commit",
        "alias.save=status",
        "alias.save=other",
        "alias.save=!echo saved",
    ],
)
def test_shell_git_command_local_alias_from_anchor_denies(
    tmp_path, anchor, config,
):
    gp = anchor[0]["path"]
    d = guard.decide(
        _shell(f'git -C "{gp}" -c "{config}" save -m example', tmp_path),
        env={}, home=tmp_path, anchors=anchor,
    )
    assert d and d["permissionDecision"] == "deny"


@pytest.mark.parametrize(
    "config",
    [
        "user.name=Example; User",
        "user.name=Example && User",
    ],
)
def test_shell_git_quoted_config_separator_from_anchor_denies(
    tmp_path, anchor, config,
):
    gp = anchor[0]["path"]
    command = (
        f'git -C "{gp}" -c "{config}" '
        "commit --allow-empty -m example"
    )
    d = guard.decide(_shell(command, tmp_path), env={},
                     home=tmp_path, anchors=anchor)
    assert d and d["permissionDecision"] == "deny"


def test_shell_git_alias_with_quoted_separator_from_anchor_denies(
    tmp_path, anchor,
):
    gp = anchor[0]["path"]
    command = (
        f'git -C "{gp}" -c "alias.save=!echo before; git commit" save'
    )
    d = guard.decide(_shell(command, tmp_path), env={},
                     home=tmp_path, anchors=anchor)
    assert d and d["permissionDecision"] == "deny"


@pytest.mark.parametrize(
    "command",
    [
        'echo "$(echo ok; git commit --allow-empty -m example)"',
        'echo "$(echo "$(git commit --allow-empty -m example)")"',
        'echo "$(printf ")"; git commit --allow-empty -m example)"',
        "echo `git commit --allow-empty -m example`",
    ],
)
def test_bash_git_command_substitution_from_anchor_denies(
    tmp_path, anchor, command,
):
    gp = anchor[0]["path"]
    d = guard.decide(_shell(command, gp), env={},
                     home=tmp_path, anchors=anchor)
    assert d and d["permissionDecision"] == "deny"


@pytest.mark.parametrize(
    "command",
    [
        "echo $(git commit --allow-empty -m example; echo ok)",
        "echo `git commit --allow-empty -m example; echo ok`",
    ],
)
def test_bash_unquoted_substitution_separator_from_anchor_denies(
    tmp_path, anchor, command,
):
    gp = anchor[0]["path"]
    d = guard.decide(_shell(command, gp), env={},
                     home=tmp_path, anchors=anchor)
    assert d and d["permissionDecision"] == "deny"


@pytest.mark.parametrize(
    "command",
    [
        "cat <(git commit --allow-empty -m example; echo ok)",
        "cat >(git commit --allow-empty -m example)",
    ],
)
def test_bash_process_substitution_from_anchor_denies(
    tmp_path, anchor, command,
):
    gp = anchor[0]["path"]
    d = guard.decide(_shell(command, gp), env={},
                     home=tmp_path, anchors=anchor)
    assert d and d["permissionDecision"] == "deny"


def test_bash_single_quoted_git_substitution_text_allows(tmp_path, anchor):
    gp = anchor[0]["path"]
    assert guard.decide(
        _shell("echo '$(git commit --allow-empty -m example)'", gp),
        env={}, home=tmp_path, anchors=anchor,
    ) is None


def test_powershell_git_command_substitution_from_anchor_denies(
    tmp_path, anchor,
):
    gp = anchor[0]["path"]
    payload = {
        "toolName": "powershell",
        "cwd": str(gp),
        "toolArgs": {
            "command": 'Write-Output "$(git commit --allow-empty -m example)"'
        },
    }
    d = guard.decide(payload, env={}, home=tmp_path, anchors=anchor)
    assert d and d["permissionDecision"] == "deny"


def test_powershell_git_array_subexpression_from_anchor_denies(
    tmp_path, anchor,
):
    gp = anchor[0]["path"]
    payload = {
        "toolName": "powershell",
        "cwd": str(gp),
        "toolArgs": {
            "command": (
                "Write-Output @(git commit --allow-empty -m example; "
                "Write-Output ok)"
            )
        },
    }
    d = guard.decide(payload, env={}, home=tmp_path, anchors=anchor)
    assert d and d["permissionDecision"] == "deny"


@pytest.mark.parametrize(
    "command",
    [
        "Write-Output foo#bar; git commit --allow-empty -m example",
        "<# note #>; git commit --allow-empty -m example",
    ],
)
def test_powershell_comment_boundaries_preserve_git_command(
    tmp_path, anchor, command,
):
    gp = anchor[0]["path"]
    payload = {
        "toolName": "powershell",
        "cwd": str(gp),
        "toolArgs": {"command": command},
    }
    d = guard.decide(payload, env={}, home=tmp_path, anchors=anchor)
    assert d and d["permissionDecision"] == "deny"


@pytest.mark.parametrize(
    ("tool", "command"),
    [
        (
            "bash",
            "echo ok # $(\ngit commit --allow-empty -m example",
        ),
        (
            "powershell",
            "Write-Output ok # (\ngit commit --allow-empty -m example",
        ),
    ],
)
def test_shell_comment_openers_do_not_hide_next_git_command(
    tmp_path, anchor, tool, command,
):
    gp = anchor[0]["path"]
    payload = {
        "toolName": tool,
        "cwd": str(gp),
        "toolArgs": {"command": command},
    }
    d = guard.decide(payload, env={}, home=tmp_path, anchors=anchor)
    assert d and d["permissionDecision"] == "deny"


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("git commit --allow-empty -m example", "deny"),
        ("git merge-base main HEAD", "allow"),
    ],
)
def test_powershell_git_grouping_expression_from_anchor(
    tmp_path, anchor, expression, expected,
):
    gp = anchor[0]["path"]
    payload = {
        "toolName": "powershell",
        "cwd": str(gp),
        "toolArgs": {"command": f"$result = ({expression})"},
    }
    decision = guard.decide(payload, env={}, home=tmp_path, anchors=anchor)
    if expected == "deny":
        assert decision and decision["permissionDecision"] == "deny"
    else:
        assert decision is None


def test_powershell_doubled_apostrophe_path_denies(tmp_path, anchor):
    literal = _main_checkout(tmp_path, "anchor'name")
    escaped = str(literal).replace("'", "''")
    anchors = [*anchor, {"name": "literal", "path": str(literal)}]
    payload = {
        "toolName": "powershell",
        "cwd": str(tmp_path),
        "toolArgs": {
            "command": f"git -C '{escaped}' commit -m example"
        },
    }
    decision = guard.decide(payload, env={}, home=tmp_path, anchors=anchors)
    assert decision and decision["permissionDecision"] == "deny"


def test_cmd_apostrophe_does_not_quote_separator(tmp_path, anchor):
    gp = anchor[0]["path"]
    payload = {
        "toolName": "cmd",
        "cwd": str(gp),
        "toolArgs": {
            "command": (
                "echo 'ignored & git commit --allow-empty -m example'"
            )
        },
    }
    d = guard.decide(payload, env={}, home=tmp_path, anchors=anchor)
    assert d and d["permissionDecision"] == "deny"


def test_shell_git_config_env_alias_from_anchor_denies(tmp_path, anchor):
    gp = anchor[0]["path"]
    command = (
        "VALUE='commit -m example' "
        f'git -C "{gp}" --config-env=alias.save=VALUE save'
    )
    d = guard.decide(_shell(command, tmp_path), env={},
                     home=tmp_path, anchors=anchor)
    assert d and d["permissionDecision"] == "deny"


@pytest.mark.parametrize("dash_c", ["-C", "-C{}"])
def test_shell_git_commit_with_dashC_forms_still_denies(
    tmp_path, anchor, dash_c,
):
    gp = anchor[0]["path"]
    option = f'-C "{gp}"' if dash_c == "-C" else dash_c.format(gp)
    d = guard.decide(_shell(f"git {option} commit -m example", tmp_path),
                     env={}, home=tmp_path, anchors=anchor)
    assert d and d["permissionDecision"] == "deny"


def test_shell_git_commit_with_attached_quoted_dashC_denies(tmp_path, anchor):
    spaced = _main_checkout(tmp_path, "anchor with spaces")
    anchors = [*anchor, {"name": "spaced", "path": str(spaced)}]
    d = guard.decide(
        _shell(f'git -C"{spaced}" commit -m example', tmp_path),
        env={}, home=tmp_path, anchors=anchors,
    )
    assert d and d["permissionDecision"] == "deny"


@pytest.mark.parametrize("empty_path", ['""', "''"])
def test_shell_git_empty_dashC_from_anchor_denies(
    tmp_path, anchor, empty_path,
):
    gp = anchor[0]["path"]
    d = guard.decide(
        _shell(f"git -C {empty_path} commit -m example", gp),
        env={}, home=tmp_path, anchors=anchor,
    )
    assert d and d["permissionDecision"] == "deny"


@pytest.mark.parametrize("suffix", ["%literal", "$literal"])
def test_shell_git_quoted_literal_expansion_character_path_denies(
    tmp_path, anchor, suffix,
):
    literal = _main_checkout(tmp_path, f"anchor{suffix}")
    anchors = [*anchor, {"name": "literal", "path": str(literal)}]
    d = guard.decide(
        _shell(f"git -C '{literal}' commit -m example", tmp_path),
        env={}, home=tmp_path, anchors=anchors,
    )
    assert d and d["permissionDecision"] == "deny"


@pytest.mark.parametrize("suffix", ["$literal", "`literal"])
def test_bash_double_quoted_escaped_character_path_denies(
    tmp_path, anchor, suffix,
):
    literal = _main_checkout(tmp_path, f"anchor{suffix}")
    escaped = str(literal).replace(suffix[0], f"\\{suffix[0]}")
    anchors = [*anchor, {"name": "literal", "path": str(literal)}]
    d = guard.decide(
        _shell(f'git -C "{escaped}" commit -m example', tmp_path),
        env={}, home=tmp_path, anchors=anchors,
    )
    assert d and d["permissionDecision"] == "deny"


def test_shell_git_unresolved_dashC_variable_still_allows(tmp_path, anchor):
    gp = anchor[0]["path"]
    command = f"$repo='{gp}'; git -C $repo commit -m example"
    assert guard.decide(_shell(command, tmp_path), env={},
                        home=tmp_path, anchors=anchor) is None


@pytest.mark.skipif(os.name != "nt", reason="PowerShell backslash separator")
def test_powershell_git_dashC_trailing_separator_denies(tmp_path, anchor):
    gp = anchor[0]["path"]
    payload = {
        "toolName": "powershell",
        "cwd": str(tmp_path),
        "toolArgs": {"command": f'git -C "{gp}\\" commit -m example'},
    }
    d = guard.decide(payload, env={}, home=tmp_path, anchors=anchor)
    assert d and d["permissionDecision"] == "deny"


def test_powershell_git_dashC_backtick_spaces_denies(tmp_path, anchor):
    spaced = _main_checkout(tmp_path, "anchor with spaces")
    escaped = str(spaced).replace(" ", "` ")
    anchors = [*anchor, {"name": "spaced", "path": str(spaced)}]
    payload = {
        "toolName": "powershell",
        "cwd": str(tmp_path),
        "toolArgs": {"command": f"git -C {escaped} commit -m example"},
    }
    d = guard.decide(payload, env={}, home=tmp_path, anchors=anchors)
    assert d and d["permissionDecision"] == "deny"


def test_shell_git_commit_with_escaped_space_dashC_denies(tmp_path, anchor):
    spaced = _main_checkout(tmp_path, "anchor with spaces")
    escaped = str(spaced).replace(" ", "\\ ")
    anchors = [*anchor, {"name": "spaced", "path": str(spaced)}]
    d = guard.decide(
        _shell(f"git -C {escaped} commit -m example", tmp_path),
        env={}, home=tmp_path, anchors=anchors,
    )
    assert d and d["permissionDecision"] == "deny"


def test_shell_git_repeated_dashC_uses_only_final_directory(tmp_path, anchor):
    other = _main_checkout(tmp_path, "other-repo")
    gp = anchor[0]["path"]
    assert guard.decide(
        _shell(f'git -C "{gp}" -C "{other}" commit -m example', tmp_path),
        env={}, home=tmp_path, anchors=anchor,
    ) is None


@pytest.mark.parametrize(
    "option",
    [
        '--git-dir "{}/.git"',
        '--git-dir="{}/.git"',
        '--work-tree "{}"',
        '--work-tree="{}"',
    ],
)
def test_shell_git_commit_with_repository_target_options_denies(
    tmp_path, anchor, option,
):
    gp = anchor[0]["path"]
    d = guard.decide(_shell(
        f"git {option.format(gp)} commit -m example", tmp_path,
    ), env={}, home=tmp_path, anchors=anchor)
    assert d and d["permissionDecision"] == "deny"


def test_shell_git_dir_elsewhere_overrides_anchor_cwd(tmp_path, anchor):
    other = _main_checkout(tmp_path, "other-repo")
    gp = anchor[0]["path"]
    assert guard.decide(
        _shell(f'git --git-dir "{other / ".git"}" commit -m example', gp),
        env={}, home=tmp_path, anchors=anchor,
    ) is None


def test_shell_git_dir_elsewhere_overrides_anchor_dashC(tmp_path, anchor):
    other = _main_checkout(tmp_path, "other-repo")
    gp = anchor[0]["path"]
    assert guard.decide(
        _shell(
            f'git -C "{gp}" --git-dir "{other / ".git"}" commit -m example',
            tmp_path,
        ),
        env={}, home=tmp_path, anchors=anchor,
    ) is None


@pytest.mark.parametrize("use_dash_c", [False, True])
def test_shell_git_dir_elsewhere_checkout_keeps_implicit_anchor_work_tree(
    tmp_path, anchor, use_dash_c,
):
    other = _main_checkout(tmp_path, "other-repo")
    gp = anchor[0]["path"]
    prefix = f'git -C "{gp}"' if use_dash_c else "git"
    command = (
        f'{prefix} --git-dir "{other / ".git"}" checkout -- tracked.txt'
    )
    cwd = tmp_path if use_dash_c else gp
    d = guard.decide(_shell(command, cwd), env={},
                     home=tmp_path, anchors=anchor)
    assert d and d["permissionDecision"] == "deny"


@pytest.mark.parametrize("option", ["--git-dir", "--work-tree"])
def test_shell_repeated_repository_target_uses_final_value(
    tmp_path, anchor, option,
):
    other = _main_checkout(tmp_path, "other-repo")
    gp = anchor[0]["path"]
    anchor_target = f"{gp}/.git" if option == "--git-dir" else gp
    other_target = f"{other}/.git" if option == "--git-dir" else str(other)
    assert guard.decide(
        _shell(
            f'git {option} "{anchor_target}" {option} "{other_target}" '
            "commit -m example",
            tmp_path,
        ),
        env={}, home=tmp_path, anchors=anchor,
    ) is None


@pytest.mark.parametrize("option", ["--git-dir", "--work-tree"])
def test_shell_repeated_repository_target_final_anchor_denies(
    tmp_path, anchor, option,
):
    other = _main_checkout(tmp_path, "other-repo")
    gp = anchor[0]["path"]
    anchor_target = f"{gp}/.git" if option == "--git-dir" else gp
    other_target = f"{other}/.git" if option == "--git-dir" else str(other)
    d = guard.decide(
        _shell(
            f'git {option} "{other_target}" {option} "{anchor_target}" '
            "commit -m example",
            tmp_path,
        ),
        env={}, home=tmp_path, anchors=anchor,
    )
    assert d and d["permissionDecision"] == "deny"


def test_shell_work_tree_elsewhere_keeps_anchor_repository_target(
    tmp_path, anchor,
):
    other = tmp_path / "other-work-tree"
    other.mkdir()
    gp = anchor[0]["path"]
    d = guard.decide(
        _shell(f'git --work-tree "{other}" commit -m example', gp),
        env={}, home=tmp_path, anchors=anchor,
    )
    assert d and d["permissionDecision"] == "deny"


@pytest.mark.parametrize(
    "command",
    [
        "git --help commit",
        "git --version commit",
        "git --exec-path commit",
        "git -C . --help commit",
    ],
)
def test_shell_git_terminal_global_options_allow(tmp_path, anchor, command):
    gp = anchor[0]["path"]
    assert guard.decide(_shell(command, gp), env={}, home=tmp_path,
                        anchors=anchor) is None


@pytest.mark.parametrize("separator", ["\n", "; ", " && "])
def test_shell_git_batch_with_real_mutation_still_denies(
    tmp_path, anchor, separator,
):
    gp = anchor[0]["path"]
    command = separator.join([
        "git fetch origin refs/pull/42/head",
        "git commit -m example",
    ])
    d = guard.decide(_shell(command, gp), env={}, home=tmp_path,
                     anchors=anchor)
    assert d and d["permissionDecision"] == "deny"


def test_shell_git_bare_pull_without_ff_only_from_anchor_cwd_denies(
    tmp_path, anchor,
):
    """A bare ``git pull`` (no ``--ff-only``) is NOT exempt -- on a diverged
    anchor its default merge could create a genuine new local commit, so it
    stays denied exactly like every other git-write verb."""
    gp = anchor[0]["path"]
    d = guard.decide(_shell("git pull origin main", gp), env={},
                     home=tmp_path, anchors=anchor)
    assert d and d["permissionDecision"] == "deny"


def test_shell_git_pull_with_ff_only_substring_in_branch_name_denies(
    tmp_path, anchor,
):
    """The ``--ff-only`` match must require a standalone argument, not a
    substring anywhere in the segment -- a branch name that merely CONTAINS
    the literal text ``--ff-only`` (no real flag passed) is still an unsafe
    bare pull and must still deny."""
    gp = anchor[0]["path"]
    d = guard.decide(
        _shell("git pull origin release/--ff-only", gp),
        env={}, home=tmp_path, anchors=anchor)
    assert d and d["permissionDecision"] == "deny"


@pytest.mark.parametrize(
    "command",
    [
        "git pull --server-option='foo --ff-only' origin main",
        "git pull origin main -- --ff-only",
    ],
)
def test_shell_git_pull_with_ff_only_argument_text_denies(
    tmp_path, anchor, command,
):
    gp = anchor[0]["path"]
    d = guard.decide(_shell(command, gp), env={}, home=tmp_path,
                     anchors=anchor)
    assert d and d["permissionDecision"] == "deny"


def test_shell_git_pull_last_no_ff_mode_denies(tmp_path, anchor):
    gp = anchor[0]["path"]
    d = guard.decide(
        _shell("git pull --ff-only --no-ff origin main", gp),
        env={}, home=tmp_path, anchors=anchor,
    )
    assert d and d["permissionDecision"] == "deny"


def test_shell_git_pull_last_ff_only_mode_allows(tmp_path, anchor):
    gp = anchor[0]["path"]
    assert guard.decide(
        _shell("git pull --no-ff --ff-only origin main", gp),
        env={}, home=tmp_path, anchors=anchor,
    ) is None


def test_shell_git_commit_with_pull_ff_only_in_message_denies(
    tmp_path, anchor,
):
    """The ``--ff-only`` exemption must key off the actual git SUBCOMMAND,
    not a bare substring search -- a ``commit`` whose message happens to
    contain the literal text ``pull --ff-only`` is still a genuine commit
    and must still deny."""
    gp = anchor[0]["path"]
    d = guard.decide(
        _shell("git commit -m 'pull --ff-only'", gp),
        env={}, home=tmp_path, anchors=anchor)
    assert d and d["permissionDecision"] == "deny"


def test_shell_git_commit_still_denies_alongside_pull_exemption(
    tmp_path, anchor,
):
    """The ``pull --ff-only`` exemption must not have loosened any OTHER git
    mutation verb -- ``commit`` (and by the same list, merge/rebase/
    checkout/etc.) still denies."""
    gp = anchor[0]["path"]
    d = guard.decide(_shell("git commit -m x", gp), env={}, home=tmp_path,
                     anchors=anchor)
    assert d and d["permissionDecision"] == "deny"


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


def test_shell_explicit_linked_worktree_targets_allow(tmp_path, anchor):
    anchor_root = Path(anchor[0]["path"])
    git_dir = anchor_root / ".git" / "worktrees" / "explicit"
    git_dir.mkdir(parents=True)
    wt = tmp_path / "myrepo.worktrees" / "explicit"
    wt.mkdir(parents=True)
    (wt / ".git").write_text(f"gitdir: {git_dir}\n", encoding="utf-8")

    command = (
        f'git --git-dir "{git_dir}" --work-tree "{wt}" commit -m example'
    )
    assert guard.decide(_shell(command, anchor_root), env={},
                        home=tmp_path, anchors=anchor) is None


def test_shell_linked_worktree_git_dir_from_cwd_allows(tmp_path, anchor):
    anchor_root = Path(anchor[0]["path"])
    git_dir = anchor_root / ".git" / "worktrees" / "cwd-only"
    git_dir.mkdir(parents=True)
    wt = tmp_path / "myrepo.worktrees" / "cwd-only"
    wt.mkdir(parents=True)
    (wt / ".git").write_text(f"gitdir: {git_dir}\n", encoding="utf-8")

    command = f'git --git-dir "{git_dir}" commit -m example'
    assert guard.decide(_shell(command, wt), env={},
                        home=tmp_path, anchors=anchor) is None


def test_shell_fake_linked_worktree_pointing_at_anchor_git_dir_denies(
    tmp_path, anchor,
):
    anchor_root = Path(anchor[0]["path"])
    external = tmp_path / "external-work-tree"
    external.mkdir()
    (external / ".git").write_text(
        f"gitdir: {anchor_root / '.git'}\n",
        encoding="utf-8",
    )
    command = (
        f'git --git-dir "{anchor_root / ".git"}" '
        f'--work-tree "{external}" commit -m example'
    )
    d = guard.decide(_shell(command, tmp_path), env={},
                     home=tmp_path, anchors=anchor)
    assert d and d["permissionDecision"] == "deny"


def test_shell_symlinked_anchor_git_dir_denies(tmp_path, anchor):
    anchor_git = Path(anchor[0]["path"]) / ".git"
    alias = tmp_path / "anchor-git-link"
    try:
        alias.symlink_to(anchor_git, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlink unavailable: {exc}")
    d = guard.decide(
        _shell(f'git --git-dir "{alias}" commit -m example', tmp_path),
        env={}, home=tmp_path, anchors=anchor,
    )
    assert d and d["permissionDecision"] == "deny"


def test_shell_spoofed_linked_git_dir_symlink_denies(tmp_path, anchor):
    anchor_git = Path(anchor[0]["path"]) / ".git"
    spoof_parent = tmp_path / "outside" / ".git" / "worktrees"
    spoof_parent.mkdir(parents=True)
    spoof = spoof_parent / "id"
    try:
        spoof.symlink_to(anchor_git, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlink unavailable: {exc}")
    external = tmp_path / "external-work-tree"
    external.mkdir()
    (external / ".git").write_text(
        f"gitdir: {spoof}\n",
        encoding="utf-8",
    )
    command = (
        f'git --git-dir "{spoof}" --work-tree "{external}" '
        "commit -m example"
    )
    d = guard.decide(_shell(command, tmp_path), env={},
                     home=tmp_path, anchors=anchor)
    assert d and d["permissionDecision"] == "deny"


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
    cmd = f"gh issue create --repo o/r --title 'bug' --body '{body}'"
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


def test_shell_sudo_prefixed_write_into_anchor_denies(tmp_path, anchor):
    # A wrapper prefix (sudo) before the write verb must not be a blind spot.
    gp = anchor[0]["path"]
    d = guard.decide(_shell(f'sudo rm -rf "{gp}/src"', tmp_path),
                     env={}, home=tmp_path, anchors=anchor)
    assert d and d["permissionDecision"] == "deny"


def test_shell_env_assignment_prefixed_git_write_from_anchor_cwd_denies(
    tmp_path, anchor
):
    # A leading env-assignment (VAR=...) before `git commit` must still be seen.
    gp = anchor[0]["path"]
    d = guard.decide(_shell("GIT_AUTHOR_NAME=x git commit -m y", gp),
                     env={}, home=tmp_path, anchors=anchor)
    assert d and d["permissionDecision"] == "deny"


# -- in-command `cd <anchor>` moves the effective cwd (dotfiles#1144 follow-up) --

def test_shell_cd_into_anchor_then_git_commit_denies(tmp_path, anchor):
    # The tool cwd is elsewhere, but `cd <anchor>` inside the command makes the
    # subsequent `git commit` write the anchor. Must be caught.
    gp = anchor[0]["path"]
    d = guard.decide(_shell(f'cd "{gp}"; git commit -m x', tmp_path),
                     env={}, home=tmp_path, anchors=anchor)
    assert d and d["permissionDecision"] == "deny"


def test_shell_cd_into_anchor_subdir_then_git_write_denies(tmp_path, anchor):
    gp = anchor[0]["path"]
    d = guard.decide(_shell(f'cd "{gp}\\src" && git add -A', tmp_path),
                     env={}, home=tmp_path, anchors=anchor)
    assert d and d["permissionDecision"] == "deny"


def test_shell_cd_into_anchor_forwardslash_subdir_then_git_write_denies(
    tmp_path, anchor
):
    # Forward-slash subdir is the POSIX-native form of the same vector.
    gp = anchor[0]["path"]
    d = guard.decide(_shell(f'cd "{gp}/src" && git add -A', tmp_path),
                     env={}, home=tmp_path, anchors=anchor)
    assert d and d["permissionDecision"] == "deny"


def test_shell_cd_relative_backslash_subdir_from_anchor_then_git_write_denies(
    tmp_path, anchor
):
    # A RELATIVE backslash subpath from the anchor cwd must also normalize so the
    # effective cwd stays inside the anchor (defense-in-depth on POSIX).
    gp = anchor[0]["path"]
    d = guard.decide(_shell(f'cd "{gp}" && cd sub\\deeper && git add -A', tmp_path),
                     env={}, home=tmp_path, anchors=anchor)
    assert d and d["permissionDecision"] == "deny"


def test_shell_cd_into_non_anchor_backslash_dir_still_allows(tmp_path, anchor):
    # Separator normalization must not over-fire: a cd into an unrelated dir that
    # merely shares no ancestry with the anchor is still allowed.
    d = guard.decide(_shell('cd "/tmp/elsewhere\\src" && git add -A', tmp_path),
                     env={}, home=tmp_path, anchors=anchor)
    assert d is None


def test_shell_cmd_style_cd_slash_d_into_anchor_denies(tmp_path, anchor):
    # CMD `cd /d <path>` (SHELL_TOOLS includes cmd): the /d flag must be skipped,
    # not captured as the directory target.
    gp = anchor[0]["path"]
    d = guard.decide(
        {"toolName": "cmd", "cwd": str(tmp_path),
         "toolArgs": {"command": f'cd /d "{gp}" && git commit -m x'}},
        env={}, home=tmp_path, anchors=anchor)
    assert d and d["permissionDecision"] == "deny"


def test_shell_cd_into_anchor_then_read_still_allows(tmp_path, anchor):
    gp = anchor[0]["path"]
    assert guard.decide(_shell(f'cd "{gp}"; git status', tmp_path),
                        env={}, home=tmp_path, anchors=anchor) is None


def test_shell_cd_variable_target_does_not_move_cwd(tmp_path, anchor):
    # `cd $a` is unresolvable, so it must NOT be treated as moving into the
    # anchor -- otherwise the read-only repro would falsely deny again.
    gp = anchor[0]["path"]
    cmd = f'$a="{gp}"; cd $a; git commit -m x'
    # cwd is elsewhere and the cd target is a variable -> not attributable.
    assert guard.decide(_shell(cmd, tmp_path), env={},
                        home=tmp_path, anchors=anchor) is None


def test_shell_cd_into_then_out_of_anchor_allows(tmp_path, anchor):
    gp = anchor[0]["path"]
    cmd = f'cd "{gp}"; cd ..; git commit -m x'
    assert guard.decide(_shell(cmd, tmp_path), env={},
                        home=tmp_path, anchors=anchor) is None


def test_shell_cd_into_sibling_worktree_then_git_write_allows(tmp_path, anchor):
    # `cd <anchor>.worktrees\wt` is NOT inside the anchor -> git write there is
    # fine.
    gp = anchor[0]["path"]
    _linked_worktree(tmp_path / "myrepo.worktrees" / "wt9")
    wt = f"{gp}.worktrees\\wt9"
    assert guard.decide(_shell(f'cd "{wt}"; git commit -m x', tmp_path),
                        env={}, home=tmp_path, anchors=anchor) is None


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
    anchors = guard.load_worktree_anchors(home / ".agent-worktrees")
    names = {a["name"] for a in anchors}
    assert names == {"copilot-extensions", "other-wt"}  # singleton excluded
    paths = {a["path"] for a in anchors}
    # Double-backslash unescaped to single; both platform paths surfaced.
    assert "C:\\Data\\Src\\copilot-extensions" in paths
    assert "/home/u/copilot-extensions" in paths


@pytest.mark.parametrize("no_pyyaml", [False, True])
def test_base_repo_adoption_is_not_guarded(tmp_path, monkeypatch, no_pyyaml):
    """projects.yaml ``base_repo: true`` means the anchor IS the working checkout
    (e.g. a CodeSpace dedicated to one task), even for a ``class: worktree`` repo."""
    if no_pyyaml:
        monkeypatch.setitem(sys.modules, "yaml", None)  # force the stdlib parser
    reg = tmp_path / ".agent-worktrees"
    reg.mkdir()
    (reg / "repos.yaml").write_text(
        "repos:\n"
        "  example-web:\n"
        "    class: worktree\n"
        "    linux: /workspaces/example-web\n"
        "  other-wt:\n"
        "    class: worktree\n"
        "    linux: /src/other\n",
        encoding="utf-8")
    (reg / "projects.yaml").write_text(
        "schema_version: 2\n"
        "projects:\n"
        "  example-web:\n"
        "    base_repo: true\n"
        "    expose_agent: false\n"
        "  other-wt:\n"
        "    base_repo: false\n",
        encoding="utf-8")
    assert {a["name"] for a in guard.load_worktree_anchors(reg)} == {"other-wt"}


def test_load_worktree_anchors_missing_file_is_empty(tmp_path):
    assert guard.load_worktree_anchors(tmp_path / "nope") == []
