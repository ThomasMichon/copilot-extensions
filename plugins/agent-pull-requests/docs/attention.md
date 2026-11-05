# Inert attention foundation

Back to the [PR plugin](../README.md). The
[canonical perspective plan](../../../efforts/active/durable-pr-watch-delegation/author-reviewer-perspectives.md)
owns the broader campaign.

`agent_pull_requests.attention` supplies pure typed projections over already
admitted, normalized evidence. It does not fetch forge data, store events,
authenticate callers, perform policy checks, emit Dispatch tasks or publish
feedback. No CLI/HTTP route or standalone-host adoption is added by this module.

The existing observation/selector owner supplies a full provider/endpoint/
repository/PR/account/namespace key, exact head/base, epoch/sequence, observation
time, pending-since time, completeness and current policy recommendation flags.
`Interest` carries a consumer, role and tri-state selection result. Codeowner
matching remains with the selector; unknown paths cannot be converted to a
positive or negative match here. A provider string is identity, not proof of
that driver's qualification.

`project` requires the caller's current time and explicit freshness budget.
It distinguishes initial review, follow-up, rebase, CI re-kick, merge nudge and
final-iteration recommendations from excluded, unavailable, draft, handled and
terminal states. Recommendations carry no mutation permission.

Supply `Handled` only from the current owner's confirmed feedback baseline.
Progress belongs to the exact PR/account/namespace/consumer/role. Matching
candidate/cursor suppresses repeated work, but a new same-head discussion cursor
or new observation epoch remains actionable. Feedback ahead of observation, or
a candidate change under an unchanged cursor, is unavailable rather than an
invented clean baseline. Acquisition deduplicates poll nonevents. Feedback
owners must reconcile exact confirmed self-effects per interest without hiding
the global journal input from other participants; this module does not
manufacture those guarantees from caller numbers.

`rank` orders at most 4,096 unique PR interests, excludes non-actionable or
expired projections, applies the caller's explicit aging interval and uses a
stable scope-qualified tie break. Pending-since is meaningful unhandled work
time supplied by the existing owner, not the most recent polling time. The
owner records only the feedback's observed candidate/cursor; it must not move
the baseline over newer concurrent events.

This source foundation neither tombstones nor deletes records. Durable
journal/cursor semantics, bounded retention, provider normalization, authenticated
service/CLI adoption, task-lineage integration and actual shadow comparison
remain separate open work in the canonical effort.

## Explicit data-owner expansion

The reviewed [durable attention contract](../../../efforts/active/durable-pr-watch-delegation/durable-attention-slice.md)
separates three exits. Its A1 source expansion supplies `AttentionState` and
`AttentionStore` inside the existing complete-envelope owner, not a running
attention queue or the A2/A3 integration.

`AttentionStore.adopt` explicitly expands an already adopted owner-v3 layout to
v4 under the existing operation lock, atomic replacement and actual embedding
lifecycle checkpoint. Replay, conversations and all confirmed/unknown review
intents and receipts remain intact. The default attention reservation is 8 MiB
and participates in the existing total owner-byte limit. Admission refuses
capacity exhaustion without evicting other state. A same-adoption retry
reconciles an uncertain replacement; a changed capacity refuses.

The detached `attention-state/v1` value object validates at most 4,096 unique
PR/account/namespace/consumer/role rows, at most 16 KiB each. Rows contain the
existing typed `Facts` and `Interest`, an optional captured `Handled` baseline,
and bounded references to the original operation, generation and receipt
action/attempt identities. The actual receipts remain in their current owner.
These are data references, not proof of permission, confirmed feedback or a
new effect ledger. There is no ingest, feedback-write, selector, automatic
acquisition or public read operation in the A1 data-only slice.

`snapshot` requires v4 and never adopts or writes. Malformed/null, duplicate,
foreign-namespace, invalid-cursor and over-capacity persisted attention refuses
every owner entry point without reinitializing state. Existing replay/review
operations and internal conversation transactions preserve v4 data; calling
conversation adoption again cannot downgrade it or discard its journal.

Default provisioning, native read-only host configuration and v2/v3 operations
remain unchanged. Existing strict older disk readers cannot open v4. The
current v3 conversation-serving descriptor deliberately refuses v4 rather
than misreporting its disk layout; the authenticated reader and its negotiated
compatibility are A3 work. Do not adopt a serving volume for this source
foundation. No HTTP/CLI operation, automatic migration, activation, rollback
rewrite, terminal retention or production deletion is added.

## Trusted acquisition composition

The finite acquisition half of A2 lets the existing `WorkspaceOperations`
embedding supply an optional `attention_owner` callback. Its typed
`AttentionInputs` names the exact acquired generation, complete admitted
interest set and explicit current policy recommendation/completeness flags.
This is a trusted existing-owner callback, not caller JSON, a selector
implementation, an assignment or effect permission. Default native read-only
and watch-only hosts do not supply one and remain unchanged.

After confirmed cache publication, `ConversationStore.observe_workspace`
commits the normalized observation, actual journal candidate/cursor and
attention rows in one existing complete-envelope transaction. It requires
already-adopted v4 when attention inputs are present. A failed cache CAS never
calls the attention owner or updates either section. Cache publication is
still a separate commit: a later policy/storage failure leaves that published
generation intact and requires reconciliation of the same acquisition, not
replay of a provider effect.

Identical semantic polls refresh observation freshness without resetting
pending age. Unhandled dialogue retains its first pending age; the first new
event after an existing handled baseline starts its own age. The original
baseline/provenance remains unchanged. A genuine reopen clears the old epoch's
baseline and starts a fresh pending age. Omitted formerly admitted interests
become selection-unknown, not silently deleted or still positively selected.
Recovery from unknown or temporarily omitted selection preserves pending age;
only a confirmed excluded-to-selected admission starts a new selection age.
Missing native draft evidence, incomplete policy and unknown path selection
are unavailable rather than a positive queue decision.
If missing candidate, state or interval evidence prevents a new journal
observation, retained scoped rows become unavailable and omitted interests
become selection-unknown. Their last real candidate, cursor, pending age and
handled provenance remain intact; no journal progress is invented.

This source composition is not A2's complete exit. It introduces no confirmed
feedback writer or self-echo suppression, and never derives handled progress
from a receipt reference or latest journal cursor. Durable capture before
feedback, race-safe exact confirmed readback and interest-specific echo
reconciliation remain required. In particular, another participant must still
see a confirmed comment as new input. The existing v3 serving descriptor still
refuses v4; A3 must qualify negotiated reading before a serving host can adopt
this layout. No HTTP/CLI operation or default host enables this source seam.
