# Performance measurement

This pattern serves Picker responsive-by-budget and the process-telemetry
vision's cheap, optional provenance. Bridge/Dispatch's callable seam and
`libs/process-telemetry` are producer exemplars, not proof that every native
launch or hook has already met its budget. Remaining suite qualification is
tracked by #7935 and the
[performance telemetry effort](../../efforts/active/performance-telemetry/README.md).

## Contribution contract

A change to a launch, callback, repeated scan, hook or process-producing path
includes a **Performance impact** statement:

- caller, frequency, workload/roster cardinality and blocking stages;
- subprocess/network/installation/tree/hash/history work added or removed;
- reused evidence, invalidation and stale/missing-evidence behavior;
- deterministic work-count checks and a reproducible native measurement flow;
- what was measured, unavailable or intentionally left to a named owner.

A repeated provenance check names its real failure boundary and why existing
generation-validated evidence cannot be reused. Lower process counts alone
do not establish lower wall/CPU cost. Watchdog limits are not latency budgets.
Required decisions remain conservative when evidence is unavailable;
dropping optional diagnostics never changes an admission result.

## Measurement boundaries

Keep the existing Picker budgets: first interactive frame under two seconds,
Actions under one second, key acknowledgement under 100ms. Measure a complete
hook dispatch against one two-second ceiling including startup, IPC, fallback
and cleanup, not a separate allowance per enabled plugin. These are acceptance
targets; publishing this pattern is not proof the suite currently meets them.
New/resume budgets and CPU/spawn limits need representative baselines and review.

The native harness observes actual executable-to-visible/input-ready and
gesture-to-result boundaries. Mount callbacks, subprocess existence, file
publication and hook exit do not prove a visible frame, model admission or
first prompt. Record unavailable when the boundary cannot be observed.
End-to-end monotonic timing is authoritative; overlapping spans are not summed.

Reports preserve individual samples and errors, roster and workload cardinality,
source/installed/serving identity, OS/cache conditions, expected/completed count,
quantile method and p50/p95/p99/max. CPU/spawn figures identify their source and
coverage; missing child measurements are not zero. Small-sample tail estimates
are labeled insufficient for tail qualification. Drops/incomplete traces cannot
produce a passing qualification.

## Cheap diagnostic transport

No-op telemetry does no external work. Enabled producers use bounded in-process
records/queues, explicit owner lifecycle and drop counters. Sink/network/file
work stays off callbacks; hooks never bootstrap an SDK, exporter or reporter
process. Retain the existing owner IPC/result path where possible, or omit the
diagnostic. A slow/disconnected sink cannot block execution or multiply writer
threads. Diagnostic evidence never replaces durable recovery records.

Measure the measurement: compare disabled, enabled and unavailable/full-sink
conditions under the same workload, retaining added wall/CPU/spawn cost and
drops. Recommendations such as 1ms/event or 1% aggregate cost are not established
limits until baselined and accepted. No implicit off-site destination is allowed.

## Validation ownership

Required PR CI keeps pure deterministic work-count/schema tests, not native
process matrices. Explicit native lanes own cold/warm composed observations.
Inject an extra child process, full-history read and serial delay in the
appropriate isolated lane and prove the guard/benchmark catches each.
The failure must be attributable to a measured contract, not a timeout chosen
to hide unknown behavior. Runtime and test consumers use the same stage meanings.
