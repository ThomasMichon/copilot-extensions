# Isolated scoped observation reader

**Status:** Implemented local prerequisite (#8052); resident/provider/adoption
gates remain open in the parent effort.

Back to [Fleet Control V2](README.md). This is an **agent-recommended bounded
implementation contract**, accepted by the operator as `isolated-scoped-reader`.
It closes a local composition prerequisite of the reviewed
[current-state plan](central-state-backing-store.md), not its serving, provider
or adoption gates.

## Scope and explicit exclusions

Compose the existing `AdmissionAuthority` and `ObservationStore` in one small
transport-neutral reader. Accept a real verifier result and exactly one explicit
`ObservationRead` branch. Omitted, empty and multiple branches are unsupported
by this point-reader surface; they must not become an implicit broad query.

Own only the new reader, terminal permit completion in the existing admission
library, directly related tests and documentation. Do not edit resident Gateway
consumers, identity providers, enrollment, producer or Manager implementations,
installers, deployment, leases or another campaign's files. No network endpoint,
wire cursor, subscription, command execution or automatic activation is added.

## Read and authorization boundary

- Require an explicit trusted owner-supplied lock with bounded acquisition.
  Policy/enrollment/revocation writers must use this same fence. The reader
  cannot authenticate an arbitrary callback or force an unrelated writer to
  participate; the eventual serving integration must prove that contract.
  The authority must bind this exact reentrant fence at construction and acquire
  it before its internal lock on every admission/checkpoint path, including
  direct callers. A reader refuses unbound or differently fenced authorities.
- While holding the fence, authorize the exact request immediately before its
  one indexed storage lookup. Check the store's authority against the permit.
  A denied principal, actor/fleet mismatch or forbidden correlated branch
  reaches no entity lookup.
- Use the existing store's validated point read. The trusted store still
  validates its complete canonical row internally; this is not SQL-level
  selected-field hydration. The adapter's result contains only selected state
  fields and explicitly defined metadata for that authorized entity.
- Project an immutable result containing the requested scope, selected scalar
  state, source revision/generation, observation time, availability, reason and
  coverage. Preserve unobserved, removed and origin-resync-required distinctions.
  Do not return the store identity, global position/cursor, export sequence,
  producer, installation, account, enrollment or permit.
- After lookup and projection, consume the permit through a final fresh
  authorization checkpoint before releasing the fence and returning data.
  Revocation, changed enrollment/scope, expiry and owner failures withhold the
  result. Terminal completion must not retain another live permit or extend
  its validity. Storage exceptions remain errors, never successful empty rows.

The returned value is a local point observation at completion, not an
authorization handle or permission to emit later. Real transport emission,
asynchronous work, multi-point snapshot consistency and shared durable policy
epochs remain separate integration gates. A future caller must not queue this
result as a substitute for current execution/emission authorization.

## Validation

- [x] Denied/empty/foreign or unsupported requests perform zero entity lookups.
- [x] Real verifier evidence for two principals yields isolated SQLite views,
  with no field/entity cross-product widening.
- [x] Exact field projection and immutable results omit every global cursor,
  position, sequence and unselected field; missing fields remain distinct
  from explicit nulls.
- [x] Missing rows, removals and stale origin generations remain explicit.
- [x] Real lock contention is bounded; a participating revocation writer is
  serialized with the read. Revocation/expiry at the completion checkpoint
  withholds results.
- [x] Successful and failed reads release terminal permit capacity, while
  proof replay and fabricated/foreign permits remain refused.
- [x] Storage and callback failures propagate; no silent retry or cached allow.
- [x] A fresh-process real-identity/SQLite consumer uses production modules,
  and importing the adapter preserves the dependency-free client boundary.
- [x] Existing admission/store tests retain the unchanged 120-second contained
  selection bound; exact-head review and required CI pass before implementation
  merge (#8052).

No first-machine installer behavior changes. Clean-room role installation and
live external provider/serving proof are not supplied by this isolated library;
those larger Plan and Validation Plan gates remain open.

## Validation evidence

The owned reader/admission/store selection passes 123 tests in 48.21 seconds
under the unchanged 120-second containment limit, including the real independent
consumer and lock cases. The dated parent Journal records execution history,
including failed attempts.

The [implemented API](../../../fleet-manager/docs/scoped-observation-reader.md)
keeps the trusted full-row parser distinct from selected result projection.
Current-head hosted review and required CI remain the independent merge gate
above, not a result inferred from this local run.

The authority binds the reader's exact reentrant fence and acquires it before
its internal lock on every admission/checkpoint path, including direct calls.
The reader rejects an unbound or differently fenced authority. This common
ordering prevents an opposing-lock deadlock when the snapshot callback also
acquires the owner fence. A bounded subprocess regression
exercises mixed direct/reader calls with a fence-acquiring callback, plus an
inverted-order negative control that reproduces the deadlock. Existing unfenced
callers keep their original lock semantics.

Current PR CI deliberately executes eligible units and guards, not the former
native full-Fleet matrix. Its green result is not evidence that the new
identity/SQLite/concurrency integration ran on Linux. The available periodic
container workflow covers plugin portfolios, not this standalone reader, and
its native Windows lane is explicitly unenrolled. This slice therefore records
native Windows contained integration and the independent consumers above,
without claiming unavailable hosted integration or expanding shared CI.
