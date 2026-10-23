# Structured logs and metrics — proposed source slice

Back to [Performance Telemetry](README.md). Part of #7935 / #5900.

**Status: design reviewed in #8187; listed source implementation merged in #8228.**
The campaign coordinator accepted the exact shared API and file scope
below. Neither approval enrolls an installed producer
or authorizes collector configuration, ports, installation or restart.

## Request and reconciliation

Public-safe direction, captured from the current request:

> concrete STRUCTURED LOGS + METRICS contract/design only before implementation.
> Reuse existing typed core/OTLP transport/bounded writer seams, no second
> bus/queue/exporter thread/hotpath SDK/network/file/provision.
> Structured logging must be closed-content/schema (no raw message/prompt/code/
> credentials/freeform exception/env/tool payload); metrics bounded labels and
> honest units/temporality/no small-N tail claims.

Additional constraints: preserve prior native reports, environments and historical
wrong-attribution artifacts until their durable copy is verified; treat identity
as data, never authority; no global SDK/environment mutation, ambient destination,
headers or redirects. No existing-agent/chat/remote coordination. The retained
hook clauses and independent Manager, entry, cache and process-custody owners
are unchanged. This text intentionally omits private deployment bindings.

This closes part of [Portable Telemetry](../../../visions/telemetry/README.md)'s
common structured-signal/interoperability intent and preserves
[Process Telemetry](../../../visions/process-telemetry/README.md)'s one-seam
rule. It does not extend either vision. À-la-carte independence, smallest runtime
surface, explicit installation identity and
[performance measurement](../../../docs/patterns/performance-measurement.md)
remain binding. Collector storage and policy stay consumer-owned.

**The mechanism below is agent-recommended**, proposed for coordinator/reviewer
acceptance rather than an assertion that the user selected these exact APIs,
instrument names, bounds or retention numbers.

## First real consumer and ownership

Extend only the already-merged Manager **source observation** consumer:
`SourceObservation.capture/deliver/close/summary`, through the existing
`FrameHealthReporter` queue and writer. The owned native probe's real source
Picker refresh callback and matching Key receipt supply observations; its
headless fixture viewport is still not production row authority. No synthetic
canary can substitute for this consumer's acceptance.

One captured observation remains one queue item. On the same writer, the item
can produce an existing trace, a typed log, and measurements. No signal-specific
queue, background SDK processor, periodic metric reader, retry worker, spool
replay or additional file writer. Existing source JSONL is the only local
observation stream; report summaries carry delivery outcomes off-path.

Do not change constructor call sites, `engine_loading`, `project_config`,
Actions, entry/cache helpers, launchers, process custody or collector installers.
The initial metric stream is scoped to sequential owned source probes, with one
active emitting process per admitted metric stream. Concurrent installed
producers need a separately reviewed stream-identity/aggregation contract; do
not smuggle a run UUID into metric labels or invent a global lock/registry here.

## Exact proposed API

These are **new proposed signatures**, not currently available interfaces:

```python
# process_telemetry.signals: stdlib only
@dataclass(frozen=True)
class SignalEndpoints:
    traces: str | None = None
    logs: str | None = None
    metrics: str | None = None

@dataclass(frozen=True)
class SignalIdentity:
    source_revision: str | None = None
    payload_version: str | None = None
    selected_version: str | None = None
    serving_version: str | None = None

# process_telemetry.otlp: optional SDK, writer construction only
class LocalOtlpSignalsSink:
    def __init__(
        self, endpoints: SignalEndpoints, *,
        identity: SignalIdentity, timeout: float = 2.0,
    ): ...
    def export_batch(self, spans: list[dict]) -> SignalBatchResult: ...
    def close(self) -> None: ...

# SourceObservation: existing args preserved, one new keyword
SourceObservation(identity, *, endpoint=None, signal_endpoints=None, ...)
```

- `endpoint` remains the legacy trace-only shorthand; supplying it together
  with `signal_endpoints` is an admission error, not silent precedence.
- An absent/all-None `signal_endpoints` adds no signal work. Endpoint validation
  and immutable configuration admission happen once before UI callbacks.
- `SignalIdentity` copies the four existing admitted identity fields once,
  validating the same token bounds. It is not a selector or authority proof.
- `LocalOtlpSink` and its single-trace API remain supported. Factor their finite
  HTTP transport into a private helper, not another public client framework.
- `export_batch` consumes 1–32 **existing validated typed spans**. No arbitrary
  log body, instrument name, attributes map, exception, SDK record or URL can
  enter through this API. This first adapter accepts only the fixed Manager
  source component/stage vocabulary; additional consumers need a reviewed
  closed mapping. Manager's projection is checked again at
  the SDK boundary. No general Python `logging.Handler` is installed.
- `SignalBatchResult` is a frozen stdlib result with one entry per requested
  signal: `attempted_records`, `accepted_records`, `rejected_records`,
  `unknown_records`, `not_attempted_records`, and a closed `status`.
  A record means a span, log record or metric **data point**, respectively.
  `generated = accepted + rejected + unknown + not_attempted` is the outcome
  partition; `attempted = accepted + rejected + unknown` is its overlapping
  transport subtotal, not an additional outcome. Omitted/not-requested signals
  are distinct from zero accepted records.
- Status vocabulary: `accepted`, `accepted_warning`, `partial`,
  `unsupported`, `transport_unknown`, `invalid_ack`, `sdk_unavailable`,
  `deadline_exhausted`, `inactive`, `not_requested`. No remote text is retained.
  On partial rejection, only aggregate counts are known, never which records.
- `SourceObservation.summary()` adds per-signal counts/status and missing/
  unavailable fields without renaming existing counters. Legacy `exported`
  remains **trace acceptance**, not “all signals exported” or persistence.
- `SourceObservation.deliver` does the projection and SDK work on its existing
  writer. `close` performs no implicit network flush; it only closes resources.
  Existing nonblocking UI close and separate bounded owner drain stay intact.
- The probe gains explicit optional `--logs-endpoint` and `--metrics-endpoint`
  arguments and optional signal readback inputs; omission preserves trace-only
  behavior. It never derives sibling endpoints from a trace URL or probes ports.

## Closed structured-log schema

Schema `agent.source-observation.log.v1`, one log for each delivered eligible
source observation. Required body is the constant `agent.source.observation`;
event name is the same constant. Severity is a fixed mapping: `error` → ERROR,
`unavailable` → WARN, `ok`/`cancelled` → INFO. Severity text is the corresponding
fixed token. No user-supplied message is accepted.

Resource: exactly `service.name=worktree-manager-source` and
`service.namespace=copilot-extensions`. Scope name/version are fixed adapter
constants, with no scope attributes.

Allowed attributes only:

| Key | Type / meaning |
|---|---|
| `ce.schema` | Constant schema above |
| `ce.component` | Constant `worktree-manager-source` |
| `ce.stage` | `source_entry`, `runtime_entry`, `first_refresh`, `input_ack`, `fixture_rows`, `ui_stop` |
| `ce.outcome` | `ok`, `error`, `cancelled`, `unavailable` |
| `ce.duration_ns` | Optional nonnegative signed-64-bit integer from a measured typed observation; omitted for unavailable duration |
| `ce.queue_wait_ns` | Same bound, measured from enqueue to writer; not pre-enqueue work |
| `ce.roster_count` | Optional integer 0–1,000,000; fixture cardinality only |
| `ce.source_revision`, `ce.payload_version`, `ce.selected_version`, `ce.serving_version` | Optional once-admitted identity tokens, existing 80-character vocabulary |

Trace/span IDs use the actual observation's dedicated OTLP correlation fields,
not attributes, and remain diagnostic correlation only. Timestamp denotes
observation completion; observed timestamp denotes writer observation. Reuse
the once-admitted wall/monotonic anchor, not cross-process clock subtraction.
Unknown identities are omitted and listed as unavailable in the owner report.
They never select a sink, authorize an operation or establish serving equality.

Reject unknown keys/types and non-finite/negative/overflow measurements. Never
stringify unexpected objects. Strip nothing into a disguised freeform field:
raw logs, debug/busy strings, exception messages/stacks, paths, environment,
prompts, code and tool payloads cannot be represented by this schema.

## Metrics: bounded observed population, not inferred totals

Only accepted, sampled source records reaching the writer contribute. Do not
inverse-weight sampling or turn a missing callback/drop into a zero latency.
“Accepted” here is local schema admission, not receiver acknowledgement.

| Instrument | SDK type / unit | Dimensions | Population |
|---|---|---|---|
| `ce.source.observations` | Monotonic Sum, `{observation}` | stage × outcome (6 × 4) | Count of eligible records in this batch |
| `ce.source.duration` | Histogram, `s` | stage × outcome (2 × 4) | Only `first_refresh` and `input_ack` actual durations |
| `ce.source.queue_wait` | Histogram, `s` | stage × outcome (6 × 4) | Measured enqueue-to-writer waits |

Maximum 56 data points per export (24+8+24), from at most 32 input observations.
No run/session/task/worktree/trace/span IDs, revisions, versions, paths, exception
classes or unconstrained strings in metric attributes **or resource dimensions**.
Resource/scope constants match logs. No exemplars in this slice.

All three use **DELTA** temporality. Each writer batch produces one interval:
previous batch collection boundary (initially admitted source origin) to current
collection boundary. Use the admitted clock anchor; enforce nondecreasing,
nonoverlapping intervals within one producer. A duration observation may describe
work begun earlier than that interval; it is recorded once at completion, not
repeated across intervals. A new source process starts a new epoch; sequential
probe ownership prevents overlapping streams. No zero-filled absent series.
The `unavailable` outcome contributes to observation counts, not the duration
histogram or a zero-valued log duration; queue wait can still be independently
measured. Nominal cardinality ceilings above include this reserved outcome even
where a duration point is intentionally absent.

Histogram explicit bounds, in seconds:
`[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10]`.
Retain count, sum, min, max and 13 bucket counts (including +Inf).
Convert nanoseconds to seconds exactly once; assert bucket sum equals count.
No percentile is exported. Reports keep sample counts and the existing
small-N/quantile limits; these buckets do not qualify a latency budget.

Drop/sample/invalid/after-close/writer/export/abandonment counters remain exact
**local owner-report counters**, outside the observed metric population. Do not
export a transport failure through itself or claim remote drop accounting while
the receiver is unavailable. State each counter's snapshot boundary and unknown
tail while the writer is alive. A future terminal health-metric design is a
separate slice, not an implicit zero or this plan's completion claim.

## SDK, protobuf and packaging

Source inspection of the existing isolated **1.45.1** packages supports this
proposal; no SDK execution or wire fixture has been run for this design step.

- Logs: documented public exports `opentelemetry._logs.LogRecord`,
  `SeverityNumber`, `opentelemetry.sdk._logs.ReadableLogRecord`, and
  `opentelemetry.sdk._logs.export.LogRecordExporter/LogRecordExportResult`.
  Construct records on the existing writer and use a private subclass of that
  public exporter extension point. The `_logs` namespace is the SDK's documented
  experimental API, not permission to import its `_internal` implementation.
- The installed canonical log encoder is in **`_log_encoder.py`**, a private
  module. Do not assume a public `logs_encoder` exists or import private helpers.
  Serialize the bounded public SDK log records to official
  `ExportLogsServiceRequest` / `ResourceLogs` / `ScopeLogs` / `LogRecord`
  protobuf classes, with an explicit allowlisted scalar mapping. Test against
  an actual official collector after authorization; handwritten JSON is not
  the protocol or proof. A future public log encoder can replace this narrow
  serialization after review.
- Metrics: public `MetricExporter/MetricExportResult`, `MetricsData`,
  `ResourceMetrics`, `ScopeMetrics`, `Metric`, `Sum`, `Histogram`,
  `NumberDataPoint`, `HistogramDataPoint`, `AggregationTemporality` from
  `opentelemetry.sdk.metrics.export`, and public
  `opentelemetry.exporter.otlp.proto.common.metrics_encoder.encode_metrics`.
  Build those SDK objects from the fixed batch aggregation, not private SDK
  aggregators or a periodic reader.
- Both use `Resource({...})`, not environment/resource detectors. No global
  providers, logging handlers, shutdown registration, propagators, ambient
  SDK limits/temporality/header/endpoint selection or SDK-global mutation.
  Direct models/exporters avoid provider-created threads and exit hooks.
- Reuse the existing optional `agent-process-telemetry[otlp]` extra:
  SDK, proto-common and proto, floor 1.45.1, major upper bound 2.
  Base dependencies remain empty; Manager has no mandatory SDK.
  Qualify the floor and the selected seasoned resolution in isolated tests.
  Because logs are experimental, incompatible future versions fail only the
  requested signal with `sdk_unavailable`; change the supported constraint
  through reviewed packaging if qualification requires it. Never install on use.
- No dependency is added in this plan. Future changes must pass dependency-age,
  standalone materialization/import, both-platform packaging and consumer
  changefile gates. No source-tree dependency in deployed runtime artifacts.

## One finite transport, explicit endpoints

Explicit per-signal literal-loopback HTTP URLs only, with the exact path
`/v1/traces`, `/v1/logs` or `/v1/metrics`. Missing ports, DNS names, userinfo,
query/fragment, proxies, netrc, credentials, redirects and ambient headers are
not accepted. The consumer supplies destinations; no new discovery authority.

Reuse a private finite stdlib transport extracted from `otlp.py`. One attempt
per requested signal/batch, sequentially on the same writer, no retries or
unbounded disk backlog. At most three requests per batch, versus one today.
Use one absolute monotonic batch deadline (default 2s, configurable up to 5s);
start it before mapping, and each blocking operation consumes its remaining
allowance. Bound response reads
and test a slow-drip peer, not just a connection timeout. The existing UI path
never waits; a short owner drain may still report an incomplete writer.

Request cap 256 KiB per signal; response cap 64 KiB. Content type is
`application/x-protobuf`. New log/metric success requires HTTP 200 plus the
matching decodable `ExportLogsServiceResponse` or
`ExportMetricsServiceResponse`; an empty protobuf response is valid success.
Check framing/truncation and the existing contradictory length/chunk rules.
Preserve legacy trace response semantics while extracting the common helper;
do not quietly tighten its compatibility in a logs/metrics feature.

Read `partial_success.rejected_log_records` or `rejected_data_points` and bound
them by the generated count. Zero rejected with a warning is accepted-warning;
nonzero preserves accepted/rejected aggregate counts without identifying them.
Malformed counts/protobuf, wrong content type and oversize/truncated bodies
produce `invalid_ack` with acceptance unknown. Discard remote warning/error
strings; they are not structured producer content.

404/405/501: `unsupported`, not empty-success. Other non-success status, timeout,
disconnect or exhausted budget retains unknown/not-attempted counts as
appropriate; no redirect or Retry-After retry. A failed/unsupported signal
latches inactive for this source-observation lifetime, so later batches count
not-attempted rather than retrying an outage. Other signals remain independent.
Budget exhaustion before a signal's first attempt does not latch that signal;
a later batch can attempt it after the failing sibling has become inactive.
No failed DELTA batch is merged into a later success or resent: known loss and
uncertain receipt remain visible, and cumulative replay cannot double-count it.
Transport acceptance is never durable persistence.

## Collector-owner handshake (future authorization, not action)

The currently selected consumer deployment is trace-only: log/metric routes
return 404. Do not modify it, scan for replacements, change ports/configuration,
wake its owner, or treat a trace canary as log/metric acceptance.

For a later authorized slice, the consumer owner supplies:

1. Exact endpoint/signal support, official collector image/version and lifecycle
   owner; explicit local-only authorization with no off-site exporter.
2. Separate logs/metrics pipelines preserving the closed schema and metric
   units/DELTA temporality. Unknown log body/attrs, resource/scope extras,
   exemplars and unconstrained labels are rejected or sanitized with visible
   rejection accounting. No conversion to traces.
3. Bounded storage proposal: **agent-recommended ceilings**, independently
   approved by that owner: each signal at most 100 MiB retained, 10 MiB segments,
   24-hour age cap and 2 MiB maximum collector batch. Include active, rotated
   and temporary segments in the total. Keep no compression staging outside
   the bound. Stop retaining/drop visibly when storage cannot honor the cap.
   A plain file-exporter rotation flag alone is not proof of byte/age cleanup.
4. Receive/persist/restart/retention readback interfaces and the disposition of
   sanitized/rejected records, so HTTP acknowledgement is not mistaken for
   exact durable receipt. Storage stays outside source/session/OneDrive.

Keep trace pipeline behavior and its existing evidence unchanged. Deployment
qualification is separately owned; this plan neither creates its installer nor
sets a fleet-wide default.

## Proposed files and explicit exclusions

After coordinator acceptance and reviewed plan merge only:

- `libs/process-telemetry/src/process_telemetry/signals.py`: stdlib closed
  endpoints, schema/constants, aggregation/result models.
- `libs/process-telemetry/src/process_telemetry/otlp.py`: compatibility-preserving
  private transport extraction and public composite sink.
- `libs/process-telemetry/src/process_telemetry/_otlp_transport.py` and
  `otlp_signals.py`: finite transport and optional SDK log/metric serializers.
- `worktree-manager/src/worktree_manager/performance_observation.py` and
  `performance_report.py`: same-writer consumer and per-signal accounting.
- `tools/performance/manager_native_probe.py`: explicit optional signal
  arguments/readback; **no process-custody changes**.
- `worktree-manager/tests/test_performance_observation.py`,
  `test_performance_native.py`; `plugins/agent-bridge/tests/test_otlp_telemetry.py`
  plus a focused `test_otlp_signals.py` alongside it: real shared-library
  consumer tests, not an unowned library test runner.
- Existing library README, Manager source-observation doc, TESTING and release
  changefiles; `libs/process-telemetry/pyproject.toml` only if the tested optional
  support matrix requires a reviewed constraint change.

No planned edit to `frame_health.py` itself is needed: its existing
`deliver/close` callbacks suffice. If actual implementation needs that file,
a reserved caller, a new runtime worker or an SDK-private import, return an
exact amended proposal rather than extending scope silently.

## Validation and completion gates

- [x] Coordinator accepts shared API, first-consumer mapping and exact file
  ownership. Plan clears normal source review/CI/merge before any execution.
- [x] Pure stdlib tests: disabled new signals have no extra clock/UUID/import/
  queue/file/network effects; closed schema rejects unknown/hostile values,
  non-finite numbers, overflow and unbounded labels. Trace-only API unchanged.
- [x] Existing writer only: single queue/thread, caller never maps SDK data,
  capture/close races, sampled-out/no-writer path, overflow, writer errors,
  failed drain and no recursive telemetry; all local loss counts retained.
- [x] Optional SDK floor and seasoned-version tests: actual public SDK objects,
  official protobuf decode/type/attributes/timestamps/severity/IDs; sums,
  histogram buckets/count/min/max/units and DELTA interval/reset semantics.
  No instrumentation/global/environment/atexit mutation, ambient poison ignored.
- [x] Isolated port-zero receiver after plan review: real SDK/protobuf requests
  for each signal, exact full/partial/empty ACK, wrong MIME, 404, bad counts,
  malformed/truncated/oversize/slow-drip responses, connection failure and
  deadline/circuit behavior. Verify no retries, no cross-signal success masking
  and counts for not-attempted versus uncertain acceptance.
- [ ] After separate implementation/collector authorization: actual source
  Picker callback/Key observations produce logs and metrics; exact durable log
  correlation/labels and metric counts/intervals/buckets read back independently.
  Metric matching uses the expected instrument, closed labels and exact
  interval/count/bucket tuple recorded in the owner report, not a high-cardinality
  run label. Retain duplicates and missing/extra points as readback failures.
  Do not accept manually shaped JSON, span attrs or synthetic canaries instead.
- [ ] Measure disabled / trace-only / all-signals / outage runs with same
  workload and explicit sampling. Record expected/completed callbacks, actual
  per-signal generated/attempted/accepted/rejected/unknown/persisted counts,
  local drops, request counts/bytes, writer/drain cost and source-process CPU.
  Separate observed queue wait from unavailable lock/descendant measurements.
  Preserve all failures and raw runs; no p99/budget claim from tiny samples.
- [x] Keep required CI pure and scoped; native Windows and Linux validation
  remain explicit integration lanes. Test absent SDK and standalone packaging,
  source gates and source merges; installed adoption/rollout remain separate.

The design episode completed in #8187/#8194; the later admitted source episode
merged in #8228. Isolated source-reporter API/wire controls passed, including
complete and intentional overflow/outage populations. The native/collector
receive and measurement items above remain unchecked: API fixtures do not
substitute for them. Actual Linux-native qualification was not run in the
Windows lane and remains with #7935, as do installed adoption and deployment.
Neither this source slice nor its accounting completes the broader campaign.
