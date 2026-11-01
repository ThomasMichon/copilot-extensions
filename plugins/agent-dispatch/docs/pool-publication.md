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
The additive `configured_cohort_fencing_versions: [1]` advertises parsing and
storage fences only, not an active configuration publisher or runtime admission.
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
Both bodies also accept optional integer `expected_cohort_revision`. Absence
preserves legacy metadata writes only while no configured observation has been
projected. After projection, fresh writes require its current coordinator
revision and a complete observation containing that actual execution scope.
An unfenced legacy writer receives `409` before mutation, not guessed coverage.

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
coordinator registration revisions, publisher positions and the whole retained
configured-cohort observation, exact local-input
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
A new synchronous source observation supersedes any older cached discovery
result for that key, so a later bounded read cannot restore an obsolete worker
or clear a newer veto. The original future remains in shutdown accounting until
its actual completion; discarding cached read data is not release of its work.

This is **optimistic observed coherence**, not an atomic filesystem transaction
or a remote attestation. The digest includes the entire observed declaration
and override inputs, not just surviving workers; it is a correlation value and
does not renew freshness. A separate observation revision is retained even
when complete definitions are unavailable. Unreadable, cached or uncopyable
local inputs have no comparable observation revision and cannot authorize a
write; equal unavailable reasons are not evidence that inputs stayed unchanged.
An observed final digest change refuses preparation before retaining a request
and refuses sending any older request, including an unavailable publication.
External file writers and the complete configured cohort remain integration
gates; the explicit sender's final recheck below is still optimistic, not an
atomic filesystem fence.

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
Its additive cohort reader preserves the coordinator revision, configured
revision, exact source-byte digest, logical and intended execution identities,
state and unavailable reason. Missing legacy fields and an explicit unprojected
observation remain distinct from confirmed-empty, unconfirmed or unavailable
configuration. Duplicate or malformed members refuse the whole observation;
reported scopes cannot stand in for missing configured members.
Once a projected cohort exists, preparation requires a complete observation
containing this actual execution machine/environment; a matching logical name
alone is insufficient. The original request emits that retained coordinator
revision as `expected_cohort_revision`. Fenced POSTs additionally require the
running peer's optional version-1 cohort support before local admission or
effects. Unfenced requests retain their supported legacy wire form; an old or
rolled-back peer never receives a downgraded copy of a fenced request.
Subsequent preparation cannot silently lose/regress the settled cohort revision,
reuse it for different observation data, or change the same-position retained
scope's attribution. These are observed metadata fences, not an attestation
of current external configuration or runtime membership.
Missing, old or unsupported support refuses the new operation without trying a
legacy write. **No serve-loop caller or pool activation is wired.**
These source prerequisites leave the provider's
metadata-only/false admission and placement advertisements unchanged.

### Fresh configured-owner projection preparation

`PoolCohortPreparation` explicitly brackets two fresh
`RegistrarSources.configured_supervisor_cohort()` reads with whole coordinator
cohort observations. It retains one immutable original observation and its
original coordinator CAS only when both source reads and both coordinator
observations agree. It does not accept a preferred member subset or inferred
enrollment roster. Logical names and intended execution identities remain
separate. Original source/coordinator target paths remain in local request
custody, not the metadata body; equal bytes or revisions at a different target
cannot silently replace either owner.

Confirmed file absence, digest-bearing legacy configuration, explicit empty
membership and digest-bearing invalid cohort metadata retain their distinct
whole observations. Unreadable or invalid-registry observations without
comparable source evidence refuse preparation; two identical error reasons are
not a freshness fence. A pending request blocks another preparation before
source I/O. Returned dictionary copies cannot alter its original members,
source digest, configured revision or coordinator CAS.

Acknowledgment validates the complete original observation and its exact
result revision, including an already-satisfied no-op, and requires the
original coordinator target even if another database returns identical data.
Before clearing custody it also verifies that exact result in the original
coordinator history and rechecks the target after the read. A caller-supplied
lookalike is not accepted-result evidence. Unknown or mismatched results retain
custody. Recovery reads the existing coordinator cohort history
for that original observation and CAS, including after a newer projection or
queue reopen. It neither reads a newer source nor writes, advances clocks,
renews freshness or erases retained publication/startup history.
`observe_pool_configured_cohort()` likewise recovers an exact historical
original result before applying current-state admission to a fresh write.

This is **source-only optimistic preparation and result accounting**. No
projection sender, live HTTP configuration publisher, ordinary caller,
configuration setter, membership activation or placement is wired. The local
custody mutex is not an OS-lock acquisition or coordinator lease. External-file
atomicity, admitted execution, mixed-writer exclusion and rollback remain
separate integration gates; a projected observation is not current-file or
runtime attestation.

## Implemented explicit sender and unknown-outcome recovery

`send_pool_metadata_request()` sends or recovers only the request already
retained by `prepare_pool_metadata_request()`. It does not prepare another
request, enroll automatically, run from a tick/serve loop or enable placement.
The existing read executor carries one request-bound future in the daemon's
probe/retirement/shutdown accounting. Concurrent callers reuse that future.
A bounded caller timeout does not cancel, replace or forget the original RPC.
Completed failures leave the immutable request pending; the next explicit
invocation recovers it, not a newer input revision.
Successful completed flights stay retained until acknowledgment, including a
timeout racing completion or an unavailable acknowledgment lock. They can
settle after shutdown without needing another recovery read.
A failed flight that finishes after a caller timeout is observed and logged
on the next invocation, which recovers the same original request immediately
rather than requiring another invocation to discard the old failure.
Validated acknowledgment stays on that request-bound flight, so a delayed
waiter cannot acknowledge an old result against a newer pending request.

Recovery first reads the unfiltered catalog through the negotiated client. An
already-accepted original generation/revision is parsed and checked against all
original request and definition-attribution fences before custody is settled.
This read may account for a result after release/reacquisition or a source change,
without another POST or freshness renewal. A successor or different result
cannot rebase the pending request. Required response fields, scope, versions,
integer revisions, finite freshness and lossless definitions are validated;
additive response fields remain compatible.
The original catalog read retains each complete scope, not just its position.
Enrollment acknowledgment must preserve that original definition/freshness
baseline exactly, including empty, unattributed initial enrollment.
Its enrollment attribution matches the original begin fence; a publication
preserves the original scope's enrollment attribution even when the current
cohort has advanced. Complete definitions receive the original publication's
cohort revision, while unavailable results retain the last-settled definition
attribution. A fabricated or missing expected attribution leaves the same
request pending. Accepted original results can still be accounted after
cohort removal, uncertainty or revision advancement without another POST,
freshness renewal or re-enrollment.
Pending replies cannot attribute definitions to their new, unpublished
generation; complete and unavailable replies retain their respective
current-versus-prior attribution rules. Malformed attribution cannot become
the baseline for a later otherwise-valid acknowledgment.

If the original result is not observable, the client negotiates the running
peer again before POST. Its write boundary rechecks the actual original
singleton acquisition and pending request, fresh complete local sources (or
the same whole-scope unavailable observation), coordinator registration revision
and every original publisher position. Changed sources, delayed observations,
withdrawals, a changed publisher or closed admission refuse the write.
The original and current final local-read revisions are retained separately from
the collection revision and must both match it. A repeated final-read failure
cannot qualify merely because its reason and earlier collection stayed equal.
The original capture deadline is checked again immediately before POST admission,
after parsing and final comparisons, not just before source collection.
That final closed-state/deadline decision shares the short shutdown admission
guard. Native acquisition and source guards are also released after that
decision, before blocking I/O; an admitted operation stays tracked even if
shutdown closes or the acquisition is released immediately afterward. The
request body retains its original generation, revisions and definitions; an
unavailable request still carries an empty definition array.
These are bounded, optimistic source checks, not atomic external-file fencing
or remote OS-lock verification.

Shutdown closes metadata-send admission before draining existing futures.
Submission and future registration share a short admission guard with that
close, separate from source collection. Closed admission
refuses a new flight; it does not prevent a retained flight from settling.
A delayed preflight cannot initiate a new write after that close. An
already-admitted POST remains under its original request-bound future owner until
it finishes, not under the singleton release mutex. Its caller's bounded wait
and shutdown drain may return with visible
pending custody. A later matching result can settle that custody, but cannot
reopen send admission or the original acquisition. No new lease, registry,
executor, timer or durable history store is introduced.
The actual self-update/release path can therefore proceed after its bounded
drain without waiting for that RPC's network timeout. The provider's original
generation/revision fences and retained request still govern the late result;
this is not remote OS-lock verification or permission for replacement work.

Control credentials authorize metadata only. Missing/unsupported running-peer
support, authentication refusals, conflicts, malformed replies and transport
errors remain explicit; no new write is replayed as a legacy registration.
Configured-scope completeness, mixed-cohort qualification, external atomic
fencing and placement remain separate integration gates.

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

### Configured cohort source

The existing registrar pointer-registry owner now has an explicit read-only
`RegistrarSources.configured_supervisor_cohort()` observation. An optional
version-1 configuration envelope in its existing `pointers.json` contains
the complete intended supervisor cohort, independent of publishers that have
already enrolled or registrations that have already been reported:

```json
{
  "version": 1,
  "pointers": [],
  "configured_supervisors": {
    "revision": 0,
    "members": [
      {"machine": "host", "execution_machine": "host-linux", "env": "linux"},
      {"machine": "host", "execution_machine": "host-windows", "env": "windows"}
    ]
  }
}
```

The configuration authority must declare the **whole** cohort, not the currently
reachable hosts or a caller's preferred pools. `machine` is logical identity;
`execution_machine` and `env` identify the intended distinct execution scope.
None is inferred from the observer's host, a label or a published registration.
Duplicate execution scopes, missing identities and malformed members withhold
the entire membership tuple. A valid explicit empty list means the configuration
declares no supervisors; a missing file, legacy pointer list or omitted cohort
does not prove that. These remain explicitly unconfirmed. Unreadable, malformed
or unsupported configuration is unavailable, never a retained complete result.
Duplicate JSON keys are rejected at every object level before selecting
pointers or membership, so a later edit cannot erase hidden configuration.

The observation retains the configured revision and a separate digest of the
whole file's exact bytes, including pointer inputs. A fresh read does not modify
either, and an earlier immutable observation cannot change when the file is
replaced. The digest is optimistic input correlation, not a monotonic writer
generation, an atomic external-file fence or runtime attestation.
Existing pointer add/ensure/remove/save operations share the existing pointer
lock and preserve the envelope, cohort revision and other configuration fields.
An unchanged save does not rewrite the file. Invalid cohort metadata remains
visible rather than being erased by an unrelated pointer edit.

This is a **source-only provider expansion**. Legacy list readers cannot read
the envelope, so no existing configuration is migrated or rewritten into it
automatically. All configuration writers and running readers must be qualified
before an authorized adoption; provider rollback must remain fenced rather than
silently stripping membership metadata. No CLI setter, serve/tick caller,
runtime configuration publication, enrollment, scope withdrawal or placement
is enabled. The health descriptor still advertises
`configured_scope_membership: false`. Configured intent is not evidence that
the named runtime exists, holds its singleton or supports atomic placement.

### Coordinator projection and metadata fences

`observe_pool_configured_cohort()` is an explicit source-only seam on the
existing catalog owner. It accepts the registrar's whole immutable observation,
not a pool list, preferred subset or caller completeness flag. The existing
catalog singleton retains it with a separate monotonic coordinator cohort
revision; its history is append-only. The source's configured revision and
exact-byte digest remain separate. Equal fresh observations and original
immediate retries do not advance the clock. Changed membership requires a
new configured revision; a pointer-only byte change can retain that configured
revision but advances the coordinator fence. Regression or revision reuse for
changed members refuses even after an unavailable observation.

The same SQLite write transaction checks cohort revision and actual
`execution_machine`/`env` membership before fresh publisher enrollment or
publication. Logical `machine` is retained, never substituted for that scope.
Unavailable and unconfirmed observations block new writes without deleting
scopes, definitions or history. Missing configured scopes, retained unconfigured
scopes, stale cohort attribution, pending, unavailable or expired publications
block the global catalog. Membership removal does not silently withdraw a scope.
Accepted original begin/publication retries remain recoverable after membership
changes without another write or freshness renewal. An unavailable publication
preserves the prior definitions' cohort attribution.

Unfiltered GET adds `configured_cohort`, `configured_metadata_ready` and each
scope's enrollment/definition cohort revisions. Explicit complete empty intent
can have confirmed configured coverage; the legacy `metadata_ready` still
requires at least one scope. A missing projection preserves the legacy enrolled
metadata contract but never claims configured completeness. Additive response
readers preserve the optional attribution and reject malformed revisions.
The existing unfenced request-custody owner cannot acknowledge a response as
though its original request carried a cohort fence.

No HTTP configuration-publisher route or ordinary consumer is wired. This
projection is retained metadata, not proof that an external file is still
unchanged, that all configured writers enforce the fence, or that the named
process holds its singleton. External atomicity, runtime publisher integration,
old-writer exclusion and supported rollback remain prerequisites to adoption;
the runtime membership/lock-holder/placement advertisements stay false.
In particular, older positional catalog-clock initialization cannot reopen the
expanded clock schema. The source upgrade preserves legacy rows without
backfilling membership; it does not qualify an older executable against that
schema or authorize a live database migration.

### Runtime and placement qualification

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
The local collector, bounded preparation and explicit sender refuse
failed/cached/uncertain complete observations. The sender carries the retained
unavailable request rather than reusing an older complete tuple, checks the
original acquisition at the write boundary and recovers unknown outcomes before
changing requests. External source atomicity and installed publisher activation
remain unqualified.

The controlling registration owner must still integrate fresh configured-source
publication through the coordinator's admission owner. The source projection
and fences above do not install that caller. These primitives
retain every scope it enrolls; an empty store does not prove there are no
configured pools. Scope withdrawal and
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
