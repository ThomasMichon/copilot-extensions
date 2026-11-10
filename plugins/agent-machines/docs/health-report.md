# Local telemetry reports

`agent-machines health report` is an explicit, bounded offline consumer of
existing local telemetry. It starts no collector, scheduler or service, never
changes telemetry configuration, and does not discover or publish to OneDrive.

```text
agent-machines health report --input <local-jsonl> --input <another-local-jsonl>
agent-machines health report --input <local-jsonl> --chart <new-chart.svg>
agent-machines health report --store-dir <absolute-local-health-store> --service agent-dispatch --chart <new-chart.svg>
```

The command writes `agent-machines.health-report.v1` JSON to stdout. `--now`
accepts an aware ISO timestamp for a reproducible observation cutoff; the
default is current UTC time. `--chart` creates a new self-contained SVG and
refuses an existing destination, including an input file. Parent directories
are not created. Nothing opens a browser or sends network traffic.

## What is measured

- Typed `agent.performance.v1` spans use their source completion time
  (`start_time_ns + duration_ns`), component, known stage and outcome. Successful
  observed durations provide descriptive p50/p95/max per homogeneous group.
- Existing Bridge session transition/health records and Dispatch task
  lifecycle records contribute only closed-vocabulary counts. Their explicit
  `ts` is epoch milliseconds, not nanoseconds. Abandoned tasks are not silently
  called transport errors, and lifecycle records do not acquire a duration.
- Other schemas, malformed/partial records, missing timing, future timestamps
  and expired data have explicit coverage counters. Unknown raw fields,
  identifiers, routing metadata, messages and exception text are not copied.

There are 168 absolute UTC-hour buckets, including the current hour. Empty
buckets say `no-observation`; zero observed events is not a measured healthy
interval. Producer population and loss remain unknown, p99 is not qualified,
and availability is not inferred from event silence. This is neither a complete
agent inventory nor an OpenTelemetry/OTLP receiver.

The chart plots observed counts/errors and separate successful-duration p95
series for each compatible timing group. It leaves gaps and displays scan
coverage. It does not merge stage percentiles into a fleet latency metric.

## Bounds and input behavior

Each invocation accepts at most 16 explicit regular local files, 8 MiB total,
20,000 records, 64 KiB per record and 32 distinct report groups. Inputs are
processed in argument order from a size-qualified snapshot, not continuously
tailed. A repeated file identity is not counted twice. A linked/reparse,
nonregular, missing, replaced or unreadable input is reported unavailable
without exposing its path in the report. Concurrent truncation and scan limits
remain visible; no current-write or complete-population guarantee is made.

Select relevant active/archive files through their producer's existing `logs`
inventory. Do not feed native stdout/stderr, arbitrary logs, authoritative
databases or encrypted Vault artifacts to this typed consumer. Existing
producer-owned retention and custom sinks are unchanged.

Exit 0 means a report was emitted, including reports with unavailable,
unsupported or scan-limited evidence. Inspect `coverage`; it is not service
health or successful capture. Exit 2 means invalid invocation or an unavailable
output. Configuration, selected writer, actual persisted events, source release,
running adoption and synchronized peer receipt are separate facts.

## Installed service contributions

```text
agent-machines health report --service agent-dispatch
agent-machines health report --service queue=agent-dispatch --hour 2026-11-09T12:00:00Z --chart <new-chart.svg>
```

An explicit `--service` selects the existing same-cell installation owner,
not PATH, a home-directory scan or a synchronized command. At least one
`--input`, `--service` or `--store-dir` is required. `--hour` selects exactly one absolute UTC
hour inside the report's 168-hour window; the default is its current hour.
There are at most eight selections, 30 seconds for collection, ten seconds per
child and 64 KiB each for complete stdout/stderr. Native tree and retained-pipe
cleanup use the existing caller-owned process primitive and a shared absolute
execution budget. Cleanup is reserved inside both deadlines, not added after
the child timeout; a collection response margin is retained.

The selected immutable runtime must contain a supported
[hourly reporting declaration](../../../libs/process-telemetry/hourly-reporting.md).
Missing metadata is unsupported without trying new flags. The operation
starts no producer, capture, credential acquisition, installer or periodic job.
Legacy/unconfigured installation contexts report unavailable. Other-cell and
standalone-service inventory adapters are not implemented by this first seam.

The additive `services` array keeps each presentation label, native service,
requested hour, state and admitted contribution together. Local installation
identity stays local. Repeated selections of the same installation are marked
duplicate, not executed twice; presentation aliases do not create instances.
Independently qualified same-named selections retain their own namespace.
Invalid/unsupported/unavailable/timed-out providers do not suppress independent
valid results. Unknown cleanup custody remains a command failure.

`services_collected_at_ms` records collection completion separately from
`generated_at_ms` inside a contribution and its first/last source times.
`--now` remains the explicit-file cutoff and service-hour window selector;
service exports summarize their complete requested hour at collection time,
not a reconstructed within-hour cutoff in the past.
Dispatch's first exporter reduces only its own retained typed telemetry.
No current service health, authenticated access or logical-agent population
is inferred. No observations remain a gap, never a healthy zero.

Service contributions remain separate from explicit-file totals and from each
other. The static chart lists their namespace, source hour, coverage state and
observed metric groups separately; it never averages percentiles or double-adds
potentially overlapping direct-file/export observations.

The focused Dispatch packet
`tests/test_hourly_reporting.py` + `tests/native/test_hourly_export.py` proves
actual producer events, native export, fixture-selected installed metadata,
the normal machine report/chart and rollback to absent metadata.
Machines' `tests/test_service_reports.py` +
`tests/native/test_service_report_cleanup.py` cover independent gaps, wrong
owners/hours, namespaces, strict UTF-8, output/deadline limits and retained
streams after root exit. These are isolated source/fixture controls, not
deployed installed-owner inventory or activated hourly publication.

## Reproducible validation

Pure tests cover time units, mixed event families, malformed/partial input,
coverage bounds, descriptive statistics and inactive chart output.
The opt-in `tests/native/test_health_report_consumer.py` exercises the existing
configured retained sink and measurement producer, independent local-file
readback, the normal CLI, chart generation and input preservation. It creates
no production service and qualifies neither a carrier nor scheduled execution.

From the source root, run the focused consumer packet with the existing managed
runner:

```text
python tools/run-plugin-tests.py agent-machines --test-file tests/test_health_report.py --test-file tests/native/test_health_report_consumer.py --allow-explicit-tiers
```

## Explicit local collection candidates

`health collect` adds an explicit one-hour candidate flow; it does not change
the read-only report command or register a recurring job. Prepare a **new**
exclusive unsynchronized store through the local configuration owner with:

```json
{
  "schema": "agent-machines.health.store",
  "version": 2,
  "publisher": "example-worker",
  "continuity": "example-generation",
  "collection": {
    "bridge_port": null,
    "services": ["queue=agent-dispatch"]
  }
}
```

The store also needs a preprovisioned one-byte `collection.lock` regular file,
initially containing a zero byte.
Preparation is explicit: collection creates neither the directory, policy nor
lock, and does not migrate an existing v1 store. Select an existing admitted
loopback Bridge listener by numeric `bridge_port` when wanted; null means do
not probe. Service selections use the same installed-owner contract as report,
with at most eight unique presentation labels and no executable configuration.

```text
agent-machines health collect --store-dir <absolute-v2-store>
agent-machines health collect --store-dir <absolute-v2-store> --hour 2026-11-09T11:00:00Z
agent-machines health record --store-dir <absolute-v2-store> --input <candidate.json>
agent-machines health record --store-dir <absolute-v2-store> --input <candidate.json> --apply --expect <preview-token>
agent-machines health report --store-dir <absolute-v2-store> --chart <new-chart.svg>
```

Collect writes one `agent-machines.health.hourly` v1 envelope to stdout. Save it
outside the store as the explicit record input. Current-hour collection runs
only the selected local listener check; an explicit older hour within the
168-hour window exports retained services only, never present-time probes.
Run the two explicit commands to obtain current health and previous-hour
telemetry, or use the separately approved one-shot operation below.
Native periodic enrollment is not implemented. An early/current-hour export from the separate report command
remains its existing bounded snapshot, not a complete-hour total.

Collection holds a nonblocking OS-backed run lock, uses a 45-second admission
budget and passes the remaining ceiling to existing 30-second/ten-second
service collection. It reserves response time and refuses on a changed policy,
clock/hour boundary or expired budget. The native process owner retains cleanup
custody. Before exporting, collection durably changes the existing lock byte
to one; confirmed settlement clears it to zero. Unconfirmed native cleanup
retains both admission locks and registers their release with that exact
native owner's settlement callback. A failed cleanup retry cannot release them.
If the CLI exits before settlement, the byte remains a fail-closed fence:
later invocations refuse `collection-cleanup-unconfirmed` even after the OS
lock releases. There is no automatic fence reset or recovery command; the
original native owner must confirm settlement, or an explicit containment
handoff must reconcile that exact custody before store recovery. File age,
an exited CLI or a missing PID is not confirmation. Thus collection never writes
evidence or slots, but can change this existing admission metadata.
The final deadline and hour checks run after admission cleanup; late successful
fence clearing cannot produce an on-time candidate.
Lock, provider and filesystem failures never start a replacement
service, change credentials or write a slot. Filesystem stalls defeating the
budget remain a native qualification failure, not a hard-real-time guarantee.

The candidate retains existing unselected components and its target revision;
confirmed `health record` revalidates the policy, input and previous revision
under its existing mutation lock before atomic replacement. There is no
multi-slot transaction or automatic replay after an unknown write outcome.
The new closed envelope holds only same-hour observation v1 and namespaced
service contribution v1 data, with collection time separate from evidence
times. A valid older service contribution survives a later failed or
evidence-free export, even when the response contains a valid contribution
wrapper. This rule is also enforced for directly supplied record candidates.
Components without evidence remain unknown; a fresh envelope does not refresh
their source ages. Supplied input remains unverified, not an attested probe.

## Policy-approved one-shot collection

```text
agent-machines health run --store-dir <absolute-v2-store>
agent-machines health run --store-dir <absolute-v2-store> --apply --expect-policy <policy-token>
```

Without `--apply`, this reads only the selected store identity and exact local
policy. It returns an `agent-machines.health.run` v1 plan and `policy_token`;
no observation, export, admission lock, slot write, provisioning or job
registration occurs. The token binds the canonical store path, filesystem
identity, exact policy bytes (including publisher/continuity), and collection
operation v1. It deliberately does not bind the hour or slot revision.
Missing, wrong or changed approval refuses before effects; a token without
`--apply` also refuses. Keep this local policy and approval outside synchronized
report data. They authorize neither service changes nor carrier writes.
Both payload entry points select an already provisioned runtime read-only for
`health run`, including apply. A missing runtime refuses with the existing
launcher failure status before calling an installer, stamping diagnostics or
creating runtime/store state; use the normal setup owner separately.

Apply holds one existing admission lock and one 45-second parent budget for
two independent targets: current-hour listener health first, then the preceding
complete hour's selected service exports. Empty selections are explicitly
`not-requested`, not permission to enumerate providers. Both candidates pass
through the same policy/revision planner, mutation lock and atomic writer as
manual record; no intermediate candidate file is created. Policy approval and
the frozen hour/deadline are rechecked under each mutation lock. Lock waiting,
collection and native settlement share the remaining parent budget.

The closed `targets` results retain absolute `hour`, `component`, `requested`,
`collection`, `transaction`, `applied`, `changed` and bounded `reason`.
The top-level `qualification` distinguishes an on-time response from a timeout,
hour/policy change or unconfirmed effect. A second target's failure cannot
erase or relabel the first commit. A late successful write retains its actual
applied/changed facts with timeout qualification, never an on-time success.
Exit 0 means preview or an on-time completed run; exit 2 retains partial/failed
results on stdout, or a bounded pre-execution/output error on stderr.
Collected service rows still retain their independent unsupported/unavailable
coverage: a completed staging transaction is not healthy service evidence.

Unknown write outcomes stop the invocation without replaying the target.
Apply sets the existing admission fence before each transaction too; an unknown
write leaves that byte at one after releasing the native lock. Subsequent
invocations refuse rather than automatically retry a potentially committed
write. Reconcile the exact returned hour and transaction through the original
owner before any explicit store recovery; there is no automatic fence reset.
Unconfirmed exporter cleanup preserves the existing native owner's callback
and admission custody. Neither outcome rolls back earlier confirmed slots.
In-process Python callers receive the original `OwnedCleanupError` with a
bounded `health_run_result` attribute, retaining access to `retry_cleanup()`.
The CLI emits that partial result and exits 2; a process exit does not clear
its durable fence. Interrupted commits are marked unknown before entering the
writer and preserve the fence even when a `KeyboardInterrupt` escapes.
Python interruption errors also carry `health_run_result`; the CLI emits it
for a keyboard interruption rather than claiming no write happened.

Independently installed callers must first qualify the exact selected Machines
runtime's packaged `agent_machines/health-collection-v1.json` declaration:

```json
{"schema":"agent-machines.health.collection","version":1,"operations":["preview","apply"]}
```

This declaration is separate from the package version and from service-export
metadata. Missing, changed or unsupported metadata is unavailable support,
never permission to trial a new command, resolve PATH, provision or update.
Runtime replacement/rollback invalidates the selection and requires fresh
qualification. This provider declaration prepares that seam; it does not
implement or enroll a native scheduler caller.

The focused one-shot controls cover pure preview/approval, empty selections,
mutation-time policy/revision drift, shared lock budget, partial outcomes,
late commits and unconfirmed native cleanup. The opt-in collection consumer
packet exercises a real fixture-owned listener and retained Dispatch data
through both slots and the ordinary reread/report/chart, plus a failed second
export preserving the confirmed first transaction. Required actor-free CI
substitutes native/storage owners and starts none of those actors. Installed
adoption, Linux/Windows job qualification, carrier behavior and activation
remain separate gates.
Listener-only recollection replaces only `local_listener`; retained SSH,
session and other unselected domains keep their exact evidence and source
timestamps. The record boundary also refuses changes to those domains.

V2 slots have a 768 KiB per-envelope limit and the same 168-name ring. The ring
accounts for at most 126 MiB; failed writes or unrelated filesystem usage are
not disk-quota enforcement. Old v1 observations/stores retain their original
schema and 64 KiB limit; an older runtime cannot read or write the new format.
Use explicit store selection for adoption/rollback, never dual writers or
synchronized execution policy. The report/chart shows stored service namespaces
separately from direct-file totals without launching exports.

`tests/test_health_collection.py` covers candidate, clock, overlap, old-format,
revision and storage-failure controls. The selected native
`tests/native/test_health_collection_consumer.py` exercises actual retained
Dispatch events and a private listener through collect, confirmed staging,
reread and chart, plus two-process overlap and normal/crash lock release.
Its installation admission is fixture-resolved. Native scheduler qualification,
installed adoption, carrier publication and activation remain separate gates.

## Explicit local hourly observations

`health record` and `health inspect` are a separate typed local-storage flow,
not an extension of the telemetry parser or a live probe. Prepare an exclusive,
unsynchronized local directory containing `policy.json`:

```json
{
  "schema": "agent-machines.health.store",
  "version": 1,
  "publisher": "example-worker",
  "continuity": "example-generation"
}
```

The publisher is the explicitly selected logical machine identity. Continuity
is a stable publisher generation, not the current process or session. Preserve
it across restarts. A different generation requires a separately prepared
namespace; receiving a record never rewrites policy or adopts another owner.
These identifiers are data, not authentication. The command does not resolve
machine topology, register a machine or attest supplied observations.

An explicit input uses this closed schema:

```json
{
  "schema": "agent-machines.health.observation",
  "version": 1,
  "publisher": "example-worker",
  "continuity": "example-generation",
  "hour": 1,
  "revision": 1,
  "parent": null,
  "observed_at_ms": 3600000,
  "domains": {
    "local_listener": {
      "state": "ok",
      "evidence": "listener-check",
      "reason": "none",
      "observed_at_ms": 3600000
    }
  }
}
```

The epoch-start example illustrates the shape, not current usable evidence.
`hour` must equal epoch-millisecond `observed_at_ms` divided by 3,600,000,
rounded down. Integer timestamps/revisions cannot exceed `2^53-1`; booleans
are not integers. Publisher/generation identifiers are bounded ASCII values.
Each policy/record is at most 64 KiB. Duplicate keys, unsupported schemas,
unknown fields, malformed UTF-8/JSON, nonregular, linked/reparse or hardlinked
files and noncanonical parent directories refuse. Paths must be absolute;
parent traversal refuses. Nothing provisions a store or repairs its contents.

Domain states are `ok`, `failed`, `unknown` or `unsupported`. Each observation
has `state`, `evidence`, `reason` and `observed_at_ms`. Known observations require
an integer source timestamp no later than the record and the matching evidence:

| Domain | Evidence |
|---|---|
| `local_listener` | `listener-check` |
| `ssh_transport` | `ssh-banner` |
| `ssh_authenticated` | `authenticated-session` |
| `gateway_visibility` | `gateway-owner` |
| `authorization` | `authorization-owner` |
| `credential_readiness` | `credential-owner` |
| `logical_agents` | `session-owner` |

Unknown/unsupported observations use null time and `not-observed` evidence.
Reasons are only `none`, `timeout`, `unavailable`, `refused`, `invalid`,
`clock-skew` or `not-observed`; an `ok` observation requires `none`.
SSH authentication additionally has an `inbound`/`outbound` `direction` for a
known result, null otherwise. Listener/banner evidence cannot qualify it.
Logical agents additionally have `count`: an observed `ok` count from 0 to
65,535, null otherwise. Absence is unknown, never an inferred zero. No raw
errors, process names, routes, credentials or arbitrary labels are accepted.
Synthesized missing domains retain the same timestamp/evidence fields,
including `count: null` for logical agents and `direction: null` for SSH.

```text
agent-machines health record --input <absolute-observation.json> --store-dir <absolute-local-store>
agent-machines health record --input <same-input> --store-dir <same-store> --apply --expect <preview-token>
agent-machines health inspect --store-dir <absolute-local-store>
```

Both commands accept aware ISO `--now` for reproducible evaluation, defaulting
to current UTC time. Preview is read-only. Apply rechecks exact input/policy/slot
bytes, namespace identity and the evaluation hour under the existing bounded
local writer lock. Changed previews refuse. Inputs cannot alias the policy,
writer lock or output. Complete records are flushed and atomically replace only
`000.json` through `167.json`, selected by absolute hour modulo 168. The local
`health.lock` is not a synchronized fleet claim. POSIX publication also syncs
the directory; this does not qualify Windows crash durability or cloud receipt.
Writer exclusion covers participating local writers, not arbitrary external
editors or filesystem attacks.

New hourly records begin at revision 1 with null parent. Within one hour,
identical records are unchanged; a higher revision must name the current
revision as parent and cannot regress observation time. Same-revision conflicts,
older revisions, missed parents, foreign owners, future observations and input
outside the last 168 absolute hours refuse. A delayed previous-week replica
cannot overwrite its newer slot. Missing hours are never fabricated.

Inspect reads exactly those 168 bounded files, never arbitrary directory
contents. It reports observed, missing, invalid, expired, future and owner-mismatch
slots without echoing bad data. Known source evidence at least one hour old is
`stale`, retaining its original `observed_state` and age separately. Missing
domains remain unknown; historical observations are not current uptime.
All results explicitly say `input-supplied-not-verified` and `probe_execution:
not-performed`. Source labels are not proof that a domain owner was invoked.

Record exit 0 means eligible preview, recorded or unchanged data; 1 means a
blocked preview; 2 means invalid/unavailable input, refused confirmation or an
unknown write outcome. After publication starts, an acknowledgement failure
retains `applied: null` and never retries. Inspect current state before choosing
another operation. Inspect exit 0 includes missing/invalid evidence; it is not
healthy-machine proof. No probes, credentials, scheduled jobs, services, carrier
publication or peer receipt are activated or qualified.
A response write/flush failure after confirmed apply also reports an unknown
acknowledgement through stderr when available, never an untouched slot.

Inspect rechecks policy bytes and namespace identity after its scan; a concurrent
policy change requires another read, not a view qualified by the old owner.
These are additive explicitly selected commands. Discover `health --help` on the selected runtime before
calling them; an older runtime's missing command does not authorize a fallback
writer, direct slot edits, runtime update or carrier enrollment.

## Composed local health view

`health report --store-dir <absolute-local-store>` composes the existing
`health inspect` reader with the report, optionally alongside explicit telemetry
files and installed service contributions. It is one read-only report invocation,
not a combined observe/record job: it neither calls the listener nor changes the
store, its policy, revisions or writer lock. Observation and confirmed storage
remain separate explicit operations. A chart destination inside the selected
store is refused, including new paths; keep presentation artifacts outside its
exclusive namespace. Both lexical and resolved overlap refuse. A store that
cannot be resolved remains unavailable evidence while a valid external chart
can still be written; an unresolvable chart is instead an output refusal.

The additive `health` object has `state: available` and the complete age-qualified
`view`, or `state: unavailable` and `reason: local-store-unavailable` if the store
cannot be qualified. Available means readable, not healthy. A missing, invalid
or changed store policy cannot suppress independent valid telemetry or service
results. It creates no missing directory and includes no raw path/error content.
An available view retains per-slot missing, invalid, expired, future and
owner-mismatch conditions, original domain times, observed states, proof ages,
unknown counts/directions and the reader's `input-supplied-not-verified`
qualification. Explicit publisher/continuity identifiers remain local view data,
not verified identity or permission to share them.

The file report and health reader use the same `--now` cutoff and 168 absolute
UTC hours. Service contributions keep their separately documented collection
time semantics. A readable historical listener observation never becomes
authenticated SSH, a fresh proof or evidence of active agents. Health slots are
not added to event totals or duration statistics. The SVG displays separate
slot/domain rows, visible gaps and stale evidence; cell titles retain observed
state, source age, evidence and reason. A report without `--store-dir` retains
its prior output and does not inspect a store.

The reader adds at most 168 slot reads of 64 KiB each plus its bounded policy
checks; it does not enumerate unrelated files or acquire a writer lock. These
are work/byte bounds, not a wall-clock deadline for a blocked filesystem.
Native job overlap, stopped storage, Windows/Linux lifetime, installed adoption,
scheduling and carrier qualification remain open before enrollment.

This expands the existing `agent-machines.health-report.v1` result with an
opt-in field, without changing either strict v1 policy/observation schema.
Providers expand first; no automated caller or schedule is migrated here.
Check `health report --help` on the exact selected runtime for `--store-dir`
before selecting this optional feature. Legacy/rolled-back providers retain
their old report syntax; missing support means the composed view is unavailable,
not permission to drop a required health input or write slots directly.

`tools/promotion_unit_tests/test_health_composition.py` covers actual age
projection, report/parser composition, independent unavailable evidence and
static rendering without native actors. The selected
`tests/native/test_health_observe_consumer.py` extends actual loopback observation
through confirmed slot storage to the normal report/chart and verifies storage
is untouched by reporting. `tests/native/test_health_report_consumer.py` retains
the real measurement/sink flow when an explicitly selected store is unavailable.
These source/fixture controls do not establish installed or activated publishing.

## Passive local Bridge listener

```text
agent-machines health observe --bridge-port <selected-port> --publisher <publisher> --continuity <generation>
```

This additive, on-demand command performs one unauthenticated `GET /health`
against numeric IPv4 loopback on the explicitly supplied port. Select that port
through the existing Bridge owner; the observer does not discover endpoints,
ensure a daemon, bootstrap a runtime or start a service. It never uses proxies,
redirects, cookies or credentials. The read has a two-second absolute transport
deadline and a 64 KiB total HTTP-wire budget, including headers and framing.
There is no retry or network-host option.

The actual response must identify `agent-bridge`, report `status: ok`, and
advertise a protocol range containing the baseline health contract, generation
1. Missing, malformed or incompatible support remains unknown/unsupported.
Additive owner fields are tolerated but discarded. A truncated declared body
does not qualify, even when its received prefix is a complete JSON object.
A supported response proves
only `local_listener: ok`; `ready`, credential-relay flags, carrier diagnostics
and process/host counts are not authentication, Gateway, credential or logical
agent evidence. All other domains remain explicitly unknown with null count
and direction. A refused, invalid, oversized, unavailable or timed-out response
is also unknown, not a diagnosed machine failure.

Stdout is one existing `agent-machines.health.observation` version-1 record,
with current source timestamps, revision 1 and null parent. There is no `--now`
override, file write, cloud publication or scheduled execution. Save it only to
an explicitly selected local input, then use the normal `health record` preview
and confirmation. Preserve the store's configured publisher and continuity.
Repeated observations within one hour still require the record owner's explicit
revision/parent handling; the observer never edits a prior slot.

Exit 0 means an observation was emitted, including unknown/unsupported evidence;
it does not mean a healthy machine. Exit 2 means invalid invocation or unavailable
output. The local store continues to label supplied input as unverified, even
when its producer was this command: it cannot attest a saved file's provenance.
An aged listener result becomes stale through the existing reader.

`tests/test_health_observe.py` covers the projection and unsupported/error
contracts. The explicitly selected native packet below exercises real loopback
HTTP, normal isolated CLI subprocesses, local recording and stale readback, plus
redirect, overflow and continuously trickling-response controls:

```text
python tools/run-plugin-tests.py agent-machines --test-file tests/test_health_observe.py --test-file tests/native/test_health_observe_consumer.py --allow-explicit-tiers
```

Its finite listener supplies synthetic owner responses, not an installed Bridge.
An additional opt-in case invokes the original Bridge health handler in a
separately prepared source-bound test interpreter, with diagnostic dependencies
isolated, then sends its actual response through the same HTTP/CLI/store flow.
Select that interpreter through `AGENT_MACHINES_TEST_BRIDGE_PYTHON`; obtain it
from `python tools/run-plugin-tests.py agent-bridge --prepare-only`, not a
production runtime or ambient executable. Absence skips only this extra case,
not the synthetic transport controls. This proves source-response compatibility,
not the full daemon's ASGI/lifespan or installed listener.

Neither case is full host/SSH/Gateway qualification. Windows source success does not
qualify Linux, installed runtime selection, scheduler lifetime, carrier delivery
or enrollment. Those remain separate acceptance gates.
