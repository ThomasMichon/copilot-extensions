# Pool catalog metadata provider

`queue_pool_catalog.py` and its control-authorized coordinator routes provide
the metadata expansion for the
[reviewed assignment/startup amendment](../../../efforts/active/agent-dispatch-monitor-and-confirmed-state/assignment-startup-amendment.md#selected-pool-scope-and-ordering).
It is **not used by a production supervisor or allocator**. Transport
authorization does not prove publisher OS-lock admission, complete configured
scope membership or atomic-placement support.

## Implemented authenticated transport

The running coordinator's existing `/health` response adds
`pool_catalog_transport`, with protocol `pool_catalog_transport/v1`, supported
metadata/definition versions and whether control authority is configured.
The descriptor explicitly advertises metadata-only support and no lock-holder
verification, configured-scope completeness or atomic placement. Read this
running-peer declaration rather than inferring support from a package version
or newer installed payload.

All three new routes require the existing control bearer, independently of
whether ordinary API authentication is enabled. On an authenticated coordinator,
the app-wide API dependency runs first: a missing or invalid bearer yields `401`
with a string `detail`. Only after that check passes does the catalog dependency
evaluate control authority. It yields structured catalog `503` detail for a
missing or empty configured control token, or `403` for missing, wrong or
ordinary data authority. On an open coordinator there is no preceding API
bearer check. Callers must handle both inherited and catalog-specific failures,
not assume every refusal has a catalog error code.
Task-scoped authority cannot access these routes. Authentication is
authorization for retained metadata, not a certificate of local source
completeness or a supervisor process identity.

| Route | Metadata operation |
|-------|--------------------|
| `GET /pool-catalog` | One unfiltered transaction over every retained scope, the catalog/registration revisions, original definitions, blockers and `metadata_ready` |
| `POST /pool-catalog/scopes/begin` | Compare `expected_generation` for the explicit machine/environment/publisher incarnation |
| `POST /pool-catalog/scopes/publish` | Compare generation, publication and registration revisions and replace the whole scope, or record unavailable inputs |

Both write bodies require integer `version: 1`, `machine`, `env` and
`publisher_id`. Begin also requires `expected_generation`; publish requires
`generation`, `expected_revision`, `registration_revision`, `input_revision`
and the entire `definitions` array, with an optional unavailable `reason`.
Definitions use the lossless [version-1 model](pool-instances.md); its reader
tolerates additive fields without dropping required permit/reject semantics.
The write envelope rejects unknown fields and coerced revisions, including
booleans. There is no caller clock, TTL, preferred subset, winner, slot count
or completeness flag. Scoped/partial GET requests are refused.

Malformed request bodies yield `422`; invalid normalized definitions yield
`400`; stale generation/revision/input fences or incompatible scope publication
yield `409`, with a visible rejection reason. A malformed array cannot publish
its otherwise-valid subset. An unknown write response must be recovered by
retrying the **original** request; successful immediate replay observes the
retained result without extending freshness. Inspection also never renews it.

This is a provider-first expansion: legacy registration requests/responses and
supervisor behavior are unchanged; ordinary callers emit no new syntax.
Missing/out-of-range transport declarations on old or rolled-back providers
are unsupported for this new operation, not permission to replay a write as a
legacy registration or to reject an otherwise-supported legacy operation.
Publisher enrollment, consumer migration and the retirement/rollback window
remain gated by the integration obligations below.

## Implemented local collection boundary

`SupervisorSources`, composed through the existing `BoundedReconciliation`
owner, collects the same declaration/direct desired set as subprocess
reconciliation. Its `pool_source_snapshot()` is an immutable, all-or-none
local observation **before** companion resolution/materialization, **after**
declaration precedence, runtime-equivalent-source deduplication and concrete/
logical override vetoes. Paused direct rows remain excluded by the existing
scoped registration read. Non-worker registrations remain in the legacy desired
set but are not pool definitions.

Each attempt requires observations from all three sources and an identified
machine/environment. Effective worker rows need complete, scope-consistent
normalized definitions. A missing or invalid definition cannot simply disappear
from the pool list. Failed or cached reads, a future consumed from an earlier
attempt, unconfirmed declaration writes/withdrawals and duplicate source IDs
make the entire collection unavailable. Inspecting the retained snapshot does
not perform I/O or renew freshness; unsuccessful attempts do not expose a partial
definition tuple. The catalog storage owner retains earlier published evidence.

Declaration metadata confirmation must match this attempt's complete publishable
ID set and every current target. Settling an unknown older write is not confirmation
of a changed or removed declaration. An exhausted publication budget cannot
renew a complete snapshot from a previously confirmed ledger.

Ordinary supervisor declaration discovery consumes the registrar's actual
refresh report in the same bounded source call. Indeterminate trusted/plugin
scans and any plugin finding conservatively withhold complete publication,
including retained or quarantined entries. Trusted-over-plugin name precedence
still uses the registrar's selected declarations. Legacy env profiles and
plain custom iterable callbacks remain usable by subprocess reconciliation but
do not yet provide a completeness observation.

The daemon reads overrides once in strict observation mode. Confirmed absence
is an empty map; unreadable/invalid input is not. On malformed maps it retains
the same valid-entry veto subset used by the legacy reader while withholding
complete publication. Existing `load_overrides` callers keep their best-effort
default. This observer adds no registration store, source scan, timer, endpoint,
process or allocator.

Collection completeness is not a coherent publication fence: the local attempt
number is inspection correlation, not a coordinator revision. Final publication
still needs the lock-holder generation, coordinator input revision and local
source rechecks below. Collection alone does not establish the complete configured
scope membership or executing-runtime support.

## Implemented bounded preparation and request custody

`SingleInstance.acquisition` identifies one actual local OS-lock acquisition,
not a pid sidecar, schedule lease or bearer credential. Repeated acquire by the
holder preserves it; release/reacquire creates a new incarnation. A forked child
cannot inherit its parent's admission or unlock the parent's lock when releasing
its inherited descriptor. The acquisition guard serializes bounded local
preparation with release. `SupervisorPoolPublication` accepts only the daemon's
actual lock for its configured machine/environment scope, not an injected
boolean-only lock or a live lock for a different scope.
The child resets its inherited Python mutex before attempting to acquire it,
including when another parent thread held that mutex at the fork.

The explicit `capture_pool_publication_inputs()` seam brackets the existing
effective-set collection and a fresh declaration/override recheck with two
control-authorized coordinator catalog observations. It requires equal
coordinator registration revisions and publisher positions, exact local-input
digests and fresh reads from this attempt. Declaration publication or any other
registration mutation during collection makes that attempt unavailable.
Delayed futures, read failures, incomplete sources and changed local inputs do
not authorize a complete request. Override reads share the owner's bounded
discovery pool and remain in its existing future/shutdown accounting.
The new timeout path retains confirmed vetoes; without a first confirmed veto
snapshot it is indeterminate, not permission to start units against an unknown
override. Completed older reads remain unqualified until a fresh attempt.
Capture owns a context-local deadline shared by its reads and declaration
publication; concurrent tick setup/cleanup cannot overwrite that budget.
That deadline starts before admission. Both the acquisition-mutex and source
collection-lock waits consume it; request preparation preserves the same
deadline through nested capture rather than granting a fresh budget.
Completed discovery results are pruned at both capture boundaries through the
existing future owner, preserving every still-pending operation.
Valid outer override mappings are normalized without dropping vetoes.
If a valid record cannot be copied into revision evidence, its veto still
applies to legacy desired state while complete publication stays unavailable.
Collection and rechecks execute each stateful declaration/override callback
through the same per-source reader guard, including lazy iterable consumption.
A timed-out read remains tracked and serialized until it actually finishes;
it cannot overwrite a newer confirmed deletion through another future key.
Caller waits still consume their existing deadline instead of waiting
synchronously for that reader guard.

This is **optimistic observed coherence**, not an atomic filesystem transaction
or a remote attestation. The digest includes the entire observed declaration
and override inputs, not just surviving workers; it is a correlation value and
does not renew freshness. External file writers, the final effect-boundary local
recheck and the complete configured cohort remain integration gates.

`prepare_pool_metadata_request()` captures through that owner rather than
accepting a caller-supplied completeness certificate. It retains exactly one
immutable original begin or whole-scope publication request. Unresolved results
block another request before discovery; replay materialization preserves the
original generation, publication/input revisions and definitions. A successor's
position never rebases that request. An unavailable publication has an empty
definition array and an explicitly unavailable attempt correlation; its result
must retain the previous definitions' original generation/revision. Matching
late results can settle the old custody after release, but never restore its
admission. The unresolved and last-settled request/response remain inspectable
with bounded local memory; durable publication history stays provider-owned.

The client read negotiates the running peer's version-1 metadata/definition
support and configured control authority before accessing the unfiltered catalog.
Missing, old or unsupported support refuses the new operation without trying a
legacy write. **No serve-loop caller, enrollment/publication RPC sender or pool
activation is wired.** These source prerequisites leave the provider's
metadata-only/false admission and placement advertisements unchanged.

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
The local collector and bounded preparation refuse failed/cached/uncertain
observations. A future sender must carry the retained unavailable request rather
than reuse an older complete tuple, recheck the original local acquisition at
the write boundary and reconcile unknown outcomes before changing requests.
External source mutations and final write-boundary rechecks remain unqualified.

The controlling registration owner must also establish the complete expected
scope membership. These primitives retain every scope it enrolls; an empty
store does not prove there are no configured pools. Scope withdrawal and
mixed-cohort/rollback policy remain separate reviewed integration work; there
is no automatic unenrollment or legacy identity backfill.

The authenticated transport and running-peer declaration are provider-first
metadata support, not executing-runtime qualification. Only migrate callers
after those capabilities, actual lock-holder publication and complete-source
collection are qualified. A legacy
peer cannot be treated as enforcing required placement semantics by ignoring
new metadata. This expansion changes no existing registration response,
subprocess spec, reservation, worker claim, startup or suspension behavior.

See [pool definitions](pool-instances.md) for the lossless instance model and
[current-versus-target architecture](spawn-supervisor.md#assignment-lifecycle-current-versus-target)
for the still-pending allocation transaction.
