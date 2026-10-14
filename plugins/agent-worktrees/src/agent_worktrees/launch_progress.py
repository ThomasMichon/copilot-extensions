"""Honest foreground preparation stages, without contaminating JSON stdout."""

from __future__ import annotations

import sys
import threading
import time
import uuid
from contextlib import contextmanager

from . import activity

HEARTBEAT_SECONDS = 10


@contextmanager
def step(label: str, *, enabled: bool = True, worktree_id: str | None = None):
    started = time.monotonic()
    step_id = uuid.uuid4().hex
    stopped = threading.Event()

    def record(state: str, *, error_type: str | None = None):
        activity.log_event(
            "launch_preparation",
            worktree_id=worktree_id,
            phase=label,
            step_id=step_id,
            state=state,
            elapsed_ms=round((time.monotonic() - started) * 1000),
            error_type=error_type,
        )

    def report(message: str):
        if enabled:
            print(f"[launch] {label}: {message}", file=sys.stderr, flush=True)

    def heartbeat():
        while not stopped.wait(HEARTBEAT_SECONDS):
            record("waiting")
            report(f"still working ({time.monotonic() - started:.0f}s elapsed)")

    record("started")
    report("starting")
    thread = None
    if enabled:
        thread = threading.Thread(target=heartbeat, name="launch-progress", daemon=True)
        thread.start()
    outcome = "finished"
    error_type = None
    try:
        yield
    except BaseException as exc:
        outcome = "failed"
        error_type = type(exc).__name__
        raise
    finally:
        stopped.set()
        if thread is not None:
            thread.join()
        record(outcome, error_type=error_type)
        report(f"{outcome} ({time.monotonic() - started:.1f}s)")
