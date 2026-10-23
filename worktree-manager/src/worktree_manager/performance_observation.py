"""Explicit source observations; delivery runs only on the existing frame writer."""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from itertools import count
from types import MappingProxyType

_EVENTS = {
    "source_entry": "source_entry",
    "textual_app_start": "runtime_entry",
    "textual_first_refresh": "first_refresh",
    "input_ack": "input_ack",
    "fixture_rows": "fixture_rows",
    "picker_stop": "ui_stop",
}
_IDENTITIES = frozenset({
    "source_revision", "payload_version", "selected_version", "serving_version",
})
_TOKEN = re.compile(r"[a-zA-Z0-9_.+-]{1,80}\Z")


class SourceObservation:
    """Once-admitted source-only metadata and one sampled trace, not a writer.

    A source measurement owner creates this before UI callbacks and passes it
    to FrameHealthReporter. No SDK is imported until that reporter's writer
    calls deliver(). The object never starts a process, thread or listener.
    """

    def __init__(
        self, identity: dict, *, endpoint: str | None = None, sampled: bool = True,
        origin_ns: int | None = None, wall_ns: int | None = None, source_clean: bool | None = None,
        signal_endpoints=None,
    ):
        if type(identity) is not dict or identity.keys() - _IDENTITIES:
            raise ValueError("unsupported observation identity")
        if any(value is not None and (type(value) is not str or not _TOKEN.fullmatch(value))
               for value in identity.values()):
            raise ValueError("invalid observation identity")
        if type(sampled) is not bool or (source_clean is not None and type(source_clean) is not bool):
            raise ValueError("invalid observation flags")
        if endpoint is not None:
            from process_telemetry.otlp import validate_endpoint
            validate_endpoint(endpoint)
        if signal_endpoints is not None:
            from process_telemetry.signals import SignalEndpoints
            if type(signal_endpoints) is not SignalEndpoints or endpoint is not None:
                raise ValueError("explicit signal endpoints cannot accompany legacy endpoint")
        self._identity = MappingProxyType({key: identity.get(key) for key in sorted(_IDENTITIES)})
        self._endpoint = endpoint
        self._signal_endpoints = signal_endpoints if signal_endpoints and signal_endpoints.requested else None
        self._signal_totals = {}
        self._sampled = sampled
        self._source_clean = source_clean
        self._origin_ns = time.perf_counter_ns() if origin_ns is None else origin_ns
        self._wall_ns = time.time_ns() if wall_ns is None else wall_ns
        if any(type(value) is not int or not 0 <= value < 2**63
               for value in (self.origin_ns, self.wall_ns)):
            raise ValueError("invalid observation clock origin")
        self._trace_id = uuid.uuid4().hex
        self._sequence = count(1)
        self._counts = {key: count() for key in (
            "queued", "queue_full", "after_close", "sampled_out", "omitted",
            "written", "exported", "export_failed", "writer_failed", "sdk_unavailable",
        )}
        self._reads = dict.fromkeys(self._counts, 0)
        self._read_lock = threading.Lock()
        self._sink = None
        self._sink_attempted = False

    identity = property(lambda self: self._identity)
    endpoint = property(lambda self: self._endpoint)
    signal_endpoints = property(lambda self: self._signal_endpoints)
    sampled = property(lambda self: self._sampled)
    source_clean = property(lambda self: self._source_clean)
    origin_ns = property(lambda self: self._origin_ns)
    wall_ns = property(lambda self: self._wall_ns)
    trace_id = property(lambda self: self._trace_id)

    def increment(self, key: str, amount: int = 1):
        for _ in range(amount):
            next(self._counts[key])

    def counts(self) -> dict[str, int]:
        """Off-path cumulative snapshot; producer advances never acquire this lock."""
        with self._read_lock:
            result = {}
            for key, iterator in self._counts.items():
                result[key] = next(iterator) - self._reads[key]
                self._reads[key] += 1
            return result

    def capture(self, event: dict) -> dict | None:
        """Project only known source observations; never stringify payload/debug data."""
        if not self.sampled:
            self.increment("sampled_out")
            return None
        name = event.get("event")
        if type(name) is not str or name not in _EVENTS:
            self.increment("omitted")
            return None
        now = time.perf_counter_ns()
        observed = event.get("observed_ns", now)
        if type(observed) is not int or not self.origin_ns <= observed <= now:
            self.increment("omitted")
            return None
        duration = event.get(
            "duration_ns", 0 if name in ("source_entry", "picker_stop")
            else max(0, observed - self.origin_ns),
        )
        outcome = event.get("outcome", "ok")
        if type(duration) is not int or not 0 <= duration <= max(0, observed - self.origin_ns):
            self.increment("omitted")
            return None
        if outcome not in ("ok", "error", "cancelled", "unavailable"):
            self.increment("omitted")
            return None
        span = {
            "schema": "agent.performance.v1", "kind": "span",
            "component": "worktree-manager-source", "stage": _EVENTS[name],
            "trace_id": self.trace_id, "span_id": f"{next(self._sequence):016x}",
            "parent_span_id": None,
            "start_time_ns": self.wall_ns + max(0, observed - self.origin_ns - duration),
            "duration_ns": duration, "outcome": outcome,
        }
        for target, key in (
            ("source_revision", "source_revision"), ("payload_version", "payload_version"),
            ("runtime_version", "serving_version"),
        ):
            if self.identity[key] is not None:
                span[target] = self.identity[key]
        roster_count = event.get("roster_count")
        if type(roster_count) is int and 0 <= roster_count <= 1_000_000:
            span["roster_count"] = roster_count
        return {
            "schema": "manager.source-observation.v1", "event": name,
            "observed_ns": observed, "span": span,
        }

    def deliver(self, stream, records: list[dict]):
        """Called by the existing frame writer only; no additional queue or thread."""
        for record in records:
            record["span"]["queue_wait_ns"] = max(0, time.perf_counter_ns() - record["enqueued_ns"])
            stream.write(json.dumps(record, separators=(",", ":")) + "\n")
            self.increment("written")
        if self.signal_endpoints is not None:
            self._deliver_signals(stream, records)
            return
        if self.endpoint is None:
            return
        if not self._sink_attempted:
            self._sink_attempted = True
            try:
                from process_telemetry.otlp import LocalOtlpSink
                self._sink = LocalOtlpSink(self.endpoint)
            except Exception:
                self.increment("sdk_unavailable")
        if self._sink is None:
            self.increment("export_failed", len(records))
            return
        try:
            self._sink.export_batch([record["span"] for record in records])
        except Exception:
            self.increment("export_failed", len(records))
        else:
            self.increment("exported", len(records))

    def _deliver_signals(self, stream, records):
        from dataclasses import asdict
        from process_telemetry.otlp import LocalOtlpSignalsSink
        from process_telemetry.signals import SIGNALS, SignalIdentity

        if not self._sink_attempted:
            self._sink_attempted = True
            self._sink = LocalOtlpSignalsSink(
                self.signal_endpoints, identity=SignalIdentity(**dict(self.identity)),
            )
        result = self._sink.export_batch([record["span"] for record in records])
        totals = {}
        for signal in SIGNALS:
            current = getattr(result, signal)
            row = asdict(current)
            row["generated_records"] = current.generated_records
            previous = self._signal_totals.get(signal, {})
            totals[signal] = {
                key: value + previous.get(key, 0) if type(value) is int else value
                for key, value in row.items()
            }
            totals[signal]["first_failure"] = previous.get("first_failure") or (
                current.status if current.status not in ("accepted", "accepted_warning", "not_requested") else None
            )
        self._signal_totals = totals
        if self.signal_endpoints.traces:
            self.increment("exported", result.traces.accepted_records)
            self.increment("export_failed", len(records) - result.traces.accepted_records)
        if any(getattr(result, signal).status == "sdk_unavailable" for signal in SIGNALS):
            self.increment("sdk_unavailable")
        stream.write(json.dumps({
            "schema": "manager.signal-batch.v1",
            "trace_id": self.trace_id, "signals": asdict(result),
            "metrics": self._sink.metric_expectations,
        }, separators=(",", ":")) + "\n")

    def close(self):
        """Writer-owned shutdown, never called from a UI callback."""
        if self._sink is not None:
            self._sink.close()

    def summary(self) -> dict:
        """Owner's report metadata; identity availability is explicit."""
        return {
            "trace_id": self.trace_id, "sampled": self.sampled,
            "source_clean": self.source_clean, "identity": dict(self.identity),
            "identity_missing": [key for key, value in self.identity.items() if value is None],
            "export_requested": self.endpoint is not None or bool(
                self.signal_endpoints and self.signal_endpoints.traces
            ), "counts": self.counts(),
            "signals_requested": list(self.signal_endpoints.requested) if self.signal_endpoints else [],
            "signals": {key: dict(value) for key, value in self._signal_totals.items()},
            "clock_domain": "child-perf-counter", "persistence": "not-verified",
        }
