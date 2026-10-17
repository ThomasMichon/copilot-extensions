"""Honest foreground preparation stages, without contaminating JSON stdout."""

from __future__ import annotations

import sys
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from . import activity
from . import output

HEARTBEAT_SECONDS = 10
_preview = ContextVar("launch_preview", default=False)


@contextmanager
def preview_mode(enabled: bool) -> Iterator[None]:
    """Keep preview progress visible without recording launch activity."""
    token = _preview.set(enabled or _preview.get())
    try:
        yield
    finally:
        _preview.reset(token)


@contextmanager
def step(label: str, *, enabled: bool = True, worktree_id: str | None = None):
    started = time.monotonic()
    recording = not _preview.get()
    step_id = uuid.uuid4().hex
    stopped = threading.Event()
    def record(state: str, *, error_type: str | None = None):
        if not recording:
            return
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
            if human is not None:
                human.progress(message)
            else:
                print(f"[launch] {label}: {message}", file=sys.stderr, flush=True)

    def heartbeat():
        while not stopped.wait(HEARTBEAT_SECONDS):
            record("waiting")
            report("running" if human is not None
                   else f"still working ({time.monotonic() - started:.0f}s elapsed)")

    record("started")
    with output.human_operation(f"launch/{label}", enabled=enabled) as human:
        if human is None:
            report("starting")
        thread = None
        outcome = "finished"
        error_type = None
        try:
            if enabled:
                thread = threading.Thread(target=heartbeat, name="launch-progress", daemon=True)
                thread.start()
            yield
        except BaseException as exc:
            outcome = "failed"
            error_type = type(exc).__name__
            raise
        finally:
            stopped.set()
            if thread is not None and thread.ident is not None:
                thread.join()
            record(outcome, error_type=error_type)
            if human is not None:
                human.complete("failed" if outcome == "failed" else "done")
            elif enabled:
                report(f"{outcome} ({time.monotonic() - started:.1f}s)")
