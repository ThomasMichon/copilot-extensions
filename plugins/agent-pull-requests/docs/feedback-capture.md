# Feedback capture data

Back to [attention](attention.md). The reviewed
[captured-feedback contract](../../../efforts/active/durable-pr-watch-delegation/captured-attention-feedback.md)
owns protected integration and the remaining echo qualification.

`FeedbackCaptureStore` implements only B1's explicit owner-v4 to owner-v5
data expansion. It uses the current ReplayStore lock, atomic replacement and
actual lifecycle checkpoint to exclude incompatible readers. It does not admit
captures, send feedback, record handled progress or enable a service route.
Default provisioning and native read-only hosts remain unchanged.

The separately versioned `feedback-captures/v1` section holds at most the
existing 1,024 retained review operations, with at most 64 KiB per capture.
Each capture contains operation/intent/generation/source identities, the
provider-qualified PR/account/namespace key, exact head/target candidate,
actual epoch/sequence, observation time and unique consumer/role interests.
Interest count uses the existing 4,096 bound; the record and total byte bounds
can refuse a smaller set. No bodies, patches, credential values or native
receipts are duplicated.

The complete owner validates each capture against its retained original review
intent: operation ID, intent digest, generation/source, scoped PR and source
head must match. These structural checks are not proof that the target was
fresh, the interests admitted or effects confirmed.

## Protected original-position reconciliation

The optional trusted `WorkspaceOperations.feedback_owner` supplies typed
`FeedbackInputs` for the exact retained generation: positively selected
consumer/role interests and an explicit acquisition freshness bound. Request
JSON cannot provide or override them. The default embedding has no feedback
owner; constructing one never adopts storage or enables serving support.
The workspace also retains the generation's actual successful journal-acquisition
position in its bounded in-process metadata. Matching content alone cannot bind
an older generation to a later equal-content position or reopened epoch.
Unbound/evicted acquisition metadata refuses a new capture; after restart, the
embedding must reacquire before admitting new effects. An existing durable
capture remains sufficient for its original retry. Direct protected embeddings
likewise supply the position returned by that generation's actual acquisition,
never a cursor fetched from whatever happens to be current at submission.

`ReviewFeedback` reserves the capture under ReplayStore's existing complete-
envelope transaction before the first native attempt. Complete selected
generation meaning must match the current complete journal observation;
every selected retained attention row must match its positive selection,
candidate, cursor and observation time. Stale/future acquisition, a newer
unavailable watermark, missing interests and exhausted capture capacity refuse
before effects. A retry retains the original capture rather than the newest
cursor. An attempted operation without a capture stays tracking-unavailable:
there is no backfill or resend to manufacture one. Read-only recovery of its
original native receipt remains possible, but cannot start remaining actions.

The existing protected executor additionally checks the actual current PR
target tip against the capture, independently of source head/common/iteration.
Native effects, original attempt identities, receipts, policy and credential
custody remain unchanged. After all selected actions are confirmed, including
on all-confirmed retries, the same pinned context performs fresh native
readback outside the envelope lock. Confirmed comment/reply/owned-edit
identities and exact selected content/metadata must still match.
Independent later dialogue need not invalidate proof of the original effect;
it remains new work rather than being added to the capture. The execution
disposition, including `candidate_changed`, remains separate from the
`feedback` tracking result.

After admissible readback, the complete-envelope transaction records only the
captured candidate/cursor and original operation/generation/action/attempt
references for the captured interests. A newer unavailable row remains
unavailable; selection and recommendation flags are not revived. Equal/later
handled progress is unchanged, and an old capture cannot cross a reopen epoch.
Remaining pending age uses the earliest retained unhandled event; a retention
gap conservatively retains the old age. Readback or tracking-replacement
failure preserves confirmed native receipts and reports tracking unavailable.
Retry reconciles that same operation without resending confirmed effects.
Target motion cannot block original unknown-receipt attribution: reconciliation
precedes that target guard, which still blocks subsequent effects and handled
progress against a changed target.
Permission denial still propagates through the existing owner and never
rolls back a retained effect.

Current reviewer vote or equal VoteUpdate counts cannot prove an immutable
vote event. Operations containing a verdict therefore remain explicitly
tracking-unavailable even when their native receipts are confirmed. Exact
vote attribution remains unavailable; the shared journal never suppresses
provider input globally.

## Bounded comment-echo reconciliation

The first B3 source leaf supports only new standalone comment threads whose
confirmed protected response supplied complete native version timestamps.
`NativeActionOutcome.response` is optional, ephemeral in-process evidence,
excluded from its representation. The feedback-enabled caller immediately
normalizes that original response and retains only a `thread_state_id` digest
in the existing extensible receipt `platform_ids`; it never persists or
returns the response body. An old caller ignores this additive provider field;
a new caller with an old provider still confirms native effects and original
handled progress, but cannot claim version-proven echo coverage. A recovered
lost response cannot manufacture its original version from current readback.

The trusted direct embedding may supply `feedback_current`, returning a
generation and the position from its actual successful acquisition.
`WorkspaceOperations` supplies this from the same existing cache and bounded
generation-to-position metadata; request JSON cannot override it. A missing
binding, evicted generation or restarted embedding needs actual reacquisition,
not equal-content inference. The immutable capture remains the original
handled position.

Coverage requires exactly one later semantic position in the same epoch.
Every non-discussion section must match the captured generation, and the
complete discussion set must differ only by distinct selected new threads
matching the original response fingerprints. The same pinned protected
context then uses the existing finite owner-evidence collector to read all
sections again, including policies, reviewers and the exact source range.
Incomplete or differing readback refuses coverage. No new transport,
credential owner, response ledger or cache is introduced.

After readback, ReplayStore atomically rechecks the original capture/receipts,
actual acquired position, complete current journal meaning, retained event,
freshness, attention watermarks and positively selected captured interests.
Only those interests may advance their handled cursor to the proven echo.
Original capture/provenance remains unchanged; other interests retain the
shared event and pending age. Subsequent independent input starts its actual
unhandled age. Newer handled progress is never overwritten.

The tracking result distinguishes original `cursor`/`status` from optional
`echo` coverage. Missing versions, reply/edit/verdict version attribution,
multiple intervening positions, gaps, reopen, concurrent input and unavailable
acquisition remain explicitly unavailable. Failure to classify or persist
echo coverage does not undo original handled progress or resend effects.
Optional cache lookup contention/unavailability and echo-store lock contention
also return unavailable echo evidence without discarding the original result.
This conservative leaf is not B3/A2's full exit or A3 serving adoption.

Adoption adds an 8 MiB default reservation to the existing total owner-byte
limit and preserves every replay, conversation, attention and review record.
Capacity exhaustion refuses rather than evicting. Repeating the same adoption
reconciles an uncertain replacement; changing its reservation refuses.
Snapshot reads neither adopt nor write. A prior v2/v3 owner requires its
existing explicit conversation/attention adoption first.

Current replay/review/conversation/attention transactions preserve the full v5
envelope. Repeating an earlier adoption never downgrades it. Malformed/null,
duplicate, orphaned, wrong-scope or over-capacity captures refuse all owner
entrypoints without resetting the bytes. Strict old v4 readers refuse the
expanded format; downgrade is not automatic. The current v3 conversation
serving descriptor also refuses v5. Disk-reader support is not HTTP-client
compatibility or permission to adopt a serving volume.

Protected synthetic fixtures qualify original-position comment/reply/edit
reconciliation, restart/lost responses, pre-effect refusal, actual-target
motion, newer input, age gaps, reopen fencing and tracking-write uncertainty.
There is no public capture/handled ingest, automatic migration, receipt
backfill, terminal retention/deletion, live provider qualification or installed
adoption. A3 still owns negotiated serving readiness.
