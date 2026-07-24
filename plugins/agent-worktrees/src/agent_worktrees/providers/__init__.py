"""PR provider plugins for agent-worktrees (Gitea / GitHub / Azure DevOps)."""

from __future__ import annotations

from .base import (
    ProviderError,
    PRProvider,
    PRScope,
    PullResult,
    account_token_for_slug,
    get_provider,
    resolve_token,
    run_cli,
    scope_from_create_result,
)

__all__ = [
    "PRProvider",
    "PRScope",
    "ProviderError",
    "PullResult",
    "account_token_for_slug",
    "get_provider",
    "resolve_token",
    "run_cli",
    "scope_from_create_result",
]
