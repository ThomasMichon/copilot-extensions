"""Optional terminal reader selection; lifecycle adoption is fresh-only."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agent_pull_requests.watch_replay_store import ReplayStore


def core() -> tuple[Any, Any, Any]:
    try:
        from agent_pull_requests.workspace_terminal_read import contract
        from agent_pull_requests.terminal_retention import TerminalRetentionStore
        from agent_pull_requests.terminal_recovery import TerminalRecoveryStore
        from agent_pull_requests.terminal_cache_retirement import TerminalCacheRetirementStore

        value = contract()
        if value != {
            "protocol": "agent-pull-requests.terminal-workspace-read/v1",
            "effects": "none", "retirement": "existing_lifecycle_only",
            "terminal_retention_version": 1, "terminal_recovery_version": 1,
            "terminal_cache_retirement_version": 1,
        } or any(type(value[name]) is not int for name in (
            "terminal_retention_version", "terminal_recovery_version",
            "terminal_cache_retirement_version",
        )):
            raise ValueError("unsupported terminal declaration")
        return TerminalRetentionStore, TerminalRecoveryStore, TerminalCacheRetirementStore
    except (ImportError, AttributeError, TypeError, ValueError, RuntimeError):
        raise ValueError("selected terminal read core unsupported") from None


def adopt(store: ReplayStore, checkpoint: Callable[[], None], expires_at: int) -> None:
    for selected in core():
        checkpoint()
        selected(store).adopt(
            lifecycle_checkpoint=checkpoint, expires_at=expires_at, checkpoint=checkpoint,
        )
    checkpoint()
