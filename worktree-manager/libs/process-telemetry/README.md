# process-telemetry

`agent-process-telemetry` is the dependency-free common callable seam used by
Bridge and Dispatch. It is diagnostic only: sampled/dropped events never replace
durable update, recovery, delivery, approval or process-membership facts.

## Existing consumers

Manager's explicit [source observation](../../worktree-manager/docs/source-observation.md)
consumer also uses this schema/exporter, through its **existing frame-health
writer**, not a second buffer or exporter worker. Its `source_entry`,
`first_refresh`, `input_ack`, `fixture_rows` and `ui_stop` stages intentionally
do not claim headed paint, installed startup or production row authority.

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

This is the bounded diagnostic producer from the reviewed
[performance telemetry effort](../../efforts/active/performance-telemetry/README.md).
Base installation has no SDK dependency or telemetry auto-install/start.
Explicit consumers may install the optional `[otlp]` extra through their own
approved dependency/deployment flow. Nothing installs it on a hook or import.

### Optional real OpenTelemetry export

`process_telemetry.otlp.LocalOtlpSink(endpoint, timeout=2)` constructs a real
OpenTelemetry Python SDK `SpanExporter` only when explicitly called. It uses
the official OTLP protobuf encoder and a bounded stdlib HTTP transport. The
stock SDK HTTP exporter constructor parses/logs ambient headers even with
explicit arguments; this adapter uses the public exporter extension point
instead, without mutating process environment or SDK globals.
The endpoint must include a literal loopback IP, explicit port and `/v1/traces`.
No DNS name, implicit environment destination, proxy, netrc credentials,
redirect, query or user-info is accepted. A remote consumer requires a separately
authorized local transport owned elsewhere; this package never creates one.
Resources are explicitly constructed, not discovered from environment/host data.

```python
from process_telemetry import SinkSlot
from process_telemetry.otlp import LocalOtlpSink
from process_telemetry.measurement import begin

sink = LocalOtlpSink(configured_loopback_endpoint)
slot = SinkSlot()
writer = slot.set_buffered(sink.export_batch, batch_sink=True)
measurement = begin(slot, "example-component", "preparation")
if measurement is not None:
    measurement.finish()
# Owner shutdown: stop new work, never wait on UI/hook callbacks.
slot.set(None)
if writer.join(2):  # explicit off-path wait; lost diagnostics are permitted
    sink.close()
```

Batch mode calls the existing callable with up to 32 records; there is no
second SDK processor queue/thread. The exporter validates the typed performance
schema before constructing SDK `ReadableSpan` objects; arbitrary lifecycle
events or raw logs are refused. Export errors propagate to the buffer's
per-record `sink_failed` counter. Each batch gets one HTTP attempt with the
configured timeout (positive, at most five seconds); no retry queue is added.
Non-2xx, malformed/oversize acknowledgements and reported rejected spans fail
export rather than counting as successful delivery.
Timeout is a transport/export budget, not a hard OS cancellation promise;
a blocked call still cannot block producers/close or multiply writers.

Wire contract: OTLP/HTTP protobuf traces, resource keys `service.name` (component)
and `service.namespace` (`copilot-extensions`); span name
`agent.<component>.<stage>`. Span attributes are `ce.component`, `ce.stage`,
`ce.outcome`, `ce.schema`, plus `ce.`-prefixed known counter/version fields from
`measurement.py`. Trace/span/parent identifiers are protocol fields, not metric
dimensions. No descriptions, payload strings or free-form events are exported.

An explicit synthetic canary is available in an environment with `[otlp]`:

```text
python -m process_telemetry.canary --endpoint http://127.0.0.1:<selected-port>/v1/traces --timeout 2
```

It prints generated trace/span IDs only after actual SDK export and explicitly
reports `persistence: not-verified`. The receive owner must independently read
that ID from durable local storage; an HTTP success is not persistence proof.
The canary never starts a listener, discovers an endpoint or installs anything.
Traces are the first interoperability slice. The closed source-signal adapter
below adds real Logs and Metrics protobuf, not a generic log forwarding surface.
Native product spans, overhead qualification and actual adopter receive/persist
proof remain explicit effort acceptance; this is not all-telemetry completion.

Manager first-frame/new-resume and composed hook instrumentation remain with
their existing owners. Short-lived hooks must not create writers: use their
existing owner result/IPC if available, otherwise drop diagnostics. No default
fleet host, shared collector, off-site exporter or storage lifecycle is created.

### Closed Manager source signals

`signals.SignalEndpoints(traces=None, logs=None, metrics=None)` and
`SignalIdentity(source_revision=None, payload_version=None, selected_version=None,
serving_version=None)` are frozen stdlib configuration. Each requested endpoint
is independently explicit, with a literal loopback address, port and matching
`/v1/traces`, `/v1/logs` or `/v1/metrics` path. No sibling URL is inferred.

`otlp.LocalOtlpSignalsSink(endpoints, identity=identity, timeout=2)` is a
**single-writer** adapter, not another queue, worker, provider or logger.
`export_batch(spans)` consumes 1–32 existing typed source spans from one trace;
only the fixed `worktree-manager-source` component and six source stages are
admitted. Identities in a record must agree with the once-admitted metadata.
The existing Manager source writer calls it; Bridge/Dispatch legacy lifecycle
dicts are not newly eligible log content.

Logs use public SDK `LogRecord`, `ReadableLogRecord` and `LogRecordExporter`
with official Logs protobuf classes. No private SDK log encoder is imported.
Body/event name are `agent.source.observation`; `ce.schema` is
`agent.source-observation.log.v1`. Fixed outcome/severity mapping is
ok/cancelled → INFO, error → ERROR, unavailable → WARN. Attributes contain only
component, stage, outcome, measured duration/queue wait, optional fixture roster
and the four admitted identity fields. Unavailable duration is absent, not zero.
Correlation uses actual trace/span fields. Resources contain only service name
and namespace; scope is `copilot-extensions.process-telemetry`, version `1`.
No prompt, raw message, code, exception text, environment, path or arbitrary
attribute map is accepted.

Metrics use public SDK data models, `MetricExporter` and public `encode_metrics`:

| Name | Type / unit | Population |
|---|---|---|
| `ce.source.observations` | monotonic DELTA Sum / `{observation}` | observed eligible records |
| `ce.source.duration` | DELTA Histogram / `s` | actual first-refresh and input-receipt durations |
| `ce.source.queue_wait` | DELTA Histogram / `s` | independently measured queue waits |

Only `ce.stage`/`ce.outcome` labels are admitted; there are at most 56 data
points per batch, no dynamic resource dimensions, identity labels or exemplars.
Histogram bounds are 1, 5, 10, 25, 50, 100, 250, 500 ms and 1, 2, 5, 10 s,
plus +Inf. Count/sum/min/max remain measurements, not a percentile estimate.
Sampling is not inverse-weighted. Local drop/loss counters are not silently
converted into remote measurements.

The metric epoch starts at the observed `source_entry` marker. Missing origin
or a non-advancing collection clock yields not-attempted points, never
a fabricated zero interval. Collection and log observed-time use one
writer-admitted wall/high-resolution monotonic (`perf_counter_ns`) anchor;
source timestamps/durations remain the
source's measurements, with no cross-clock duration subtraction. Subsequent
DELTA intervals begin at the previous collection boundary, even when a batch
is lost. Each explicit source lifetime has one trace and resets its epoch.
Qualification is sequential source processes only, not concurrent installed
metric-stream ownership.

`SignalBatchResult` has one closed `SignalResult` per signal. Generated records
partition into accepted + rejected + unknown + not-attempted; attempted is the
accepted + rejected + unknown subtotal. Units are spans/log records/**metric
data points**, respectively. Request counts and attempted payload bytes are
reported; the latter are not a claim about bytes successfully transferred.

The three signals share one monotonic batch deadline, including mapping time
before transport. No network starts after it expires. The finite transport
budgets each socket operation, including header/body slow-drip reads, uses
256-KiB request and 64-KiB body/72-KiB wire response caps, closes every socket,
and never follows redirects or retries. This is not OS cancellation of an
arbitrarily blocked Python import. UI callbacks/close never wait for delivery.
Logs/Metrics require HTTP 200, protobuf MIME and their matching protobuf ACK.
Partial-success counts remain aggregate (which records failed is unknown);
remote error text is discarded. Trace-only ACK behavior remains compatible.

A failed/unsupported signal becomes inactive for that source lifetime; siblings
remain independent. A signal not attempted because a sibling exhausted the
budget can try on the next batch. No failed DELTA is replayed or carried into a
later success. 404 is unsupported, not accepted. Empty/full/partial ACKs are
transport acceptance only: collector policy filters may still drop accepted
data, so independent exact readback is required for persistence.

The optional floor and current seasoned resolution are SDK/proto/proto-common
1.45.1. Missing/incompatible optional APIs mark the affected signal unavailable;
there is no provider, global SDK/environment mutation, handler or exit hook.
No runtime installs the extra automatically.

## Validation

The shared contract tests live in the concrete Bridge consumer's
`tests/test_process_telemetry.py`; both plugins' existing telemetry suites
exercise compatibility. Run the targeted suites through the repository test
runner; thread/process/native work remains outside required unit-only CI.
Tests cover disabled effects, bounded copying, full/contended/blocked/throwing
sinks, no-wait close, trace/privacy/clock semantics and incomplete summaries.
With `[otlp]` explicitly installed in the isolated test environment,
`tests/test_otlp_telemetry.py` exercises genuine SDK protobuf encoding into an
owned port-zero loopback receiver, file readback/decoding, hostile ambient
export configuration, redirect refusal and receiver outage. Without the extra
that optional module is explicitly skipped, not counted as export proof.
An isolated `-I -S` native control proves base imports work without the SDK.
`tests/test_otlp_signals.py` adds closed-schema/aggregation/count tests and
optional genuine SDK/protobuf loopback fixtures, malformed/partial ACKs,
slow-drip/deadline controls, missing SDK and legacy trace compatibility.
Manager's existing source-observation tests exercise the same writer through
actual SDK export and protobuf-derived JSONL readback. Test receivers own
their port-zero socket/thread and close them; they never configure a shared
collector. These are isolated API/wire controls, not a live native rollout.
Native source/installed/serving measurements and overhead qualification are
separate acceptance, not asserted by these unit/integration tests.
