"""The full account-resolution snapshot gets headroom a loaded host needs."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock

import pytest

from agent_codespaces import gh_account, worktrees


@pytest.fixture
def run(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    monkeypatch.setattr(worktrees, "explicit_context", lambda: False)
    monkeypatch.setattr(gh_account, "_agent_worktrees_bin", lambda: "aw")
    mock = MagicMock(return_value=subprocess.CompletedProcess([], 0, "", ""))
    monkeypatch.setattr(subprocess, "run", mock)
    return mock


def test_snapshot_read_gets_more_headroom_than_point_lookups(run: MagicMock) -> None:
    gh_account._lookup("repos", "account", "list", "--json", "--with-resolutions", strict=True)
    gh_account._lookup("repos", "account-for", "owner/repo")
    assert [call.kwargs["timeout"] for call in run.call_args_list] == [30, 10]


def test_snapshot_timeout_reports_the_budget_that_expired(run: MagicMock) -> None:
    run.side_effect = subprocess.TimeoutExpired("aw", 30)
    with pytest.raises(gh_account.AccountInventoryError, match="timed out after 30 seconds"):
        gh_account.account_inventory()
