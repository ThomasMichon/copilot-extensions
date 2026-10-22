# Startup completion and dormant-process fencing

Back to the [Dispatch guide](../README.md).

Task status, resource custody and process capacity have different lifetimes.
A terminal or suspended task does not prove its original launch caller has
returned. `startup_complete` is recorded only by that caller after its
synchronous launch/resume succeeds. Recovery publication cannot manufacture
completion. A new startup invalidates completion and advances
`startup_revision`; an older callback cannot complete the newer revision.

Incomplete `reserving`, `spawned`, `releasing` and historical incomplete `cold` reservations retain
process capacity before and after task abandonment, regardless of a cached
cooling observation or a currently absent body. Completed terminal startups
must be fenced against future startup before exact absence can free capacity.
This does not settle the reservation, release an exclusive resource or
authorize workspace cleanup.

Interactive re-embodiment also remains incomplete through the final ownership
bind, not merely through acquisition. Release requests do not close a pending
startup; exact current absence cannot retire it while its original caller may
still act. A definitively failed original caller can supply
`finished_startup_revision` to `request_spawn_release`. The coordinator checks
that revision and fences further startup without setting `startup_complete`.
This is not an observer's reconciliation shortcut: lost responses or remote
operations that can still apply must remain unresolved. The first-party
failed-create paths supply this closure only for a definitive `not_started`
outcome. A governance cancellation after workspace creation but before launch
also closes its own startup revision through that failed-caller path.

A successful original callback can publish its exact identity and matching
completion revision while release is pending. Publication preserves
`releasing`, the release disposition and exclusive-resource release state;
it never promotes the reservation back to `spawned`. Revision mismatch,
fenced startup and replacement of an already completed released identity
remain errors. Cleanup still requires exact body absence after completion.

Retained native CLI liveness uses the exact session's machine from current
ownership or durable attachment history. An abandoned task's cleared owner
must not redirect a remote-body probe to the local registry. Missing or
conflicting location retains capacity and cleanup custody.

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

Resume keeps startup incomplete through task-state finalization, publishing
completion only after the same owned task is resumed. Otherwise a cooler could
observe a completed startup while the task still appears suspended, stop its
body and leave the subsequent task transition incorrectly marked started.
An error finalizing that transition is not proof that the request cannot
still apply. It retains incomplete startup and occupied capacity rather than
automatically recooling a body under an unproven task-resume outcome.
If the successful process-resume callback instead observes an already
releasing reservation before task finalization, it publishes only the
matching startup outcome and leaves task state and release intent unchanged.
Task start/resume transitions also reject a retained cooling guard. Steering
during a guarded headless stop remains durable and requests a later resume;
it does not start the task or enqueue an interactive wake through that guard.

## Compatibility and graceful cutover

The columns are additive. Existing rows receive `startup_complete=false`,
`startup_revision=1`, `startup_fenced=false` and `cooling_token=NULL`;
migration does not infer historical launch completion. Historical unproven
rows stay blocked pending explicit reconciliation.

The affected residents are the coordinator (`serve`), supervisor daemon
(`supervise serve`) and its worker-lane supervisors (`supervise`). The existing
`scripts/install.sh` / `scripts/install.ps1` install/update/activation seams own
the coordinator's routed graceful cutover and the supervisor's separate
singleton handoff; neither is changed here. See
[supervisor lifecycle](supervisor-lifecycle.md),
[graceful daemon cutover](../../../docs/patterns/graceful-daemon-cutover.md)
and [service lifecycle supervision](../../../docs/patterns/service-lifecycle-supervision.md).

Old launch callbacks that omit completion remain unproven. A new supervisor
against an older coordinator cannot acquire `claim-cooling`, so it does not
perform the physical stop. This is not safe mixed-supervisor compatibility:
an old supervisor can still stop a process without acquiring the new guard.
Close old admission and drain original launch/stop callers, old coordinator
requests and old supervisors before an authorized coordinator/schema cutover.
Preserve unresolved reservations, then start only compatible supervisors.
Coordinator routing alone is not that drain: it intentionally retains the
separately supervised processes. Do not roll back to guard-unaware writers
while guarded operations remain. This applies on Windows, Linux and macOS;
the storage guard introduces no platform-specific process or signal primitive.
A source merge grants no migration or rollout.
