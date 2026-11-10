"""Selected-store identity checks under one caller-owned execution budget."""

import json
import subprocess
from types import SimpleNamespace

import pytest

from agent_procutil.owned_budget import OwnedExecutionBudget
from agent_procutil.owned_process import OwnedCleanupError

from agent_worktrees import github_account_observation as native
from agent_worktrees.github_credentials import CredentialObservationError, inspect_github_account


@pytest.fixture
def account(monkeypatch):
    calls = []
    state = {"token": "fixture-secret", "login": "selected", "exit": 0}
    for key in (
        "GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN",
        "GH_HOST", "GH_REPO", "GH_DEBUG", "GH_FORCE_TTY", "GH_HTTP_UNIX_SOCKET",
    ):
        monkeypatch.setenv(key, "hostile-ambient")
    monkeypatch.setattr(native.shutil, "which", lambda name: "/fixture/gh")
    budget = OwnedExecutionBudget.from_timeout(3, cleanup_reserve=.2)
    checkpoints = []

    def run(argv, **kwargs):
        assert kwargs["budget"] is budget
        assert 0 < kwargs["timeout"] <= 3
        assert kwargs["env"]["GH_PROMPT_DISABLED"] == "1"
        assert kwargs["env"]["GH_PAGER"] == "cat"
        assert all(
            key not in kwargs["env"] for key in (
                "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN",
                "GH_HOST", "GH_REPO", "GH_DEBUG", "GH_FORCE_TTY", "GH_HTTP_UNIX_SOCKET",
            )
        )
        calls.append(argv)
        if argv[1:3] == ["auth", "token"]:
            assert argv[3:] == ["--hostname", "github.com", "--user", "selected"]
            assert "GH_TOKEN" not in kwargs["env"]
            return SimpleNamespace(returncode=state["exit"], stdout=state["token"], stderr="secret-diagnostic")
        assert kwargs["env"]["GH_TOKEN"] == "fixture-secret"
        assert argv[1:5] == ["api", "--hostname", "github.com", "--include"]
        if argv[-1] == "https://api.github.com/user":
            body = {"login": state["login"]}
        else:
            assert argv[-1] == "https://api.github.com/repos/example/project"
            body = {"full_name": "example/project"}
        return SimpleNamespace(returncode=0, stdout="HTTP/2 200 OK\r\n\r\n" + json.dumps(body), stderr="")

    monkeypatch.setattr(native, "run_caller_owned", run)
    selected = native.SelectedGitHubAccount(
        "github.com", "selected", "example/project",
        budget=budget, checkpoint=lambda: checkpoints.append(True),
    )
    return selected, state, calls, checkpoints, budget


def test_selected_active_store_is_verified_once_and_fixed_for_following_reads(account):
    selected, state, calls, checkpoints, _ = account
    selected.verify()
    state["token"] = "rotated-entry"
    selected.verify()
    assert selected.json("repository", "/repos/example/project")["full_name"] == "example/project"
    assert len(calls) == 3 and len(checkpoints) == 7
    assert "fixture-secret" not in repr(selected)


@pytest.mark.parametrize("login", ["other", "", None, 7, "github_pat_secret"])
def test_wrong_or_invalid_authenticated_login_prevents_later_read(account, login):
    selected, state, calls, _, _ = account
    state["login"] = login
    with pytest.raises(CredentialObservationError) as caught:
        selected.verify()
    assert caught.value.stage == "identity"
    assert len(calls) == 2
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize("token,exit_code", [("", 0), ("two tokens", 0), ("bad\nvalue", 0), ("secret", 1)])
def test_unavailable_store_never_falls_back_to_ambient(account, token, exit_code):
    selected, state, calls, _, _ = account
    state.update(token=token, exit=exit_code)
    with pytest.raises(CredentialObservationError):
        selected.verify()
    assert len(calls) == 1


def test_original_deadline_and_withdrawal_prevent_later_process(account, monkeypatch):
    selected, _, calls, _, budget = account
    monkeypatch.setattr(native.time, "monotonic", lambda: budget.execution_deadline + 1)
    with pytest.raises(CredentialObservationError, match="timeout"):
        selected.verify()
    assert calls == []
    monkeypatch.setattr(native.time, "monotonic", lambda: budget.execution_deadline - 1)
    selected.checkpoint = lambda: (_ for _ in ()).throw(RuntimeError("withdrawn"))
    with pytest.raises(RuntimeError, match="withdrawn"):
        selected.verify()
    assert calls == []


def test_cleanup_custody_is_not_converted_to_credential_failure(account, monkeypatch):
    selected, _, _, _, _ = account
    retained = OwnedCleanupError(None, cleanup_retry=lambda deadline: None)
    monkeypatch.setattr(native, "run_caller_owned", lambda *a, **k: (_ for _ in ()).throw(retained))
    with pytest.raises(OwnedCleanupError) as caught:
        selected.verify()
    assert caught.value is retained


def test_native_timeout_never_discloses_child_material(account, monkeypatch):
    selected, _, _, _, _ = account
    monkeypatch.setattr(
        native, "run_caller_owned",
        lambda *a, **k: (_ for _ in ()).throw(subprocess.TimeoutExpired("secret-command", 1)),
    )
    with pytest.raises(CredentialObservationError) as caught:
        selected.verify()
    assert caught.value.code == "timeout"
    assert "secret-command" not in str(caught.value)


def test_existing_observer_keeps_observation_shape(account, monkeypatch):
    selected, _, _, _, _ = account
    monkeypatch.setattr(native, "SelectedGitHubAccount", lambda *a, **k: selected)
    assert inspect_github_account("github.com", "selected", "example/project") == {
        "status": "observed",
        "credential": {"status": "available", "owner": "github-cli"},
        "identity": {"status": "verified"},
        "repository_access": {"status": "verified", "capability": "repository-metadata-read"},
    }


def test_existing_observer_keeps_mismatch_shape(account, monkeypatch):
    selected, state, _, _, _ = account
    state["login"] = "other"
    monkeypatch.setattr(native, "SelectedGitHubAccount", lambda *a, **k: selected)
    assert inspect_github_account("github.com", "selected", "example/project") == {
        "status": "unavailable",
        "credential": {"status": "available", "owner": "github-cli"},
        "identity": {"status": "mismatch"},
        "repository_access": {"status": "not-observed"},
    }
