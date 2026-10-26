"""Callable-bound execution capability and receiver-first intake negotiation."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .change_hints import ChangeHints

CONTRACT_ID = "agent-index.change-hints"
WORKER_HINT_REFUSAL = "Selected worker does not support change-hints v1"


def execution_versions(selected_run_reindex: object) -> list[int]:
    """Report only v1 support explicitly declared by the selected callable."""
    if not callable(selected_run_reindex):
        return []
    versions = getattr(selected_run_reindex, "change_hints_versions", ())
    if not isinstance(versions, (tuple, list, set, frozenset)):
        return []
    return [1] if any(type(version) is int and version == 1 for version in versions) else []


def selected_engine() -> Callable[..., Any]:
    """Select the actual execution callable, never module-level metadata."""
    from agent_index.indexing import engine

    return engine.run_reindex


def current_execution_versions() -> list[int]:
    """Describe this runtime's execution support without starting an engine."""
    try:
        return execution_versions(selected_engine())
    except ImportError:
        return []


def intake_contract() -> dict[str, Any]:
    """Describe durable intake independently of available engine support."""
    return {
        "id": CONTRACT_ID,
        "intake_versions": [1],
        "execution_versions": current_execution_versions(),
    }


def require_intake_v1(health: dict[str, Any]) -> None:
    """Refuse unadvertised intake before sending any effectful hinted request."""
    contract = health.get("indexing_contract")
    versions = contract.get("intake_versions") if isinstance(contract, dict) else None
    if (
        not isinstance(contract, dict)
        or contract.get("id") != CONTRACT_ID
        or not isinstance(versions, list)
        or not any(type(version) is int and version == 1 for version in versions)
    ):
        raise RuntimeError("Selected service does not advertise change-hints intake v1")


def validate_scope(source: str | None, full: bool, hints: ChangeHints) -> None:
    """Require a typed envelope with the same exact incremental source scope."""
    if type(hints) is not ChangeHints:
        raise ValueError("change_hints must be a ChangeHints value")
    if full or source != hints.source:
        raise ValueError("Change hints require matching exact-source incremental scope")


def require_execution_v1(selected_run_reindex: object) -> None:
    """Refuse unsupported execution without calling an equivalent-looking path."""
    if not execution_versions(selected_run_reindex):
        raise RuntimeError("Selected indexing callable does not support change-hints v1")
