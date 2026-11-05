# Existing-owner durable attention

Back to the [canonical effort](README.md). This is an **agent-recommended,
review-first source contract**, not an operational migration or a completed
P1/P2/P3 phase. It realizes the already reviewed
[role-aware attention direction](author-reviewer-perspectives.md#role-aware-attention-not-autonomous-authority)
through the current PR storage, acquisition and protected-feedback owners.
Workspace #9535, provider #7051 and Dispatch retain their existing custody.

## Outcome and present gap

Retain an interest's meaningful pending age and confirmed handled baseline
across restart, then expose a bounded read-only attention projection through the
existing authenticated service and CLI. The existing pure `attention.project`
and `rank` remain the projection and ordering rules; this is not another task
queue, forge reader, watcher, effect ledger, credential source or service.

Today those pure functions receive caller-supplied facts and a confirmed
`Handled` value. The v3 conversation journal retains semantic events but neither
interest state nor handled progress. Its current summary also does not contain
draft state or action-specific policy recommendations. A journal cursor, ACK,
reviewer vote, iteration number or successful materialization cannot fill those
gaps or prove feedback was posted.

This closes standing PR-domain intent rather than extending it. Apply
[version-skew-safe contracts](../../../docs/patterns/service-contract-evolution.md)
and the existing service writer/operation-lock lifetime; no new shared owner
agreement or deployment permission is introduced.

## A1 - Explicit storage expansion

- Add a separately versioned attention value object inside the existing
  `ReplayStore` whole-envelope transaction. Explicit owner-v3 to owner-v4
  adoption adds only its attention section and reserved byte capacity, while
  preserving replay, conversations and all review intents/receipts exactly.
  Do not add optional fields to strict v3 or create another file/database/lock.
- Adoption runs only through the existing lifecycle owner after incompatible
  file readers are excluded. It is atomic, bounded, and reconciles an uncertain
  replacement against that same adoption. A callable checkpoint is not a
  substitute for the embedding's actual custody.
- New code retains ordinary v2/v3 operations without enabling attention there.
  Serving and read requests never adopt, provision, repair or downgrade.
  Older strict disk readers refuse v4 without rewriting it. A rollback after
  adoption needs a capable reader or a separately authorized downgrade.
- Bound total interests and bytes explicitly; start with at most 4,096 scoped
  PR/consumer/role records within a reserved 8 MiB default. Admission refuses
  capacity exhaustion without evicting another interest, receipt or delivery.
  Include this reservation in the existing total owner-byte limit.
- Store provider-qualified PR/account/namespace identity, consumer/interest
  identity, candidate/cursor, observation/freshness, meaningful pending age and
  minimal handled provenance. Do not copy bodies, patches, credentials,
  callback argv or source archives into attention state.

The finite A1 source exit is a tested, explicit data-owner expansion. It does
not advertise a running queue or close A2/A3 below.

## A2 - Actual observation and feedback integration

- Successful existing-owner acquisition supplies admitted facts and the
  journal's actual candidate/cursor. Commit the attention update with the
  journal observation under the same owner-envelope transaction. Cache
  publication remains the already separate commit with explicit reconciliation.
  A cache CAS failure cannot update either journal or attention.
- Obtain draft, selection and action-specific recommendations from their real
  current owners. Missing/partial/path-incomplete or stale evidence is explicit
  unavailable work, never empty success or a positive policy decision.
  Do not reconstruct these facts from the journal's section hashes.
- Interests are preferences/selection, not permissions or assignments.
  Maintainer, path-Codeowner and SME remain distinct; unsupported predicates
  refuse instead of broadening. Scope and consumer come from the admitted
  embedding/caller binding, never a remote request's labels.
- Persist meaningful pending age rather than the last poll time. Identical
  semantic polls, retries and restart preserve it. New same-head dialogue and
  candidate/epoch movement remain visible; retained age and deterministic
  ranking prevent repeated fresh polls from defeating fairness.
- Only the existing protected feedback owner may record handled progress.
  Bind it to the exact selected generation, candidate, consumer/role and
  captured conversation position, with the original operation/native receipt
  identity. No public `handled`, cursor-ACK or arbitrary receipt-ingest route
  is introduced. A role label or caller-asserted success is not confirmation.
- Preserve partial/unknown feedback as its existing reconciliation obligation.
  Do not infer completion from an admitted intent, returned transport status,
  current vote, or solely from an action count. The protected sender's actual
  confirmed result and readback determine the admissible baseline.
- Feedback records only the position actually handled. Input arriving during
  feedback remains pending, with its own meaningful age; do not jump to the
  newest cursor just because an operation finished. An older feedback retry
  cannot regress a newer baseline or reset another participant's queue age.
- Normalize a proven self-feedback echo through the existing selected-action
  and native-identity comparison. Suppress only the exact confirmed self-effects;
  concurrent replies, edits, target/head changes and independent votes survive.
  If exact attribution is unavailable, report it explicitly rather than
  swallowing an unclassified change or treating an echoed vote as a new review.
- Terminal/reopen observations retain their actual epoch distinction. A
  terminal hint, selector departure or access loss cannot tombstone work.
  A genuine reopen cannot inherit an old epoch's handled baseline.

A2's exit requires actual acquisition and protected synthetic-feedback owners,
including the race and self-echo controls. A data helper alone does not close it.
This plan does not authorize actual provider writes.
The [captured-feedback refinement](captured-attention-feedback.md) proposes
the remaining immutable pre-effect capture, confirmed retry readback and
interest-specific echo boundary, with its own review before layout/runtime work.

## A3 - Optional read-only service/CLI consumer

Extend the existing Gateway handler and PR CLI/client instead of adding a
listener or local database. Publish a distinct optional attention contract,
including its required disk layout, bounded page/batch limits, observation
freshness and availability semantics. Preserve existing workspace/conversation
baseline wire behavior; never describe v4 persisted bytes as v3 to satisfy an
old reader. Disk-reader compatibility and HTTP-client compatibility are separate.

The authenticated scope selects only that admitted target/account/consumer's
interests. A read returns captured candidate/cursor, reasons, availability and
deterministic order, without advancing handled state, watch ACK or Dispatch.
Scope overrides, unsupported filters and unknown CLI options refuse.
New clients negotiate before using the optional operation; prior servers remain
usable for their old operations, and missing attention support is explicit.

An actual embedding must supply supported interest/policy and feedback owners;
configuration labels cannot manufacture them. Default watch-only and native
read-only hosts remain unchanged. Advertise only executing support after the
required state and owners validate under the service writer.

A3's exit is the real CLI parser/client -> authenticated Gateway -> retained
owner path with isolated inputs, including restart and old/new peer controls.
Dispatch emission, shared reviewer-population discovery, complete CLI parity
and production host enrollment retain their separate parent acceptance.

## Validation and sequencing

Land this contract before its persisted/runtime changes. Implement A1, then
compose A2 and A3 in separately reviewable source commits/PRs; journal each
finite exit without marking the parent phase complete prematurely.

- Atomic v3 adoption preserves replay registrations/events, journal cursors
  and unknown/confirmed review receipts; interrupted replacement reconciles
  without a second adoption or lost capacity reservation.
- Malformed/null/duplicate/foreign state, incompatible readers, wrong namespace,
  invalid candidate/cursor and capacity exhaustion refuse without rewriting
  the original bytes. Ordinary v2/v3 operations remain covered.
- Same-head external dialogue is actionable; repeated polls and exact retries
  are nonevents. Handled progress is independent across consumers and roles.
- Confirmed feedback rotates only its handled work. Exercise newer input during
  feedback, an exact self-echo mixed with an independent change, partial/unknown
  outcomes, restart and response loss. Unknown attribution never swallows input.
- Complete/partial path interests, stale evidence, unsupported predicates,
  deterministic ordering, aging fairness, terminal hints and reopen epochs retain
  their actual meanings and cannot authorize an effect.
- Real store locks, service drain, authenticated Gateway and CLI capabilities
  prove scope isolation and no hidden writes on reads. Keep unit/pure fixtures,
  native platform, live provider, image/installed and live-shadow evidence distinct.

Global terminal removal of body-bearing generations, materializations, outbox
copies and submission payloads remains a coordinated-retention contract, not an
attention cleanup side effect. Existing unknown receipts and terminal delivery
cannot be erased to make this slice appear finished. No live migration,
deletion, zero-fill/TRIM, runtime update or activation is authorized here.
