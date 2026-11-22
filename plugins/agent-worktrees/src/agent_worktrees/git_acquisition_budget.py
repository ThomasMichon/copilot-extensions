"""Transient deadline shared by creation's Git and credential acquisitions."""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

NETWORK_HARD_TIMEOUT = 300.0
NETWORK_SOFT_TIMEOUT = 30.0

_deadline: ContextVar[float | None] = ContextVar("git_acquisition_deadline", default=None)


@contextmanager
def acquisition_budget(deadline: float) -> Iterator[None]:
    outer = _deadline.get()
    token = _deadline.set(deadline if outer is None else min(outer, deadline))
    try:
        yield
    finally:
        _deadline.reset(token)


def remaining_timeout(timeout: float | None) -> float | None:
    deadline = _deadline.get()
    if deadline is None:
        return timeout
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("worktree source preparation exceeded its fetch deadline")
    return remaining if timeout is None else min(timeout, remaining)
