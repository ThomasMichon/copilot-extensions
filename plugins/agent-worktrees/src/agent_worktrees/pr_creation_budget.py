"""One local-child creation deadline, including retained-receipt settlement."""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
from functools import wraps
import os
import time
from typing import Any, ParamSpec

from agent_procutil.owned_budget import OwnedExecutionBudget

ENVIRONMENT = "AGENT_WORKTREES_FOREIGN_CREATE_DEADLINE_V1"
MAX_TIMEOUT = 75.0
SETTLEMENT_RESERVE = 15.0
_current: ContextVar[OwnedExecutionBudget | None] = ContextVar("foreign-create-budget", default=None)
P = ParamSpec("P")


def remaining(maximum: float, *, provider: bool = False) -> float:
    budget = _current.get()
    if budget is None:
        return maximum
    deadline = budget.execution_deadline if provider else budget.deadline
    return max(0.0, min(maximum, deadline - time.monotonic()))


def bounded(function: Callable[P, dict[str, Any]]) -> Callable[P, dict[str, Any]]:
    @wraps(function)
    def invoke(*args: P.args, **kwargs: P.kwargs) -> dict[str, Any]:
        if _current.get() is not None:
            return function(*args, **kwargs)
        try:
            now = time.monotonic()
            raw = os.environ.get(ENVIRONMENT)
            deadline = now + MAX_TIMEOUT if raw is None else float(raw)
            budget = OwnedExecutionBudget(deadline, SETTLEMENT_RESERVE)
            if deadline <= now or deadline > now + MAX_TIMEOUT:
                raise ValueError("Foreign creation deadline expired or exceeds the supported budget.")
        except ValueError as exc:
            return {"error": str(exc), "publication_state": "not-started", "success": False}
        token = _current.set(budget)
        try:
            return function(*args, **kwargs)
        finally:
            _current.reset(token)

    return invoke
