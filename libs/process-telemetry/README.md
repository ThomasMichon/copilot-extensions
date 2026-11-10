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

The legacy JSONL spools encode a complete record and newline before one native
append write: POSIX `O_APPEND`, Windows `FILE_APPEND_DATA` without
`FILE_WRITE_DATA`. This preserves record boundaries among concurrent threads
and processes writing a local file, without a server, queue, or dropping policy.
Fields, emit-time `ts` defaults and existing timestamps are unchanged; parent
directories are not created. The callbacks remain synchronous and fail-open.
This is not bounded-latency delivery, an `fsync`/durability promise, or a
network-filesystem guarantee. A failed/short write may leave a partial record;
the helper reports failure without retrying a suffix that could interleave.
The callbacks log that uncertainty at debug level, not a successful delivery.
Existing partial tails, rotation/truncation coordination, and writes by older
seek-then-write implementations are not repaired or fenced by this fix.

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
for content-free performance events; do not enqueue raw logs or payloads through
the lifecycle/performance telemetry seam. The separately owned application
adapter below admits only its explicitly shaped local logging fields.

## Optional retained application diagnostics

`process_telemetry.diagnostic_logs` is an optional stdlib-only implementation of
the existing Dispatch JSONL handler, now shared by the actual Bridge and Dispatch
application adapters. Importing the base package still discovers no configuration,
starts no writer and creates no files. Consumers inject their resolved directory,
service/component attribution, timestamp decoder and existing custody context.
The library does not import a plugin or `single_instance_lease`.

The shared writer compares each aware record timestamp with UTC now minus 30
elapsed days, retaining equality and future records. It maintains active and
UTC/unique archived segments, preserves malformed evidence with degraded status,
reclaims recognized interrupted prune copies under custody, and caches validated
unchanged-segment expiry information. It never applies policy to a legacy/native
handle destination. Owners explicitly schedule startup and quiet maintenance;
ordinary emission does no history scan. Dispatch's adapter retains its public
paths, discovery schema and synchronous behavior.
Maintenance attempts are debounced even when storage is unavailable; only an
explicit forced retry bypasses that window.

### Explicit strict pre-effect write

`DiagnosticLog.write_event_strict(event)` is a separate synchronous API, not a
change to `write_event`, logging handlers, legacy/retained spool callbacks or
buffer delivery. `process_telemetry.append.strict_write_capability()` reports
`linux-file-directory-fsync-v1` only when the executing implementation has the
Linux primitives; other platforms return `None` and strict writes raise
`NotImplementedError` before custody or file effects. Package version, a queue
offer, callback count and later readback are not this capability or readiness.
Windows append-only writes are unchanged; no `FlushFileBuffers` or Windows
strict-durability support is claimed.

The caller supplies an already provisioned persistent local directory, bounded
privacy-filtered JSON fields with an explicit aware timestamp, and the same
participating custody owner used for rotation/retention. Strict writes create
no parent directory, perform no history maintenance, and refuse expired or
unparseable timestamps rather than silently skipping them. Equality at the
30-day cutoff and future timestamps retain the existing policy. The encoded
record must be one complete UTF-8 JSONL object, preserve the caller's fields,
contain no nonfinite JSON numbers, and fit **65,536 bytes including newline**.
Field preservation compares exact primitive JSON types recursively, including
booleans versus numbers and integers versus floats. Nonfinite literal tokens
and overflow notation such as `1e400` refuse, as do duplicate object fields,
unsupported input types and lossy custom encoders.
Finite primitive input is validated and deeply snapshotted before any callback.
The timestamp decoder and encoder each receive an independent deep copy;
encoded fields are compared with the untouched snapshot, not an event that
the encoder could modify. Mutations that alter serialized fields refuse
before custody, including timestamp or nested correlation changes.
This bounds the submitted write, not arbitrary caller encoder work or heap use;
owners must bound inputs before encoding.

Under that existing custody, rollover preserves an interrupted old tail, and
the append owner opens the directory and no-follow plain single-link file,
performs exactly one `O_APPEND` write, checks its complete byte count, syncs
the file and then its containing directory, and closes both descriptors before
return. This includes active-file creation/rollover name changes. Any failure,
including custody, encoding, append, short write, sync or close, is reported as
degraded and propagated. A failure can leave a partial or complete record;
there is no suffix retry, rollback, automatic replay or grant to call a backend.

Call the backend only after the direct method returns successfully. An async
owner must await this synchronous operation off the event loop; a timeout or
lost reply is unknown, never permission to continue or repeat accepted work.
Successful return means these kernel sync calls completed, not a hardware
power-loss guarantee, network-filesystem guarantee, mounted-volume proof,
permanent authority ledger or completion of the following effect. Caller
provisioning must durably establish ancestors and exclude nonparticipating
writers, foreign mounts and concurrent namespace replacement. Existing
diagnostic age retention still applies: authoritative task/operation state
remains in its existing journal/database, not this log.

Synthetic syscall and caller-ordering tests live in
`tests/test_strict_diagnostics.py`. The separately selected
`tests/native_strict_diagnostics.py` exercises actual Linux local-file append,
sync, lock contention, interrupted-tail rollover and refusal fixtures. Its
sync failures are injected, not real disk exhaustion. Actual shared-volume,
crash/interrupted-driver, storage-failure and other-platform qualification remain
separate integration work; this API does not automatically wire or admit a
reproducer case. Neither file expands the required unit-only CI packet.

Bridge's optional `BufferedDiagnosticLog` uses the **existing** `BufferedSink`
writer, not another collector or service. Its producer copies bounded primitives
without a blocking logging-handler lock, formatting, file access or history work.
The existing copier limits (including 512 characters per string and supported
primitive types) apply. The logging adapter retains nonprimitive arguments as
explicit type placeholders and clips long primitive strings, without calling
repr/str/equality hooks or serializing exception arguments. Unsupported envelopes,
contention, overflow, failed callbacks and abandoned work remain observable losses,
not lossless delivery. Exception type and up to eight frame
locations are copied without exception objects/arguments, source reads, arbitrary
repr, environment or credentials. Formatting, retention and kernel I/O occur on
that one existing worker. Dynamic or over-512 format width/precision and
over-8192-character formatted results fail the callback, preventing a small
accepted format string from allocating unbounded output.
Every conversion is parsed before interpolation; nested-parenthesis mapping keys
are explicitly unsupported, so they cannot bypass width validation.
Eligible maintenance also emits an ordinary local `diagnostic_buffer_callbacks`
status record through the same retained writer. It is diagnostic history, not a
new status registry, service endpoint or authority receipt; shutdown losses may
remain unreported. Existing Bridge health packets stay unchanged.

`BufferedSink(..., on_start=callback)` optionally performs owner initialization
once on the same worker before consuming records. Existing callers default to
no callback. This avoids depending on a droppable queue offer for startup
maintenance. A callback failure increments `sink_failed`; there is no new retry
or replacement worker. `close()` remains nonjoining/nonflushing; `closed` reports
that state. The owner must not replace a writer still blocked after closure.
Initialization and maintenance callbacks are not persisted log records, and
`delivered` still counts successful callbacks, not receiver-confirmed persistence.
`on_iteration=callback` optionally reuses the same existing per-batch/idle worker
iteration and stop wait for owner maintenance, with no separate timer or task.
Bridge uses that seam so its protected application, CLI orchestrator and health
wire sources, registry and captured evidence remain unchanged. Successful queued
sink callbacks (including explicit maintenance requests) count as `delivered`;
initialization and per-iteration maintenance do not.

See the actual adapters' [Bridge](../../plugins/agent-bridge/docs/diagnostic-logs.md)
and [Dispatch](../../plugins/agent-dispatch/docs/diagnostic-logs.md) contracts.

## Explicit retained-spool API (not legacy-path rotation)

Both consumers expose `telemetry.make_retained_spool_sink(directory)` as a
separate **Python API**. It is not a replacement for `make_spool_sink`, not a new
zero-argument configuration selector, and does not automatically adopt a
previously configured spool.

The existing owner must first provision a **new, exclusively participating**
`<directory>/retained/` namespace, verify that no legacy/nonparticipating writer
targets it, and own maintenance and discovery. The factory never creates parent
directories. Its active files are `agent-{bridge,dispatch}-telemetry.jsonl` under
that child namespace, with the same UTC/unique archive convention. Never point an
old factory at these files or retrofit retention behind an old writer.

```python
from agent_bridge import telemetry

# Explicit caller-owned opt-in after normal owner provisioning/authorization.
sink = telemetry.make_retained_spool_sink(preprovisioned_diagnostics_directory)
sink(already_privacy_filtered_event)
sink.maintain()          # normal owner cadence, including when no events arrive
locations = sink.inventory()
```

The sink copies the producer dict, preserves fields and an existing `ts`, and
adds the same epoch-millisecond default only when `ts` is absent. Retention's
decoder explicitly expects epoch milliseconds; unsupported timestamps remain
unaged/degraded, never guessed or rewritten. The underlying optional `RetainedSpool`
also accepts an explicit owner timestamp decoder. Under the injected custody,
each encoded JSONL record uses the unchanged `append_bytes` one-write primitive:
no suffix retry, no parent creation, no arbitrary envelope or identity fields.
Failures remain advisory. The sink is synchronous, not bounded-latency; an async
producer must explicitly use its existing bounded adapter. Quiet/startup
maintenance is not an autonomous service.

This API is available for deliberate helper/owner adoption. Existing configured
spools retain their original format, append behavior and **unretained** status.
It does not adopt native stdout/stderr, installer attempts, lifecycle/bootstrap
files, authoritative state or container output.
An async owner may construct the sink off its serving path and explicitly use
the existing adapter with `on_start=lambda: sink.maintain(force=True)` and
`on_iteration=sink.maintain`. The sink constructor and direct callable remain
synchronous; these options do not silently alter an old configuration.

Bridge and Dispatch also own an additive configuration adapter:
`{"retained_spool": {"directory": "<absolute base directory>"}}`, mutually
exclusive with legacy `sink`/`spool`. `configured_spool` reuses their existing
slot and buffer, constructs the retained sink on that worker after checking the
preprovisioned exclusive child namespace, and supplies startup/busy/idle hooks.
Their local `logs` inventories report configured paths with unknown live health,
without importing arbitrary factories or reading event payloads. Old factories
remain synchronous and unchanged; explicit clear/lifecycle closure remains
nonjoining, and a still-running previous buffer prevents replacement.
Configured buffers opt into `stamp_missing_ts=True`: only absent timestamps are
generated on their already-bounded producer snapshot, before enqueue, with budget
reserved for that field. Existing timestamps and default buffer behavior remain
unchanged. Configured cleanup checks the slot's actual owned buffer type and
clears it atomically under its existing configuration lock; there is no second
plugin-global cleanup reference to go stale after rejected or concurrent reloads.

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
Correlation uses actual trace/span fields. All three signals' Resources contain
service name, namespace and `service.instance.id`: one opaque random UUID per
configured signals sink, stable across its exports and distinct from other sinks
in the same process. It is not derived from trace, session or source metadata
and is never a datapoint or record attribute. Scope is
`copilot-extensions.process-telemetry`, version `1`.
No prompt, raw message, code, exception text, environment, path or arbitrary
attribute map is accepted.

Metrics use public SDK data models, `MetricExporter` and public `encode_metrics`:

| Name | Type / unit | Population |
|---|---|---|
| `ce.source.observations` | monotonic DELTA Sum / `{observation}` | observed eligible records |
| `ce.source.duration` | DELTA Histogram / `s` | actual first-refresh and input-receipt durations |
| `ce.source.queue_wait` | DELTA Histogram / `s` | independently measured queue waits |

Only `ce.stage`/`ce.outcome` labels are admitted; there are at most 56 data
points per batch, no identity labels or exemplars. The resource instance UUID
distinguishes independently owned DELTA streams without adding a datapoint label.
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
their port-zero socket/thread and close them. Manager readback accepts legacy
service-only Resources or a canonical instance UUID, rejecting malformed instance
IDs and unexpected Resource attributes. These controls never configure a shared
collector and are isolated API/wire controls, not a live native rollout.
Native source/installed/serving measurements and overhead qualification are
separate acceptance, not asserted by these unit/integration tests.
