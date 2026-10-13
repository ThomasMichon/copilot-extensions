"""Honest foreground preparation stages, without contaminating JSON stdout."""

from __future__ import annotations

import sys
import threading
import time
from contextlib import contextmanager

HEARTBEAT_SECONDS = 10


@contextmanager
def step(label: str, *, enabled: bool = True):
    if not enabled:
        yield
        return
    started = time.monotonic()
    stopped = threading.Event()

    def report(message: str):
        print(f"[launch] {label}: {message}", file=sys.stderr, flush=True)

    def heartbeat():
        while not stopped.wait(HEARTBEAT_SECONDS):
            report(f"still working ({time.monotonic() - started:.0f}s elapsed)")

    report("starting")
    thread = threading.Thread(target=heartbeat, name="launch-progress", daemon=True)
    thread.start()
    outcome = "finished"
    try:
        yield
    except BaseException:
        outcome = "failed"
        raise
    finally:
        stopped.set()
        thread.join()
        report(f"{outcome} ({time.monotonic() - started:.1f}s)")
