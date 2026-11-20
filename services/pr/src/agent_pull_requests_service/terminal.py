"""Optional terminal reader selection and explicit existing-owner adoption."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agent_pull_requests.watch_replay_store import ReplayStore
from agent_pull_requests.watch_replay import ObservationTarget


def core() -> tuple[Any, Any, Any]:
    try:
        from agent_pull_requests.workspace_terminal_read import contract, target_capture_contract
        from agent_pull_requests.terminal_retention import TerminalRetentionStore, native_watch_contract
        from agent_pull_requests.terminal_recovery import TerminalRecoveryStore
        from agent_pull_requests.terminal_cache_retirement import TerminalCacheRetirementStore
        from agent_pull_requests.watch_admission import contract as admission_contract

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
        if admission_contract() != {
            "protocol": "agent-pull-requests.terminal-watch-admission/v1",
            "profile": "urn:agent-pull-requests:terminal-watch-admission:v1",
            "discovery": "watch_capabilities_content_type",
        }:
            raise ValueError("unsupported terminal watch admission discovery")
        if native_watch_contract() != {
            "protocol": "agent-pull-requests.terminal-native-watches/v1",
            "observations": "native_only", "workspace_deadlines": "sweep_before_retirement",
        }:
            raise ValueError("unsupported terminal native watch preservation")
        return TerminalRetentionStore, TerminalRecoveryStore, TerminalCacheRetirementStore
    except (ImportError, AttributeError, TypeError, ValueError, RuntimeError):
        raise ValueError("selected terminal read core unsupported") from None


def retained_cache_supported() -> bool:
    """Unknown optional support keeps the existing bootstrap-only contract."""
    try:
        from agent_pull_requests.retained_live_cache import contract
        return contract() == {
            "protocol": "agent-pull-requests.retained-cache-metadata-adoption/v1",
            "source": "original_first_epoch", "effects": "no_observations_or_bindings",
        }
    except (ImportError, AttributeError, TypeError, ValueError, RuntimeError):
        return False


def active_review_core() -> None:
    """Require the executing readers, not a package version or body-read profile."""
    try:
        from agent_pull_requests.retained_review_adoption import contract
        from agent_pull_requests.review_recovery import recovery_contract
        from agent_pull_requests.workspace_retained_review import contract as inspection_contract
        if contract() != {
            "protocol": "agent-pull-requests.retained-active-review-adoption/v1",
            "storage_extension": "retained_active_review_version:1",
            "operations": "original_active_v1", "admission": "permanently_recovery_only",
            "effects": "none",
        } or recovery_contract() != {
            "protocol": "agent-pull-requests.retained-active-review-recovery/v1",
            "intent_versions": [1], "operations": ["inspect", "reconcile"], "effects": "none",
        } or inspection_contract() != {
            "protocol": "agent-pull-requests.retained-active-review-inspection/v1",
            "operations": ["inspect"], "representation": "original_active_v1",
            "storage_extension": "retained_active_review_version:1",
            "effects": "none", "reconciliation": False,
        }:
            raise ValueError("unsupported original active review declaration")
    except (ImportError, AttributeError, TypeError, ValueError, RuntimeError):
        raise ValueError("selected retained active review core unsupported") from None


def adopt(
    store: ReplayStore, checkpoint: Callable[[], None], expires_at: int, *,
    retained_cache: Any = None, retained_targets: list[dict[str, Any]] | None = None,
) -> None:
    for index, selected in enumerate(core()):
        checkpoint()
        options = (
            {"retained_cache": retained_cache, "retained_targets": retained_targets}
            if index == 0 and retained_cache is not None else {}
        )
        selected(store).adopt(
            lifecycle_checkpoint=checkpoint, expires_at=expires_at, checkpoint=checkpoint,
            **options,
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

    def capture(document: dict[str, Any]) -> ObservationTarget:
        from agent_pull_requests.terminal_retention import replay_epoch

        selected = ObservationTarget(account, repo, number, frozenset(
            record["registration_id"] for record in document["replay"]["records"]
            if record["outcome"] == "active" and record["scope"]["account"] == account
            and record["scope"]["repo"] == repo and record["number"] == number
        ))
        return ObservationTarget(
            account, repo, number, selected.registration_ids, replay_epoch(document, selected),
        )

    return store._review_transaction(
        capture, expires_at=expires_at, checkpoint=checkpoint, persist=False,
    )
