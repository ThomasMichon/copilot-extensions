"""Flushed, content-free timings for scheduled sync phase diagnosis."""

from __future__ import annotations

import sys
import time


def timed_call(name, function, *args, **kwargs):
    started = time.monotonic()
    print(f"session-sync: phase={name} started", file=sys.stderr, flush=True)
    outcome = "interrupted"
    try:
        result = function(*args, **kwargs)
        outcome = "finished"
        return result
    finally:
        print(
            f"session-sync: phase={name} {outcome} elapsed={time.monotonic() - started:.3f}s",
            file=sys.stderr, flush=True,
        )
