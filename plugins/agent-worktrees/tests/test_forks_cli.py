"""Dispatch-level tests for the ``forks`` CLI command (forks_cli.py)."""

from __future__ import annotations

import json

from agent_worktrees import forks_cli


def test_set_requires_owner(capfd):
    rc = forks_cli.cmd_forks_dispatch(["set", "octo-org/widgets"])
    assert rc == 1
    assert "requires --owner" in capfd.readouterr().out


def test_set_resolves_account_by_default(monkeypatch, capfd):
    monkeypatch.setattr(
        "agent_worktrees.fork_pr._resolve_fork_credential",
        lambda slug, prcfg: (None, "resolved-login"),
    )
    rc = forks_cli.cmd_forks_dispatch(
        ["set", "octo-org/widgets", "--owner", "octocat"],
    )
    assert rc == 0
    out = capfd.readouterr().out
    assert "resolved-login" in out

    rc = forks_cli.cmd_forks_dispatch(["show", "octo-org/widgets", "--json"])
    payload = json.loads(capfd.readouterr().out)
    assert rc == 0
    assert payload == {
        "version": 1,
        "forks": [
            {
                "repo": "octo-org/widgets",
                "owner": "octocat",
                "remote": "fork",
                "account": "resolved-login",
                "confirmed_at": payload["forks"][0]["confirmed_at"],
                "notes": "",
            }
        ]
    }
    assert payload["forks"][0]["confirmed_at"]


def test_set_rejects_unresolvable_empty_account(monkeypatch, capfd):
    """An entry recorded under an empty account scope can never be looked
    up later -- resolve_fork_publish only consults the registry when
    effective_account is truthy -- so it would be permanently dead weight
    while 'forks set' falsely reports success. Must reject instead."""
    monkeypatch.setattr(
        "agent_worktrees.fork_pr._resolve_fork_credential",
        lambda slug, prcfg: (None, ""),
    )
    rc = forks_cli.cmd_forks_dispatch(
        ["set", "octo-org/widgets", "--owner", "octocat"],
    )
    assert rc == 1
    assert "could not resolve an account" in capfd.readouterr().out

    from agent_worktrees import fork_pr
    assert fork_pr.find_forks_for_repo("octo-org/widgets") == []


def test_set_explicit_account_overrides_resolution(monkeypatch, capfd):
    monkeypatch.setattr(
        "agent_worktrees.fork_pr._resolve_fork_credential",
        lambda slug, prcfg: (None, "would-be-resolved"),
    )
    rc = forks_cli.cmd_forks_dispatch(
        ["set", "octo-org/widgets", "--owner", "octocat", "--account", "explicit"],
    )
    assert rc == 0
    capfd.readouterr()
    forks_cli.cmd_forks_dispatch(["show", "octo-org/widgets", "--json"])
    payload = json.loads(capfd.readouterr().out)
    assert payload["forks"][0]["account"] == "explicit"


def test_set_with_token_stdin_derives_scope_create_pr_would_compute(monkeypatch, capfd):
    """``--token-stdin`` must derive the SAME scope create_pr's gate would
    compute for a token_command/token_env-bound repo (``_token_scope``,
    exercised here with NO mocking so a real divergence between the CLI's
    derivation and create_pr's own would show up), rather than guessing a
    login account alone cannot reproduce for an opaque token -- and must
    read the secret from stdin, never argv (shell history / process
    listing), and never surface the raw token anywhere."""
    import io

    from agent_worktrees import fork_pr

    monkeypatch.setattr("sys.stdin", io.StringIO("ghp_exampletoken\n"))
    rc = forks_cli.cmd_forks_dispatch(
        ["set", "octo-org/widgets", "--owner", "octocat", "--token-stdin"],
    )
    assert rc == 0
    expected_scope = fork_pr._token_scope("ghp_exampletoken")
    out = capfd.readouterr().out
    assert expected_scope in out
    assert "ghp_exampletoken" not in out  # the raw secret itself never prints

    forks_cli.cmd_forks_dispatch(["show", "octo-org/widgets", "--json"])
    payload = json.loads(capfd.readouterr().out)
    assert payload["forks"][0]["account"] == expected_scope

    # The raw token must never land in the durable registry file either --
    # only its derived (one-way hashed) scope.
    raw_file = fork_pr._forks_yaml_path().read_text(encoding="utf-8")
    assert "ghp_exampletoken" not in raw_file
    assert expected_scope in raw_file


def test_set_token_stdin_and_account_mutually_exclusive(capfd):
    rc = forks_cli.cmd_forks_dispatch(
        [
            "set", "octo-org/widgets", "--owner", "octocat",
            "--account", "explicit", "--token-stdin",
        ],
    )
    assert rc == 1
    assert "mutually exclusive" in capfd.readouterr().out
    from agent_worktrees import fork_pr
    assert fork_pr.find_forks_for_repo("octo-org/widgets") == []


def test_set_token_stdin_empty_fails(monkeypatch, capfd):
    import io

    from agent_worktrees import fork_pr

    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    rc = forks_cli.cmd_forks_dispatch(
        ["set", "octo-org/widgets", "--owner", "octocat", "--token-stdin"],
    )
    assert rc == 1
    assert "no token read from stdin" in capfd.readouterr().out
    assert fork_pr.find_forks_for_repo("octo-org/widgets") == []


def test_option_with_missing_value_is_a_usage_error(capfd):
    """A flag present with nothing after it (or immediately followed by
    another flag) must fail loudly: 'forks remove <repo> --account' with a
    missing value is a malformed command, never an implicit "remove every
    account for that repo"."""
    from agent_worktrees import fork_pr
    fork_pr.record_confirmation("octo-org/widgets", "octocat", account="account-a")

    rc = forks_cli.cmd_forks_dispatch(["remove", "octo-org/widgets", "--account"])
    assert rc == 1
    out = capfd.readouterr().out
    assert "--account requires a value" in out
    # The rejected command must leave the existing confirmation untouched.
    assert fork_pr.is_confirmed("octo-org/widgets", account="account-a") is True


def test_option_followed_by_another_flag_is_a_usage_error(capfd):
    rc = forks_cli.cmd_forks_dispatch(
        ["set", "octo-org/widgets", "--owner", "--remote"],
    )
    assert rc == 1
    assert "--owner requires a value" in capfd.readouterr().out


def test_unknown_flag_is_rejected_not_silently_ignored(monkeypatch, capfd):
    """An unsupported or mistyped flag (and its value) must never be
    silently dropped while the command still succeeds under an unintended
    default -- it must fail loudly instead."""
    monkeypatch.setattr(
        "agent_worktrees.fork_pr._resolve_fork_credential",
        lambda slug, prcfg: (None, "would-be-ambient"),
    )
    rc = forks_cli.cmd_forks_dispatch(
        ["set", "octo-org/widgets", "--owner", "octocat", "--token", "secret"],
    )
    assert rc == 1
    assert "unknown option" in capfd.readouterr().out
    # And nothing was recorded under the unintended ambient default.
    from agent_worktrees import fork_pr
    assert fork_pr.find_forks_for_repo("octo-org/widgets") == []


def test_unknown_flag_on_remove_is_rejected(capfd):
    """The same unknown-flag rejection must hold for 'remove', not just
    'set' -- a malformed removal command must never silently fall through
    to an unintended default scope."""
    from agent_worktrees import fork_pr
    fork_pr.record_confirmation("octo-org/widgets", "octocat", account="account-a")

    rc = forks_cli.cmd_forks_dispatch(
        ["remove", "octo-org/widgets", "--bogus-flag", "x"],
    )
    assert rc == 1
    assert "unknown option" in capfd.readouterr().out
    assert fork_pr.is_confirmed("octo-org/widgets", account="account-a") is True


def test_extra_positional_is_rejected(capfd):
    rc = forks_cli.cmd_forks_dispatch(["list", "unexpected-extra-arg"])
    assert rc == 1
    assert "unexpected extra argument" in capfd.readouterr().out


def test_list_json_and_text(capfd):
    forks_cli.cmd_forks_dispatch(
        ["set", "octo-org/widgets", "--owner", "octocat", "--account", "octocat"],
    )
    capfd.readouterr()

    rc = forks_cli.cmd_forks_dispatch(["list", "--json"])
    assert rc == 0
    payload = json.loads(capfd.readouterr().out)
    assert [f["repo"] for f in payload["forks"]] == ["octo-org/widgets"]

    rc = forks_cli.cmd_forks_dispatch(["list"])
    assert rc == 0
    out = capfd.readouterr().out
    assert "octo-org/widgets" in out
    assert "owner=octocat" in out


def test_list_empty(capfd):
    rc = forks_cli.cmd_forks_dispatch(["list"])
    assert rc == 0
    assert "No forks confirmed yet." in capfd.readouterr().out


def test_show_unknown_repo_fails(capfd):
    rc = forks_cli.cmd_forks_dispatch(["show", "no/such-repo"])
    assert rc == 1
    assert "No confirmed fork" in capfd.readouterr().out


def test_remove(capfd):
    forks_cli.cmd_forks_dispatch(
        ["set", "octo-org/widgets", "--owner", "octocat", "--account", "octocat"],
    )
    capfd.readouterr()

    rc = forks_cli.cmd_forks_dispatch(["remove", "octo-org/widgets"])
    assert rc == 0

    rc = forks_cli.cmd_forks_dispatch(["remove", "octo-org/widgets"])
    assert rc == 1
    assert "No confirmed fork" in capfd.readouterr().out


def test_remove_with_account_only_removes_that_account(capfd):
    """'remove <repo> --account A' must forget only A's entry, leaving a
    different account's confirmation for the same repo intact -- the exact
    multi-account-per-repo contract the composite key exists to support."""
    from agent_worktrees import fork_pr

    fork_pr.record_confirmation("octo-org/widgets", "alice", account="account-a")
    fork_pr.record_confirmation("octo-org/widgets", "bob", account="account-b")
    capfd.readouterr()

    rc = forks_cli.cmd_forks_dispatch(
        ["remove", "octo-org/widgets", "--account", "account-a"],
    )
    assert rc == 0
    assert fork_pr.is_confirmed("octo-org/widgets", account="account-a") is False
    assert fork_pr.is_confirmed("octo-org/widgets", account="account-b") is True


def test_help_and_unknown_subcommand(capfd):
    rc = forks_cli.cmd_forks_dispatch(["--help"])
    assert rc == 0
    assert "forks <command>" in capfd.readouterr().out

    rc = forks_cli.cmd_forks_dispatch(["bogus"])
    assert rc == 1
    err = capfd.readouterr()
    assert "Unknown forks subcommand" in err.out
