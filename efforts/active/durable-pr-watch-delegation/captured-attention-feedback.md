# Captured attention feedback

Back to [existing-owner durable attention](durable-attention-slice.md).
This is an **agent-recommended, review-first refinement of A2**, not executing
support, a completed feedback lane or permission to post provider feedback.
Acquisition preserves actual conversation positions; protected submission must
also retain which position a selected operation actually handles.

The existing strict `review-intent/v1` and `attention-state/v1` records remain
unchanged. A current vote, cursor ACK, selected role or successful transport is
neither an immutable operation capture nor confirmation of handled progress.
This closes the already reviewed PR-domain intent under the existing
[version-skew-safe contract pattern](../../../docs/patterns/service-contract-evolution.md).

## B1 - Explicit existing-owner capture storage

Expand owner v4 to v5 explicitly under the same ReplayStore operation lock and
actual lifecycle exclusion of incompatible file readers. Add a separately
versioned, body-free feedback-capture section and its reserved byte capacity.
Preserve all original replay, conversation, attention, review intent, action
and receipt records exactly. This is operation selection context, not another
effect ledger, file, lock, database, queue, transport or credential owner.

Bound capture count by the existing retained-review-operation limit, interest
count by attention admission, and actual serialized bytes by the new capacity
included in the existing total owner-byte limit. Reserve before the first
native attempt, not after successful feedback; exhaustion refuses without
eviction. No automatic provisioning, adoption, backfill or deletion.

Provider-first support must cover every whole-envelope entrypoint: replay,
review and conversation transactions preserve the added section, and repeated
earlier adoption cannot downgrade v5. Old strict readers refuse v5 without
rewriting it. Ordinary v2/v3/v4 behavior remains available without capture.
Uncertain adoption reconciles against the same layout and capacity. The current
v3 serving descriptor continues refusing expanded layouts until A3 qualifies an
actual receiver; unsupported metadata must not recommend provisioning an
already adopted owner.

B1's finite exit is tested storage and compatibility, not working feedback.
It does not add fields to existing strict wire or review-intent contracts.

## B2 - Immutable pre-effect capture and protected reconciliation

Only the existing trusted protected-submit embedding selects bound
consumer/role interests; request JSON cannot enroll or override an interest.
In the complete-envelope transaction bind the operation ID and immutable intent
digest to its exact generation/source identity, provider/account/namespace PR
key, actual head and target tip, epoch/sequence and selected admitted interests.
Keep the original protected owner responsible for all effects and receipts.

Capture admission requires complete current acquisition with matching
candidate and journal meaning. Every selected retained attention row must be
positively selected, complete and fresh under the actual embedding's bound,
with matching candidate/cursor. An older complete journal summary cannot
override a newer unavailable acquisition watermark. Missing, stale, partial,
foreign or future-dated evidence refuses before effects.

Capture the selected position once, never the latest position on a retry.
A same-semantic poll can retain the same cursor; generation time is not a new
conversation event. An existing capture must match its original operation and
interest binding. If an operation already attempted or confirmed effects but
has no capture, report tracking unavailable and retain the operation. Do not
attach the newest cursor or resend feedback to manufacture a baseline.

The existing protected sender retains authentication, selected-action policy,
exact source checks, operation fences, finite IO/checkpoint budgets, action
attempts and receipt reconciliation. The attention-enabled path additionally
checks the actual current target tip, not only ADO head/common/iteration.
Do not hold the envelope lock across network IO or invent another cache owner.

Native confirmation, complete selected-action execution and complete fresh
readback are distinct requirements. A transport success, action count, current
reviewer vote or provenance reference is insufficient. All-confirmed retries
must still perform required readback and tracking reconciliation without
sending again; the current executor's skip-confirmed loop alone cannot do so.
Partial or unknown actions retain their original reconciliation obligation and
cannot mark a complete selected operation handled.

After admissible confirmation/readback, atomically record only the captured
candidate/cursor and original operation/generation/action/attempt references for
the captured interests. Native receipts remain in their current review section.
Readback or attention persistence failure after an effect preserves confirmed
native outcomes and exposes tracking unavailable/pending. Retry the original
readback/tracking operation, never confirmed effects. Permission withdrawal
does not roll back a posted effect or authorize another send.

New external input during feedback remains pending; do not jump to the newest
cursor because execution finished. Progress over the original capture may be
admissible even when later input changed; retain the native execution
disposition separately, including `candidate_changed` and already confirmed
actions. An older operation retry cannot regress a newer handled baseline,
reset another interest's age or cross a reopen epoch. Derive remaining age from
the earliest actually unhandled retained event. A journal gap or missing proof
stays conservative/unavailable, not an invented recent age.

## B3 - Interest-specific proven echo coverage

The shared journal retains every actual semantic change. One participant's
feedback is input for others: never globally suppress an event because the
service posted it.

For the originally captured interests only, classify a later position as
echo-only using the selected baseline generation, exact confirmed native
action/object identities and complete fresh readback. The existing
`_expected_discussions` checks are a starting point for comment/reply/edit
identity, content and metadata, not blanket proof for all feedback. Current
vote/reviewer state or a count of equal VoteUpdate entries cannot establish an
immutable vote event. Missing exact vote attribution remains explicitly
unavailable; do not guess an echo.

Complete comparison must preserve concurrent replies, edits, deletions,
thread-state changes, independent votes, head/target motion and other input.
An unavailable baseline, incomplete page, stale proof or unclassified
difference cannot advance coverage. A proven echo-only position may be covered
for its original captured interests with minimal provenance naming that same
capture, operation and receipt references. Mixed echo/external observations
retain the independent work and meaningful pending age. No newer operation,
other interest or reopened epoch inherits coverage merely because hashes match.

Keep captured handled work and later proven echo coverage distinguishable.
Do not silently rewrite the original capture to the later observed cursor or
turn an ACK/read into handled progress.

## Sequencing and qualification

Land this refinement before persisted/runtime changes. Implement B1 separately,
then actual protected synthetic execution and acquisition reconciliation in
reviewable B2/B3 slices. B1 alone does not close A2; A3 serving and all parent
provider/retention/CLI/Dispatch/installed acceptance remain open.

Focused source fixtures must cover:

- Original capture across restart and lost responses; attempted/confirmed
  effects with missing capture; generation, actual-target, principal, namespace
  and consumer/role mismatch; complete journal over a newer unavailable
  attention watermark; capacity refusal before sending.
- All-confirmed retry readback with zero sends; partial/unknown actions;
  readback/tracking failure after confirmed effects; older retry versus newer
  handled state; independent input during feedback; retained journal gaps.
- Echo-only and mixed echo/external observations with independent interests;
  unavailable vote attribution; terminal/reopen epoch fencing; strict old
  reader refusal; complete-envelope preservation and no serving adoption.

Distinguish synthetic protected-context fixtures, native platform execution,
live provider writes and installed/shadow evidence. ADO sender source fixtures
do not qualify GitHub/Gitea merely because the data model accepts three provider
names. No real provider write, retained daemon/container, automatic migration,
runtime update or production activation is authorized by this source plan.
