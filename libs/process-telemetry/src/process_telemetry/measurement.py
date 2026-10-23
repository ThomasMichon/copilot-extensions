"""Content-free operation spans, explicitly supplied identity and trace context."""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass

from .emission import SinkSlot

STAGES = frozenset({
    "executable", "runtime_entry", "first_frame", "current_rows", "actions",
    "new", "resume", "preparation", "spawn", "host_ready", "first_prompt",
    "hook_dispatch", "hook", "ipc", "fallback", "cleanup",
})
OUTCOMES = frozenset({"ok", "error", "cancelled", "unavailable"})
_TAG = re.compile(r"[a-z0-9][a-z0-9_.-]{0,63}\Z")
_TRACE = re.compile(r"00-([0-9a-f]{32})-([0-9a-f]{16})-(00|01)\Z")
_COUNTERS = frozenset({
    "cpu_ns", "child_cpu_ns", "peak_memory_bytes", "spawn_count",
    "queue_wait_ns", "lock_wait_ns", "visibility_delay_ns", "stale_generation_ns",
    "git_count", "mux_count", "enumeration_count", "recompute_count", "roster_count",
})
_IDENTITY = frozenset({"source_revision", "payload_version", "runtime_version", "serving_generation"})


@dataclass(frozen=True)
class TraceContext:
    """A bounded, explicitly propagated W3C trace context without baggage."""

    trace_id: str
    span_id: str
    sampled: bool = True

    def __post_init__(self):
        if (
            not re.fullmatch("[0-9a-f]{32}", self.trace_id)
            or not int(self.trace_id, 16)
            or not re.fullmatch("[0-9a-f]{16}", self.span_id)
            or not int(self.span_id, 16)
            or type(self.sampled) is not bool
        ):
            raise ValueError("invalid trace context")

    def traceparent(self) -> str:
        """Serialize correlation only; never treat this as ownership evidence."""
        return f"00-{self.trace_id}-{self.span_id}-{'01' if self.sampled else '00'}"

    @classmethod
    def parse(cls, value: str) -> TraceContext:
        """Accept only the supported bounded version and flag vocabulary."""
        match = _TRACE.fullmatch(value)
        if not match:
            raise ValueError("invalid traceparent")
        return cls(match[1], match[2], match[3] == "01")


@dataclass
class Measurement:
    """One owner-completed span; timestamps are not user or payload content."""

    slot: SinkSlot
    context: TraceContext
    parent_span_id: str | None
    component: str
    stage: str
    started_ns: int
    wall_ns: int
    cpu_ns: int
    identity: dict[str, str]
    finished: bool = False

    def finish(self, outcome: str = "ok", **counters: int) -> None:
        """Emit one closing record; unknown measurements stay absent."""
        if self.finished:
            return
        if outcome not in OUTCOMES:
            raise ValueError("invalid outcome")
        if any(
            key not in _COUNTERS or type(value) is not int or not 0 <= value < 2**63
            for key, value in counters.items()
        ):
            raise ValueError("invalid counter")
        self.finished = True
        duration = max(0, time.perf_counter_ns() - self.started_ns)
        record = {
            "schema": "agent.performance.v1",
            "kind": "span",
            "component": self.component,
            "stage": self.stage,
            "trace_id": self.context.trace_id,
            "span_id": self.context.span_id,
            "parent_span_id": self.parent_span_id,
            "start_time_ns": self.wall_ns,
            "duration_ns": duration,
            "outcome": outcome,
            "cpu_ns": max(0, time.process_time_ns() - self.cpu_ns),
            **self.identity,
            **counters,
        }
        self.slot.emit(record)


def begin(
    slot: SinkSlot, component: str, stage: str, *,
    parent: TraceContext | None = None, sampled: bool = True,
    identity: dict[str, str] | None = None,
) -> Measurement | None:
    """Start an optional measurement; disabled/sampled-out calls have no effects."""
    if not slot.enabled() or not (parent.sampled if parent else sampled):
        return None
    if not _TAG.fullmatch(component) or stage not in STAGES:
        raise ValueError("invalid component or stage")
    if identity is not None and (type(identity) is not dict or len(identity) > len(_IDENTITY)):
        raise ValueError("invalid identity metadata")
    identity = dict(identity or {})
    if any(
        key not in _IDENTITY or type(value) is not str
        or not re.fullmatch(r"[a-zA-Z0-9_.+-]{1,80}", value)
        for key, value in identity.items()
    ):
        raise ValueError("invalid identity metadata")
    context = TraceContext(parent.trace_id if parent else uuid.uuid4().hex, uuid.uuid4().hex[:16])
    return Measurement(
        slot, context, parent.span_id if parent else None, component, stage,
        time.perf_counter_ns(), time.time_ns(), time.process_time_ns(), identity,
    )
