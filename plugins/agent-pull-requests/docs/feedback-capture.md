# Feedback capture data

Back to [attention](attention.md). The reviewed
[captured-feedback contract](../../../efforts/active/durable-pr-watch-delegation/captured-attention-feedback.md)
owns the remaining protected integration.

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
fresh, the interests admitted or effects confirmed. Actual pre-effect capture,
target-tip/freshness checks and all-confirmed retry readback are B2 work.
Interest-specific echo coverage is B3; shared journal input is never globally
suppressed.

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

There is no public capture/handled ingest, automatic migration, receipt
backfill, terminal retention/deletion, live provider qualification or
installed adoption in this source slice.
