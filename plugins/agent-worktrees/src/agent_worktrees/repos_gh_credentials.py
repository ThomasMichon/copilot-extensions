"""Require a configured GitHub identity before the CLI can issue a request."""
from __future__ import annotations

import re

from agent_procutil.owned_budget import OwnedExecutionBudget

from .github_account_observation import SelectedGitHubAccount
from .github_credentials import account_selection


def environment(target: str) -> dict[str, str]:
    """Keep credential acquisition and identity verification with their owner."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", target):
        raise ValueError("Repository-scoped GitHub access requires an owner/repository target")
    selection = account_selection(target)
    if not selection.configured or not selection.login:
        raise ValueError("Repository-scoped GitHub access requires a configured account")
    selected = SelectedGitHubAccount(
        "github.com", selection.login, target,
        budget=OwnedExecutionBudget.from_timeout(18, cleanup_reserve=2),
        checkpoint=lambda: None,
    )
    return selected.verified_environment()
