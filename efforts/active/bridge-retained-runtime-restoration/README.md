# Bridge Retained Runtime Restoration

- **Slug:** `bridge-retained-runtime-restoration`
- **Repo:** copilot-extensions
- **Branch(es):** proposal first, then sequential source PRs against `dev`
- **Created:** 2026-10-23
- **Status:** Active; local HTTP-48 transport merged; ordinary producer/SDK integration and full Phase 1 open
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
      The explicit local HTTP-48 adapter merged in #8540; ordinary producer and
      actual extension SDK enrollment/delivery remain open.
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
Full custody, authenticated consumer/SDK integration and restoration remain open.

## Journal

### 2026-10-24 - Wire ordinary keyed producer admission through current enrollment
- The ordinary message route now asks the existing SQLite inbox owner to
  select current enrollment and insert the original producer association in
  one write transaction. It does not initialize an epoch or enroll a consumer.
  Never-enrolled legacy targets remain separate; stale, retired, unsupported
  or missing retained owners refuse instead of downgrading.
- Retained admission requires an original operation key. Exact retries recover
  the unchanged original row after frontend replacement, closure, registration
  cleanup or a real producer-process exit before reply. Changed inputs refuse;
  a recovered admission does not authorize an SDK invocation.
- Unqualified control/startup/interrupt insertion and pending-control enrollment
  are fenced at their existing DB owners. The actual startup-readiness path
  refuses before creating or waiting for an unsupported legacy control.
- The bounded producer/inbox/freshness/legacy/readiness cohort passes 430 cases
  with three capability skips and verified cleanup. The stronger two-owner
  race test explicitly distinguishes a stale closure CAS from the subsequent
  fresh reconciliation; no runtime fence was relaxed to make the test pass.
- The independent HTTP-48 probe now observes the ordinary producer rather than
  seeding its admission through an internal DB method. Actual write/no-write
  capture passes both installation modes against committed source
  `d411d903e347e41d558ad32eee45e078b06df486`, retaining 75 outputs and every
  older packet/provenance record. The canonical checker accepts two contracts
  and 657 fixtures; 124 contract/fixture/current-capture replay cases pass,
  with two collection capability skips. Read-only producer refusal passes
  separately in both installation modes.
- The first capture correctly refused publication when equal observation
  objects had different insertion order. A reproduced regression now sorts
  only object keys, preserving all values and array order. The full current
  capture/refusal portfolio passes; no fixture expectation was relaxed.
- Normal review/merge remain required. Actual extension
  enrollment, SDK invocation/outcome/consumption recovery, native qualification
  and all remaining parent Plan/Validation items stay open.
- The managed publication refreshed the base and exposed a required
  install-contract failure: three Python installer helper copies lagged the
  canonical engine. Its existing generator refreshed exactly those three
  copies, without changing canonical behavior. Six bounded Python-engine
  vendoring controls, the install contract and both canonical Bridge contracts
  pass on that integrated source. Fresh current-head CI/review remain required.
- Review reproduced original-key recovery failure after same-process native
  renames and registration cleanup. The alias owner now forwards only legacy
  rows: retained messages keep their original native target and handoff.
  Twelve actual ASGI cases cover repeated renames, deregistration/purge,
  unoffered/offered/started messages, reopened storage, refused changed input
  and wrong targets, a successor that cannot inherit input, and late original
  outcomes. The combined retained/legacy regression cohort passes 291 cases.
  Committed source `79d457e533cc1d165c03557d4da83f3725e31aa4` passes actual
  HTTP-48 write/no-write capture in both installation modes with 76 outputs,
  including executed alias folding and retained outcomes after alias cleanup.
  The checker accepts both contracts and 658 fixtures. Current-head review
  remains owed; this correction does not complete the actual SDK or parent
  restoration gates.
- A subsequent review found active-alias gaps before cleanup: the HTTP route
  erased the original target before admission, and receipt projection followed
  aliases for retained rows. Nine focused cases reproduced those failures and
  the prior enrollment test's obsolete row-moving expectation. The route now
  preserves its requested target through admission and receipt recovery;
  retained duplicate matching and read-only projections use exact native ids,
  while legacy rows retain alias forwarding. Internal unqualified controls
  also retain the original enrollment refusal. The corrected enrollment test
  requires an empty successor inbox and the complete unchanged original row.
  All 312 selected producer/enrollment/receipt/legacy cases pass. Actual
  write/no-write capture from source
  `1c8c09755d0d1a0dcb4a4cabe539032320485d3c` records 77 outputs, including
  active-alias receipt recovery and successor/new-key refusal. The complete
  current capture/contract/maintenance cohort passes 124 cases; two contracts
  and 659 fixtures pass the canonical checker. Independent review remains
  required; no runtime or parent gate is inferred.
- The next independent pass found earlier readiness-control admission still
  discarded the original alias target. Eight ASGI cases reproduced the same
  failure for startup, standalone readiness and mode controls, asserting that
  neither a control row nor any readiness/outcome waiter may be reached.
  Existing readiness/control owners now retain the requested id through the
  DB fence while keeping canonical identity for legacy response routing.
  The coupled producer/readiness/control/create portfolio passes 146 cases.
  Its parser test now selects an isolated metadata owner and a real temporary
  checkout policy instead of depending on an ambient Worktrees executable;
  production hosting admission is unchanged.
- Actual committed source `7342384d638d71b9af4d8db745e55d1a203da24e`
  passes write/no-write HTTP-48 capture with 77 outputs, including all four
  alias-control refusals and no inserted control. The full 124-case current
  capture/contract/maintenance cohort, two-contract/659-fixture checker,
  install contract and changefile-presence checks pass. Current-head independent
  review remains required; SDK/native and restoration gates remain open.
- Review measured a new projection performance regression: custody-aware
  matching scanned unrelated retained history. The real SQLite regression
  reproduced 140,000 VM steps for an empty inbox with 20,000 unrelated retained
  rows. An indexable requested/canonical-session prefilter now narrows candidates
  before exact custody matching; zero-history and 20,000-row controls both stay
  within the 1,000-step budget. All 173 selected projection/receipt/result/alias
  cases pass without changing native identity or legacy forwarding semantics.
- Committed source `e008e31f57455885dd352d912e830c14e8d132be` passes actual
  HTTP-48 write/no-write capture with 77 outputs. The probe now exercises
  pending inspection through both original and successor ids. All 124 current
  capture/contract/maintenance cases and both contracts/659 fixtures pass;
  independent current-head review remains required.
- Review then found ordinary admission's consumer lookup scanning unrelated
  retired history inside the writer transaction. Six real-SQLite controls
  reproduced unbounded work for current, retired and never-enrolled targets.
  Schema 32 adds a nonpartial session-history index through the existing schema
  owner; current selection and history existence are separate indexed lookups,
  never a filter that erases retirement. Ten zero/20,000-row controls cover both
  unrelated history and the target's own retired incarnations within the
  10,000-VM-step admission budget.
- Normal schema-31 preparation adds only the index and preserves complete
  enrollment, message, handoff, epoch and producer rows. Read-only/adoption
  paths refuse old or damaged prepared state without repair; current-schema
  missing-index controls refuse too. The 143 migration/custody/adoption controls
  and 120 final producer/enrollment/index controls pass. No running database
  was migrated and no target-state compatibility is inferred.
- Source `329b9319c6cd3f3c4caf97a33caecd36e5d22ce8` passes actual
  HTTP-48 write/no-write capture in both installation modes with 78 outputs,
  including schema-32 and nonpartial-index observations. The unchanged checker
  accepts both contracts and 660 fixtures; all 124 current
  capture/contract/maintenance cases and the 18 changed-source module limits
  pass. Current-head independent review remains required.
- The reviewed head received approval, but a further same-owner check found
  the retained-row fallback scanning the legacy target's own old unassociated
  messages: 140,200 VM steps with 20,000 historical rows. The same unmerged
  schema-32 preparation now also owns a partial retained-message session index.
  Positive/negative controls require never-enrolled targets to stay legacy,
  while an original retained row without consumer enrollment must still refuse
  and remain unchanged. Both prepared indexes are required without passive DDL.
  The final 199-case custody/adoption/producer/index/capture portfolio passes,
  including the retained-row negative control. Refreshed committed-source
  evidence and independent review remain required. The earlier approval is not
  used for these additional changes.
- Committed refinement `019bf810e360b7b219f7467f85de4d121afe7893`
  passes actual write/no-write capture with 78 outputs and both prepared-index
  observations. The 124-case current capture/contract/maintenance cohort,
  two-contract/660-fixture checker and all changed-module/install/documentation/
  changefile guards pass. Fresh independent exact-head review remains owed.
- Review identified a version-skew defect: merged HTTP-48 transport predates the
  new ordinary producer guarantee. Add a distinct producer floor/current
  advertisement of 49, retaining transport floor 48, minimum 1 and every older
  floor. Six capability tests reproduced the missing distinction; the corrected
  mixed-version and capture-time historical-scope controls pass ten cases.
- Shared capture mechanics were extracted without changing HTTP-48 policy
  (28 controls passed before the version change). HTTP-49 has its own policy,
  independent input and actual ASGI driver; all existing HTTP-48 packets and
  input bytes stay frozen. Both policies share publication guards and retain
  their own source generation and primary/child scope. The separate original
  child-process binding portfolio passes 21 cases; observer support for 49
  does not relax its default worker/host/peer requirements.
- Current HTTP-49 source capture, complete canonical-reader checks and fresh
  independent review remain required. The older approval does not cover this
  new capability declaration; no SDK/native/adoption gate closes from it.
- The cohesive protocol/both-writer/historical-scope selection passes 98 cases.
  Two historical HTTP-48 execution-driver cases correctly require matching
  source rather than the current HTTP-49 checkout; two unrelated collection
  capability skips are not execution evidence. The current HTTP-49 selected
  ASGI driver executes both installation modes without a skip.
- Genuine source `7c61179a52beb96699f3e9f3191e54b52e7d958c` passes actual
  HTTP-49 write/no-write capture in both installation modes with 78 outputs.
  Every one of the 77 HTTP-48 packets and its independent input remains
  byte-identical. The canonical checker accepts both contracts and 737 fixtures;
  the complete current/historical reader, protocol and writer portfolio passes
  194 cases, and the final HTTP-49 custody/adoption/producer/index portfolio
  passes 199. The live contribution base still advertises 48, so 49 is not
  reused from an independently landed capability. Fresh review and required CI
  remain owed for the resulting evidence head.
- Required CI then identified one more release consumer in its advanced merge
  tree: `agent-ssh` now references the shared update driver through its editable
  source, unlike this worktree's older dependency graph. The literal failure
  was missing changefile coverage, not a capture or runtime failure. Add its
  patch entry for the already-regenerated canonical helper; do not weaken the
  guard or infer that the local consumer graph equals the CI merge tree.
  Fresh required CI and exact-head review remain owed.

### 2026-10-24 - Local HTTP-48 adapter merged; wire the ordinary producer and SDK next
- #8540 merged as `d67f4a1d4d75d4ddea5c5a721983a5aa4d25073c` after the
  current-head Copilot review reported no open issues on
  `d43e98f857d1746422797f3be078d36ecdc29544`, the installation-helper finding
  was resolved, and provider-required checks passed.
- The explicit local adapter and its corrected committed-source evidence are
  landed prerequisites. They do not switch ordinary message producers or the
  actual extension SDK to retained custody, enroll the full producer cohort,
  qualify cross-container ingress, or change a running installation.
- Reread the full Plan and Validation Plan. Full Phase 1b and the parent gates
  remain open. Next connect the existing atomic message admission to the
  current enrollment and bind the actual SDK send/consumption boundaries,
  preserving original unknown work instead of legacy ACK/resend fallback.

### 2026-10-24 - Close the installation-owner capture gap found in review
- Review of #8540 identified that the initial HTTP-48 closure covered the
  separately packaged installation module but omitted the actual payload
  `scripts/installation-context` entrypoint and fragments used by LoopGovernance.
  Those initial observations did not qualify this dependency. Their provenance
  remains retained; corrected current evidence supersedes that incomplete claim.
- The closure now pins every Python file in that payload helper directory.
  Required execution is derived from the captured entrypoint's fragment
  declaration: the entrypoint and all 18 fragments must execute under the
  source/origin/code guard, including their non-`src` paths.
- Controls refuse dirty entrypoint/fragment inputs before probes and reject
  foreign helper locations, forged executable code, incomplete inventories and
  missing required execution before effects or publication.
- Genuine source `b179c886e69306bc7318c9d39e24dce6b7820b99` completed actual
  write/no-write capture in both fixture installation modes with all 19 helper
  executions required. The 74 current outputs preserve every HTTP-47 packet and
  complete provenance; the checker still accepts two contracts and 656 fixtures.
  The corrected contract/fixture consumer cohort passes 123 cases.
- Current-head review and merge remain open. Ordinary producer/SDK integration,
  native and deployed qualification, and the parent restoration gates are not
  closed by this evidence repair.

### 2026-10-24 - HTTP-48 adapter source qualified; review and SDK integration open
- Added explicit `retained-inbox-v1` negotiation, predecessor-CAS enrollment,
  original-row polling, one-shot final admission and original outcome reporting
  through the existing installation and DB owners.
- Ordinary startup and registration do not initialize custody. Negotiation
  enrolls only the actual serving frontend, not the complete producer cohort.
  The existing shared-token boundary remains; unsupported network/proxy ingress
  refuses before enrollment or body exposure.
- Real ASGI/installation/SQLite controls cover reply loss, reopened storage,
  original outcome reporting after closure/retirement, wrong-context refusals,
  stale registration and unsupported interrupts without partial handoff commits.
  These are source controls, not SDK/native or deployed evidence.
- Declared HTTP generation 48 and retained-inbox capability floor 48 without
  changing the supported minimum or older floors. Independent local ASGI vectors
  cover both installation modes; publication controls reject self-consistent
  source/operation tampering and preserve the default HTTP-47 child portfolio.
  Historical HTTP-47 validation retains original tree and primary/child bindings
  without treating later source files as historical execution inputs.
- Genuine source `56bd5bebb1de57347db8c002dd9ecd4434fcd620` completed actual
  write and same-source no-write capture through both fixture installation
  modes. The 74 outputs preserve every HTTP-47 packet, prior capability floor
  and provenance prefix. The canonical checker accepts two contracts and all
  656 fixtures against the reviewed predecessor.
- Publication controls qualify initial capture and registered current-generation
  refresh without replacing foreign/dirty packets or concurrent registry edits.
  The separate original child-binding portfolio remains required; it is not
  combined with publication tests that intentionally substitute fixture owners.
  Replay guards explicitly distinguish additive health metadata and closed,
  negotiated control/identity envelopes from ordinary tolerant peer models.
- Normal review/merge remains required before this adapter is landed. Ordinary
  message producer and actual SDK wiring remain next. These observations do not
  qualify native execution or a deployed receiver, and close no Phase 1b or
  parent item.

### 2026-10-24 - Current-consumer DB prerequisite merged; wire the transport next
- #8526 merged as `43117b1fbba5024372eec1a435c32d44fc6b8b7f` after
  current-head Copilot approval `5637530050` on
  `f093bf76b9015b507a1d74e68fb7776db069681e`, its birth-tolerance finding
  resolved, and all three required checks passing.
- Reread the full Plan and Validation Plan. The durable current-consumer DB
  prerequisite is complete; actual local registration/enrollment negotiation,
  retained polling, final SDK admission and outcome/consumption recovery remain
  open. No Phase 1b Plan item or native/adoption gate closes from this merge.
- Continue through the existing registration and extension helpers under
  `consumer-recovery.md`; preserve original handoffs and visibly outstanding
  uncertainty, without new credentials, blind resend or legacy downgrade.
  No installed authentication, running service or network boundary changes.

### 2026-10-24 - Durable current-consumer DB prerequisite
- Add schema-31 enrollment through the existing DB owner: exact local
  registration, installation/open epoch and consumer incarnation, with a
  compare-and-swap predecessor and idempotent lost-reply reconciliation.
- Retain superseded enrollment; ordinary heartbeat, alias rollover, registration
  cleanup and historical handoffs cannot reactivate an old incarnation.
  Poll and one-shot final admission require the current enrollment; original
  outcomes remain recordable after retirement without renewing send permission.
- The 216 focused isolated DB/inbox/registry controls pass, including both
  final-admission/reload serial orders, competing enrollment, actual same-process
  alias folding, retained outcomes and schema migration/passive adoption.
  A historical v23 fixture now removes later-schema guards before dropping its
  process-birth column; its original migration assertion remains intact.
- This closes no Phase 1b Plan or Validation item: the internal prerequisite
  still needs source review, and actual user-scoped registration, retained
  transport/SDK integration and native recovery remain separate open gates.
  No installed authentication or running service changes.
- Align birth-time retirement with the registration owner's existing tolerance:
  an accepted rounding-only heartbeat must not permanently retire enrollment.
  Null transitions and real process-birth changes still retire it. Registered
  DB-owner changes also require fresh committed-source HTTP-47 evidence through
  the existing capture owner; no historical packet or protocol floor changes.
- All 219 focused enrollment/registry controls pass after that correction.
  The actual write and no-write HTTP-47 portfolios each pass 117 required probes
  from source `2bf3a75667603cf2ba4880e7320e2048c874c278`, reproducing 66
  outputs through the existing capture owner. This is committed-source evidence,
  not qualification of a native consumer or running installation.

### 2026-10-24 - Installation-binding prerequisite merged; enroll the consumer next
- #8519 merged as `5f4bc412a942b11719393c1e056499c860243b53` after
  exact-head Copilot approval on
  `1a6e64a5c1420e246dd7ae4d853603462e11ceac`, the legacy-transfer thread
  resolved and all three required checks passing.
- Reread the full Plan and Validation Plan. This completes only the read-only
  installation-binding prerequisite, not Phase 1b's installation-and-current-
  consumer binding, actual SDK recovery or any full restoration gate.
- Continue through the existing live registration and DB owner: persist the
  negotiated current consumer incarnation, prevent a reload or alias successor
  from inheriting an old final-send opportunity, then wire retained polling and
  the actual SDK admission/outcome/consumption path. No credential hierarchy,
  independent outbox or runtime activation is authorized by this merge.

### 2026-10-24 - Derive inbox installation observations through the existing owner
- Add a read-only `LoopGovernance.runtime_installation(database)` prerequisite
  for local enrollment. The daemon's existing deployment, canonical receipt,
  installation-mode and governance owners select the association, including the
  actual open DB path; a client cannot supply an installation or payload path.
- Isolated legacy and namespaced controls cover active receipt state,
  payload/source/generation drift, maintenance, changed runtime/DB/manifest
  paths and a mid-resolution generation change. All 32 focused governance
  cases pass; two unrelated collection skips are not qualification evidence.
- No public route, ordinary startup, extension poller or SDK effect activates
  this helper yet. Durable current consumer-incarnation enrollment and actual
  poll/final-admission/outcome wiring remain the next Phase 1b stretch.
  No Plan or Validation Plan completion is inferred from this prerequisite.
- Source review found that a preexisting legacy transfer could already be part
  of a ready baseline. The real transferred-root regression reproduced it;
  binding now consumes the existing legacy entrypoint owner's mutation
  decision. All 34 governance cases pass, including legacy refusal and an
  unaffected namespaced installation. Current-head review remains required.

### 2026-10-24 - Consumer trust and recovery refinement merged
- #8515 merged as `ba25d5f5d58cdc02736cce67da42a39a72737076` after
  Copilot approval on exact head `fe823c41870de3b8ac8541ad80cbd34d24e44286`,
  no findings and all required checks passing.
- Reread the effort's Plan and Validation Plan. Only Phase 1b's design-review
  prerequisite is complete; local enrollment, actual SDK delivery/recovery,
  native qualification and all remaining restoration gates stay open.
- Continue source implementation through the existing installation,
  registration, inbox and extension owners. No per-consumer authentication
  prerequisite or runtime/network change is introduced.

### 2026-10-24 - Refine the trust boundary around message recovery
- Traced the actual shared-token middleware, registration, extension process
  metadata and SDK send path. Request quiescence is not consumer identity;
  caller fields alone are not a new security boundary.
- Operator direction explicitly keeps Bridge and Dispatch within user-owned
  bounds, with authentication at cross-container proxy wrappers. Do not add
  per-consumer tokens or independent native authentication as a local
  enrollment prerequisite. Exact incarnation checks remain correctness guards.
- Hashing and retained outcomes serve corruption detection, data recovery and
  accidental-replay prevention. A timeout or disconnect cannot drop an accepted
  message or turn unknown SDK acceptance into automatic resend permission.
- Submit the linked refinement through normal architecture review before
  implementation. Runtime, proxy deployment, native qualification and the
  complete restoration effort remain open.

### 2026-10-23 - Inbox owner merged; continue at the real consumer boundary
- #8389 merged as `d9363f0fd16539ebbd10fdf6a4ad724be4629023` after
  Copilot approval on final head `fd4b237e7ef09b37d4c15ca64e103a44f66ce0af`.
  The final associated-control finding is fixed; the non-blocking documentation
  assessment was corrected in the PR description without changing the head.
  Every review thread is resolved.
- The first managed merge refused an expected required PR-gate check.
  After that exact check and the other required checks reported success, one
  normal managed retry completed. No required check was bypassed.
- The final isolated inbox/capture-refusal cohort passed 293 cases with two
  unrelated collection skips and verified cleanup. The genuine source
  capture/no-write pair and complete provenance preservation are recorded below.
- Reread this effort after merge. Phase 1a and the bounded Phase 4a provenance
  prerequisite are complete; the full effort remains Active. Continue Phase 1
  at authenticated consumer enrollment and the actual poll-to-SDK handoff
  through existing registration, transport and extension owners. First trace
  their identity, negotiation and interruption boundaries; do not activate the
  internal primitives by treating caller-supplied observations as authentication.
- Ordinary legacy delivery is not restore-qualified. Actual SDK/native
  qualification, target compatibility, installer restoration, release and
  separately authorized running adoption remain unresolved.

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
