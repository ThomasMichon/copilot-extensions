# Bridge Retained Runtime Restoration

- **Slug:** `bridge-retained-runtime-restoration`
- **Repo:** copilot-extensions
- **Branch(es):** proposal first, then sequential source PRs against `dev`
- **Created:** 2026-10-23
- **Status:** Active; opt-in SDK, original-turn/acquisition/provider DB and explicit local native/acquisition/creation source merged; full custody/qualification/restoration open
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
architecture acceptance and proposal review/landing are complete.
The first bounded Phase 1 DB epoch/producer association and strict-inventory
foundation merged in #8203. The Integration owner has assigned the next bounded
internal DB inbox-admission/handoff slice. Actual authenticated producer/consumer
enrollment, public poll/SDK integration, native and installer wiring remain
separate source slices under this same contract. Source authorization is
not permission for deployment, process stops, protected-owner operations,
credential work or production capture. No private permission record or
operational evidence is incorporated into this public effort.

Later accepted source-only scope, summarized: retain complete historical
contract provenance through immutable, hash-checked JSON sidecars while
preserving the unchanged size guard, every canonical reader/capture/validation
surface and normal review. The bounded
[provenance layout proposal](provenance-layout.md) is a Phase-4 prerequisite
for the existing inbox source PR #8389; it grants no runtime operation.

Later operator direction, quoted verbatim:

> In the end, we're keeping agent-dispatch and agent-bridge within user-owned bounds, so we can walk back heavy token authentication. We'll only need such a thing between containers, for which we can use authenticated proxy wrappers in the container network bridges.

> Right. Focus hashing and other safety-focused things on data recovery and robustness against accidental replay in the face of disconnects and timeouts. We don't want to drop messages.

The [consumer enrollment and recovery refinement](consumer-recovery.md)
replaces the proposed per-consumer authentication prerequisite with that
user-owned trust boundary. It retains exact custody associations and lossless
recovery. This is a source-design review, not permission to remove installed
authentication, change network/proxy configuration or qualify a running cohort.

All decomposition, proposed API/storage names and test cases below and in the
linked contracts are **agent-recommended design**. The existing custody and
installer plan cleared proposal review; the provenance contract cleared its
separate gate in #8420. Implementation and its source/native evidence still
require their own review and qualification.

## Plan

### Phase 0 - Review the Bridge specialization
- [x] Verify target effort adoption, resolve the effort home, retain #8063 and
      reconcile the existing public handoff contract.
- [x] Record local architecture acceptance of the proposal, ownership boundaries
      and acceptance matrix.
- [x] Land the proposal through normal repository review before implementation.

### Phase 1 - Close and inventory through existing Bridge owners
- [ ] Implement installation-bound admission epochs, producer enrollment and
      strict cross-owner inventory as specified in [custody-contract.md](custody-contract.md).
      The first [DB/storage foundation](../../../plugins/agent-bridge/docs/runtime-custody-foundation.md)
      merged in #8203; it does not close this item or qualify
      complete runtime custody.
- [ ] Fence keyed creation, start/resume, queued/native sends, inbox/control
      handoffs, wake/redrive and host/lifecycle mutations at their effect owners.
      The [original-turn DB foundation](../../../plugins/agent-bridge/docs/native-turn-custody.md)
      merged in #8713 and retains internal atomic handoffs and one-shot/outcome
      state. Explicit internal local process-owned ACP selection and final-send
      wiring merged in #8732. Full lifecycle/cohort coverage and installed
      qualification remain open. The
      [original-acquisition DB foundation](../../../plugins/agent-bridge/docs/acquisition-custody.md)
      merged in #8750 with internal epoch association and one-shot launch observations.
      Explicit internal local acquisition, Host/child/new/load and prompt
      custody merged in #8783, including no-downgrade lifecycle and recovery
      exclusions. Other creation/background/host/remote cohorts and full
      installed qualification remain open. The internal
      [keyed-creation DB foundation](../../../plugins/agent-bridge/docs/creation-custody.md)
      merged in #8823. Its separately selected local allocation subprocess
      fence merged in #8869. Explicit original local Host/child/native creation,
      binding and first-turn source merged in #8895. Ground-owner child and
      actual native/platform qualification, provider and remaining effect-owner
      integration stay open. Original provider native-handoff storage merged in
      #8935 as an internal prerequisite, not provider effect-owner mediation.
      The [provider boundary specialization](provider-custody.md) cleared review
      in #8940. Immutable provider declarations merged in #8958 and explicit
      original binding storage in #8960. Exact selected-slot retention merged
      in #8967; actual immutable invocation, provider/guest admission and native
      integration remain unimplemented.
      The package-owned executing-slot retention context merged in #8976 as
      an internal prerequisite only; no provider launch path consumes it yet.
      Both profiles still declare retained creation unsupported.
- [ ] Preserve unresolved operations and refuse incomplete or legacy coverage;
      do not synthesize quiescence from an empty projection.

### Phase 1a - Retain internal DB inbox handoffs
- [x] Extend existing inbox rows with producer association, immutable original
      handoff/consumer observations and a one-shot final-admission primitive.
- [x] Retain distinct SDK acceptance/consumption/unknown outcomes through
      registration cleanup, closure and restart; preserve legacy uncertainty.
- [x] Validate the bounded owner methods and normal migration/passive adoption.
      This slice does not close actual transport/SDK integration or Phase 1.

### Phase 1b - User-owned consumer enrollment and recovery
- [x] Review [consumer-recovery.md](consumer-recovery.md) before implementing
      its local enrollment/poll/SDK integration; no per-consumer token or
      independent native-authentication prerequisite.
- [ ] Bind the existing installation and current SDK consumer through the
      established user-owned boundary; keep cross-container ingress under
      authenticated proxy owners and reject unsupported exposure.
      The explicit local HTTP-48 adapter merged in #8540 and ordinary HTTP-49
      producer admission/recovery merged in #8569. Actual extension SDK
      enrollment/delivery merged in #8674 behind an explicit opt-in, with
      controlled source qualification; actual native/platform qualification
      remains open.
      Bounded, read-only original-handoff
      recovery and one-message polling through the existing inbox/receipt
      owners merged in #8661. The
      [consumer guide](../../../plugins/agent-bridge/docs/retained-sdk-consumer.md)
      records the current source boundary and unsupported cases.
- [ ] Preserve original durable messages, handoffs and outcomes across
      disconnect, timeout, restart and lost replies; reconcile uncertainty
      without dropping work or blindly resending it.
- [ ] Qualify the actual consumer/SDK boundaries and explicit unsupported cases
      in the refinement's validation matrix before claiming this slice complete.

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

### Phase 4a - Preserve source evidence with lossless provenance sidecars
- [x] Review the [versioned provenance layout](provenance-layout.md) before
      implementing the bounded source-format change.
- [x] Complete its reader/writer, publication and validation gates, then
      return to #8389 without dropping evidence or increasing the size guard.

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
- [ ] Local enrollment uses the established user-owned access boundary, not a
      new per-agent token/attestation chain; cross-container ingress has no
      unauthenticated bypass and accepted messages survive every recovery case
      in [consumer-recovery.md](consumer-recovery.md#validation-obligations).
- [ ] Target compatibility, immutable assembly and actual platform/route
      convergence are independently proved, including negative controls.
- [ ] Unsettled effects retain a continuing custodian and real slot protection;
      file receipts are never described as surviving kernel leases.
- [ ] Tests retain the bounded execution and portfolio separation in
      [validation.md](validation.md); absent native evidence remains unqualified.
- [ ] Parent acknowledgement, operational permission and protected-owner gates
      remain distinct from this effort's source completion.
- [x] Historical contract provenance materializes completely and losslessly
      through all canonical consumers, with immutable artifacts below the
      unchanged per-file cap; no runtime qualification is inferred.

## Proposal

Read [custody-contract.md](custody-contract.md),
[installer-contract.md](installer-contract.md) and [validation.md](validation.md).
They are prospective contracts, not supported APIs or operating instructions.
The source ownership and integration order are in those documents.
The proposal merged through normal repository review in
[#8179](https://github.com/ThomasMichon/copilot-extensions/pull/8179).
This establishes the reviewed plan, not implementation or running adoption.
The later [provenance layout](provenance-layout.md) cleared normal review in
[#8420](https://github.com/ThomasMichon/copilot-extensions/pull/8420).
The source-format implementation merged in
[#8469](https://github.com/ThomasMichon/copilot-extensions/pull/8469);
historical capture-owner qualification merged in
[#8503](https://github.com/ThomasMichon/copilot-extensions/pull/8503).
The dependent internal inbox owner and fresh committed-source evidence merged
in [#8389](https://github.com/ThomasMichon/copilot-extensions/pull/8389).
Full custody, actual native consumer/SDK qualification and restoration remain open.

## Journal

Dated entries preserve the complete source history; the headings below retain
existing README anchors.

### 2026-10-25 - Executing-runtime resources merged; provider admission next

[Full journal entry](journal/2026/10.25-source-custody-01.md#2026-10-25---executing-runtime-resources-merged-provider-admission-next).

### 2026-10-25 - Executing-runtime resource prerequisite

[Full journal entry](journal/2026/10.25-source-custody-01.md#2026-10-25---executing-runtime-resource-prerequisite).

### 2026-10-25 - Selected-slot retention prerequisite merged

[Full journal entry](journal/2026/10.25-source-custody-01.md#2026-10-25---selected-slot-retention-prerequisite-merged).

### 2026-10-25 - Exact selected-slot retention prerequisite

[Full journal entry](journal/2026/10.25-source-custody-01.md#2026-10-25---exact-selected-slot-retention-prerequisite).

### 2026-10-25 - Original provider storage prerequisite merged

[Full journal entry](journal/2026/10.25-source-custody-01.md#2026-10-25---original-provider-storage-prerequisite-merged).

### 2026-10-25 - Provider-owned original binding storage prerequisite

[Full journal entry](journal/2026/10.25-source-custody-01.md#2026-10-25---provider-owned-original-binding-storage-prerequisite).

### 2026-10-25 - Immutable declaration prerequisite merged

[Full journal entry](journal/2026/10.25-source-custody-01.md#2026-10-25---immutable-declaration-prerequisite-merged).

### 2026-10-25 - Immutable provider declaration prerequisite

[Full journal entry](journal/2026/10.25-source-custody-01.md#2026-10-25---immutable-provider-declaration-prerequisite).

### 2026-10-25 - Local/provider selection prerequisite merged

[Full journal entry](journal/2026/10.25-source-custody-01.md#2026-10-25---localprovider-selection-prerequisite-merged).

### 2026-10-25 - Refuse provider selection at the local creation owner

[Full journal entry](journal/2026/10.25-source-custody-01.md#2026-10-25---refuse-provider-selection-at-the-local-creation-owner).

### 2026-10-25 - Provider boundary proposal merged; provider-first source next

[Full journal entry](journal/2026/10.25-source-custody-01.md#2026-10-25---provider-boundary-proposal-merged-provider-first-source-next).

### 2026-10-25 - Select the actual provider custody boundary

[Full journal entry](journal/2026/10.25-source-custody-01.md#2026-10-25---select-the-actual-provider-custody-boundary).

### 2026-10-25 - Provider native-handoff storage merged; actual effect owners next

[Full journal entry](journal/2026/10.25-source-custody-01.md#2026-10-25---provider-native-handoff-storage-merged-actual-effect-owners-next).

### 2026-10-25 - Provider native-handoff DB prerequisite under validation

[Full journal entry](journal/2026/10.25-source-custody-01.md#2026-10-25---provider-native-handoff-db-prerequisite-under-validation).

### 2026-10-25 - Original local native creation merged; provider handoff storage next

[Full journal entry](journal/2026/10.25-source-custody-01.md#2026-10-25---original-local-native-creation-merged-provider-handoff-storage-next).

### 2026-10-24 - Original local creation native source controls

[Full journal entry](journal/2026/10.24-source-custody-01.md#2026-10-24---original-local-creation-native-source-controls).

### 2026-10-24 - Original local allocation subprocess source merged

[Full journal entry](journal/2026/10.24-source-custody-01.md#2026-10-24---original-local-allocation-subprocess-source-merged).

### 2026-10-24 - Original local allocation owner under validation

[Full journal entry](journal/2026/10.24-source-custody-01.md#2026-10-24---original-local-allocation-owner-under-validation).

### 2026-10-24 - Keyed-creation DB source merged; allocation effect owners next

[Full journal entry](journal/2026/10.24-source-custody-01.md#2026-10-24---keyed-creation-db-source-merged-allocation-effect-owners-next).

### 2026-10-24 - Original keyed-creation DB foundation under validation

[Full journal entry](journal/2026/10.24-source-custody-01.md#2026-10-24---original-keyed-creation-db-foundation-under-validation).

### 2026-10-24 - Local acquisition source merged; keyed creation custody next

[Full journal entry](journal/2026/10.24-source-custody-01.md#2026-10-24---local-acquisition-source-merged-keyed-creation-custody-next).

### 2026-10-24 - Original local acquisition effect owners under validation

[Full journal entry](journal/2026/10.24-source-custody-01.md#2026-10-24---original-local-acquisition-effect-owners-under-validation).

### 2026-10-24 - Original-acquisition DB prerequisite merged

[Full journal entry](journal/2026/10.24-source-custody-01.md#2026-10-24---original-acquisition-db-prerequisite-merged).

### 2026-10-24 - Local native transport source merged; lifecycle custody next

[Full journal entry](journal/2026/10.24-source-custody-01.md#2026-10-24---local-native-transport-source-merged-lifecycle-custody-next).

### 2026-10-24 - Explicit local native transport source controls

[Full journal entry](journal/2026/10.24-source-custody-01.md#2026-10-24---explicit-local-native-transport-source-controls).

### 2026-10-24 - Original-turn DB foundation merged; native owner wiring next

[Full journal entry](journal/2026/10.24-source-custody-01.md#2026-10-24---original-turn-db-foundation-merged-native-owner-wiring-next).

### 2026-10-24 - Original-turn native handoff DB foundation

[Full journal entry](journal/2026/10.24-source-custody-01.md#2026-10-24---original-turn-native-handoff-db-foundation).

### 2026-10-24 - Creation/turn durability prerequisite merged

[Full journal entry](journal/2026/10.24-source-custody-01.md#2026-10-24---creationturn-durability-prerequisite-merged).

### 2026-10-24 - Retained creation/turn durability prerequisite

[Full journal entry](journal/2026/10.24-source-custody-01.md#2026-10-24---retained-creationturn-durability-prerequisite).

### 2026-10-24 - Retained SDK source merged; continue remaining effect owners

[Full journal entry](journal/2026/10.24-source-custody-01.md#2026-10-24---retained-sdk-source-merged-continue-remaining-effect-owners).

### 2026-10-24 - Wire the explicit retained SDK consumer

[Full journal entry](journal/2026/10.24-source-custody-02.md#2026-10-24---wire-the-explicit-retained-sdk-consumer).

### 2026-10-24 - Recovery prerequisite merged; continue the actual SDK consumer

[Full journal entry](journal/2026/10.24-source-custody-02.md#2026-10-24---recovery-prerequisite-merged-continue-the-actual-sdk-consumer).

### 2026-10-24 - Qualify original-handoff recovery before SDK integration

[Full journal entry](journal/2026/10.24-source-custody-02.md#2026-10-24---qualify-original-handoff-recovery-before-sdk-integration).

### 2026-10-24 - Ordinary producer merged; continue at the actual SDK boundary

[Full journal entry](journal/2026/10.24-source-custody-02.md#2026-10-24---ordinary-producer-merged-continue-at-the-actual-sdk-boundary).

### 2026-10-24 - Wire ordinary keyed producer admission through current enrollment

[Full journal entry](journal/2026/10.24-source-custody-02.md#2026-10-24---wire-ordinary-keyed-producer-admission-through-current-enrollment).

### 2026-10-24 - Local HTTP-48 adapter merged; wire the ordinary producer and SDK next

[Full journal entry](journal/2026/10.24-source-custody-02.md#2026-10-24---local-http-48-adapter-merged-wire-the-ordinary-producer-and-sdk-next).

### 2026-10-24 - Close the installation-owner capture gap found in review

[Full journal entry](journal/2026/10.24-source-custody-02.md#2026-10-24---close-the-installation-owner-capture-gap-found-in-review).

### 2026-10-24 - HTTP-48 adapter source qualified; review and SDK integration open

[Full journal entry](journal/2026/10.24-source-custody-02.md#2026-10-24---http-48-adapter-source-qualified-review-and-sdk-integration-open).

### 2026-10-24 - Current-consumer DB prerequisite merged; wire the transport next

[Full journal entry](journal/2026/10.24-source-custody-02.md#2026-10-24---current-consumer-db-prerequisite-merged-wire-the-transport-next).

### 2026-10-24 - Durable current-consumer DB prerequisite

[Full journal entry](journal/2026/10.24-source-custody-02.md#2026-10-24---durable-current-consumer-db-prerequisite).

### 2026-10-24 - Installation-binding prerequisite merged; enroll the consumer next

[Full journal entry](journal/2026/10.24-source-custody-02.md#2026-10-24---installation-binding-prerequisite-merged-enroll-the-consumer-next).

### 2026-10-24 - Derive inbox installation observations through the existing owner

[Full journal entry](journal/2026/10.24-source-custody-02.md#2026-10-24---derive-inbox-installation-observations-through-the-existing-owner).

### 2026-10-24 - Consumer trust and recovery refinement merged

[Full journal entry](journal/2026/10.24-source-custody-02.md#2026-10-24---consumer-trust-and-recovery-refinement-merged).

### 2026-10-24 - Refine the trust boundary around message recovery

[Full journal entry](journal/2026/10.24-source-custody-02.md#2026-10-24---refine-the-trust-boundary-around-message-recovery).

### 2026-10-23 - Inbox owner merged; continue at the real consumer boundary

[Full journal entry](journal/2026/10.23-source-custody-01.md#2026-10-23---inbox-owner-merged-continue-at-the-real-consumer-boundary).

### 2026-10-23 - Capture the reconciled inbox source

[Full journal entry](journal/2026/10.23-source-custody-01.md#2026-10-23---capture-the-reconciled-inbox-source).

### 2026-10-23 - Return to the inbox source with both complete histories

[Full journal entry](journal/2026/10.23-source-custody-01.md#2026-10-23---return-to-the-inbox-source-with-both-complete-histories).

### 2026-10-23 - Qualify the historical HTTP-46 capture owner

[Full journal entry](journal/2026/10.23-source-custody-01.md#2026-10-23---qualify-the-historical-http-46-capture-owner).

### 2026-10-23 - Provenance implementation merged; finish capture-owner closure

[Full journal entry](journal/2026/10.23-source-custody-01.md#2026-10-23---provenance-implementation-merged-finish-capture-owner-closure).

### 2026-10-23 - Validate consumers after actual version-2 publication

[Full journal entry](journal/2026/10.23-source-custody-01.md#2026-10-23---validate-consumers-after-actual-version-2-publication).

### 2026-10-23 - Tighten provenance publication and capture recovery

[Full journal entry](journal/2026/10.23-source-custody-01.md#2026-10-23---tighten-provenance-publication-and-capture-recovery).

### 2026-10-23 - Provenance contract merged; source implementation next

[Full journal entry](journal/2026/10.23-source-custody-01.md#2026-10-23---provenance-contract-merged-source-implementation-next).

### 2026-10-23 - Propose the lossless evidence-format prerequisite

[Full journal entry](journal/2026/10.23-source-custody-01.md#2026-10-23---propose-the-lossless-evidence-format-prerequisite).

### 2026-10-23 - Repair reviewed producer and durability boundaries

[Full journal entry](journal/2026/10.23-source-custody-01.md#2026-10-23---repair-reviewed-producer-and-durability-boundaries).

### 2026-10-23 - Assign the internal inbox custody owner slice

[Full journal entry](journal/2026/10.23-source-custody-01.md#2026-10-23---assign-the-internal-inbox-custody-owner-slice).

### 2026-10-23 - Require prepared custody storage and finite host metadata

[Full journal entry](journal/2026/10.23-source-custody-01.md#2026-10-23---require-prepared-custody-storage-and-finite-host-metadata).

### 2026-10-23 - Bound the first DB/storage foundation

[Full journal entry](journal/2026/10.23-source-custody-01.md#2026-10-23---bound-the-first-dbstorage-foundation).

### 2026-10-23 - Proposal merged; implementation remains paused

[Full journal entry](journal/2026/10.23-source-custody-01.md#2026-10-23---proposal-merged-implementation-remains-paused).

### 2026-10-23 - Draft the Bridge participant and restore contract

[Full journal entry](journal/2026/10.23-source-custody-01.md#2026-10-23---draft-the-bridge-participant-and-restore-contract).
