# Isolated conditional point-set resnapshot

**Status:** Proposed; actual plan review and merge precede runtime edits.

Back to [Fleet Control V2](README.md). This **agent-recommended bounded
implementation contract** advances #5862's authoritative-snapshot recovery
option after the [implemented point-set snapshot](scoped-observation-snapshot.md).
It does not implement event replay, subscriptions or resident federation.

## Request and ownership

Continue the operator's recorded direction, "Great. Keep driving.", in the
existing isolated observation lane. The mechanism and limits here are
agent-recommended, not a transfer of retained ownership. Preserve the
no-other-agent-coordination constraint.

Own only `observation_reader.py`, a directly related private serialization
helper if needed, their tests and documentation. Reuse the existing admitted
point-set snapshot and shared reentrant fence. Do not change store schema,
ingestion, admission/verifier semantics, providers, producers, resident Gateway,
Manager, SSH, enrollment, installers, deployment, leases or default commands.

This realizes part of `machine-fleet`'s `resumable-observation` intent: recovery
may use an authoritative snapshot rather than mandatory historical replay.
It follows the patterns' right-sized surface, independent standalone operation,
explicit ownership and fail-loud requirements. No new service, timer, queue,
database, cryptography dependency or installation is needed.

## Why not forward the raw replay cursor?

The trusted store retains 1,024 events across all scopes. Unrelated writes can
therefore expire a raw cursor. Hiding its numeric position while exposing that
retention failure would still reveal unrelated activity. Filtering events only
after retrieval also does not establish the reader's correlated field contract.

This slice instead recomputes the explicitly selected current point set. It
never reads the global head, replay log, retention floor, page API or source
hosts. It does not disguise resnapshot as lossless replay: intermediate changes
between calls may be coalesced, and there is no snapshot/stream attachment or
no-missed-event guarantee. Those full serving gates remain open.

## Explicit API and result

Add `ScopedObservationReader.refresh(identity, request, *, role, installation,
validator=None)`. Use the snapshot method's exact one-to-64 distinct correlated
origin/project/entity branches and canonical field order. Reject unsupported
requests and malformed validators before fence acquisition or storage access.
The only accepted validator shape is a fixed-length lowercase SHA-256 hex
string; omission requests a full snapshot.

Return one frozen result with `validator`, `status` and `observations`:

| Status | Observations | Meaning |
| --- | --- | --- |
| `snapshot` | Complete immutable selected tuple | Replace the caller's baseline with these current point observations. |
| `unchanged` | Empty tuple | The supplied validator matches this exact independently authorized current view; retain the caller's own baseline. |

There is no separate "unknown cursor", foreign principal, expired history or
restart reason. Any well-shaped nonmatching validator yields the same complete
`snapshot` response after ordinary authorization. An `unchanged` response is not
an empty fleet or proof of history continuity; callers without their associated
baseline must omit the validator and obtain a full snapshot.

## Admission and consistency

Reuse the reader's existing request validation, exact shared owner fence,
whole-request authorization before every lookup, authority/store binding, one
finite permit and one query-only SQLite point-set transaction. Do not call the
public `snapshot` method and then compare outside its admission interval.
Factor the existing internal retrieval/projection seam only as needed.

Both statuses require actual fresh verifier evidence and the complete explicit
request. Possessing a validator grants no read, cached allow, enrollment,
replacement permit or permission for later wire emission.

Prepare the validator and immutable result inside that same admitted interval.
Run the existing fresh terminal completion checkpoint after all retrieval,
serialization and comparison, before releasing the fence or returning either
status. Revocation, changed enrollment/scope, expiry, clock rollback, storage
failure and owner callback errors withhold the whole response and free permit
capacity. Preserve proof replay consumption and the one-second fence bound.

The result retains point-only coverage, source generation/revision, observation
time, availability and missing/removal/resync semantics. It does not assert
complete roster publication or simultaneous observations at every source.
Existing `read` and `snapshot` behavior and shapes remain unchanged.

## Opaque validator, not a cursor or authority

Use standard-library HMAC-SHA-256 with one private random 32-byte key generated
for each reader instance. Domain-separate and canonically serialize:

- The admitted authority/fleet, issuer/audience/subject and exact enrollment
  binding, including principal/account/role/installation/generation.
- The exact correlated request.
- Only the complete **visible** immutable point-set DTO: selected state pairs
  and the entity-local metadata already returned by `snapshot`.

Keep these binding values inside the keyed computation; return only its
fixed-length digest. Do not include the global policy revision, store position,
retention state, export sequence, unselected fields, other enrollments, proof
nonce, token issuance/expiry time or read time. Fresh equivalent proof evidence
must not invalidate an unchanged baseline. Changes to another scope or policy
entry must not change this validator when this admitted binding and visible
view are unchanged.

Compare well-shaped validators with the standard constant-time comparison.
Do not invent an encryption/signature format, expose the key, persist it, or
allow a caller to supply it. The key prevents externally comparable/guessable
content fingerprints; it is not an authentication credential or a new trust
root. A new reader instance naturally requires a full resnapshot, with no
special externally visible restart identifier.

Retain no per-client baseline, cursor map, queue or snapshot transaction between
calls. Work is bounded by the selected points and their existing field/row size
limits, not total fleet cardinality or history. Serialization must preserve
missing versus null, booleans versus strings, tuple order and every visible
metadata field without reading excluded values.

## Validation plan

All gates are **agent-recommended**; none are claimed complete by this proposal.

- [ ] Plan passes actual current-head review and required CI, merges, and the
  source worktree synchronizes to that reviewed plan before runtime edits.
- [ ] Initial, matching and stale validators produce exactly the frozen
  `snapshot`/`unchanged` shapes. A caller can replace or retain its baseline
  correctly; missing/null/removal/resync metadata stays truthful.
- [ ] Invalid/implicit/duplicate/oversized requests and malformed validators
  touch neither fence nor storage. A forbidden branch causes zero point
  lookups, even with a real validator from another permitted request.
- [ ] Equal visible selections with fresh real proofs preserve the validator.
  Changes to selected fields and every visible metadata field invalidate it.
  Unselected payload changes alone do not, when visible metadata is unchanged.
- [ ] Principal/account/role/installation/enrollment-generation, fleet,
  issuer/audience/subject, request and reader-instance boundaries cannot reuse
  another baseline as `unchanged`; independent authorization always precedes
  access. Well-shaped nonmatching inputs disclose no validity classification.
- [ ] More than the entire raw retention window of unrelated committed writes
  leaves the exact selected response/validator unchanged. Unrelated enrollment
  or policy-revision changes do not become a recovery side channel.
- [ ] Terminal revocation, changed enrollment/scope, expiry, rollback, callback,
  storage and serialization failures withhold both prepared result shapes,
  consume the permit and recover capacity; direct callers retain lock order.
- [ ] Verify one query-only consistent read transaction, at most 64 indexed
  point lookups, no raw replay/page/head access and no retained client state.
  Exercise concurrent real-process writes with an independent-read control.
- [ ] A fresh production-only process consumer uses actual signed evidence,
  SQLite and both result statuses; importing remains optional-crypto-free.
  Run focused unit/process selections, touched-code lint and relevant guards
  within unchanged contained deadlines; do not expand required CI matrices.
- [ ] Record unit, simple process E2E, clean-room and available live/platform
  tiers honestly. This local API changes no installer or first-provision path;
  it supplies no resident/provider/live acceptance.
- [ ] Complete documentation-impact review, standalone consumer changefile,
  current-head implementation review and required CI before code merge.

## Remaining full recovery contract

Conditional current-state recovery is not a push client or periodic remote
polling architecture. No resident consumer is wired by this plan. Authenticated
origin provenance, durable shared policy epochs and actual wire emission stay
with their retained owners. Complete roster publication, scoped retained event
replay where appropriate, coherent snapshot/subscription attachment, isolated
backpressure, Windows/Linux serving proof and authorized one-Gateway adoption
remain required under the parent Plan/Validation Plan.

The larger effort remains Active. This slice cannot complete #5862 or #6236,
retire any source, replay a mutation or settle another owner's obligation.
