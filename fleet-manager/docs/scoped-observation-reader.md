# Isolated scoped observation reads

`fleet_manager.observation_reader.ScopedObservationReader` composes the existing
[admission library](runtime-admission.md) and trusted
[observation store](current-observations.md). It is an explicit local Python API,
not a resident Gateway route, provider adapter, wire protocol or subscription.
Default commands do not construct or invoke it. Importing it requires no
optional cryptography dependency.
Its explicit `read` method selects one point; `snapshot` selects a bounded
point set from one committed local read view.

## Trusted owner boundary

The caller supplies an existing `AdmissionAuthority`, `ObservationStore` and
mandatory shared owner fence. The fence supports `acquire(timeout=...)` and
`release()` and must be reentrant; a `threading.RLock` is suitable.
Configure the authority with this exact same fence. Acquisition waits at most one second
for a participating lock before refusing `authority-busy`.

Every policy, enrollment and revocation writer must use that same fence.
For a fenced authority, **all** direct admission, revalidation and completion
calls acquire the fence before the authority's internal lock. A reader refuses
an authority configured with no fence or a different fence; direct callers
cannot invert that lock order. Existing unfenced authority callers are unchanged.
The admission callback still has to independently authenticate **current** source
policy and provider/issuer revocation freshness. An arbitrary lock or constructed
snapshot establishes neither provenance nor protection against an uncooperative
writer. Durable policy/enrollment generations and real provider integration
remain the serving owner's responsibility.

```python
import threading
from fleet_manager.observation_reader import ScopedObservationReader
from fleet_manager.runtime_admission import AdmissionAuthority, ObservationRead, ObservationScope

fence = threading.RLock()
authority = AdmissionAuthority(snapshot=current_policy, clock=clock, fence=fence)
reader = ScopedObservationReader(authority=authority, store=store, fence=fence)
selection = ObservationRead("example-fleet", (
    ObservationScope("origin-a", "project-a", "entity-a", ("status",)),
))
point = reader.read(verified_identity, selection, role="client", installation="client-a")
```

`current_policy`, `clock`, `store` and `verified_identity` in this example are trusted,
explicitly configured objects, not records deserialized from a caller.
Participating writers acquire `fence` before modifying the policy source.
Reentrancy is required because the reader and authority acquire the same fence;
a snapshot callback may also acquire it without changing lock order.

## One explicit correlated point

`read` accepts exactly one explicit `ObservationRead` scope. Omitted,
empty, multi-branch and other request types refuse `unsupported-read`, rather
than becoming broad or guard-only reads. Existing lower-level admission
semantics for other callers do not change.

Under the fence it authorizes the exact principal, fleet, role, installation
and correlated origin/project/entity/fields before the indexed entity lookup.
The permit authority must match the configured store authority. Denials perform
no point lookup. This adds no scan, count, page, replay or backend delegation.

The trusted store continues validating the **complete** canonical stored row
internally. Field selection is adapter-result projection, not a claim of
SQL-level selected-field hydration.

The frozen `ScopedObservation` contains its requested scope, source generation
and revision, observation time, availability, reason, coverage, and an immutable
tuple of selected scalar state pairs. A selected absent field is omitted;
an explicitly null field remains present with `None`. Only the authorized
entity's defined metadata is returned. There is no global position/cursor,
store ID, export sequence, producer, installation, enrollment, account or permit.

A missing row remains `unknown` / `unobserved` with
`point-observations-only` coverage, not confirmed absence or a complete roster.
Explicit removals retain `projection-removed`. Older origin-generation rows
remain `origin-resync-required`; their original generation is not relabeled.
Storage errors propagate rather than becoming successful empty results.

## Explicit point-set snapshots

`snapshot(identity, request, role=..., installation=...)` accepts one to 64
explicit correlated branches and returns a tuple of existing frozen
`ScopedObservation` values in the request's canonical scope order. Omitted,
empty, oversized and repeated origin/project/entity selections refuse
`unsupported-read` before fence acquisition or storage access. Different field
sets for the same entity are not implicitly merged.

The same exact owner fence and authority binding apply. The entire request is
authorized before any entity lookup; a forbidden branch with otherwise permitted
points yields no partial retrieval. One permit and verifier result cover the
whole finite batch. The store's trusted `read_points` seam uses one query-only
SQLite transaction and one indexed current lookup per requested key, retaining
complete row/binding validation rather than field-only SQL hydration.

All returned points come from one central committed snapshot. Their individual
source revisions and observation times need not be simultaneous, and the tuple
does not imply complete machine/project/fleet coverage. Missing fields, explicit
nulls, unobserved rows, projection removals and resync-required generations keep
the same meanings as a point read. Unbound origins and corrupt/foreign/unavailable
storage are errors, never partial successful tuples.

The same fresh terminal completion checkpoint runs after all retrieval and
projection, including on failure. Revocation, enrollment/scope changes, expiry,
clock rollback or owner failure withhold the entire tuple and release permit
capacity. No cursor, global position, export sequence, store/producer/installation
identity or reusable grant is returned.

The snapshot is only a bounded local read at completion. It adds no pagination,
replay, persistent view session, All-view assembly, origin push or later
wire-emission permission. In particular, the raw store's retention floor and
cursors are not a scope-safe recovery contract.

## Completion, expiry and failure

After lookup and result projection, `AdmissionAuthority.complete(permit, request)`
performs the same fresh checkpoint as `revalidate`, consumes the exact retained
handle and does **not** issue a replacement. It runs before the fence is
released, including when storage or projection raises. Proof replay remains
fenced independently; completing a permit does not restore consumed evidence.
Unknown or reconstructed handles cannot release another request's permit.

Revocation, scope/enrollment changes, expiry, clock rollback and unavailable
policy withhold a prepared result. There is no cached allow or implicit retry.
The existing permit/request bounds do not extend and do not impose a human
session countdown. Successful and failed terminal reads free permit capacity.

The returned point or tuple describes a **local read at completion**. It is not
a bearer grant or permission to emit later. Wire emission, queued/asynchronous work,
durable shared policy epochs and installed consumer adoption are outside this
API. A future transport must prove its own
real execution/emission fence; it cannot queue this value as current authority.

The reader/store/admission tests cover both APIs. A native separate-process
writer proves a point-set snapshot cannot mix a concurrent committed update;
an independent-point-read control demonstrates that mixed-state failure.
A production-only child consumer verifies actual signed identity evidence and
selected fields for two principals without importing test helpers. These are
local library proofs, not Linux, resident-federation or live-provider adoption.
