# Opt-in publication on existing reconciliation cadences

`AGENT_DISPATCH_POOL_PUBLICATION=1` selects metadata maintenance in the existing
coordinator and singleton master. It defaults **off**. This is a source-level,
opt-in composition, not ordinary allocator or initial-prompt migration and not
an installed-runtime qualification.

**Do not enable it as a migration shortcut.** The first configured-cohort
projection fences fresh legacy queued spawning. Ordinary allocator/prompt and
mixed-driver consumers are not migrated by this setting; an unchanged ordinary
lane cannot continue fresh spawning merely because the metadata is ready.
Disabling maintenance does not erase the retained cohort or clear that fence.
The [assignment/startup amendment](../../../efforts/active/agent-dispatch-monitor-and-confirmed-state/assignment-startup-amendment.md)
retains those separate integration and adoption gates.

## One whole-cohort writer

`server.build_app` passes its actual `RegistrarSources` to `CoordinatorPoolSource`.
Only the selected coordinator projects the whole configured cohort. It uses
the existing liveness-GC iteration, not another loop, timer, service or executor.
Installed coordinator bootstrap, deployment and source reconciliation select
the installation's `registrar/` unless its `service.env` explicitly sets
`AGENT_DISPATCH_REGISTRAR_DIR`. A named supervisor's inherited registrar override
does not select the shared coordinator's cohort. Direct foreground `serve`
continues to honor its explicit process configuration.
An enabled positive GC interval, configured control authority and the actual
active-route callback are required. Construction and reads still write nothing;
the first eligible GC iteration performs maintenance.

Each iteration uses `PoolCohortPreparation` with the original registrar target,
queue target, observation and coordinator CAS. It shares the existing registrar
pointer-writer lock. The final source boundary checks current coordinator
activity, target identity, closed admission and drain state before the write.
The entire real operation remains counted in the existing drain gate, including
source preparation and post-write checks.

The GC caller waits at most the lesser of its interval and three seconds for
metadata maintenance before continuing its other work. Timeout or caller
cancellation retains the **same** underlying operation; another iteration
cannot submit a competing projection. Shutdown closes admission and waits up
to ten seconds for that actual operation. An unfinished worker remains retained
and counted, with a visible unconfirmed-drain warning; cancellation of a waiter
is not completion of its thread.

A known no-effect refusal permits discarding only that original request.
An admitted or possibly accepted projection is retained. The next iteration
recovers exact accepted history before attempting a fresh observation. A
post-commit source-recheck failure can therefore recover its original result,
then publish changed source on a later iteration without replaying the old
write. Reobserving unchanged intent does not increment the cohort revision.

## Per-scope master publication

`supervise serve` selects the same default-off setting for its actual
`SupervisorDaemon`. It retains the existing registrar declaration callback,
coordinator client/authority, machine/environment identity and OS singleton.
A master publishes **only its effective scope**; it never projects its own
host-local pointer file as the coordinator's whole cohort.

At discovery, one bounded operation prepares/sends or recovers the original
metadata request. Begin and whole-scope publish occur on separate iterations.
The actual source and sender guards still require fresh complete observations,
current singleton acquisition and an already-projected complete cohort containing
this execution scope. No configured revision, an excluded scope, unsupported
running-peer metadata support or missing control authority withholds publication.
Legacy subprocess reconciliation is not silently converted into pool allocation.

The operation shares the current reconcile deadline with nested source reads,
declaration writes and sender waits. Effective-source lock acquisition is also
bounded: a later tick cannot hang indefinitely behind a still-running sender's
source capture. Failed publication logs the hold and preserves accountable
work. Only the existing safe-discard operation can supersede an original
never-posted or definitively rejected request; live futures, unacknowledged
success and ambiguous POSTs remain retained.

The regular five-second master cadence renews the provider's thirty-second
publication freshness through new requests, not replay. Initial declaration
mirroring can change the coordinator registration fence and withhold the first
attempt; a later fresh attempt enrolls, then publishes. Incomplete effective
definitions never become a valid subset. In particular, legacy direct worker
rows lacking complete pool filters remain unsupported for complete publication.
Configured membership changes, retained obsolete scopes and direct-row migration
still need their owning integration; metadata maintenance does not erase them.

The installed `supervise serve --legacy-env` composition observes both current
registrar sources and the legacy env profiles. A fresh strict legacy read can
qualify the complete set; it no longer withholds every publication merely
because the compatibility reader is enabled. Missing primary/profile paths are
confirmed absence. Unreadable, invalid-encoding, unsupported-line or wrong-type
inputs withhold metadata qualification instead of certifying a partial empty
set. Strict keys must use the simple env assignment syntax; shell `export`
statements are not silently treated as inert keys. Profile enumeration retains
platform-native extension matching, including `.ENV` on Windows. Both readers
skip opening non-regular profile entries, so a failed strict read cannot block
its compatibility fallback on a FIFO or device. Legacy launch discovery
otherwise keeps its best-effort behavior. First-class
declarations still supersede same-name legacy profiles, and label-less profiles
remain inert. No source file, supervisor argv or activation setting is migrated.

The [original transport custody](pool-publication.md#implemented-explicit-sender-and-unknown-outcome-recovery)
remains in force. The sender can continue a quiescent original request or settled
publisher across endpoints only after confirming the same existing durable queue
identity. Its original body and the provider's transactional identity fence remain
in force; equal catalog revisions or definitions never establish continuity.
The cadence tests exercise both settled continuation and recovery of a lost
original reply through the actual existing reconcile owner.

## Source validation

`tests/test_pool_publication_cadence.py` exercises the real registrar, queue,
HTTP client, ASGI provider, master reconcile and coordinator GC/lifespan owners.
It covers opt-in/default-off composition, declaration mirroring, enrollment,
publication/renewal, override withdrawal, incomplete direct rows, missing or
excluded cohort, actual singleton refusal, original lost-response recovery,
safe pre-POST refresh, source/queue/route/drain changes, post-commit recovery,
timeout/cancellation without duplicate work, shutdown custody and bounded
source-lock contention.

The existing client-authority test also checks both enabled and disabled
`supervise serve` composition. Four actual `_cmd_serve()` controls verify that
the command's reconstructed configuration preserves both the opt-in and its
GC interval, including explicit refusal when publication is enabled with GC
disabled. These are contained source controls, not evidence
of live activation, native continuity or full assignment/startup acceptance.

See also: [metadata publication](pool-publication.md),
[configured-cohort transport](pool-cohort-transport.md) and
[explicit claim/start](pool-claim-start.md).
