# Hourly service reporting v1

This is a read-only contribution protocol, not a telemetry sink, inventory,
collector, scheduler, health publisher or activation mechanism. It implements
the [reviewed service contribution design](../../efforts/active/machine-health-heartbeats/service-contributions.md).

## Installed support and operation

An immutable selected service wheel may include `<package>/hourly-reporting.json`
in its normal package data:

```json
{
  "schema": "process-telemetry.hourly-reporting",
  "version": 1,
  "service": "agent-dispatch",
  "operation": "telemetry-hourly",
  "metrics": ["task-lifecycle", "operation-duration"]
}
```

The logical operation maps to the native `telemetry hourly --hour
YYYY-MM-DDTHH:00:00Z` parser. The declaration supplies no executable, shell,
path, import hook or arbitrary arguments. Package versions do not declare
protocol support. Missing, invalid or newer declarations do not authorize a
trial invocation or an effectful fallback. Older commands remain unchanged.

The first Machines adapter reuses its existing explicit installation context,
same-cell peer owner and immutable-runtime retention. It runs a packaged
stdlib entry under the selected retained interpreter, reads only that
interpreter's normal installed package data, qualifies the declaration, and
then invokes the native service parser in the same child. It never uses the
latest marketplace payload as reporting metadata or a self-provisioning shim.
No manifest or declaration is imported from synchronized report data.

This first adapter selects one installation per service in its current cell.
It does not enumerate other cells, legacy homes or standalone installations.
Those owners can later supply independently qualified selections to the same
collection seam. Collection keeps local installation identity separate from
explicit presentation labels, de-duplicates the same selected installation,
and preserves independently selected same-named services in separate namespaces.
An alias alone does not create another installation.

Discovery and export do not start a service, writer, factory, credential flow,
update, repair or registration. A stopped service may export retained history;
this is not evidence that it is currently running.

## Contribution

`process-telemetry.hourly-contribution`, integer `version: 1`, has exactly:

| Field | Meaning |
|---|---|
| `service`, `hour` | Selected native service identity and canonical absolute UTC hour |
| `generated_at_ms` | Export generation time, not source observation or collection time |
| `source` | First/last admitted epoch-millisecond observation; `continuity: null` means no qualified native continuity |
| `coverage` | Observed/partial/unavailable/unsupported, closed reason and scan counters; unknown expected population and producer loss |
| `groups` | Namespaced homogeneous count or duration groups, never merged percentiles |
| `health` | Optional native-owner observations of the service's own health |
| `diagnostics` | Optional bounded, classified diagnostic summaries, not messages |

`hourly.py` is the closed v1 validator. Strict UTF-8/JSON rejects duplicate and
unknown fields, nonfinite or mistyped numbers, excessive structure, unsupported
metrics, mismatched owners/hours and inconsistent populations. JSON is capped
at 64 KiB, depth 8, 4,096 visited values and 4,096 characters per decoded
string; field vocabularies further restrict output strings. There are at most
32 metric groups and 16 health/diagnostic entries each. A future schema or
metric needs a separately supported declaration, not an arbitrary extension map.

`task-lifecycle` is a count of the known Dispatch task transitions, not active
tasks or agents. `operation-duration` uses existing performance stage/outcome
vocabularies, nanoseconds and nearest-rank p50/p95/maximum of successful observed
durations, with the successful population retained. Neither is uptime or a
qualified p99. Units, aggregation (`count`/`distribution`) and `observed-events`
scope are explicit. Unsupported stage/metric/unit combinations refuse.

The initial health vocabulary is only `domain: service`, `evidence:
native-owner`, source time and healthy/degraded/unavailable/unknown state.
It cannot claim SSH authentication, Gateway access or logical-agent counts.
Diagnostic entries have the owning component, `operation-error`/`capture-gap`,
source first/last times and occurrence count, separately classified as symptom,
confirmed-cause, hypothesis or recovery-outcome. No adapter derives a confirmed
cause from an error count. Dispatch currently exports neither native health
nor diagnostic entries; those arrays remain empty, not healthy defaults.

Source observations must fall inside the requested hour and precede export
generation. Delayed export may summarize retained history; generation cannot
move present health into a past hour. An empty population remains unavailable.
Any known malformed/partial/unavailable/future source or scan truncation
prevents complete observed coverage. Producer loss and expected events remain
unknown even after a complete local scan.

## First producer and bounds

Dispatch reads its existing `telemetry.json` retained configuration and only
its `agent-dispatch-telemetry` active/archive namespace. Legacy/custom or
environment-selected sinks remain unsupported, never imported or overwritten.
Its native adapter admits only known lifecycle records and its own typed
performance spans. It discards task IDs, routing, owners, prompts, arbitrary
labels and raw error content before producing the contribution.

The shared read-only scan considers at most 256 directory entries, 16 files,
8 MiB, 20,000 records and 64 KiB per record. Limits, partial tails, replacement,
unavailable files, outside-hour/future events and repeated file identity remain
explicit. A direct active-file lookup precedes bounded archive enumeration,
so directory/file limits cannot omit the known active source. Archives are
prioritized by rotation near the requested hour, with an overflow sentinel
marking incomplete enumeration. Rotation time is only a selection heuristic;
it never assigns an event to an hour.

Each size-qualified byte window is read once and reduced newest-line first,
so old archives or an old prefix within the active file cannot consume the
record budget before its recent tail. A clipped prefix, partial tail or
oversized record never becomes an admitted suffix. This is a bounded snapshot,
not continuous following. It does not repair, prune or write source files. Concurrent
append/rotation can make coverage partial; scan completion is not lossless
capture. Historical availability cannot be inferred from silence.

Machines admits at most eight explicit service selections per report, with
one 30-second collection deadline and at most ten seconds per native child.
Complete stdout and stderr each have a 64 KiB cap. The existing caller-owned
process primitive owns native descendants and stream settlement, including
root exit with a retained pipe. One `OwnedExecutionBudget` caps execution and
settlement by both the child and collection deadlines, reserving up to two
seconds for cleanup inside that lifetime and a separate collection response
margin. Cleanup waits and later providers do not renew that deadline.
Unconfirmed cleanup propagates its original
custody error rather than becoming an ordinary missing-provider result.
Independent unsupported, invalid, unavailable or timed-out providers are gaps.
The machine slot/revision/publisher owner remains separate.

## Qualification

The Dispatch native packet exercises real service-produced retained events,
the normal export/parser, installed package data in a fresh fixture interpreter,
the normal Machines report and static chart, and rollback to absent metadata.
The existing measurement producer also reaches the normal report/chart.
Controlled retained span cases qualify completion-hour attribution and
successful-only duration percentiles across mixed outcomes; invalid timing
and vocabulary are explicitly refused.
Installation admission/retention selections in that flow are fixture-resolved;
it is not deployed-host inventory, installed adoption or activation evidence.
Machines' separate native cleanup packet exercises deadlines, actual inherited
streams after root exit, byte limits, strict UTF-8 and native settlement callbacks.
Saturated archive and active-prefix controls retain current-hour observations;
late-timeout controls verify cleanup/response reserves and no renewed lifetime.
No test touches an operator service, real carrier or credentials.
