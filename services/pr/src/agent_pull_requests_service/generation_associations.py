"""Optional fresh-baseline selection against the executing PR core."""

from __future__ import annotations

from typing import Any


def core(*, native_targets: bool = False) -> Any:
    """Resolve the selected core; absence never falls back to another writer."""
    try:
        from agent_pull_requests import generation_associations
        if generation_associations.contract() != {
            "protocol": "agent-pull-requests.workspace-generation-associations/v1",
            "storage_extension": "generation_associations_version:1",
            "selection": "fresh_baseline_only", "legacy_backfill": False,
        }:
            raise ValueError("unsupported generation association declaration")
        if native_targets:
            from agent_pull_requests.generation_native_targets import contract
            if contract() != {
                "protocol": "agent-pull-requests.workspace-native-generation-associations/v1",
                "storage_extension": "generation_associations_version:2",
                "selection": "fresh_baseline_only", "capture": "before_acquisition",
                "legacy_backfill": False, "terminal_adoption": False,
            }:
                raise ValueError("unsupported native generation association declaration")
        return generation_associations
    except (ImportError, AttributeError, TypeError, ValueError, RuntimeError):
        raise ValueError("selected generation association core unsupported") from None


def recorded_core(*, native_targets: bool = False) -> None:
    """Combined selection requires the executing transition reader, not its writer alone."""
    core(native_targets=native_targets)
    try:
        from agent_pull_requests.recorded_terminal_cache import contract
        if contract() != {
            "protocol": "agent-pull-requests.recorded-terminal-cache-adoption/v1",
            "storage_extension": "recorded_terminal_cache_version:1",
            "bindings": "recorded_workspace_epochs_only", "native_targets": False,
        }:
            raise ValueError("unsupported recorded cache declaration")
        if native_targets:
            from agent_pull_requests.recorded_native_cache import contract as native_contract
            if native_contract() != {
                "protocol": "agent-pull-requests.recorded-native-cache-adoption/v1",
                "storage_extension": "recorded_terminal_cache_version:2",
                "bindings": "original_confirmed_native_targets", "legacy_backfill": False,
            }:
                raise ValueError("unsupported recorded native cache declaration")
    except (ImportError, AttributeError, TypeError, ValueError, RuntimeError):
        raise ValueError("selected recorded terminal cache core unsupported") from None
