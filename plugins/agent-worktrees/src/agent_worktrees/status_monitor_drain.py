"""Bounded, payload-free diagnostics for the monitor's existing drain path."""

from __future__ import annotations

import math
import sys
import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path

from zdd import lifecycle

BUSY_REASONS = (
    "sweep", "hook", "classify", "tracking-write", "worktrees-feed", "worktrees-read",
    "repos-map-read", "repos-map-producer",
)
COUNT_FIELDS = (*BUSY_REASONS, "tracking-write-compute", "lifecycle")
_PROGRESS_INTERVAL_S = 5.0


def _finite(value: float | None) -> float | None:
    return value if value is not None and math.isfinite(value) else None


class DrainTrace:
    def __init__(
        self,
        directory: Path,
        *,
        role: str,
        port: int,
        action: str = "drain",
        timeout: float | None = None,
        poll: float | None = None,
        rpc_timeout: float | None = None,
        force: bool | None = None,
        observe: Callable[[], Mapping[str, int]] | None = None,
    ) -> None:
        self.directory = directory
        self.role = role
        self.port = port
        self.action = action
        self.timeout = _finite(timeout)
        self.poll = _finite(poll)
        self.rpc_timeout = _finite(rpc_timeout)
        self.force = force
        self.observe = observe
        self.trace_id = uuid.uuid4().hex
        self.started = time.monotonic()
        self.warned = False

    def emit(
        self,
        phase: str,
        outcome: str = lifecycle.BEGIN,
        *,
        reasons: list[str] | None = None,
        remaining: float | None = None,
        receipt: Mapping[str, object] | None = None,
    ) -> None:
        counts = self.observe() if self.observe is not None else {}
        detail = {
            "trace_id": self.trace_id,
            "role": self.role,
            "control_action": self.action,
            "phase": phase,
            "elapsed_ms": round(max(0.0, time.monotonic() - self.started) * 1000),
            "timeout_s": self.timeout,
            "poll_s": self.poll,
            "rpc_timeout_s": self.rpc_timeout,
            "requested_force": self.force,
            "remaining_ms": (
                round(max(0.0, remaining) * 1000)
                if _finite(remaining) is not None else None
            ),
            "observed_counts": {
                key: counts[key] for key in COUNT_FIELDS
                if key in counts and type(counts[key]) is int and counts[key] >= 0
            },
            "invalid_count_fields": [
                key for key in COUNT_FIELDS
                if key in counts and (type(counts[key]) is not int or counts[key] < 0)
            ],
            "busy_reasons": [key for key in reasons or [] if key in BUSY_REASONS],
            "unknown_busy_reason_count": sum(
                key not in BUSY_REASONS for key in reasons or []
            ),
            "receipt": {
                key: receipt.get(key) if type(receipt.get(key)) is bool else None
                for key in ("drained", "clean", "forced")
            } if receipt is not None else None,
        }
        if lifecycle.record(
            self.directory, "monitor-drain", service="agent-worktrees",
            outcome=outcome, port=self.port, detail=detail,
        ) is None and not self.warned:
            self.warned = True
            try:
                print("status-monitor drain diagnostics unavailable: log-write-failed", file=sys.stderr)
            except (OSError, ValueError):
                # Diagnostics may outlive the caller's warning stream.
                pass

    @contextmanager
    def stage(self, phase: str) -> Iterator[None]:
        self.emit(phase)
        completed = False
        try:
            yield
            completed = True
        finally:
            self.emit(phase, lifecycle.OK if completed else lifecycle.FAIL)


def drain_with_diagnostics(
    *,
    state_lock: AbstractContextManager,
    enter: Callable[[], None],
    cleanup: Callable[[], None],
    busy: Callable[[], list[str]],
    observe: Callable[[], Mapping[str, int]],
    directory: Path,
    port: int,
    timeout: float,
    poll: float,
    force: bool,
) -> dict[str, object]:
    trace = DrainTrace(
        directory, role="daemon", port=port, timeout=timeout, poll=poll,
        force=force, observe=observe,
    )
    with trace.stage("state-lock"):
        with state_lock:
            with trace.stage("close-admission"):
                enter()
    # Preserve the existing clock start: admission/lock waits precede this budget.
    deadline = time.time() + max(0.0, timeout)
    next_sample = time.monotonic()
    completed = False
    try:
        while True:
            sample = time.monotonic() >= next_sample
            if sample:
                trace.emit("retired-cleanup", remaining=deadline - time.time())
            cleanup()
            reasons = busy()
            if sample:
                trace.emit("busy-check", reasons=reasons, remaining=deadline - time.time())
                next_sample = time.monotonic() + _PROGRESS_INTERVAL_S
            if not reasons:
                completed = True
                result: dict[str, object] = {
                    "drained": True, "clean": True, "forced": False, "busy_sessions": [],
                }
                trace.emit(
                    "complete", lifecycle.OK, remaining=deadline - time.time(), receipt=result,
                )
                return result
            if timeout <= 0 or time.time() >= deadline:
                completed = True
                result = {
                    "drained": force, "clean": False, "forced": force,
                    "busy_sessions": reasons,
                }
                trace.emit(
                    "timeout", lifecycle.FAIL, reasons=reasons, remaining=0.0, receipt=result,
                )
                return result
            time.sleep(max(0.05, poll))
    finally:
        if not completed:
            trace.emit("aborted", lifecycle.FAIL, remaining=deadline - time.time())
