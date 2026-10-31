# Pool catalog provider storage

`queue_pool_catalog.py` is the internal storage expansion for the
[reviewed assignment/startup amendment](../../../efforts/active/agent-dispatch-monitor-and-confirmed-state/assignment-startup-amendment.md#selected-pool-scope-and-ordering).
It is **not yet exposed over HTTP or used by a production supervisor or
allocator**. Its metadata does not advertise atomic-placement support.

## Implemented storage boundary

Each enrolled machine/environment scope has a monotonically increasing
publisher generation. Enrollment compares the prior generation; a previously
used process incarnation cannot enroll again. An ambiguous enrollment retries
its original request, not a new request rebased onto a successor's generation.
Scope replacement compares the current publisher, generation and publication
revision inside the existing SQLite write transaction. An identical immediate
retry observes the prior result without renewing its freshness. Generation and
publication history are retained.

The registration writer increments a coordinator-owned input revision in its
existing transaction when effective kind/spec/scope/status changes or a row is
removed. Identical upserts, unchanged status and absent removals do not
invalidate the catalog. Collection must begin with that revision; publication
refuses an intervening registry mutation. The current conservative revision
invalidates every scope, including changes outside a particular worker lane.

A successful publication replaces the **whole** scope definition tuple,
including a confirmed empty set. Unavailable collection cannot present a partial
set: it records a visible reason and retains the previous definitions for
inspection with their original generation/revision attribution, but blocks the
global snapshot. Unknown, pending, expired or
registry-stale expected scopes also block it. An enrolled scope never disappears
merely because its publisher stopped refreshing.

`pool_catalog_snapshot` reads every enrolled expected scope in one transaction,
with deterministic global priority ordering. Version 1 describes this internal
metadata shape, not runtime enforcement. Freshness expires after 30 seconds,
including the exact deadline; expiry is only a new-admission blocker, never
evidence of process death or permission to free incumbent capacity.
Future placement must recheck both freshness and revisions in its own write
transaction, not rely on a preceding inspection response.

## Remaining integration gates

The daemon's actual singleton is a crash-safe **local OS lock**, not a renewable
coordinator schedule lease. Existing schedule leases are pin-not-failover;
their `holder_session` is not a shared publisher-incarnation fence. Do not
repurpose them or the producer-specific execution-runtime enrollment as proof
of a supervisor generation.

The existing reconciliation owner must enroll only its actual admitted lock
holder, preserve unknown acquisition/publication outcomes, and publish its
complete effective worker set **after** declaration/direct precedence,
equivalent-source deduplication, pauses and overrides. This storage API does not
prove that collection is complete. Its opaque `input_revision` is a correlation
provided by that owner, not an arbitrary allocator's completeness certificate.
No ordinary allocator may publish a preferred subset or available-slot count.
Input failure or cached fallback must produce unavailable metadata, not a
success-shaped publication. Pending local inputs and withdrawals need the
owner's own revision fencing before a source snapshot can be qualified.

The controlling registration owner must also establish the complete expected
scope membership. These primitives retain every scope it enrolls; an empty
store does not prove there are no configured pools. Scope withdrawal and
mixed-cohort/rollback policy remain separate reviewed integration work; there
is no automatic unenrollment or legacy identity backfill.

Add the authenticated transport and running-peer capability declaration
provider-first through existing coordinator seams. Only migrate callers after
those capabilities and complete-source collection are qualified. A legacy
peer cannot be treated as enforcing required placement semantics by ignoring
new metadata. This expansion changes no existing registration response,
subprocess spec, reservation, worker claim, startup or suspension behavior.

See [pool definitions](pool-instances.md) for the lossless instance model and
[current-versus-target architecture](spawn-supervisor.md#assignment-lifecycle-current-versus-target)
for the still-pending allocation transaction.
