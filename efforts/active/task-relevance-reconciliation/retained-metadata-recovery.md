# Retained native recovery after managed metadata loss

Part of [Task Relevance Reconciliation](README.md), continuing
[#9532](https://github.com/ThomasMichon/copilot-extensions/issues/9532)
at the retained-task boundary of
[#6934](https://github.com/ThomasMichon/copilot-extensions/issues/6934).
**Status: plan reviewed in #12929; provider implementation passes bounded source
controls, with publication and runtime qualification still open.** Source
acceptance is not runtime recovery authority.

## Existing boundary

The [writer adapter](retained-writer-compatibility.md) can read an original
keyed-creation journal with a compatible newer serializer. The
[cleanup correction](journal/2026/11.16-retained-conversation-cleanup.md)
prevents failed-attempt housekeeping from disposing of a still-needed ordinary
conversation. Neither correction reconstructs previously removed metadata.

An original keyed journal and authoritative native/workspace head can survive
without their managed Bridge session, turns, events or delivery cursors.
`retained_session_recovery.resume` currently requires that exact managed
session before inspecting its original journal. Ordinary exact-head recovery
can import native history, but its unassociated control record does not carry
the keyed task's retained writer, operation or exclusion lineage.

This closes the existing conversation-preservation and version-skew-tolerant
service intent. It is not a new conversation, a task acquisition, a generic
database repair facility or a takeover of #6934's broader native acceptance.

## Chosen representation

Reuse the native owner's exact conversation and the existing Bridge
native-import/lifecycle primitives. Preserve the immutable keyed birth record
and original managed ID. When an authoritative complete managed snapshot exists,
its owner may recover that record with its actual history and cursors.

When those historical rows are absent, do **not** recreate the old managed ID
with empty history. Adopt a new control record for the **same native
conversation**, explicitly linked to its original keyed journal and missing
predecessor. Keep the old control namespace excluded: delayed old requests must
not become newly admissible because their receipt rows disappeared. The new
record has a distinct event/turn epoch; report the historical gap rather than
inventing prior events, resetting a cursor in the old stream or claiming that
the native conversation is new. A managed record and a native conversation
are different identities.

This is an implementation choice, not additional operator intent or effect
authority. If existing evidence cannot support this adoption safely, return an
inspectable held result through its owner; do not silently use generic resume.

## Implementation sequence

1. **Expand the retained owner contract first.** Extend the existing retained
   recovery journal and receiver with a versioned metadata-adoption outcome.
   Bind the operation to the original creation key, managed/native identities,
   workspace incarnation, retained writer semantics and current head. Keep
   birth facts, original operations, handoff history and native identity intact.
   Never interpret a missing receipt as a negative outcome.
2. **Prepare metadata independently of execution.** Reuse the native-import
   primitive under the existing workspace/recovery serialization boundaries.
   Retain original task/emitter/pool origin and required launch configuration
   from authoritative owner records, not caller assertions or guessed stamps.
   Ambiguous configuration, lineage, live holders, incomplete Host inventory,
   unresolved launches, stop holds or pending handoffs refuse. Persist adoption
   before returning its control record; a crash or lost response must discover
   that same record, not allocate another.
3. **Carry custody through the existing consumers.** Make writer inspection,
   strict native resume, provider refresh and operation inspection understand
   the explicit adopted lineage. Preserve the original sender's exclusion and
   unknown-effect state. Worktrees still verifies its current native head and
   incarnation at effect time. The old managed ID never becomes an alias that
   can accept delayed prompts into the adopted record.
4. **Resume only through qualified owners.** After metadata adoption, use the
   existing retained native recovery admission, launch gate and exact native
   load. Do not drain queued prompts, recreate a native conversation, replay
   an initial seed or mint a new task episode. No automatic task attachment,
   attempt/generation change, policy-clock reset or production allocation is
   implied. An independently authorized owner must accept any later attachment
   with its existing fences.
5. **Negotiate before enabling callers.** Declare support on the actual
   receiving runtime and client attempt, not through package equality.
   Existing present-record recovery and ordinary operator-owned worktree resume
   retain their compatible behavior. Unsupported older receivers refuse the
   affected operation; rollback cannot erase adoption or reopen the old
   namespace. Keep deployment and native execution authorization separate.

No new daemon, timer, generic mutation endpoint, executable registry or
permanent build pin is proposed. Use the existing original-journal and
workspace ownership boundaries rather than a parallel recovery ledger.

## Validation plan

- [ ] A supported missing-record fixture adopts one control record for the
      original native head, retaining the keyed writer, workspace incarnation,
      launch configuration and task origin. No native launch or prompt occurs
      during metadata preparation.
- [ ] Repeated requests, independent-process races, restart, interrupted commit
      and lost responses retain one adoption and one eventual native admission.
      Unknown effects remain inspectable and are never replayed.
- [ ] Missing old turns/events/cursors produce an explicit history boundary.
      Old send, event-cursor and idempotency requests cannot be reinterpreted in
      the new control epoch; no fabricated receipt, ordinal or successful turn
      substitutes for lost history.
- [ ] Changed keys, native heads/nonces, retired or conflicting lineage,
      unsupported contracts, incomplete configuration, live/unknown holders and
      stop or launch holds refuse without modifying the original history.
- [ ] Existing present-record recovery and ordinary unassociated ACP/CLI resume
      retain their behavior. A generic resume cannot drop a discovered keyed
      writer or bypass an unresolved retained operation.
- [ ] Current/prior callers, provider cutover and declared rollback preserve the
      adoption/exclusion contract. Test actual receiver selection rather than
      substituting package versions or parsing errors for negotiation.
- [ ] A bounded isolated process composition loads the same native conversation,
      reaches a new turn and normal closeout through supported front doors.
      Actual original-flow recovery, source tests, release and authorized
      adoption remain separately reported.

Keep actor-free semantic controls in the admitted unit packet. Real database
owners, process races and native lifecycle controls belong in the existing
selected integration portfolio, not an exhaustive required-CI expansion.
This plan grants no live metadata mutation, prompt, deployment, termination,
deletion, credential change or policy reset.

## Provider implementation checkpoint

The existing keyed creation journal now carries an additive immutable metadata
adoption. The native-import transaction commits its new managed record and
history boundary together, under the existing creation/workspace/recovery
exclusions. The original managed ID stays excluded, the original native ID
does not alias the new stream, and prior recovery IDs cannot become new-epoch
execution receipts. Original launch preferences, tool configuration and task
origin are retained from creation facts. Missing or unsupported facts hold.

The receiver advertises `retained_native_metadata_adoption: 1` through actual
health. Negotiated Python helpers prepare or inspect the version-1 outcome;
there is no new CLI carrier or ordinary caller migration in this slice.
Malformed or mismatched outcomes refuse without replay. The
[owner guide](../../../plugins/agent-bridge/docs/retained-acquisition-writer.md#metadata-loss-without-native-conversation-loss)
documents the HTTP shape, retention, schema-49 preparation and the supported
passive-cutover rollback limitation.

All 275 selected Bridge controls pass in the isolated process portfolio,
including actual HTTP old-provider refusal, lost and malformed responses,
atomic rollback/restart and independent-process competing preparation. All
5,055 pre-existing required unit nodes are retained; 13 actor-free lineage and
import controls extend that packet to 5,068 passing nodes within the original
300-second budget. Data-only lineage is separate from the metadata receiver's
process-capable preparation dependencies. The legacy continuation controls now
require reverse lookup of both birth and adopted managed IDs.

These are source controls, not evidence that native execution reached a new
turn or closeout. The validation checkboxes intentionally retain their broader
runtime and mixed-owner acceptance gates. Actual installation, provider
cutover, native continuation, task attachment and production activation remain
unqualified and separately owned.
