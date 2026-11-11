# Bounded local hourly collection and staging

Parent: [Machine Health Heartbeats](README.md).

**Agent-recommended amendment: review before implementation.** This closes the
machine-heartbeat vision's `bounded-independent-publication`,
`hourly-seven-day-history` and `honest-offline-catch-up` intent. It specializes
the [service convention](service-contributions.md) and [outage controls](design.md);
it is not a claim that collection, a new store format or scheduling is shipped.

## One owner, distinct operations

Agent Machines remains an on-demand CLI with one optional native periodic job,
not a resident collector. Add collection inside its existing health command
owner; do not make `health report`, imports, discovery or setup start it.
Keep three operations distinct:

| Operation | Effects and authority |
|---|---|
| Inspect/report | Read admitted local evidence; no probe, lock, write or job |
| Collect/preview | Explicit bounded native observations and qualified service exports; no persistent evidence write, registration or carrier access; existing admission metadata can retain an unconfirmed-custody fence |
| Stage | Revalidate a locally approved collection policy and commit complete local slots through the existing health transaction owner |

Collection must not invoke self-update, fleet-update, restore, ensure, repair,
login or credential refresh. Neither an update opt-in nor a successful report
is collection authority. Existing native task modules are examples of platform
registration, not reusable update actions or an enrollment policy.

The local configuration owner selects publisher/continuity, the exclusive
store, admitted native adapters and at most eight service installations with
presentation labels. Reuse existing installation selection and retained-runtime
execution; no PATH search, mutable service registry or executable in report data.
Missing or unsupported owners produce gaps. An empty selection is not automatic
permission to enumerate machines, credentials, services or cloud folders.

## Hour attribution

Freeze the current absolute UTC hour at collection admission. An ordinary
hourly run has at most two target slots:

1. Observe current health for the admission hour. A check completing after
   that hour ends is not backdated: record an explicit boundary/clock gap and
   defer a new observation to the next invocation.
2. Export each selected service's retained telemetry for the immediately
   preceding complete UTC hour. Keep the contribution's original hour, source
   times, generation time and coverage; collection time does not refresh them.

The two updates are independently atomic, not an all-or-nothing transaction.
Current health can remain useful when historical export or staging fails.
Within each target hour, retain already-admitted independent components unless
an explicitly selected replacement passes validation and revision checks.
Do not replace previous evidence with an unselected component's default.
Expose per-target success, unchanged, refused and unknown outcomes.

The prior-hour export is a bounded retained-file summary, not proof of complete
capture. Producer loss and expected population remain unknown. A run need not
wait until the end of the current hour or invent current-hour totals.
An explicit historical collection may request one hour within the active
168-hour window, but cannot run present-time probes for it. No automatic
168-hour catch-up loop or fabricated observations after sleep/offline gaps.
Already-staged history remains available for a later authorized carrier copy.

Use source wall time for attribution and monotonic time for runtime budgets.
A clock regression or jump across the admitted hour is visible; do not silently
move a candidate, renew its deadline or overwrite a future/newer accepted slot.

## Versioned slot, existing transaction owner

Do not add service arrays to `agent-machines.health.observation` v1: its closed
domain-only parser and existing callers retain their meanings. Expand readers
first to recognize a separate `agent-machines.health.hourly` v1 envelope under
an explicitly versioned store policy. Its closed fields are:

| Field | Contract |
|---|---|
| Schema/version | Independent envelope identity, not a package version |
| Publisher/continuity/hour/revision/parent | The existing slot ownership and absolute-hour revision chain |
| Collection time | When this candidate was composed, not evidence freshness |
| Observation | Optional unchanged validated v1 health observation for this same hour |
| Services | At most eight namespaced, validated v1 service results for this same hour |
| Coverage | Closed collection/staging reason codes, including unsupported, timeout and not-requested components |

Nested observation ownership/hour must match the envelope. Nested observation
revisions remain source evidence, not a second envelope revision authority.
Service results preserve the existing contribution schema and instance labels;
local installation receipts, paths, commands and environments never serialize.
Only explicitly selected components may be replaced; an invalid candidate
cannot erase an accepted component. A degraded but valid new observation remains
evidence, not a reason to keep showing an older successful observation as fresh.

Reuse `health_slots` canonical-path checks, mutation lock, locked revalidation,
temporary-file/fsync/replace and unknown-write-outcome handling. Extend that
owner rather than creating another slot writer, directory ledger or lock stack.
The lock protects each short commit, not network access or service execution.
Re-read the current policy and target revision under the lock; a conflicting
revision refuses rather than blindly rebasing/replaying a preview.
An unresolved replace/durability result remains unknown and is reconciled by
reading that exact target before any retry.

Maintain exactly 168 slot names keyed by absolute hour modulo 168; absolute
hour and continuity decide freshness. Bound each envelope at 768 KiB with strict
UTF-8/JSON, duplicate/unknown-field rejection and the nested validators' existing
depth/cardinality/string limits. The active ring is at most 126 MiB; one bounded
temporary replacement plus policy/lock metadata must fit within a 128 MiB
owned namespace budget. No accumulating per-run report, chart, failure log or
staging queue. Storage refusal cannot prune producer diagnostics or unrelated
files. Recovery of an interrupted temporary replacement belongs to the existing
transaction owner's explicit recovery, not a broad startup cleanup.

A strict old store is not silently migrated. Reader support expands first;
explicit local store preparation then selects one writer format through the
configuration owner. Preserve existing data, read-only v1 access and a truthful
rollback refusal for unsupported new-format writes. Do not run old and new
writers against the same publisher/store or turn synchronized policy into
local execution authority. No installer/runtime replacement is part of this
amendment.

## Bounded execution and overlap

One invocation has a 45-second monotonic work budget from admission, including
selection, observation, export, serialization, lock acquisition and staging.
Retain the existing service collector's 30-second collection ceiling,
ten-second child ceiling and 64 KiB limits for each complete stdout/stderr.
Pass the remaining parent budget downward; no provider or cleanup gets a fresh
deadline. Reserve settlement and response time inside it. The initial local
listener adapter keeps its two-second ceiling; additional adapters need their
own admitted bounds within the same parent budget.

Use the existing caller-owned process primitive and exact native descendants.
Unconfirmed child cleanup is a custody failure, not an ordinary provider gap or
permission to start more children. A native 60-second scheduler ceiling is an
outer containment bound, not evidence that a 45-second operation passed.
Filesystem stalls or platform restrictions that defeat the bound are explicit
qualification failures; do not claim a hard deadline from a Python timer alone.

Use one local nonblocking run-admission lock in the existing health owner before
probes/exports. A second manual or scheduled run reports overlap and does no
collection. OS-backed ownership must release on actual process exit; do not
delete a lock or kill an incumbent from its age, process name, port or PID alone.
Before launching exports, persist an unsettled byte in that existing lock file.
Clear it only after native settlement; retain admission through the native
cleanup owner's callback when cleanup is unconfirmed. Process exit releases
the OS lock but not this custody fence. A later invocation must refuse rather
than infer that an exporter ended with its CLI. No automatic fence reset or
second custody registry is introduced; recovery requires the exact native
owner's settlement or explicit containment handoff.
Native scheduler IgnoreNew/single-unit behavior is defense in depth, not the
only overlap check. Run admission and the existing short slot mutation lock have
one documented acquisition order and bounded/nonblocking contention.

## Adopted one-shot operation

**Agent-recommended amendment: review before implementation.** The explicit
one-hour candidate flow does not itself authorize recurring local writes.
Add an optional one-shot operation inside the same health owner:

```text
agent-machines health run --store-dir <absolute-v2-store>
agent-machines health run --store-dir <absolute-v2-store> --apply --expect-policy <approval-token>
```

Without `--apply`, return a bounded local plan and policy approval token.
Read the explicitly selected store/policy only: no admission lock, observation,
export, slot write, runtime provisioning or schedule registration. The token
binds the canonical store and its filesystem identity, publisher/continuity,
the exact local policy and the operation contract, not one hour's candidate
or target revision. This deliberately permits a later explicitly adopted job
to repeat the same approved operation while the local policy and store identity
remain unchanged. A supplied token without `--apply`, a missing/mismatched
token or a changed store/policy refuses before observation or export.

`--apply` is the explicit request to collect and stage under that local policy.
It is not authority from report data or a synchronized policy. No setup hook
adds it to an action, and a native job may carry it only after its separate
enrollment approval. Keep the existing manual `health collect` candidate and
`health record` exact-preview/confirmation contracts unchanged.

One apply invocation freezes its current UTC hour and 45-second parent budget,
acquires one existing run-admission lock, then attempts at most two targets:
current health first, and the preceding complete hour's selected service
telemetry second. An empty component selection is an explicit not-requested
target, not permission to enumerate providers. Collection retains the same
source-hour and unselected-component rules as the manual flow.

Stage each candidate through the existing health transaction owner, reusing
its input/policy/previous-revision validation, mutation lock and atomic write.
An internal in-memory entry may avoid a second persistent candidate file, but
must share that same planner/writer rather than duplicate its checks.
Revalidate the approved policy at each commit under the mutation lock.
Hold run admission before taking the short mutation lock; constrain lock
acquisition, each collector, settlement and the final response to the remaining
parent budget, never a new per-target 45 seconds. Continue to refuse unknown
native cleanup and preserve the existing admission fence.

Report the two targets independently with a closed schema: absolute hour,
requested component, collection outcome, transaction outcome and
`applied`/`changed` facts. A later target's export or storage failure cannot
erase an earlier confirmed commit or relabel it as rolled back.
An unknown write outcome retains its exact target and forbids automatic replay.
A successful but late write reports its actual committed/unknown fact together
with timeout qualification; it must not claim an on-time successful run.
Hour changes stop further probes/exports rather than move the frozen targets.
Missing targets remain gaps, not fabricated uptime or zero telemetry.

Before any independently installed caller uses this operation, require
`agent-machines.health.collection` v1 support from its selected executable
runtime. That declaration covers policy preview and adopted one-shot apply;
it is separate from the package version and from service-export declarations.
Expand this provider before preparing a scheduler caller. Missing/unsupported
declarations mean unavailable support, never trial execution of a new command.
Runtime replacement invalidates selection and requires requalification;
unsupported rollback preserves read-only/manual flows where supported without
silently invoking an update action or adopting a new writer.

Prove preview purity, approval/store/policy drift, selected-empty targets,
partial outcomes, mutation-time revision conflicts, timeout/hour changes and
unconfirmed settlement with focused controls. The real consumer lane exercises
one invocation with fixture-owned listener and retained service data through
both committed slots and the normal reread/report/chart, plus injected second
target failure preserving the first. Keep native subprocess/storage matrices
outside required actor-free unit CI. Installed capability/mixed-version and
platform job qualification remain separate enrollment gates.

## Native job, later and explicitly enrolled

Prepare definitions separately from registering/enabling them. Use a limited
interactive-user Windows Scheduled Task and a Linux systemd-user oneshot/timer;
neither runs elevated, enables lingering nor installs a service manager.
Unsupported or logged-out/unavailable user execution remains visible.
macOS is unsupported until its own native owner is qualified.

The action resolves the exact admitted Agent Machines runtime through its
installation owner and a stable attributable launcher that never provisions or
updates. Require separately declared `agent-machines.health.collection` v1
support from that selected runtime before registration or invocation. Missing
support means unsupported collection, not an experimental new flag or fallback
to an older update command. Runtime replacement/rollback invalidates selection
and requalifies support without changing the existing update contract.

Use an hourly UTC calendar trigger with a small fixed post-boundary delay.
Late wake/start runs once for current health and the preceding hour; it does not
replay every missed trigger. Disable scheduler restart/retry storms. Require
headless launch and exact bounded child containment, definition readback and
separate configured/enabled/running/last-result evidence on both platforms.
Windows functional qualification must not depend on operator desktop attendance.
Use non-interactive launch-mechanism, execution and cleanup controls. Supplement
them opportunistically with unfiltered window/focus capture on an already active,
observable console or RDP session, binding actual task children to that session.
Unavailable or inconclusive observation records a reason, not a visual pass or
a publication/CI/enrollment gate. Preserve capture/identity and exact-cleanup
checks; do not unlock, move or elevate a session to manufacture evidence.
On Windows, a GUI-subsystem finite root must own the complete payload child
tree and propagate its result. A headless console host alone is insufficient:
its native task timeout can leave the console descendant alive, and its own
exit code does not attest collection success. Reuse the existing finite
Job/stream owner within the native 60-second ceiling; retain and refresh the
selected execution closure through installation replacement and rollback.
No hook or default setup enrolls this job. An explicitly adopted local policy
can authorize recurring stage writes without a new human preview every hour,
but is revalidated at each mutation; it cannot authorize service changes or
carrier writes. On-demand staging preserves the normal preview/confirm owner.

## First complete flow and promotion gates

Review and merge this amendment before runtime realization. Then deliver one
complete explicit local run: real fixture-owned retained Dispatch events and a
qualified listener -> normal collection command -> existing transaction owner
-> reread hourly evidence -> normal report/chart. Prove a prior-hour service
contribution and current health occupy different absolute-hour slots, preserve
an existing component and cannot manufacture past uptime.

Required pure cases cover closed schemas, component merge/invalid replacement,
clock/hour boundaries, expiry/revision conflicts, old/new readers, missing
capability and budget propagation. Native opt-in cases cover two competing
processes, writer crash/unknown commit, inherited pipes, full/read-only/unavailable
storage and bounded process settlement. Keep subprocess matrices out of required
unit CI; use the repository's existing explicit integration portfolio.

Before enrollment, qualify fresh and reconcile paths on Windows and Linux:
actual scheduler definitions, two automated cycles and non-interactive Windows
launch-mechanism evidence, overlap, suspend/late wake, deadline enforcement, failed
storage and exact fixture cleanup while preserving unrelated active work.
Visual window/focus observation is supplemental and never requires the operator
to maintain an active desktop for the test.
Record cold/warm runtime, selected service count, scan bytes/work and storage
cost for the real consumer; schema-only fixtures do not satisfy these gates.
Installed adoption, selected-carrier delay/conflict/offline/receipt evidence and
explicit cohort activation remain independent parent gates. No local source
test or reviewed schedule definition grants those effects.
