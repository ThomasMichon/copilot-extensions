# Performance telemetry interface proposal

Parent: [Performance Telemetry](README.md). This is the proposed implementation
contract, not a claim that these APIs or measurements already ship.

## Ownership and compatibility

Keep Bridge/Dispatch `set_telemetry_sink`, `clear_telemetry_sink`, `has_sink`,
`emit`, config factories and their lifecycle reducers compatible. Extract only
the common machinery when those concrete adapters adopt it in the same change.
The existing synchronous compatibility API remains explicitly synchronous;
latency-sensitive adopters must select the bounded adapter, not accidentally
run an arbitrary legacy sink on a UI/hook callback.

The shared module is independently packaged/vendored, not imported from a
sibling plugin at runtime. Manager/entry owners supply their own call sites.
Update journals, process membership, admission and recovery stores are not
replaced, inferred or made disposable by these diagnostics.

## Producer contract

An explicitly initialized owner registers one bounded callable adapter around
an existing sink. It owns one background writer for its lifetime. Registration
is not performed per event or from a short-lived hook. Emission copies only
bounded approved scalar fields into a nonblocking queue. Full/contended queues,
invalid/oversize records and unavailable consumers drop observations and count
them; no recursive per-drop log. No event can call a sink, open a file, start a
process, perform installation or contact a network on the producer path.

Disabled emission is a branch/no-op before attribute shaping or clock reads.
No config discovery or optional imports occur there. Shutdown refuses new
records and returns without waiting for a stuck sink. The owner may explicitly
wait with a bounded deadline off the UI/hook path. A blocked writer never holds
the producer lock and never leads to replacement-thread proliferation.

Short-lived hooks do not start writers or exporters. They may return metrics
through an existing owner result/IPC envelope; absent that capability, drop
in-process diagnostics at exit. The native qualification harness independently
measures the entire dispatch.

## Records and correlation

Versioned records carry a stable stage/event name, plugin/verb/source tag,
trace/span/parent identifiers, local operation identifier and sequence,
monotonic duration, outcome and measurement availability. Trace context is
correlation only, not authority. Propagate only a bounded traceparent-compatible
context explicitly to children; no baggage or environment dumps.

Owner-supplied identity metadata includes source revision, payload/runtime
version and serving generation. Read existing authority once at operation
admission, never hash trees or spawn Git to fill an event. Binary/schema version
is compatibility metadata, not ownership incarnation. Optional durable-journal
references do not turn sampled events into recovery evidence.

Default attributes exclude prompts, responses, code, argv, tool inputs/outputs,
paths, repository/host/user/session/worktree names and free-text errors.
Identifiers are opaque local correlation tokens. Metric dimensions use bounded
stage/component/outcome vocabulary, never trace IDs as histogram labels.

Whole-trace sampling is chosen at entry and inherited. Qualification uses full
sampling and rejects incomplete evidence. Cheap owner-provided counters include
queue/lock wait, spawns, enumeration/recompute counts and revision visibility
delay; missing CPU/memory figures are unavailable, never fabricated zero.

## Required stage meanings

| Stage | Actual observation |
|---|---|
| executable start | native executable boundary, not Python module import |
| first interactive frame | visible frame with input acknowledgement, not mount callback |
| current rows visible | authoritative current revision rendered; stale rows do not qualify |
| Actions | gesture to visible usable menu |
| new/resume | gesture, preparation, spawn, host readiness, first prompt separately |
| hook dispatch | complete selected roster including startup, IPC, fallback and cleanup |

The native observer owns end-to-end elapsed time. Overlapping spans are not
summed. If native first-prompt or compositor visibility cannot be observed, the
report says unavailable rather than using child existence or hook exit.

## Sinks and reporting

The first sink remains local JSONL through the existing spool seam, drained
off-path. Retention and lifecycle belong to the selected consumer, not a new
producer service. An optional OpenTelemetry adapter uses the real SDK/exporter;
it is imported only on explicit construction. Local-only destination validation
must reject implicit cloud/default environment destinations. Real isolated
receiver/export tests are required; OTLP-shaped JSON is not SDK qualification.
No shared collector is installed or started by this work without authorization.

Offline reports retain individual samples, failures, expected/completed counts,
quantile method, workload/cache mode/cardinality, source/installed/serving
identity, process metric provenance, drop counts and missing observations.
Small sample sets label tail estimates as insufficient for tail qualification.
Reports distinguish measured, qualified and unavailable.

Performance-impact descriptions name caller/frequency, blocking stages,
process/network/tree/history work, evidence reuse/invalidation and reproducible
measurement. Pure deterministic work-count tests belong in required unit CI;
native cold/warm measurement belongs in explicitly selected integration lanes.
