"""One optional command deadline for sequential read-only HTTP observations."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import time
from typing import Iterator

_deadline: ContextVar[float | None] = ContextVar("bridge_read_deadline", default=None)


class ReadDeadlineExpired(Exception):
    """The observer stopped waiting; the target's state is not confirmed."""


def remaining() -> float | None:
    deadline = _deadline.get()
    if deadline is None:
        return None
    seconds = deadline - time.monotonic()
    if seconds <= 0:
        raise ReadDeadlineExpired("bridge observation deadline expired")
    return seconds


@contextmanager
def command_deadline(timeout: float | None) -> Iterator[None]:
    parent = _deadline.get()
    deadline = time.monotonic() + timeout if timeout else None
    if parent is not None:
        deadline = min(parent, deadline) if deadline is not None else parent
    token = _deadline.set(deadline)
    try:
        yield
    finally:
        _deadline.reset(token)


def pause(seconds: float) -> None:
    left = remaining()
    time.sleep(min(seconds, left) if left is not None else seconds)
    remaining()
