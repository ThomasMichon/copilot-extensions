"""Original active-operation readback through its existing protected owner."""

from __future__ import annotations

import copy
import math
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any

from .watch_replay_store import ReplayStore

PROTOCOL = "agent-pull-requests.retained-active-review-recovery/v1"
Document = dict[str, Any]


def recovery_contract() -> Document:
    """Declare the executing library reader, not host selection or authority."""
    return {
        "protocol": PROTOCOL, "intent_versions": [1],
        "operations": ["inspect", "reconcile"], "effects": "none",
    }


def reconcile_review(
    store: ReplayStore, scope: Document, operation_id: str, *,
    context_factory: Callable[[], AbstractContextManager[Any]],
    checkpoint: Callable[[], None], expires_at: int, budget_seconds: float,
    clock: Callable[[], float] = time.monotonic,
) -> Document:
    """Read back original unknown attempts; never enter admission or a sender."""
    from agent_worktrees.providers.azure_owned_review import (
        ReviewContextUnavailable, protected_review_context,
    )

    if not callable(context_factory) or not callable(checkpoint):
        raise ValueError("existing protected recovery factory/checkpoint required")
    if type(budget_seconds) not in (int, float) or not math.isfinite(budget_seconds) or budget_seconds <= 0:
        raise ValueError("finite protected recovery budget required")
    deadline = clock() + budget_seconds

    def remaining() -> float:
        store.check_current(expires_at, checkpoint)
        value = min(deadline - clock(), expires_at - store.clock())
        if value <= 0:
            raise TimeoutError("original recovery invocation budget exhausted")
        return value

    def bounded() -> None:
        remaining()

    original = store.inspect_retained_review(
        operation_id, scope, expires_at=expires_at, checkpoint=bounded,
    )
    bounded()
    result: Document = {
        "protocol": PROTOCOL, "operation_id": operation_id,
        "scope": copy.deepcopy(original["intent"]["scope"]),
        "status": "retained", "effects": "none", "entered_writes": 0, "errors": [],
    }
    if not any(action["state"] == "unknown" for action in original["actions"]):
        return {**result, "actions": original["actions"]}
    try:
        remaining()
        with protected_review_context(context_factory, original["intent"]["scope"], remaining) as context:
            def current() -> None:
                context.check()
                remaining()

            context.read_state()
            for action, state in zip(original["intent"]["actions"], original["actions"]):
                if state["state"] != "unknown":
                    continue
                remaining()
                proof = context.reconcile(copy.deepcopy(original), copy.deepcopy(action))
                context.check()
                remaining()
                if proof is not None:
                    if not isinstance(proof, dict) or proof.get("origin") != "attributable_readback":
                        raise ValueError("original recovery requires attributable native readback")
                    store.record_review_receipt(
                        operation_id, original["intent"]["scope"], action["id"], state["attempt_id"], proof,
                        expires_at=expires_at, checkpoint=current,
                    )
    except PermissionError:
        raise
    except (ReviewContextUnavailable, TimeoutError, OSError, ValueError):
        result["errors"] = ["original_protected_recovery_unavailable"]
    retained = store.inspect_retained_review(
        operation_id, scope, expires_at=expires_at, checkpoint=bounded,
    )
    bounded()
    return {
        **result,
        "status": "unknown" if any(action["state"] == "unknown" for action in retained["actions"]) else "retained",
        "actions": retained["actions"],
    }
