# Design and outage controls

Parent: [Machine Health Heartbeats](README.md).

## First complete consumer

Proposed public surface: `agent-machines health report --input <local-jsonl>`
with explicit additional inputs and an optional report/chart destination.
Invocation is on demand; import, status discovery and configuration do not
start collection or a scheduler.

The implementation reuses the process-telemetry package through its existing
shared-library delivery model. Runtime changes to Agent Machines carry its
normal changefile; no hand-edited package versions or source-checkout dependency.
The first source slice owns new report/CLI tests and documentation only, plus
the minimal shared-library dependency declaration. Existing producer, sink and
reserved Manager/entry files retain their owners.

Inputs are explicit local regular files, never guessed cloud paths. Bound
bytes, record size, input count and group/cardinality before work; stream input
instead of loading arbitrary histories. Report incomplete reads, malformed
lines, partial tails, timestamp uncertainty and scan limits explicitly. No
report payload contains raw unknown records or arbitrary exception text.

Use the existing typed `agent.performance.v1` component/stage/outcome and
nanosecond duration contract. Existing Bridge session/health and Dispatch task
lifecycle records contribute counts from their closed event/status vocabulary,
using their explicit epoch-millisecond `ts`; they do not supply durations.
Drop their identity/routing/content fields rather than copying arbitrary labels
into reports. Neither record family is silently reinterpreted as the other.
A schema without
supported statistics remains counted as unsupported, not assigned a duration.
Explicitly timestamped source spans use their source time, not file mtime or
the time an offline report happens to run.

Produce 168 absolute UTC-hour buckets including the current hour. Empty buckets
mean no observations, not healthy zero-latency operation. Group statistics by
compatible component/stage and show observed counts, outcome counts and
missing/loss/invalid coverage. Successful observed durations can support
descriptive p50/p95/max, but no p99 qualification or end-to-end availability
claim follows from an unknown population. Do not average stage percentiles or
sum overlapping durations into an end-to-end measurement.

The optional chart is a deterministic static artifact with no active content,
remote dependencies or new display service. It shows latency and observed
outcome/coverage series with gaps. Report generation never opens a browser or
publishes raw events. Rendering shares the report's bounded data, rather than
rescanning inputs or inventing a second statistics implementation.

## Health and carrier follow-up

Health records distinguish local listener/process health, actual authenticated
SSH direction/proof age, Gateway visibility, authorization, configured-provider
credential readiness and logical active agents. Missing or unsupported owner
observations remain unknown. Readiness inspection never prompts, refreshes
credentials or tries unrelated identities.

Each selected machine writes only its own namespace. A logical slot is the UTC
hour modulo 168, with the absolute hour and publisher continuity retained in the
record. Complete atomic local publication, bounded diagnostic coalescing,
revision handling and reader expiry prevent a prior week's delayed replica
from appearing current. Conflicts are visible; there is no shared fleet-summary
writer or lock/claim protocol in synchronized files.

Collection precedes carrier publication so sync failure cannot erase local
evidence. Retained catch-up keeps original observation times; offline hours
are not fabricated. A reader shows both sample age and receipt/sync uncertainty.
Provider backups/version history are not claimed to expire with active slots.

## Outage-derived controls

These are generalized controls, not claims about private incidents or confirmed
root causes. Further operator rounds can add cases before each affected phase.

| Condition | Required evidence/result |
|---|---|
| Tunnel process and local TCP port alive, no SSH banner | Transport degraded; do not report authenticated reach or a proved cause |
| Banner/HTTP health works, inner SSH or consumer authorization fails | Distinguish transport, authenticated user route and service access |
| Token producer unhealthy while SSH still works | Separate component observations, not one fleet-wide green/red switch |
| Reboot, suspend, publisher crash or host offline | Gaps remain missing; returning samples do not backfill invented uptime |
| OneDrive paused, signed out, slow or observer replica stale | Suspected reporting/carrier outage; retain local observations and last-known ages |
| Writer blocked, queue full, disk unavailable or partial JSONL tail | Bounded producer behavior and explicit report loss/uncertainty; no suffix replay |
| Timestamp units mixed, clock skew or old weekly slot arrives late | Validate schema/time interpretation; old evidence cannot become fresh |
| Session owner unavailable | Logical agent count unknown, not zero or a count of Python processes |
| Scheduler/job overlapping or previous process stuck | Existing single-owner bounded job admission; no unverified process kill |
| Capture configured while existing service has not reloaded | Configured/pending activation, never active-capture success |

## Required qualification

Use pure in-process unit cases for required CI. The real consumer lane exercises
an existing sink/configured producer writing a private temporary local file,
then the normal CLI reading it and producing the report/chart. Use the existing
finite-process/test owner and exact cleanup, not a production daemon.

Before native scheduling or carrier enrollment, qualify Windows and Linux
one-shot/headless lifetime, a stopped reader/writer, overlap, full/invalid input,
read-only or unavailable storage, sync delay and preserved active work.
Record first-touch versus reconcile and source versus installed evidence.
Any unavailable lane stays open; a passing pure fixture does not qualify it.
