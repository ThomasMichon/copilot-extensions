# Local telemetry reports

`agent-machines health report` is an explicit, bounded offline consumer of
existing local telemetry. It starts no collector, scheduler or service, never
changes telemetry configuration, and does not discover or publish to OneDrive.

```text
agent-machines health report --input <local-jsonl> --input <another-local-jsonl>
agent-machines health report --input <local-jsonl> --chart <new-chart.svg>
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
These are additive explicitly selected commands. Existing `health report`
behavior is unchanged. Discover `health --help` on the selected runtime before
calling them; an older runtime's missing command does not authorize a fallback
writer, direct slot edits, runtime update or carrier enrollment.

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
