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

`ReviewFeedback` reserves the capture under ReplayStore's existing complete-
envelope transaction before the first native attempt. Complete selected
generation meaning must match the current complete journal observation;
every selected retained attention row must match its positive selection,
candidate, cursor and observation time. Stale/future acquisition, a newer
unavailable watermark, missing interests and exhausted capture capacity refuse
before effects. A retry retains the original capture rather than the newest
cursor. An attempted operation without a capture stays tracking-unavailable:
there is no backfill or resend to manufacture one.

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
Permission denial still propagates through the existing owner and never
rolls back a retained effect.

Current reviewer vote or equal VoteUpdate counts cannot prove an immutable
vote event. Operations containing a verdict therefore remain explicitly
tracking-unavailable even when their native receipts are confirmed. Exact
vote attribution and later interest-specific echo coverage remain B3 work;
the shared journal never suppresses provider input globally.

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
