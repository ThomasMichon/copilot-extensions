"""Optional fresh-baseline selection against the executing PR core."""

from __future__ import annotations

from typing import Any


def core() -> Any:
    """Resolve the selected core; absence never falls back to another writer."""
    try:
        from agent_pull_requests import generation_associations
        if generation_associations.contract() != {
            "protocol": "agent-pull-requests.workspace-generation-associations/v1",
            "storage_extension": "generation_associations_version:1",
            "selection": "fresh_baseline_only", "legacy_backfill": False,
        }:
            raise ValueError("unsupported generation association declaration")
        return generation_associations
    except (ImportError, AttributeError, TypeError, ValueError, RuntimeError):
        raise ValueError("selected generation association core unsupported") from None
