"""Optional terminal reader selection; lifecycle adoption is fresh-only."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agent_pull_requests.watch_replay_store import ReplayStore
from agent_pull_requests.watch_replay import ObservationTarget


def core() -> tuple[Any, Any, Any]:
    try:
        from agent_pull_requests.workspace_terminal_read import contract, target_capture_contract
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
        if target_capture_contract() != {
            "protocol": "agent-pull-requests.terminal-target-capture/v1",
            "phase": "before_acquisition", "budget": "request_checkpoint_and_expiry",
        }:
            raise ValueError("unsupported terminal target capture")
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


def mapping_core() -> Any:
    try:
        from agent_pull_requests.terminal_comment_mapping import TerminalCommentMappingStore
        from agent_pull_requests.workspace_terminal_mapping import contract

        value = contract()
        if value != {
            "protocol": "agent-pull-requests.terminal-comment-mapping-reader/v1",
            "operations": ["retained-comment"],
            "result_protocol": "agent-pull-requests.terminal-comment-observation/v1",
            "representations": ["active", "compact"],
            "terminal_comment_mapping_version": 1, "effects": "none",
        } or type(value["terminal_comment_mapping_version"]) is not int:
            raise ValueError("unsupported terminal mapping declaration")
        return TerminalCommentMappingStore
    except (ImportError, AttributeError, TypeError, ValueError, RuntimeError):
        raise ValueError("selected terminal mapping read core unsupported") from None


def target(
    store: ReplayStore, account: str, repo: str, number: int, *,
    expires_at: int, checkpoint: Callable[[], None],
) -> ObservationTarget:
    checkpoint()
    targets = store.observation_targets(expires_at=expires_at, checkpoint=checkpoint)
    checkpoint()
    return next(
        (value for value in targets if (value.account, value.repo, value.number) == (
            account, repo, number,
        )),
        ObservationTarget(account, repo, number, frozenset()),
    )
