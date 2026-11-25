"""Strict scoped execution never issues a request with an ambient identity."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from types import SimpleNamespace
from typing import Any

import pytest

from agent_worktrees import __main__ as main
from agent_worktrees import github_account_observation as observation
from agent_worktrees import repos, repos_gh_credentials
from agent_worktrees.github_credentials import AccountSelection


@pytest.mark.parametrize("outcome", ["selected", "unmapped", "unavailable", "mismatch"])
def test_strict_repos_gh_refuses_before_the_requested_operation(
    monkeypatch: pytest.MonkeyPatch, outcome: str,
) -> None:
    monkeypatch.setenv("GH_TOKEN", "ambient-fixture")
    monkeypatch.setenv("GITHUB_TOKEN", "other-ambient-fixture")
    monkeypatch.setenv("GH_HOST", "unrelated.invalid")
    monkeypatch.setattr(shutil, "which", lambda _name: "fixture-gh")
    monkeypatch.setattr(repos, "is_unresolved_registered_target", lambda _target: False)
    monkeypatch.setattr(
        repos_gh_credentials, "account_selection",
        lambda _target: AccountSelection(None, False) if outcome == "unmapped"
        else AccountSelection("account-x", True),
    )
    reads: list[list[str]] = []
    requests: list[list[str]] = []

    def capture(command: list[str], **options: Any) -> SimpleNamespace:
        reads.append(command)
        environment = options["env"]
        assert "GITHUB_TOKEN" not in environment and "GH_HOST" not in environment
        if command[1:3] == ["auth", "token"]:
            assert command == ["fixture-gh", "auth", "token", "--hostname", "github.com", "--user", "account-x"]
            assert "GH_TOKEN" not in environment
            return SimpleNamespace(
                returncode=1 if outcome == "unavailable" else 0,
                stdout="" if outcome == "unavailable" else "selected-fixture\n",
            )
        assert command == [
            "fixture-gh", "api", "--hostname", "github.com", "--include", "https://api.github.com/user",
        ]
        assert environment["GH_TOKEN"] == "selected-fixture"
        return SimpleNamespace(
            returncode=0,
            stdout="HTTP/2 200\n\n" + json.dumps({
                "login": "unrelated-account" if outcome == "mismatch" else "account-x",
            }),
        )

    def request(command: list[str], **options: Any) -> SimpleNamespace:
        requests.append(command)
        assert options["env"]["GH_TOKEN"] == "selected-fixture"
        assert "GITHUB_TOKEN" not in options["env"] and "GH_HOST" not in options["env"]
        assert options["stdout"] is sys.stdout.buffer and options["stderr"] is sys.stderr.buffer
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(observation, "run_caller_owned", capture)
    monkeypatch.setattr(subprocess, "run", request)
    result = main.cmd_repos_dispatch([
        "gh", "--require-account", "example/repository", "--", "api", "repos/example/repository",
    ])
    assert result == (0 if outcome == "selected" else 1)
    assert requests == (
        [["fixture-gh", "api", "repos/example/repository"]] if outcome == "selected" else []
    )
    assert len(reads) == {"selected": 2, "unmapped": 0, "unavailable": 1, "mismatch": 2}[outcome]
    assert os.environ["GH_TOKEN"] == "ambient-fixture"


@pytest.mark.parametrize("target", ["example-owner", "registered-repository"])
def test_strict_repos_gh_rejects_non_slug_targets_before_credential_reads(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], target: str,
) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: "fixture-gh")
    monkeypatch.setattr(repos, "is_unresolved_registered_target", lambda _target: False)
    selections: list[str] = []

    def selection(value: str) -> AccountSelection:
        selections.append(value)
        return AccountSelection("account-x", True)

    monkeypatch.setattr(repos_gh_credentials, "account_selection", selection)
    monkeypatch.setattr(
        observation, "run_caller_owned",
        lambda *_args, **_options: pytest.fail("invalid strict target read credentials"),
    )
    monkeypatch.setattr(
        subprocess, "run",
        lambda *_args, **_options: pytest.fail("invalid strict target executed gh"),
    )
    assert main.cmd_repos_dispatch(["gh", "--require-account", target, "--", "api", "user"]) == 1
    output = capsys.readouterr()
    assert "owner/repository target" in output.out + output.err
    assert selections == []
