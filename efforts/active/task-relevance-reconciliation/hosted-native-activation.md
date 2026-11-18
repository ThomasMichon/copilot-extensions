# Hosted ACP activation and post-load registration recovery

Part of [Task Relevance Reconciliation](README.md), correcting the remaining
[#12696](https://github.com/ThomasMichon/copilot-extensions/issues/12696)
boundary within [retained native recovery](retained-metadata-recovery.md).
**Status: plan reviewed in #13160; receiving writer source accepted in #13162;
Bridge negotiation and accepted-load journaling accepted in #13172.
Hosted registration accepted in #13176.
Original-owner post-load reconciliation and historical adaptation are under
source qualification; runtime acceptance remains open.**

## Measured contract gap

The source correction in #12700 can corroborate an externally reported native
PID when that process owns the conversation's actual CLI in-use hold. It keeps
closed source-bound activation history from being silently reopened. Its
synthetic open-hold fixture does not establish that a hosted ACP child acquires
the same hold after `session/load`.

A hosted ACP conversation can acknowledge loading its original native UUID
without opening that CLI hold. The existing Worktrees guard then refuses the
fresh activation after the native effect has already happened. Neither the
child wrapper PID nor a descendant PID is sufficient evidence of a particular
loaded conversation. Replacing the PID, creating a hold file, weakening the
CLI guard or retrying the load would be incorrect.

This amendment closes the existing Bridge continuity and ground-layer
session-lineage intent. It preserves the
[service-contract evolution pattern](../../../docs/patterns/service-contract-evolution.md)
and the existing Session Host/Driver ownership boundary. It adds no new host
owner, lifecycle database, daemon, scheduler or universal receipt verifier.

## Chosen owner contract

Keep CLI-native and hosted-ACP activation evidence distinct:

- CLI registration continues to require its existing original-process hold
  witness. Missing, foreign or inaccessible holds remain unconfirmed.
- A qualified retained writer may instead convey a versioned **hosted ACP
  activation** from the owner that actually observed the successful native
  load on its bound Session Host transport. Process existence alone, an ACP
  session ID assigned by `adopt_session`, a log message, a new operation ID or
  a caller-supplied success assertion does not establish that observation.
- Bind that observation to the original recovery operation, native UUID,
  workspace/head revision and incarnation, executing writer contract, local
  Host and child process births, execution epoch and accepted ACP load.
  Keep immutable birth provenance separate from the current serializer.
- Worktrees validates the declared source kind through its existing qualified
  session-registration operation. Under the canonical record lock, recheck
  the original workspace/head and activation history, the current physical
  process/epoch observations and the permitted retained-writer scope before
  acquiring claims or saving. Derive a stable opaque hosted source identity
  from that physical activation; an arbitrary operation ID must not turn a
  closed source into a fresh one.
- Preserve the old interval and append one new identified activation.
  Duplicate registration of that same open activation is idempotent.
  Changed or reused processes, a changed head/nonce, terminal worktrees,
  pruned or saturated history and unsupported evidence refuse without a
  write. A hosted source must not masquerade as an original CLI hook source.

The existing trusted owner conveys the completed ACP association; the receiver
corroborates its physical and canonical record boundaries. This is not a new
cryptographic credential or authority grant. A process observation cannot prove
ACP semantics independently of its transport owner.

## Delivery sequence

1. **Expand the receiving writer first.** Declare hosted-activation support
   separately through the existing executing-writer capability response.
   Introduce any needed strict input on the existing registration command,
   retaining legacy syntax and CLI-native behavior. A supported newer writer
   can execute original retained records without requiring their old build.
2. **Negotiate before native effects.** Bridge selects the actual compatible
   Worktrees executor before entering a retained load. If the required hosted
   source cannot be represented or corroborated, return the specific contract
   gap before starting a Host or invoking `session/load`. Preserve ordinary
   non-retained routes. Never probe an effectful new form and retry old syntax.
3. **Retain native acceptance before registration.** Extend the existing
   original-operation recovery journal with the completed load and its
   original Host/transport association. Persist it before a subsequent lineage
   write can fail. A lost response or restart discovers that accepted state;
   it does not make another load eligible. Keep the existing unknown result
   for incomplete, conflicting or unconfirmed evidence.
4. **Finish a post-load operation through its owner.** Reuse the retained
   original Host and its supported transport-reconnection path to complete
   registration and confirm the same idle native conversation. Reattachment
   must not invoke `session/new`, `session/load`, prompt delivery or a
   replacement Host. Serialize against normal resume/send/stop/handoff and
   concurrent reconciliation through existing operation/workspace owners.
   Preserve launch, permission and stop holds until this owner confirms the
   original operation complete.
5. **Adapt affirmative historical evidence deliberately.** An older operation
   may have a complete operation-tagged accepted-load history and the original
   retained Host but no new journal field. Qualify those original facts
   through the existing owner, including current physical and head checks,
   rather than interpreting missing data as success or starting another
   operation. Incomplete history or unavailable transport stays held. A
   current health check cannot retrospectively qualify the old load.
6. **Keep task acceptance independent.** A completed load/registration is not
   a delivered wake, a successful old attempt or worker acceptance. Continue
   the original reservation/task flow only through its existing owner and
   affirmative worker start. Preserve task generation, send keys, policy
   clocks, retained history and all existing allocation holds.

Missing capability on a supported older provider gives a pre-effect refusal
for this affected recovery, not a blanket denial of unrelated operations.
An older reader must preserve or explicitly refuse new pending-activation
state during rollback; it may not clear its gate or relaunch the native.
Document the supported passive rollback boundary before enabling the caller.
Unsupported platforms retain an explicit unqualified outcome; Linux source
controls must not be reported as Windows native qualification.

### Accepted-load journal and passive rollback

The first Bridge slice uses the existing operation-tagged event journal:
`retained_native_load_accepted` retains the original request, selected executing
writer, immutable Host transport association and hosted activation source.
The RPC owner writes and flushes this event immediately after the actual load
acknowledgement, before preference application or lineage registration. A stop
arriving while the RPC was in flight does not erase its acknowledgement.
The Host nonce itself is not copied into events; the association retains its
digest. Physical observations alone never emit this event.

The original recovery row keeps its existing strict request/state shape.
Prior readers continue to treat an interrupted operation as unknown and retain
its launch gate; their no-load settlement rejects the additional acceptance
event. This passive rollback does not complete an accepted load, but also cannot
turn it into a new eligible load.

The registration consumer rechecks the original operation, current transport,
physical Host/child births, epoch and executing writer against that acknowledged
event before conveying its activation to Worktrees. It explicitly names the
recorded child PID rather than attempting to make another PID satisfy the
CLI-native hold route. An unconfirmed registration leaves the original
operation held; the consumer does not itself reconcile an interrupted load.

The model-free source control in
`plugins/agent-bridge/tests/test_retained_hosted_registration.py` exercises a
real Session Host and ACP child, then the actual Worktrees parser in a
separate process. The child acknowledges loading the original UUID without
creating a CLI hold. Registration preserves the ended identified activation,
appends one hosted activation and is byte-idempotent on retry. Transport
reattachment to the same Host/child adds no load, new conversation or prompt.
The extended fixture also exercises actual original-operation admission,
post-load failure, frontend rehydration, historical adaptation and a committed
registration whose response is lost. Reconciliation retains the same Host and
child and performs no additional native load or prompt. Stop holds and a live
foreign generation refuse without altering canonical lineage or releasing the
gate. Separate child-exit controls keep unknown original Host custody intact.
This is model-free source qualification, not installed recovery or native
Windows qualification.

## Validation plan

- [x] Exercise the actual Bridge-to-Worktrees parser/process boundary with a
      model-free ACP worker that acknowledges loading the original UUID and
      deliberately creates **no CLI hold**. Preserve ended identified history,
      append one hosted activation and prove normal same-native continuation.
      A fabricated open-hold fixture is not the discriminating positive case.
- [x] Inject failure after the accepted load but before lineage commit, lose
      the response and restart the frontend. Reconcile through the surviving
      original Host with exactly one load, no new conversation/Host and no
      prompt. Independently inspect canonical lineage and the original journal.
- [ ] Race registration/reconciliation with competing resume, stop, head
      movement, incarnation change and process exit/reuse. Unknown, changed,
      foreign, remote or unsupported observations preserve custody and bytes.
- [x] Exercise repeated registration and response loss after commit: one
      activation, original intervals unchanged, one operation outcome and
      unchanged task acceptance. A closed hosted source cannot be reactivated
      by changing only an operation ID.
- [ ] Preserve actual CLI hold-positive and missing/foreign-hold negative
      controls, ordinary live attachment and non-retained/remote behavior.
      Test new/old caller-provider combinations and the declared rollback
      boundary through the real selected executor.
- [ ] Keep semantic unit controls actor-free and retain every existing unit
      node/source binding. Put real process/Host/ACP controls in the existing
      selected integration portfolio with its current cleanup and time bounds.

Source delivery, qualified release, authorized installation and actual original
conversation/task lifecycle acceptance remain separate gates. This proposal
grants no rollout, native replay, task reset, allocation, credential change,
process termination or filesystem deletion.
