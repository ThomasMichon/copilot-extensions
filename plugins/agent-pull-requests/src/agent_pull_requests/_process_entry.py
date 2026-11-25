"""Dedicated process entry; Python embeddings retain their original streams."""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
import sys

_STDIO: ContextVar[tuple[object, object] | None] = ContextVar("workspace_process_stdio", default=None)


def streams() -> tuple[object, object]:
    selected = _STDIO.get()
    return selected if selected is not None else (sys.stdin, sys.stdout)


def run(entry: Callable[[], int]) -> int:
    """Select raw handles only at the dedicated, fresh-process entry boundary."""
    incoming = getattr(sys.stdin, "buffer", sys.stdin)
    outgoing = getattr(sys.stdout, "buffer", sys.stdout)
    token = _STDIO.set((
        getattr(incoming, "raw", incoming), getattr(outgoing, "raw", outgoing),
    ))
    try:
        return entry()
    finally:
        _STDIO.reset(token)


def main() -> int:
    from .__main__ import main as command
    return run(command)
