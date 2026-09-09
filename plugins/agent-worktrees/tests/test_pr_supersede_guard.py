"""Tests for the pr_supersede_guard preToolUse hook decision logic.

The guard blocks ``gh pr close <n>`` (and the API equivalent) when the target
PR is OPEN and authored by someone other than the currently gh-authenticated
identity -- the structural enforcement of the reviewer vision's non-goal
"never close, supersede, or replace another author's open pull request".
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

# The guard ships as a standalone script under scripts/ (deployed to
# ~/.agent-worktrees/bin/), not as a package module -- load it by path.
_GUARD_PATH = Path(__file__).resolve().parents[1] / "scripts" / "pr_supersede_guard.py"
_spec = importlib.util.spec_from_file_location("pr_supersede_guard", _GUARD_PATH)
assert _spec and _spec.loader, f"cannot load guard script at {_GUARD_PATH}"
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)


def _shell(cmd: str) -> dict:
    return {"toolName": "bash", "cwd": "C:/repo", "toolArgs": {"command": cmd}}


def _lookup(login: str, state: str):
    return lambda repo, number, cwd, dl: {"login": login, "state": state}


def _whoami(login: str):
    return lambda cwd, dl: login


# --- core denial ---------------------------------------------------------

def test_close_other_authors_open_pr_denies():
    d = guard.decide(
        _shell("gh pr close 203 --repo gim-home/odsp-web-harness"),
        env={}, pr_lookup=_lookup("alice", "OPEN"), current_login=_whoami("bot"),
    )
    assert d and d["permissionDecision"] == "deny"
    assert "#203" in d["permissionDecisionReason"]
    assert "alice" in d["permissionDecisionReason"]


def test_close_own_pr_allows():
    d = guard.decide(
        _shell("gh pr close 203 --repo gim-home/odsp-web-harness"),
        env={}, pr_lookup=_lookup("bot", "OPEN"), current_login=_whoami("bot"),
    )
    assert d is None


def test_close_own_pr_case_insensitive_allows():
    d = guard.decide(
        _shell("gh pr close 203 --repo gim-home/odsp-web-harness"),
        env={}, pr_lookup=_lookup("Bot", "OPEN"), current_login=_whoami("bot"),
    )
    assert d is None


def test_close_already_closed_pr_allows():
    # Nothing left to protect -- the PR is already settled.
    d = guard.decide(
        _shell("gh pr close 203 --repo gim-home/odsp-web-harness"),
        env={}, pr_lookup=_lookup("alice", "CLOSED"), current_login=_whoami("bot"),
    )
    assert d is None


def test_close_merged_pr_allows():
    d = guard.decide(
        _shell("gh pr close 203 --repo gim-home/odsp-web-harness"),
        env={}, pr_lookup=_lookup("alice", "MERGED"), current_login=_whoami("bot"),
    )
    assert d is None


def test_api_patch_state_closed_denies():
    d = guard.decide(
        _shell(
            "gh api repos/gim-home/odsp-web-harness/pulls/203 -X PATCH "
            "-f state=closed"
        ),
        env={}, pr_lookup=_lookup("alice", "OPEN"), current_login=_whoami("bot"),
    )
    assert d and d["permissionDecision"] == "deny"


def test_api_patch_without_state_closed_allows():
    # A PATCH that doesn't touch state (e.g. editing the title) is unrelated.
    d = guard.decide(
        _shell(
            "gh api repos/gim-home/odsp-web-harness/pulls/203 -X PATCH "
            "-f title='new title'"
        ),
        env={}, pr_lookup=_lookup("alice", "OPEN"), current_login=_whoami("bot"),
    )
    assert d is None


# --- merges are NOT in scope (only close/replace is forbidden) -----------

def test_merge_other_authors_pr_allows():
    d = guard.decide(
        _shell("gh pr merge 203 --repo gim-home/odsp-web-harness --squash"),
        env={}, pr_lookup=_lookup("alice", "OPEN"), current_login=_whoami("bot"),
    )
    assert d is None


# --- fail-open on ambiguity ------------------------------------------------

def test_unresolvable_pr_lookup_allows():
    d = guard.decide(
        _shell("gh pr close 203 --repo gim-home/odsp-web-harness"),
        env={}, pr_lookup=lambda *a: None, current_login=_whoami("bot"),
    )
    assert d is None


def test_unresolvable_whoami_allows():
    d = guard.decide(
        _shell("gh pr close 203 --repo gim-home/odsp-web-harness"),
        env={}, pr_lookup=_lookup("alice", "OPEN"), current_login=lambda *a: None,
    )
    assert d is None


def test_unrelated_command_allows():
    d = guard.decide(_shell("gh pr list --repo gim-home/odsp-web-harness"), env={})
    assert d is None


def test_non_shell_tool_allows():
    d = guard.decide({"toolName": "edit", "cwd": "C:/repo",
                       "toolArgs": {"path": "x.py"}}, env={})
    assert d is None


# --- escape hatches / modes -------------------------------------------------

def test_env_off_disables_guard():
    d = guard.decide(
        _shell("gh pr close 203 --repo gim-home/odsp-web-harness"),
        env={"PR_SUPERSEDE_GUARD": "off"},
        pr_lookup=_lookup("alice", "OPEN"), current_login=_whoami("bot"),
    )
    assert d is None


def test_mode_warn_returns_additional_context_not_deny():
    d = guard.decide(
        _shell("gh pr close 203 --repo gim-home/odsp-web-harness"),
        env={"PR_SUPERSEDE_GUARD_MODE": "warn"},
        pr_lookup=_lookup("alice", "OPEN"), current_login=_whoami("bot"),
    )
    assert d and "permissionDecision" not in d
    assert "additionalContext" in d


def test_mode_ask_returns_ask_decision():
    d = guard.decide(
        _shell("gh pr close 203 --repo gim-home/odsp-web-harness"),
        env={"PR_SUPERSEDE_GUARD_MODE": "ask"},
        pr_lookup=_lookup("alice", "OPEN"), current_login=_whoami("bot"),
    )
    assert d and d["permissionDecision"] == "ask"


# --- ambient repo resolution (no --repo flag) -------------------------------

def test_close_without_repo_flag_uses_ambient_lookup():
    seen = {}

    def lookup(repo, number, cwd, dl):
        seen["repo"] = repo
        seen["number"] = number
        return {"login": "alice", "state": "OPEN"}

    d = guard.decide(
        _shell("gh pr close 203"),
        env={}, pr_lookup=lookup, current_login=_whoami("bot"),
    )
    assert d and d["permissionDecision"] == "deny"
    assert seen["repo"] is None  # caller's default lookup resolves ambient repo
    assert seen["number"] == 203


# --- main() fail-open on any crash ------------------------------------------

def test_main_never_raises_on_bad_stdin(monkeypatch, capsys):
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    assert guard.main() == 0
    assert capsys.readouterr().out == ""
