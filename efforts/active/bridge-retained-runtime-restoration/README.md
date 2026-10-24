# Bridge Retained Runtime Restoration

- **Slug:** `bridge-retained-runtime-restoration`
- **Repo:** copilot-extensions
- **Branch(es):** proposal first, then sequential source PRs against `dev`
- **Created:** 2026-10-23
- **Status:** Active; DB foundation, provenance implementation and historical capture qualification merged; inbox source in progress, full Phase 1 open
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
- [ ] Preserve unresolved operations and refuse incomplete or legacy coverage;
      do not synthesize quiescence from an empty projection.

### Phase 1a - Retain internal DB inbox handoffs
- [x] Extend existing inbox rows with producer association, immutable original
      handoff/consumer observations and a one-shot final-admission primitive.
- [x] Retain distinct SDK acceptance/consumption/unknown outcomes through
      registration cleanup, closure and restart; preserve legacy uncertainty.
- [x] Validate the bounded owner methods and normal migration/passive adoption.
      This slice does not close actual transport/SDK integration or Phase 1.

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
- [ ] Complete its reader/writer, publication and validation gates, then
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
- [ ] Target compatibility, immutable assembly and actual platform/route
      convergence are independently proved, including negative controls.
- [ ] Unsettled effects retain a continuing custodian and real slot protection;
      file receipts are never described as surviving kernel leases.
- [ ] Tests retain the bounded execution and portfolio separation in
      [validation.md](validation.md); absent native evidence remains unqualified.
- [ ] Parent acknowledgement, operational permission and protected-owner gates
      remain distinct from this effort's source completion.
- [ ] Historical contract provenance materializes completely and losslessly
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
Dependent-source capture is complete; current evidence-head review remains open.

## Journal

### 2026-10-23 - Capture the reconciled inbox source
- Genuine committed source `c0b3a2a09a67d93c7ceb8d4cc936254ba2444beb`,
  retained through managed publication before capture, completed the actual
  HTTP-47 write and same-source no-write entrypoints. Both passed all 117
  selected model-free cases and checked the complete interpreter/entrypoint
  receipts before accepting 68 outputs.
- The capture appends 67 HTTP records while retaining all 2,057 prior records
  in exact order. The complete history now contains 2,073 HTTP records and
  51 preference records. All five published chunks remain byte-identical;
  the index is 323,955 bytes under the unchanged 1 MiB guard.
- The canonical checker accepts both contracts and all 583 fixtures against
  the reviewed base. Only current HTTP-47 packets and their registry bindings
  change; older packets, generation and capability floors remain unchanged.
- Review of the final evidence head and normal merge remain required.
  This source capture does not qualify an installed receiver, native START,
  actual SDK delivery, complete runtime custody or guarded restoration.

### 2026-10-23 - Return to the inbox source with both complete histories
- #8503 merged as `17bec8794444148b65e0b78f1279d51df6a5f30b` after
  current-head Copilot approval, no open findings and green required checks.
  The source effort was reread; full restoration remains Active.
- Reconcile the same #8389 onto that reviewed source. Preserve both ordered
  append-only provenance deltas after their genuine shared prefix: 2,006 HTTP
  records plus 51 preference records. All five upstream chunks remain exact,
  and the resulting index is 300,305 bytes under the unchanged guard.
- The 68 current HTTP-47 packet conflicts differ only in source/closure/shared
  capture bindings; their actual reviewed upstream bytes and hashes remain
  paired. Original inbox captures remain in retained source history.
  No pre-47 packet, capability floor or historical record is rewritten.
- The reviewed producer/durability repair and final associated-control refusal
  retain identical implementation/test/doc patches after integration. The
  merged inbox/storage/adoption/receipt/recovery cohort passed 213 cases with
  two unrelated collection skips and verified cleanup. A genuine
  committed-source capture/no-write pair,
  canonical checker and current-head review remain required before merge.

### 2026-10-23 - Qualify the historical HTTP-46 capture owner
- The actual updated writer completed inline and chunked `--write`/no-write
  cases through its existing contained startup replay. The locally retained
  HTTP-46 source is `c0b0dbba0f639344c5e4b384ccffe7b7ede1cc02`; the complete
  historical runtime/library source remains unchanged in composed disposable
  fixtures. Actual replay imports are hash-bound to that cohort.
- Three existing chunks, complete ordered history, foreign/pre-46 packet bytes,
  floors and the unchanged artifact cap survive. No-write preserves the complete
  contract tree, including publication-journal bytes. The canonical current
  corpus still validates both contracts and all 583 fixtures.
- Eighteen default-tier safety/receipt controls pass. The real T3 qualification
  stays explicitly opt-in and outside required unit-only CI; dependency
  preparation precedes network disconnection. The two collection-time skips
  are unrelated to the selected positive case; all four inner replays must
  pass without skipped cases.
- Qualification helper review/merge completed through #8503. Return to the same
  #8389 source integration for complete-history reconciliation, fresh source
  qualification and current-head review. Full custody, native START, release,
  installation and running adoption remain separate open gates.

### 2026-10-23 - Provenance implementation merged; finish capture-owner closure
- #8469 merged to `dev` as `b24b71a93df87e8d976a6aed4ee6b099e5c17471`,
  with sole parent `e75ca8ed86212e8dcc50b4256ec47c7a5164275d`.
  The actual review on head `1315adac7e2212074b0e5f25e1bc5a8d9a631708`
  had no open findings; required CI passed before managed completion.
- Source implementation, normal review and genuine generation-47 chunked
  publication are established. Historical generation-46 capture-owner
  qualification remains the next explicit source gate before returning to
  the same #8389 continuation.
- Full Phase 4a, custody/restore implementation, native/platform qualification
  and adoption remain open. A merged source-format prerequisite is not
  running receiver recovery.

### 2026-10-23 - Validate consumers after actual version-2 publication
- The converted canonical registry exposed two test-fixture assumptions:
  an existing archived prefix does not imply a plan creates new chunks, and
  incremental plan outputs do not include unchanged retained chunks.
- HTTP-45 replay fixtures now distinguish explicit inline input, new conversion
  and retained chunks. The real-history round trip seeds complete snapshot
  inputs before applying the incremental plan and checks retained chunk bytes.
  Both original failures were reproduced before correction.
- The bounded post-conversion consumer cohort passed 218 cases with two
  capability skips, including publication, captures, fixture maintenance and
  complete registry consumers. The canonical checker still accepts two
  contracts and 583 fixtures. Only plugin tests and effort prose changed:
  captured executable/closure inputs and checkpoint `5820a7fb` remain unchanged.
- Source review/merge and the remaining historical capture-owner gate remain
  open. No new capture observation or operational effect was fabricated to
  refresh the review.

### 2026-10-23 - Tighten provenance publication and capture recovery
- Initial source review identified output-consistency, interrupted journal and
  hardlink recovery, captured-inventory and host-code-page defects. The source
  repair requires exact preceding outputs before registry commit, atomically
  stages journal files, recovers only the source-bound staging alias pair under
  the existing lock, and preserves captured rather than post-publication
  metadata inventory. Git metadata readers explicitly decode UTF-8.
- The bounded Linux capture/publication cohort passed 121 cases with two
  capability skips; 84 preparation, canonical-checker and real Git-encoding
  controls passed separately. An earlier oversized selection hit its nested
  wall-clock limit without an assertion result; it is not counted as a pass.
- Fresh capture and no-write reproduction both passed all 117 required
  model-free cases against provider-retained source checkpoint
  `5820a7fb022e869dbd192044561c44bbcab26144`. The canonical publisher produced
  73 outputs and genuinely converted to version 2: all 1,786 historical records
  remain exact ordered prefixes of the 1,853-record result, with five chunks,
  a 228,258-byte index and a largest chunk of 261,917 bytes. The unchanged
  per-artifact cap holds; the canonical checker accepts two contracts and
  583 fixtures.
- A subsequent passing current-head review remains required. No runtime,
  installer, live lane or adoption effect is authorized by this source repair.

### 2026-10-23 - Provenance contract merged; source implementation next
- #8420 merged to `dev` as `a9b307b165a8ef14a305ecf0136250572f36aa2c`,
  with sole parent `210518070267f654a1534ac98f98f602946d0cbb`.
  Copilot approved actual head `8c5b426eda1df5df2b9781224c9bfc9d15fb5198`
  and required CI passed before the normal managed merge.
- The earlier approval on the pre-index head was not treated as current
  approval. A bounded timeout led to a normal current-head review request,
  not reviewer diagnostics or a bypass.
- The existing source venue was pulled forward onto the merged plan. Next is
  the complete actual reader/writer trace and the reviewed lossless format
  implementation, followed by source review and genuine qualification.
  No codec, schema conversion, sidecar or runtime action is implemented by
  this plan merge; #8389 and all remaining effort gates stay open.

### 2026-10-23 - Propose the lossless evidence-format prerequisite
- #8203 merged the bounded DB/storage foundation; full Phase 1 remains open.
  The existing inbox source PR #8389 remains separately owned and unmerged.
- Preserving both actual provenance histories exceeds the unchanged 1 MiB cap
  even with minimal JSON. Source-only sidecar work is approved; no provenance
  was dropped, guard raised or force merge performed.
- Record a bounded versioned-layout proposal and complete reader/writer and
  validation obligations before implementation. This plan does not alter
  runtime protocols, installation, active custody or operational permission.

### 2026-10-23 - Repair reviewed producer and durability boundaries
- Source review identified mixed-producer batch refusal, non-durable final
  admission under WAL/NORMAL, keyed retry association mismatch and legacy
  exposure before handoff. Each now has a focused regression.
- Poll/final admission validates each original row's enrolled producer in the
  current installation/epoch, independently of the polling frontend. Keyed
  retries must match the original association, including legacy versus enrolled.
- Associated rows stay out of legacy poll/ACK/consumption from insertion onward.
  Custody and associated inbox insertion commit with connection-local FULL
  synchronization before returning, then restore the previous setting. Normal
  event/metadata writes retain their existing policy.
- The affected isolated cohort passed 323 cases with two unrelated collection
  skips, including normal/adopted commit-setting observations, rollback-policy
  restoration and the original process-exit/race controls. Actual power-loss,
  authenticated SDK and native platform qualification remain open.

### 2026-10-23 - Assign the internal inbox custody owner slice
- #8203 merged the bounded DB/storage foundation to `dev` as
  `ca1602f0f9e4717fc3836bc164966ca44b900ed2`. Full Phase 1 remains open.
- The Integration owner selected the existing DB inbox owner for the next
  bounded source slice: original-row producer admission, retained consumer
  handoff, one-shot final admission, separate outcome reports and history
  retention. It refines the reviewed contract without widening operational
  authority or adding a competing service, queue or operation ledger.
- The current public poll returns bodies without durable handoff; the extension
  sends and ACKs afterward, with in-memory interrupt replay. Those ordinary
  paths are not switched to the internal primitives by this slice. Actual
  authenticated consumer enrollment, negotiated transport/SDK integration,
  native custody and installer qualification remain open.
- Schema 30 and the DB owner methods passed the affected isolated development
  cohort: 314 tests, with two unrelated collection skips. This includes an
  independent child exiting after committed final admission, two-owner SQLite
  ordering, registration cleanup and legacy/passive-adoption controls. The old
  receipt migration fixture now starts from frozen pre-custody SQL instead of
  dropping a column required by today's retention trigger. No production guard
  was removed. These results are not actual SDK, Windows or live qualification.

### 2026-10-23 - Require prepared custody storage and finite host metadata
- Schema 28 explicitly versions the required storage. The canonical writable
  migration prepares it before publication; read-only and existing-only writable
  adoption reject older or partial layouts without hidden migration. The frozen
  pre-custody-v27 SQL fixture preserves independent old histories without needing
  today's constructor or local Git objects.
- Host inventory rejects exponent-overflow floats at any JSON nesting while
  retaining existing duplicate-key, path, size, mutex and cache guards.
- The focused DB/adoption/host/custody/delete cohort passed 240 cases with two
  skips. Required-shape refusal and finite decoding remain bounded foundation
  repairs, not complete producer enrollment, restoration or Phase 1 completion.
- The full canonical contract checker passed for two contracts and 456 fixtures.
  Existing HTTP/native-admission/result replays passed 229 cases with two skips.
  Only four registered source fingerprints/classifications were refreshed;
  every historical fixture, capture provenance, HTTP generation and floor was
  preserved.

### 2026-10-23 - Bound the first DB/storage foundation
- The assigned first source slice adds explicit DB epoch/producer observations,
  serial session/turn insertion fencing, original-history retention and
  revision-checked DB inventory, plus strict fresh host-index storage under its
  existing native mutex.
- Closure here covers only DB session/turn insertion. Installation/native
  authentication, complete producer mediation, SDK/host custody, compatibility,
  continuing retention and actual restoration remain open; no public controller
  or ordinary runtime path activates the foundation.
- Focused regressions target the DB portions of C1/C3/C4/C6 and strict-storage
  portions of C5/C6. Full cross-owner and native cases are not declared complete.
- The source/test diff awaits independent review before any commit, PR or
  broader wiring. Validation outcomes are recorded with the source review
  receipt, not inferred from this implementation entry.
- The bounded, network-disconnected Linux development cohort passed 209 cases
  with two skips across the new foundation/host-inventory tests and existing DB,
  adoption, host-claim and native-keyed-admission regressions. Separate BEFORE
  triggers initially made the refusal diagnostic order-dependent; one ordered
  insertion guard now gives closure precedence without relying on SQLite trigger
  ordering. This is not complete C1-C6 or Windows/native/live qualification.
- Session deletion now reserves the SQLite writer before its custody check and
  commits or rolls back all cleanup together. A statement-level ABORT must not
  leave deleted events pending for a later write to commit. Real two-owner,
  custody-refusal and cleanup-fault controls passed with the prior cohort:
  216 passed, two skipped. Complete runtime closure remains unqualified.

### 2026-10-23 - Proposal merged; implementation remains paused
- #8179 merged to `dev` as `f5d297f7787a8f3d7d70cdc7d29e62c3be328cda`,
  with sole parent `e4846bbd2838ac595285b9b7b426756f046c2b1c`.
- Copilot approved head `43e8dc2642d931a0f0078da59a2164be98d26dc2`
  with no open findings, and required CI passed. Publication changed only the
  seven proposal/index/parent documents. Both #8063 and #7268 remain open.
- The adjacent index conflict was reconciled without reverting upstream
  statuses or archive entries. Managed post-merge completion reconciled the
  source worktree; the effort was not finalized or declared Done.
- Next is Phase 1 admission/producer enrollment and strict inventory through
  existing Bridge owners, after the Integration owner assigns that specific
  source slice. No implementation, deployment, process stop, production capture
  or native qualification was performed.

### 2026-10-23 - Draft the Bridge participant and restore contract
- Confirmed compatible effort adoption and a target-local effort home.
- Retained #8063 for Bridge restore and reconciled the #7268 parent contract:
  extend existing Bridge owners; do not create a competing generic protocol.
- Recorded the accepted direction and explicit review/source/operational gates.
  No implementation, tests, production capture or lifecycle effects performed.
- Local architecture review accepted the bounded proposal. Normal proposal
  review/landing and a specific next-source-slice assignment remain prerequisites
  to implementation.
