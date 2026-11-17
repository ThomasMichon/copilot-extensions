# Retained native recovery after managed metadata loss

Part of [Task Relevance Reconciliation](README.md), continuing
[#9532](https://github.com/ThomasMichon/copilot-extensions/issues/9532)
at the retained-task boundary of
[#6934](https://github.com/ThomasMichon/copilot-extensions/issues/6934).
**Status: plan reviewed in #12929; metadata owner accepted in #12953;
ordinary worktree-resume and target-inventory provider accepted in #13007.
CLI/Dispatch consumer integration and runtime qualification remain open.**
Source acceptance is not runtime recovery authority.

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

The final frozen implementation also passes all 638 current HTTP-52 controlled
probes and their independent execution. Canonical source/preimage validation
admits the generated outputs without refreshing historical generations. The
contract guard accepts both contracts and all 1,141 registered fixtures. This
adds controlled source qualification, not native or installed acceptance.

## Next consumer slice: ordinary supervised resume

The normal caller should remain the existing supervisor-to-Bridge resume path.
Dispatch retains the current assignment and reservation before launch, Bridge
restores the assigned workspace's original native conversation, Dispatch records
the observed resumed-agent facts, and the worker explicitly accepts the current
task through its negotiated start operation. A live process or successful resume
does not itself authorize a supervisor to mark that task `STARTED`.

The receiver merged in #12953 is deliberately provider-first. Two current
consumer assumptions still prevent this ordinary composition after metadata
loss: generic worktree resume refuses a retained journal rather than importing
an unassociated session, and ordinary Dispatch startup requires its historical
managed handle, receipt stream and stored identity to remain unchanged.
Neither assumption may be removed by treating missing receipts as non-delivery.

Implement the remaining migration within those existing owners:

1. Make the ordinary Bridge resume boundary select the original keyed journal,
   native head and workspace incarnation. Internally compose metadata adoption
   and retained native recovery when needed; do not make a supervisor or operator
   reconstruct rows, invent writer facts or drive a second repair workflow.
2. Keep the existing original-journal and native launch owners authoritative.
   Concurrent calls, restart and response loss discover the same adoption and
   pending operation. An uncertain native effect is inspected, not relaunched;
   a valid current ready session remains reusable without another native load.
3. Return truthful resumed-session facts, including the adopted managed identity
   and explicit historical gap when applicable. Preserve the native conversation,
   immutable birth record, original exclusions and historical outcomes. The
   missing predecessor's old namespace never becomes a send alias.
4. Let the existing reservation storage accept that owner-qualified control
   adoption without confusing it with a different native conversation. Preserve
   old allocation/invocation history and current task-generation, reservation,
   pool and writer checks. An old missing receipt or failed startup cannot be
   relabeled successful; a settled excluded send remains excluded.
5. Continue through ordinary task delivery and the worker's explicit acceptance.
   Preserve current policy clocks, retry limits, holds and task identity. No new
   task episode, unrelated reservation release or supervisor-authored acceptance
   is part of this migration.
6. Negotiate the actual receiving support before enabling the caller. An older
   provider must refuse this recovery rather than silently dropping keyed
   custody. Keep ordinary present-record/unassociated resume behavior and
   supported rollback intact; no build-equality requirement or new service is
   introduced.

The discriminating source composition must exercise the real resume front door,
reservation recorder and worker start boundary: surviving original native with
missing managed metadata; already-present stopped and live records; simultaneous
resume and lost replies; explicit prior-history gaps and settled/unknown sends;
changed workspace/native/task/reservation evidence; and an older unsupported
receiver. Prove that resume alone leaves task acceptance unchanged, while the
same worker's affirmative start records `STARTED` once. Preserve all existing
unit nodes and source/coverage bindings; put process and native cases in the
existing selected integration portfolio.

This is the next source integration and qualification slice of the existing
plan. It grants no installed Bridge mutation, prompt, deployment, task reset,
termination, deletion or production activation. Actual original-flow recovery
and authorized rollout remain separately reported.

### Complete target inventory and lazy head hydration

The target-discovery amendment expands the same consumer boundary. Available
Bridge targets are the **union**, not the intersection, of active Session Host
or extension registrations and every workspace returned by Worktrees.
Registration-only targets remain visible without a workspace row; a listed
workspace remains addressable without a managed Bridge session. A database
cache miss must not turn either into an unknown target or a fresh conversation.

Current code exposes these facts separately: `/api/v1/sessions` lists managed
records, `/api/v1/live-sessions` lists registrations, and `/api/v1/worktrees`
decorates workspace inventory only when a managed record already exists.
Ordinary send/read matching also searches that managed-session list before
falling through to profile matching. Worktree resume reads the authoritative
head, but still refuses a missing keyed managed record. Fix those joins through
their existing owners, rather than adding a second durable target registry.

Implementation proposal:

1. Compose a shared target projection from the existing managed/session-host,
   extension-registration and Worktrees inventory owners. Preserve exact
   workspace owner/project/machine identity and registration-only targets.
   Deduplicate proven representations of the same target, not unrelated
   resources that happen to share a name or native identifier.
2. Keep target identity distinct from managed control identity. A worktree
   handle is a valid target before a managed row exists; never fabricate a
   session ID, active registration, native head, turn count or historical
   receipt to make an inventory row satisfy a managed-session schema.
   Workspaces without a usable head remain visible with their actual reason;
   visibility is not evidence that native execution is currently admissible.
3. Use the same owner-qualified resolver for normal target operations. On a
   missing managed row, read the exact workspace's current authoritative head
   and incarnation, then hydrate metadata through the existing native-import
   or keyed metadata-adoption owner. Metadata hydration must not load a native
   process, drain prompts or accept a task. Resume and delivery follow only
   when requested, through their existing admission boundaries. Ordinary raw
   messaging to a dormant target still refuses with an explicit resume
   requirement; hydration does not silently start or resume its receiver.
4. Preserve keyed predecessor exclusions and explicit history gaps. Live
   Session Host/extension registrations remain owned by their current
   transport, not silently converted to an ACP controller. Conflicting heads,
   incomplete discovery, pending handoffs and ambiguous custody retain their
   inspectable states; do not relabel an unavailable source as an empty set.
5. Carry the union through existing HTTP/Python/CLI listing and target
   resolution surfaces with negotiated additive support. Preserve the legacy
   managed-session response and callers' assumptions; new inventory consumers
   must distinguish a target projection from a real managed record. Unsupported
   receivers report the missing hydration contract before effects, rather
   than falling through to fresh profile creation.

Validation adds registration-only, workspace-only and overlapping inventory
cases; multiple machines/projects with colliding names; every listed workspace
including one without a head; stale or failed source observations; real
send/read/resume target selection with no Bridge row; exactly-once concurrent
hydration and response loss; and zero native launch/prompt/task-transition
effects during listing or hydration. Exercise keyed and unassociated native
heads, live transports, head movement, old callers and unsupported providers.
The existing reservation recorder and affirmative worker `STARTED` composition
remain required; this inventory work does not replace them.

This source proposal adds no installed rollout or live recovery authority.
