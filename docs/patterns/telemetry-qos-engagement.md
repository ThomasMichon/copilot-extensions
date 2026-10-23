# QoS and Engagement measurement contract

**Status: proposed; no runtime behavior is enabled by this document.**
Design slice: [measurement effort](../../efforts/active/telemetry-measurement-contract/README.md).
Intent: [Portable Telemetry](../../visions/telemetry/README.md).
Operations: [event catalog](../telemetry-event-catalog.md).

## Extend the existing owner

`libs/process-telemetry` already has bounded emission, typed
`agent.performance.v1` measurements and an optional real traces-only OTLP adapter.
Its `component`, `stage`, `outcome`, nanosecond measurements and closed
vocabularies are existing contracts, not this proposal's extensible event model.
Bridge/Dispatch compatibility adapters and Manager's frame-health reporter keep
their owners. This proposal specifies a normalized view and future adapter
requirements; do not send new fields through an unchanged legacy validator,
silently reinterpret legacy outcomes, or collect both views twice.

Structured events and metric mapping remain separate acceptance from traces.
No SDK, sink, collector, process or live hook is installed here. Collection and
export follow the adopting harness's selected sink and policy.

## OpenTelemetry alignment

| Signal | Meaning | Proposed projection |
|--------|---------|---------------------|
| Span | One real operation with start/end and causal context | `Qos` |
| Named LogRecord / Event | One meaningful occurrence | `Engagement`, or operation-start evidence |
| Counter | Additive observations | Starts, completions, outcomes, hook calls and drops |
| Histogram | Distribution of measured values | Duration, queue delay, startup set size |
| Gauge | Current observation, not additive snapshots | Queue depth, oldest ready-task age, current in-flight work |

These domain names and `ce.*` attributes are **custom conventions**, not OTel
standard semantic names. Reuse standard resource/context fields when their
meaning matches. OTLP wire encoding remains the export adapter's responsibility;
neither JSON stdin nor local JSON HTTP is automatically OTLP.

The stable [Trace API](https://opentelemetry.io/docs/specs/otel/trace/api/)
defines spans, context and status. The
[event conventions](https://opentelemetry.io/docs/specs/semconv/general/events/)
and [error-recording guidance](https://opentelemetry.io/docs/specs/semconv/general/recording-errors/)
are marked Development: adopt their documented meaning without claiming those
domain conventions are stable. The stable
[`error.type`](https://opentelemetry.io/docs/specs/semconv/registry/attributes/error/)
attribute is predictable and low-cardinality.

## Ergonomic records

The [proposed JSON Schema](../../schemas/telemetry-qos-engagement.schema.json)
describes these normalized records, not the existing producer's wire format:

```typescript
type ResultType = "Success" | "Failure" | "ExpectedFailure";
type Params = Record<string, string | number | boolean>;
type Common = {
  schemaVersion: 1;
  eventId: string;       // opaque occurrence identity for replay suppression
  component: string;     // registered producer, not a user/machine/repo name
  name: string;          // stable catalog name, never an instance identifier
  operationId?: string;  // required for Qos and QosStart
  traceContext?: {
    traceId: string; spanId: string; traceFlags: number; parentSpanId?: string;
  };
  params?: Params;       // catalog allowlist; not unrestricted user parameters
};
type Qos = Common & {
  kind: "qos";
  operationId: string;
  startTime: string; endTime: string; // UTC RFC3339
  duration: number;                  // seconds, measured monotonically
  resultCode: string; resultType: ResultType;
};
type QosStart = Common & {
  kind: "qos_start"; operationId: string; time: string;
};
type Engagement = Common & {
  kind: "engagement"; time: string;
  category: "user_action" | "lifecycle" | "diagnostic";
};
```

`QosStart` is nonterminal evidence for the **same** catalog operation, not a
second user action. A start marker maps to the named LogRecord
`agent.operation.started`, with `ce.operation.name` identifying the operation;
it is not a completed span or duration sample. `eventId` differs between start/end;
`operationId` joins them. A retry gets a new operation ID and links to the
logical request through an admitted opaque correlation attribute. Export replay
retains the event ID; a repeated transmission is not a new attempt.

JSON Schema validates shape, not privacy, result-code classification, cross-field
time arithmetic or the credibility of an observation. Producer helpers and
catalog-specific tests must enforce those separately.

Projection maps QoS `name` to Span name, Engagement `name` to EventName, and
timestamps to the corresponding OTel fields, `component` to resource
`service.name`, and producer instrumentation
identity to `InstrumentationScope`. Preserve valid trace flags rather than
marking every record sampled. Map approved `params` under the custom `ce.*`
namespace, not arbitrary top-level OTel fields. Bind resource/version metadata
once through existing owner identity; do not make callers supply machine paths.

## Time, context and outcomes

Capture UTC time and a monotonic origin at operation entry. Compute elapsed
duration on that same clock; the exported end timestamp may use the SDK-style
start-epoch-plus-elapsed basis so clock corrections do not produce negative spans.
Document that basis. Never subtract unrelated machines' monotonic clocks.
Persisted queue intervals use the queue owner's authoritative timestamps and
clock provenance; invalid/missing intervals are unavailable, not clamped to zero.

Use seconds for duration histograms, `By` for bytes, annotated counts such as
`{operation}`, and `1` for fractions, following
[OTel metric units](https://opentelemetry.io/docs/specs/semconv/general/metrics/).
Store context percent as 0-100 in event parameters; convert explicitly to 0-1
for utilization metrics. Never mix milliseconds and seconds under one name.

Create real trace/span context at producer entry when tracing is admitted, or
propagate validated W3C context. Do not derive trace IDs from task/session IDs.
Missing trace context permits an event/metric observation but not a sink-created
fictional span. Use links for independently scheduled work where parentage
would misrepresent causality; do not infer context from a shared daemon's last
caller. Exclude baggage and unrestricted environment propagation.

| Result | OTel status | `error.type` | Custom attributes |
|--------|-------------|--------------|-------------------|
| `Success` | `Unset` | absent | `ce.qos.result_type`, `ce.qos.result_code` |
| `ExpectedFailure` | `Error` when the operation failed | bounded failure class | same |
| `Failure` | `Error` | bounded failure class, `_OTHER` if unknown | same |

OTel has no Expected/Unexpected enum. `Failure` is the unexpected/unclassified
bucket; only catalog-declared operation-specific cases qualify as
`ExpectedFailure`. A predictable timeout or missing session is not automatically
expected. For example, probing optional session existence can succeed with
`not_found`, while sending to an admitted live session fails with
`session_not_found`. A successful idempotent no-op stays `Success`, not a failed
operation hidden under `Unset`.

Use stable codes such as `ok`, `already_current`, `cancelled_by_caller`,
`policy_refused`, `transport_disconnected`, `deadline_exceeded`,
`ambiguous_acceptance` and `unknown_error`; each catalog family must declare its
allowed codes/classification before its hooks ship. Preserve structured native
codes as allowlisted attributes when useful; never bucket on error messages.
A recovered retry may have a failed child attempt and successful logical parent.
Do not mark the recovered parent failed or erase its child attempts.

## Start/end and reliability

Start before configuration, discovery, queueing or subprocess work that the
caller actually pays. End at the catalog's meaningful result, not at process
creation, HTTP acceptance, an arbitrary callback, or an exit code of zero.
Use an existing supervisor/owner receipt for completion after the command
returns; an unavailable receipt is a coverage gap.

Emit one start and at most one terminal record per observed attempt. Exception,
refusal, cancellation and deadline paths terminate through the same owner.
For a crash or lost terminal observation, retain the unmatched start. An
independent supervisor may report an observed abort/deadline with its own
provenance, not fabricate a normal child completion. A missing terminal alone
cannot distinguish a running operation, a crash and telemetry loss.

Report starts, successes, expected failures, unexpected failures, still-open,
overdue/unmatched observations and exporter drops separately. For a closed
cohort with `N = S + EF + F + U`, show success coverage `S/N`, observed failure
rate `(EF+F)/N`, unexpected failure rate `F/N`, and unknown share `U/N`.
Also show classified-only denominators explicitly. Do not call `U` a failure
without independent outcome evidence, or silently remove it from success rates.
Live cohorts distinguish ongoing work from expired observation windows.

Metrics used for reliability are recorded independently of trace sampling.
Head-sampled spans alone are not an unbiased denominator. The same operation
must not increment a direct counter and a span-derived counter into the same
series. Record one duration sample at completion, with outcome dimensions;
missing terminals have no invented duration. Report p50/p95/p99/max, sample
count, quantile method and sufficiency; do not average percentiles.

## Dimensions, privacy and cost

Registered operation, component/provider kind, OS, transport kind, cold/warm/
first-touch mode, trigger, cache/authority observation, result and instrumentation
version are useful bounded dimensions. Keep absent/unknown values explicit.
Admit identities once from existing authority, not by hashing or invoking Git
inside an event. Actual source/payload/runtime/serving versions are distinct.

Opaque request/task/session/worktree/source-instance IDs are correlation fields
only, not metric labels. Do not export machine aliases, paths, repository/user
names, prompts, raw arguments, document content, URLs, exception strings or
arbitrary `params`. Registered provider IDs must not incorporate those values.
Attribute allowlists and field/queue bounds apply before enqueueing.

Reuse existing owner queues/reporters. Hooks must not spawn a reporter, import
an SDK or discover a sink per call. Measure the **whole applicable hook roster**
including dispatch, startup, IPC, fallback and cleanup, as well as each provider.
Instrumenting individual hook bodies alone cannot prove composition latency.
Expose telemetry's own sampling, drop, invalid-record and export-failure counts
without recursively instrumenting its delivery.

## Validation gate before hook implementation

For a reproducible shape smoke check, run this Python from the repository root
in a disposable validator environment (for example,
`uv run --no-project --with jsonschema==4.25.1 python`). It needs no runtime SDK,
service, private data or collector:

```python
import json
from jsonschema import Draft202012Validator, FormatChecker

schema = json.load(open("schemas/telemetry-qos-engagement.schema.json"))
Draft202012Validator.check_schema(schema)
validator = Draft202012Validator(schema, format_checker=FormatChecker())
common = dict(schemaVersion=1, eventId="example_1", component="agent-worktrees",
              name="agent.worktrees.create", operationId="operation_1")
qos = dict(common, kind="qos", startTime="2026-10-22T00:00:00Z",
           endTime="2026-10-22T00:00:01Z", duration=1,
           resultCode="ok", resultType="Success")
start = dict(common, kind="qos_start", time=qos["startTime"])
event = dict(common, kind="engagement", name="agent.manager.action.selected",
             component="worktree-manager", time=qos["startTime"], category="user_action")
for record in (qos, start, event):
    validator.validate(record)
for record in (dict(qos, duration=-1), dict(start, resultType="Success"),
               dict(event, unexpected="field")):
    assert not validator.is_valid(record)
```

Prove exact start/end semantics and all outcome exits with controlled clocks;
validate schema, codes, per-event attributes, replay, retries and missing ends.
Inject slow/unavailable sinks and unknown additive progress messages. Compare
disabled/enabled paths for work/latency, not only matching emitted objects.
Exercise actual producer boundaries in the owning native lane. Real OTLP
receiver tests verify span/error/event/metric meaning separately; a successful
HTTP request is not proof of backend persistence or complete signal support.

Preserve the existing [performance measurement](performance-measurement.md)
budgets and source/native/installed distinctions. This design does not amend
any timeout, authorize a collector, or claim runtime adoption.
