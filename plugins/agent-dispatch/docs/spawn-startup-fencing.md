# Startup completion and dormant-process fencing

Back to the [Dispatch guide](../README.md).

Task status, resource custody and process capacity have different lifetimes.
A terminal or suspended task does not prove its original launch caller has
returned. `startup_complete` is recorded only by that caller after its
synchronous launch/resume succeeds. Recovery publication cannot manufacture
completion. A new startup invalidates completion and advances
`startup_revision`; an older callback cannot complete the newer revision.

Incomplete `spawned` and historical incomplete `cold` reservations retain
process capacity before and after task abandonment, regardless of a cached
cooling observation or a currently absent body. Completed terminal startups
must be fenced against future startup before exact absence can free capacity.
This does not settle the reservation, release an exclusive resource or
authorize workspace cleanup.

## Physical cooling versus resume

A last-minute GET is not stop/resume exclusion. Before probing or stopping a
dormant local/fleet body, the supervisor acquires `claim-cooling` from the
coordinator for the exact state, handle and completed startup revision.
The write transaction validates task eligibility and stores a unique
`cooling_token`. Concurrent cooling, startup/resume publication, identity
replacement, release and terminal fencing cannot pass that retained guard.

The same token and revision must accompany the successful `cold` publication,
which atomically consumes the guard. Stranded queued/unowned cold recovery
also acquires this guard before a physical stop, then passes its token,
startup revision and task generation to `defer`. Deferral checks completion
and the still-queued, unowned task inside its publication transaction.
Unproven historical cold rows cannot be probed, stopped or deferred.

Tokens have no timeout. Lost claim/publication responses, failed or ambiguous
stop callbacks and coordinator/supervisor restarts retain the guard and
capacity; elapsed time or present absence cannot establish that a delayed stop
will never take effect. Reconciliation must establish that the original stop
caller can no longer act and the exact body is stopped, then publish the
matching guarded outcome through the owning coordinator. Never clear the
column directly or resume first to test whether it is safe.

## Compatibility and graceful cutover

The columns are additive. Existing rows receive `startup_complete=false`,
`startup_revision=1`, `startup_fenced=false` and `cooling_token=NULL`;
migration does not infer historical launch completion. Historical unproven
rows stay blocked pending explicit reconciliation.

Old launch callbacks that omit completion remain unproven. A new supervisor
against an older coordinator cannot acquire `claim-cooling`, so it does not
perform the physical stop. This is not safe mixed-supervisor compatibility:
an old supervisor can still stop a process without acquiring the new guard.
Quiesce original launch/stop callers and old supervisors before an authorized
coordinator/schema cutover, preserve unresolved reservations, then start only
compatible supervisors. A source merge grants no migration or rollout.
