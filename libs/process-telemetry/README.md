# process-telemetry

`agent-process-telemetry` is the dependency-free common callable seam used by
Bridge and Dispatch. It is diagnostic only: sampled/dropped events never replace
durable update, recovery, delivery, approval or process-membership facts.

## Existing consumers

Both plugins retain `set_telemetry_sink`, `clear_telemetry_sink`, `has_sink`,
`emit`, existing factory/config discovery and JSONL spool functions. Their
lifecycle reducers remain plugin-owned. `SinkSlot` extracts their per-consumer
registration and fail-open call, without a new registry or event bus.
Legacy sinks remain **synchronous**. Register a bounded adapter explicitly for
latency-sensitive producers; this release does not silently change existing
sink delivery or automatically configure a collector.

```python
from agent_bridge import telemetry

# Initialization belongs to the existing long-lived owner, never a hook.
writer = telemetry.set_buffered_telemetry_sink(
    telemetry.make_spool_sink(configured_local_file)
)
# Existing lifecycle producers now enqueue through the same public emit API.
# At owner shutdown:
telemetry.clear_telemetry_sink()  # closes owned buffer, no flush or wait
```

One daemon writer drains batches; no per-event process/thread, file write,
JSON serialization, network or sink callback runs on the producer. Bounds are
128 queued records by default plus at most one 32-record drain batch.
The record copier bounds primitive nesting, entry/string lengths and a
conservative serialization-size estimate (4 KiB default). It is not an exact
Python heap-size claim. Contention, overflow, invalid data, inactive writes,
sink exceptions and abandoned records have counters. Snapshot counters need not
be mutually consistent. A permanently stuck sink owns at most this one daemon
thread; no automatic replacements, shutdown joins or retries are introduced.
`join(timeout)` is an optional off-path wait capped at five seconds, not a flush.
Reconfiguration cannot start another owned writer until the previous one has
stopped, including a closed writer still blocked inside a sink.
`delivered` counts successful callback returns, not durable persistence: an
existing spool callback can swallow its own I/O error. Receive-to-persist
qualification requires an independent receiver readback.

The buffer copies primitives but is **not a generic redactor**. Existing
lifecycle reducers retain their own privacy policy. Use `measurement.begin`
for content-free performance events; do not enqueue raw logs or payloads.

## Measurements and reports

`measurement.begin(slot, component, stage, parent=..., identity=...)` returns
an owner-completed `Measurement`, or `None` before clock/UUID/validation work
when disabled or sampled out. `finish(outcome, **counters)` emits at most once.
The caller supplies only an approved stage, opaque correlation context, limited
version metadata and nonnegative known counters. No prompt, argv, path, code,
host/user/repository name, free-text error or environment dump is accepted.

`TraceContext` supports explicit W3C traceparent correlation without baggage.
The owner chooses sampling once; child contexts inherit it. CPU is whole-process
CPU during the interval, **not** CPU attributable exclusively to a concurrent
span. Unknown child CPU/memory must remain absent. Native executable/frame/
prompt observations must come from their real owner, not inferred timestamps.

`report.summarize` is a pure offline helper for a homogeneous workload/stage.
It retains every supplied sample, distinguishes unavailable from zero, refuses
over-counted/malformed input, reports nearest-rank p50/p95/p99/max, and leaves
budget status unknown for incomplete/lossy samples. Fewer than 100 successful
samples cannot qualify p99. Source/installed/serving metadata and workload
provenance remain the enclosing native report owner's responsibility; this
helper alone does not qualify native performance.

## Delivery status and boundaries

This is the bounded JSONL/in-process **intermediate** from the reviewed
[performance telemetry effort](../../efforts/active/performance-telemetry/README.md).
It is not OpenTelemetry implementation or collector qualification. Optional
real SDK/OTLP export and local-receiver proof remain that effort's Phase 3.
There is no SDK import/dependency or telemetry auto-install/start.

Manager first-frame/new-resume and composed hook instrumentation remain with
their existing owners. Short-lived hooks must not create writers: use their
existing owner result/IPC if available, otherwise drop diagnostics. No default
fleet host, shared collector, off-site exporter or storage lifecycle is created.

## Validation

The shared contract tests live in the concrete Bridge consumer's
`tests/test_process_telemetry.py`; both plugins' existing telemetry suites
exercise compatibility. Run the targeted suites through the repository test
runner; thread/process/native work remains outside required unit-only CI.
Tests cover disabled effects, bounded copying, full/contended/blocked/throwing
sinks, no-wait close, trace/privacy/clock semantics and incomplete summaries.
Native source/installed/serving measurements and overhead qualification are
separate acceptance, not asserted by these unit/integration tests.
