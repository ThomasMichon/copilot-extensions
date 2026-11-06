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

## B4 - Historical original-position readback proof

This **agent-recommended, review-first refinement** addresses the retained
reader's historical limitation exposed by #10906. Owner v5 retains immutable
captures and native receipts, but an interest has only its latest handled
provenance. A newer confirmed operation can replace that provenance. Cursor
order then cannot establish whether an older operation ever completed its own
readback. Keep reporting `feedback-operation-readback-unavailable` until this
refinement is implemented and qualified; the following is not executing support.

### Explicit existing-owner expansion

Expand owner v5 to v6 through the existing lifecycle exclusion and complete-
envelope transaction, with a distinct `feedback-captures/v2` section. Retain
each original capture's selection fields unchanged and add one nullable,
body-free original-readback proof per capture. This is a protected tracking
result inside the same section, not another effect ledger, file, lock,
database, cache or queue. Native attempt states and receipts remain authoritative
in their existing review records.

The proof binds that exact capture's operation, immutable intent,
generation/source, scoped PR, candidate, original epoch/sequence and captured
interests to every selected confirmed action's original attempt and receipt
identity. It records successful protected readback of the original handled
position, not a provider response body, current vote, transport success,
latest handled cursor or proof borrowed from another operation.
An existing proof is immutable and retries reconcile its exact binding;
conflicting, partial, orphaned or foreign proof refuses without rewriting bytes.

Keep the existing retained-operation and interest limits. Reserve bounded proof
capacity before the first native attempt, including exact serialized capture,
proof and complete-envelope byte limits. Explicit adoption adds its bounded
reservation to the owner's total capacity; it cannot consume another section's
reservation. Saturation refuses without eviction. A retained v5 capture gains
only a null proof during explicit adoption, never inferred historical success.
Retrying such an operation must reserve its proof capacity before any remaining
effect or protected readback. Capacity refusal after earlier effects preserves
their native outcomes and reports tracking unavailable.

All replay, review, conversation, attention and feedback entrypoints preserve
v6 under the same operation lock and atomic replacement. Earlier adoption
cannot downgrade it. Ordinary v2/v3/v4/v5 behavior stays supported without
historical proof; strict prior disk readers refuse v6 without rewriting it.
Repeated adoption reconciles an uncertain replacement against the same
reservation and layout. No automatic migration, provisioning, backfill,
deletion or lifecycle takeover is introduced.

### Protected proof and independent handled frontier

Only B2's existing trusted protected reconciliation may commit a proof, after
all selected native actions are confirmed and the original operation's required
fresh readback succeeds. Revalidate the unchanged capture and native operation
inside the complete-envelope commit; perform no network IO under its lock.
Record proof and any admissible original handled update atomically. A storage
failure preserves confirmed native receipts, exposes tracking unavailable and
retries that same reconciliation with zero resends of confirmed actions.
Attempted operations without an original capture remain unbound; migration or
current acquisition cannot manufacture their missing selection.

Historical proof and an interest's current handled frontier are distinct.
Successful original readback can be retained even when a newer operation has
already advanced that frontier; it must not regress the frontier, replace its
provenance, reset pending age or change another interest. Missing current
interests, journal gaps or a reopened epoch cannot invent a handled update.
Where the original pinned readback itself is no longer admissible, retain null
proof and explicit unavailability rather than weakening B2's source/target,
policy, credential, freshness or deadline checks.

The proof says readback succeeded at that original position, not that feedback
is still present or that today's acquisition is fresh. Subsequent edits,
deletions, votes, replies and candidate motion remain actual journal input.
Current unavailable acquisition remains unavailable. Unknown/partial native
outcomes, unsupported vote attribution and unresolved unbound operations
cannot be cleared by another capture's proof or by close/reopen.

B3 echo coverage stays separate from original readback. A certificate for one
capture cannot classify later positions, rewrite that capture, certify another
operation or cross an epoch. Preserve the existing exact original version,
contiguous-position, complete-readback and interest-specific echo requirements;
longer chains and unsupported attribution remain unavailable.

### Reader sequencing and finite exits

First land and qualify v6 storage preservation, then protected proof commits,
then the existing optional reader/CLI/client/Gateway consumer. The consumer
checks each attempted capture's own proof and retained native identities before
considering the current handled frontier. Only that frontier and its admissible
echo coverage determine remaining input; an older certified capture cannot
make already-handled work unclassified merely because it precedes the frontier.
An uncertified capture remains explicitly unavailable even if a later cursor
is handled. Reads never create or repair proof, advance handled state, ACK or
emit Dispatch.

Use a parallel negotiated attention-read contract for v6 if the current strict
v1 declaration cannot represent it. Do not advertise v6 as v5, mutate v1's
strict fields or send new syntax to an old receiver. Preserve the real
executing-owner advertisement, unchanged default hosts, old wire behavior and
explicit unsupported/rollback results. Storage support alone does not enable
serving or authorize installed adoption.

Focused qualification must cover two overlapping operations in both completion
orders and across reopen/restart; exact all-confirmed retry with zero sends;
old null-proof adoption; missing capture and wrong operation/generation/source/
candidate/interest/action/attempt/receipt proof; partial/unknown outcomes;
readback versus storage failure; pre-effect capacity exhaustion; same-cursor
operations; independent input and aging; unsupported vote/echo evidence;
strict old-reader refusal; uncertain adoption and every whole-envelope writer.
Exercise the actual protected owner before the real reader/CLI/Gateway path.
Distinguish synthetic source fixtures, native platforms, live providers and
installed/shadow evidence. Each storage/protected/consumer leaf has a finite
source exit; none closes full B3/A2 or the parent acceptance.

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
