# Bridge Retained Runtime Restoration

- **Slug:** `bridge-retained-runtime-restoration`
- **Repo:** copilot-extensions
- **Branch(es):** proposal first, then sequential source PRs against `dev`
- **Created:** 2026-10-23
- **Status:** Draft; local architecture accepted, normal proposal review pending
- **Vision:** [State-aware rollback](../../../visions/plugin-services/README.md#state-aware-rollback), [canonical Bridge deployment](../../../visions/plugins/agent-bridge/README.md#one-canonical-deploy-path), [version-skew-safe contracts](../../../visions/plugins/agent-bridge/README.md#version-skew-safe-contract-evolution)
- **Umbrella issue:** [#8063](https://github.com/ThomasMichon/copilot-extensions/issues/8063)
- **Parent contract:** [Acknowledged Execution Handoff](../acknowledged-execution-handoff/README.md)

## Guiding Intent

Restore a qualified immutable Bridge assembly through the current installer and
controller, including executable selection, wrappers, platform integration,
deployment provenance and the actual serving route. First extend Bridge's
existing custody owners so restoration cannot overlook accepted or ambiguous
work. Selecting an old slot is not restoration.

This is the canonical Bridge specialization of the existing handoff contract,
not a second generic execution-custody protocol. Bridge qualification does not
acknowledge Dispatch task execution or an adopter's external effects.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Integration owner | Architecture acceptance, issue/PR coordination and integration | Assigned owner of #8063 |
| Bridge source owner | Proposed Bridge custody and retained-installer slices | Isolated source contribution coordinated through #8063 |
| Independent reviewer | Architecture and source review | Normal repository review |
| Qualification owner | Separately authorized platform and running-adoption evidence | Explicit assignment through #8063 |

## Coordination

- **Topology:** one reviewed proposal, followed by sequential, independently
  reviewable source slices; integration belongs to the Integration owner.
- **Host (owns PRs):** Integration owner.
- **Delegates:** Bridge source owner owns only the assigned source/design slice;
  Independent reviewer owns review, not implementation.
- **Handoff:** return the changed paths, exact source revision, unresolved gates
  and evidence limitations to the Integration owner before another slice starts.
- Preserve other contributors' checkouts and unlanded work. Integrate related
  lifecycle repairs only from independently reviewed, committed source through
  their owners; do not inspect another contributor's uncommitted work.
- The parent effort owns generic execution/effect acknowledgement and handback.
  This effort owns Bridge participant enforcement and physical runtime restore.
  Each gate has one owner; the parent consumes Bridge evidence rather than
  copying its ledger or declaring all participants enrolled.

## Context

The exact target passed the native effort-adoption probe and resolves its own
repository as its effort home. No competing #8063 effort was found in the
checked-out active index. The existing issue claim is retained.

Current owners provide useful but incomplete pieces: acquisition journals,
atomic queued-turn admission, live-message receipts, native host receipts,
guarded serving reconciliation, installation publication proofs and kernel
retention leases. A poll can precede SDK send and a later ACK; cached host
enumeration is not strict inventory; ordinary drain is not durable closure.
Private component reconstruction is not installation rollback.

This closes existing vision intent and specializes the
[parent contract](../acknowledged-execution-handoff/contract.md). Follow the
[install contract](../../../docs/install-contract.md) and
[graceful-cutover pattern](../../../docs/patterns/graceful-daemon-cutover.md).
The stricter retained-restore operation adds no force/cold fallback and does not
change the ordinary deployment policy.

## Request

Accepted operator direction, quoted verbatim as a public-safe excerpt:

> Extend the Bridge custody protocols, then implement guarded restoration

Public-safe scope summary: extend existing Bridge owners, not a parallel
generic ledger or scheduler. Unknown in-flight work cannot be replayed under a
new identity. Preserve SDK acceptance, observed consumption and semantic
completion as different facts. Qualify target-declared state compatibility,
complete locked inventory, continuing custody and the full installer lifecycle.
Unsupported predecessors or participants refuse honestly.

Source implementation is authorized, with architecture acceptance and normal
proposal review required before executing the widened protocol design. Local
architecture acceptance is recorded; proposal review and landing remain open.
Implementation stays paused until the proposal merges and the Integration owner
assigns the next specific source slice. Source authorization is
not permission for deployment, process stops, protected-owner operations,
credential work or production capture. No private permission record or
operational evidence is incorporated into this public effort.

All decomposition, proposed API/storage names and test cases below and in the
linked contracts are **agent-recommended design**, pending independent review.

## Plan

### Phase 0 - Review the Bridge specialization
- [x] Verify target effort adoption, resolve the effort home, retain #8063 and
      reconcile the existing public handoff contract.
- [x] Record local architecture acceptance of the proposal, ownership boundaries
      and acceptance matrix.
- [ ] Land the proposal through normal repository review before implementation.

### Phase 1 - Close and inventory through existing Bridge owners
- [ ] Implement installation-bound admission epochs, producer enrollment and
      strict cross-owner inventory as specified in [custody-contract.md](custody-contract.md).
- [ ] Fence keyed creation, start/resume, queued/native sends, inbox/control
      handoffs, wake/redrive and host/lifecycle mutations at their effect owners.
- [ ] Preserve unresolved operations and refuse incomplete or legacy coverage;
      do not synthesize quiescence from an empty projection.

### Phase 2 - Reconcile custody and negotiate state compatibility
- [ ] Settle or transfer accepted work under its original identity, including
      consumer interruption, native send ambiguity and host handoff.
- [ ] Implement target-declared read/write/state-contract compatibility and
      actual passive preparation; neither HTTP generation nor schema equality
      qualifies a target.
- [ ] Verify Bridge-local evidence composes with, but cannot replace, the
      parent effort's Dispatch and adopter-effect barriers.

### Phase 3 - Restore through the current controller
- [ ] Implement strict capture/validation, pinned child input, retained target
      injection and continuing retention custody in [installer-contract.md](installer-contract.md).
- [ ] Converge owned wrappers, native integration, selectors, manifest and
      actual route without rebuilding, forced retirement or fallback restart.
- [ ] Recover interrupted publication through existing owners; retain
      unresolved evidence, authority and slot protection until settlement.

### Phase 4 - Validate source and native boundaries
- [ ] Complete [validation.md](validation.md), including real-process crash
      controls and separate Linux/Windows native qualification.
- [ ] Update authoritative runtime/install documentation, contract fixtures and
      plugin changefiles; preserve unit-only, impact-scoped PR/promotion gates.

### Phase 5 - Separately qualify adoption
- [ ] Record source review/merge, release, installation and running adoption
      independently, with separately authorized positive restore evidence.
- [ ] Obtain exact installed-cohort/target qualification; historical captures
      and mocked results do not satisfy it.
- [ ] Resolve or transfer every Plan and Validation Plan item before Done.

## Validation Plan

- [ ] Every custody cohort has a named owner, final effect fence, durable
      outstanding representation and explicit unsupported outcome.
- [ ] Closure and concurrent admission have one serial order; accepted work,
      legacy uncertainty and partial inventory never disappear.
- [ ] Lost replies, crashes and handback retain original identities and
      monotonic epochs without duplicate SDK/native sends.
- [ ] Target compatibility, immutable assembly and actual platform/route
      convergence are independently proved, including negative controls.
- [ ] Unsettled effects retain a continuing custodian and real slot protection;
      file receipts are never described as surviving kernel leases.
- [ ] Tests retain the bounded execution and portfolio separation in
      [validation.md](validation.md); absent native evidence remains unqualified.
- [ ] Parent acknowledgement, operational permission and protected-owner gates
      remain distinct from this effort's source completion.

## Proposal

Read [custody-contract.md](custody-contract.md),
[installer-contract.md](installer-contract.md) and [validation.md](validation.md).
They are prospective contracts, not supported APIs or operating instructions.
The source ownership and integration order are in those documents.
Local architecture acceptance permits proposal publication through normal
repository review. It does not establish merged review or implementation.

## Journal

### 2026-10-23 - Draft the Bridge participant and restore contract
- Confirmed compatible effort adoption and a target-local effort home.
- Retained #8063 for Bridge restore and reconciled the #7268 parent contract:
  extend existing Bridge owners; do not create a competing generic protocol.
- Recorded the accepted direction and explicit review/source/operational gates.
  No implementation, tests, production capture or lifecycle effects performed.
- Local architecture review accepted the bounded proposal. Normal proposal
  review/landing and a specific next-source-slice assignment remain prerequisites
  to implementation.
