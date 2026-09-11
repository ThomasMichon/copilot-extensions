"""Workspace discovery hides captured children without changing interactive SSH."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

import agent_procutil
import pytest

from agent_codespaces.__main__ import _discover_workspace_folder, _interactive_ssh

SPACE = {
    "name": "cs-example",
    "repository": "example/repo",
    "state": "Available",
}


@pytest.mark.parametrize("windows", [False, True], ids=["posix", "windows"])
@pytest.mark.parametrize("account", [None, "example-account"])
def test_workspace_discovery_uses_headless_kwargs(monkeypatch, windows, account):
    monkeypatch.setattr(agent_procutil, "_is_windows", lambda: windows)
    env = {"EXAMPLE_ACCOUNT": account} if account else None
    spaces = [
        {**SPACE, "state": "Shutdown"},
        {**SPACE, "repository": "example/other"},
        {**SPACE, "account": account},
    ]
    result = subprocess.CompletedProcess([], 0, " /workspaces/example\n", "")
    with (
        patch("agent_codespaces.gh_account.env_for_account", return_value=env) as auth,
        patch("subprocess.run", return_value=result) as run,
    ):
        assert _discover_workspace_folder(spaces, "example/repo") == "/workspaces/example"

    if account:
        auth.assert_called_once_with(account)
    else:
        auth.assert_not_called()
    flags = {"creationflags": agent_procutil._CREATE_NO_WINDOW} if windows else {}
    run.assert_called_once_with(
        ["gh", "codespace", "ssh", "-c", "cs-example", "--",
         'printf %s "$WORKING_DIRECTORY"'],
        capture_output=True,
        text=True,
        timeout=45,
        env=env,
        **flags,
    )


@pytest.mark.parametrize(
    "error",
    [FileNotFoundError("gh unavailable"), subprocess.TimeoutExpired("gh", 45)],
    ids=["missing-gh", "timeout"],
)
def test_workspace_discovery_keeps_errors_best_effort(error):
    with patch("subprocess.run", side_effect=error) as run:
        assert _discover_workspace_folder([SPACE], "example/repo") is None
    assert run.call_args.kwargs["timeout"] == 45


@pytest.mark.parametrize("returncode,stdout", [(1, ""), (0, "relative/path")])
def test_workspace_discovery_rejects_failed_or_invalid_results(returncode, stdout):
    result = subprocess.CompletedProcess([], returncode, stdout, "")
    with patch("subprocess.run", return_value=result):
        assert _discover_workspace_folder([SPACE], "example/repo") is None


def test_workspace_discovery_does_not_change_interactive_ssh():
    with (
        patch("agent_codespaces.lifecycle.account_for_codespace", return_value=None),
        patch("subprocess.call", return_value=0) as call,
    ):
        assert _interactive_ssh("cs-example", []) == 0
    call.assert_called_once_with(
        ["gh", "codespace", "ssh", "-c", "cs-example"], env=None
    )
