"""Finite protected native readback of compact terminal attempts; no write path."""

from __future__ import annotations

import copy
import math
import time
from collections.abc import Callable

from .terminal_recovery import TerminalRecoveryStore


def reconcile_terminal(
    store: TerminalRecoveryStore, scope: dict, operation_id: str, *, context_factory: Callable,
    checkpoint: Callable[[], None], expires_at: int, budget_seconds: float,
    clock: Callable[[], float] = time.monotonic,
) -> dict:
    """Reuse the actual owner's pinned transport; unsupported attribution stays unknown."""
    from agent_worktrees.providers.azure_owned_review import ReviewContextUnavailable, protected_review_context

    if not callable(context_factory) or not callable(checkpoint):
        raise ValueError("existing protected recovery factory/checkpoint required")
    if type(budget_seconds) not in (int, float) or not math.isfinite(budget_seconds) or budget_seconds <= 0:
        raise ValueError("finite protected recovery budget required")
    original = store.inspect(scope, operation_id, expires_at=expires_at, checkpoint=checkpoint)
    deadline = clock() + budget_seconds

    def remaining() -> float:
        store.owner.check_current(expires_at, checkpoint)
        value = min(
            deadline - clock(),
            original["recovery"]["deadline_at"] - store.owner.clock(),
            expires_at - store.owner.clock(),
        )
        if value <= 0:
            raise TimeoutError("original recovery deadline or current read budget exhausted")
        return value

    result = {
        "protocol": "agent-pull-requests.terminal-recovery-result/v1",
        "operation_id": operation_id, "scope": copy.deepcopy(original["scope"]),
        "status": "retained", "effects": "none", "entered_writes": 0,
        "recovery": copy.deepcopy(original["recovery"]), "errors": [],
    }
    if not any(action["state"] == "unknown" for action in original["actions"]):
        return {**result, "actions": original["actions"]}
    if store.owner.clock() >= original["recovery"]["deadline_at"]:
        return {**result, "status": "escalation_required", "actions": original["actions"]}
    try:
        remaining()
        with protected_review_context(context_factory, original["scope"], remaining) as context:
            if not callable(getattr(context, "reconcile_comment_identity", None)):
                raise ReviewContextUnavailable("executing protected recovery reader unavailable")
            context.read_state()  # Fresh principal, repository and PR through the pinned transport.
            for action in original["actions"]:
                if action["state"] != "unknown" or action["kind"] != "comment":
                    continue
                proof = context.reconcile_comment_identity(
                    operation_id=operation_id, intent_id=original["intent_id"],
                    action_id=action["id"], body_id=action["body_id"],
                )
                remaining()
                if proof is not None:
                    store.record(original, action["id"], proof, expires_at=expires_at, checkpoint=checkpoint)
                    original = store.inspect(scope, operation_id, expires_at=expires_at, checkpoint=checkpoint)
    except PermissionError:
        raise
    except (ReviewContextUnavailable, TimeoutError, OSError, ValueError):
        result["errors"] = ["original_protected_recovery_unavailable"]
    retained = store.inspect(scope, operation_id, expires_at=expires_at, checkpoint=checkpoint)
    unknown = any(action["state"] == "unknown" for action in retained["actions"])
    return {
        **result,
        "status": (
            "escalation_required" if unknown and store.owner.clock() >= retained["recovery"]["deadline_at"]
            else "unknown" if unknown else "retained"
        ),
        "actions": retained["actions"],
    }
