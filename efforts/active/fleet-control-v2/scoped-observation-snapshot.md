# Isolated scoped point-set snapshot

**Status:** Implemented local prerequisite (#8303), following reviewed plan
#8290. Resident integration, scope-safe recovery and adoption remain separate.

Back to [Fleet Control V2](README.md). This is an **agent-recommended bounded
implementation contract** for the next observation prerequisite under #5862,
not a resident federation, complete-roster or subscription implementation.

## Request and scope

After the federation intent amendment and its publication recovery, the
operator directed verbatim:

> Great. Keep driving.

Continue the existing isolated observation lane. The specific API, limits,
source seam and validation below are **agent-recommended**. Preserve the earlier
no-other-agent-coordination instruction and all retained ownership boundaries.
The plan must pass actual review and merge before runtime edits.

Own only `observation_reader.py`, `observation_store.py`, their directly related
tests and documentation. Compose existing admission and storage; do not change
the verifier, policy provider, producer, resident Gateway, Manager, SSH,
enrollment, installer, deployment, leases or default CLI behavior. The completed
single-point `read` contract remains unchanged.

## Bounded API and consistency

Add an explicit `ScopedObservationReader.snapshot(...)` returning an immutable
tuple of existing `ScopedObservation` values. Its arguments retain the real
verifier result, one explicit `ObservationRead`, role and installation.
Accept between one and 64 exact correlated branches. Refuse omitted, empty,
oversized and repeated origin/project/entity selections before opening the
fence or store; requesting the same entity through different field branches
does not silently merge grants.

Acquire the existing mandatory shared reentrant policy fence with its unchanged
one-second bound. Authorize the whole exact request before any entity lookup,
including when one forbidden branch accompanies permitted ones. Require the
same admission/store authority and exact fence binding as the point reader.
Use one finite permit for the batch rather than one permit or proof per point.

Add a trusted `ObservationStore.read_points(...)` seam accepting a bounded
tuple of exact origin/project/entity keys. Validate every key before opening
one query-only SQLite transaction. Perform only indexed point lookups within
that same committed read snapshot, retaining the existing schema, identity,
binding and complete-row validation. Reuse the point lookup and projection
logic; do not build a second query engine or repeated independent transactions.
Existing read/page/replay behavior and storage schema remain unchanged.

The batch is coherent at one central SQLite snapshot, not simultaneous source
observations across machines. Result order follows the existing request's
canonical branch order. Preserve each point's selected fields, source
revision/generation, observation time, availability, reason and coverage.
Unobserved, removed and origin-resync-required stay distinct; an unbound origin
or corrupt/unavailable store remains an error, not partial successful data.
All-or-error applies to the entire tuple.

Return no global cursor, position, export sequence, store identity, producer,
installation, enrollment, permit or unselected field. A tuple's requested
points do not establish complete roster coverage or absence beyond those
points. Trusted storage still validates whole rows internally; selected
projection is not SQL-level field hydration.

After all lookups and projection, run the existing fresh terminal completion
checkpoint and consume the exact batch permit before releasing the fence and
returning. Revocation, enrollment/scope changes, expiry, clock rollback and
callback errors withhold the whole result. Storage/projection failures also
complete the permit without restoring consumed proof evidence or retaining
replacement capacity.

## Explicit exclusions and follow-on

This result describes one bounded local point-set snapshot at completion.
It is not a wire grant, stable pagination session, replay cursor or delayed
emission authorization. No local/remote source selection, All-view assembly,
origin publication, remote probing, persistent view state or subscription
service is added.

Scope-safe recovery remains a later reviewed slice. In particular, the raw
store's global retention floor and cursor cannot simply be forwarded to scoped
clients: unrelated activity must not become exposed sequence/count metadata
or an unexamined recovery contract. Authenticated snapshot provenance, durable
shared policy epochs, transport emission and live adoption remain with their
existing owners.

## Validation plan

The following **agent-recommended gates** are complete for this bounded local
prerequisite; they do not complete the parent effort's serving/adoption gates:

- [x] Invalid/implicit/empty/over-limit/duplicate selections perform zero
  storage access; one forbidden branch in a mixed request performs zero entity
  lookups. Correlated origin/project/entity/field grants never cross-product.
- [x] Real verified identities produce exact immutable selected-field tuples,
  with canonical order, no prohibited metadata and honest point coverage.
  Missing fields remain distinct from explicit nulls.
- [x] Real SQLite separate-process contention proves a concurrent committed
  update cannot split the batch. Include a controlled sequence of independent
  point reads that demonstrates the mixed-state failure this API prevents.
  Retain bounded cleanup and process deadlines; do not weaken existing limits.
- [x] Enforce one query-only read transaction and at most one indexed current
  lookup per requested key, including the 64-point bound. No page, replay,
  history scan or per-client source query is a substitute.
- [x] Preserve missing/removal/resync and unbound/foreign/corrupt-store behavior.
  A failure on a later point or at final authorization returns no partial tuple
  and leaks no retained permit capacity.
- [x] Fresh final checkpoints withhold the entire prepared result for
  revocation, changed enrollment/scope, expiry, rollback or owner failure;
  native fence contention and mixed direct callers preserve the established
  ordering and one-second acquisition bound.
- [x] An independent fresh-process consumer uses production modules, actual
  cryptographic verification and SQLite; importing the reader remains
  dependency-free without optional cryptography.
- [x] Run the smallest affected reader/store/admission selection under the
  unchanged contained budget, touched-code lint and relevant repository guards.
  Record unit, process E2E, clean-room and available live tiers honestly;
  no installer/provider/deployment claim follows from this isolated library.
- [x] Complete documentation-impact review, the standalone consumer changefile,
  actual current-head review and required CI before implementation merge.

The parent effort remains Active. This contract closes neither the central
serving/recovery gates nor another participant's source responsibility.

## Local proof and remaining tiers

The owned snapshot/reader/admission/store selection passes 162 tests in
50.23 seconds under the unchanged 120-second contained budget on native Windows.
The 64-point case checks one query-only transaction, exact indexed lookups and
no history/replay scans. One real origin-generation transition atomically
changes coverage for two selected points: the coherent snapshot sees both old
bindings, while independent point reads demonstrate an impossible mixed view.
Both point and snapshot mixed-caller lock tests retain their inverted-order
deadlock negative controls. A production-only child consumer verifies actual
signed credentials and field isolation for both APIs.

Unit and simple real-process E2E tiers were exercised. No installer or
first-provision path changed, so no installer clean-room claim is made. Provider,
resident transport and live deployment are excluded from this isolated slice.
The available periodic hosted Linux portfolio does not include standalone
Fleet Manager, and its Windows execution is unenrolled; required unit/guard CI
is not a replacement for native full-Fleet integration. No Linux or live
provider acceptance is asserted and no shared CI lane or deadline was changed.

Implementation #8303 actually merged as
`5ec4ed938679d9bc1bb5fc9d190953e1fddaf8b7` after current-head approval,
required CI and feedback resolution. The parent Journal records publication
history; the runtime API documentation records the delivered local behavior.
