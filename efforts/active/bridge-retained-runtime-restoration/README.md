# Bridge Retained Runtime Restoration

- **Slug:** `bridge-retained-runtime-restoration`
- **Repo:** copilot-extensions
- **Branch(es):** proposal first, then sequential source PRs against `dev`
- **Created:** 2026-10-23
- **Status:** Active; opt-in SDK, original-turn/acquisition DB and explicit local native/acquisition source merged; full custody/qualification/restoration open
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
      merged in #8823; actual allocation/native effect-owner
      integration remains open.
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

### 2026-10-24 - Original local allocation owner under validation

- Add a separately selected internal allocation method on the actual creation
  frontend. Reuse the existing creation executor, embodiment row and ground
  owner command; retain one outstanding `allocation_started` transition at the
  subprocess boundary and an immutable original allocation observation.
- Keep lost/cancelled replies outstanding rather than allocate again. Validate
  exact frontend/process/DB/target identity and closure at the final spawn,
  including a synthetic actual-subprocess control and fresh-connection recovery.
- This is a bounded Phase 1 allocation slice, not full keyed creation. The full
  start route still refuses associated attempts; provider/native launch,
  bind/resume/first-turn and cross-owner/native qualification remain open.
  No default enrollment, wire generation, installer or running adoption changes.

### 2026-10-24 - Keyed-creation DB source merged; allocation effect owners next

- #8823 merged as `ae0e2416665a317fd90b8d20682256e0239ec1ac` after independent
  current-head approval `5641691401` on
  `7228f93750a99c4476c0dafe9e06a44d085a6b4b`, required green CI and resolution
  of the replacement-update retention finding. No public capability or default
  enrollment was added.
- The original schema-36 capture source remains retrievable at
  `refs/tags/contract-evidence/http50-f3f5257ed41ecf10abcb2d52bc3c80d0190d672c`.
  This retains exact source evidence, not installation or native qualification.
- Re-read the full Plan and Validation Plan after merge. The next bounded source
  stretch carries an actual original creation owner through workspace allocation
  and native launch, preserving the existing attempt and refusing unsupported
  provider/native boundaries. Full Phase 1/1b, remaining cohorts, compatibility,
  guarded installer restoration and separately authorized adoption remain open.

### 2026-10-24 - Original keyed-creation DB foundation under validation

- Extend the existing workspace/provider embodiment rows with their original
  full creation facts and epoch/producer association, retained atomically before
  any future selected effect. Exact replay observes the same original ID;
  legacy history cannot be retrospectively enrolled or erase retained inputs.
- Current-schema validation, passive existing-only adoption and strict inventory
  include both creation owners. Ordinary effect paths refuse associated rows
  instead of downgrading; no public selection or default enrollment is added.
- Focused source/storage validation passed 204 controls, followed by 239
  migration, durability and neighboring-admission controls. Fresh HTTP-50
  write/no-write capture passed against committed schema-36 source
  `f3f5257ed41ecf10abcb2d52bc3c80d0190d672c`; both semantic DB owners remain
  bound. Older-generation fixtures are unchanged. Normal review remains pending.
- Source/storage controls do not close Phase 1. Actual allocation, native launch,
  provider execution and delivery fences remain the next source integration;
  compatibility, installer and native/platform qualification stay open.

### 2026-10-24 - Local acquisition source merged; keyed creation custody next

- Normal review and required CI passed on exact source
  `8fe7532bb39233a837b084025bb3eaefa594bb06`; independent review
  `5641086211` approved with no open findings. #8783 merged as
  `f2aa840b3265f52563b256c5c01d48916b186045`. The parent #8063 remains open.
- The bounded local adapter now preserves original acquisition and turn
  custody through launch, failed capture, lifecycle refusal and shutdown.
  The eight real-Host synthetic controls are not actual model conversation,
  native/platform qualification, attributed consumption or restoration.
- Re-read the full Plan and Validation Plan after merge. No full cohort item
  closes from this local slice. The next bounded source stretch extends
  existing keyed workspace/provider creation attempt custody before allocation
  effects, starting with those owners and their original journals. Background,
  host/remote transfer, compatibility, guarded installer convergence and
  separately authorized qualification/adoption remain open.

### 2026-10-24 - Original local acquisition effect owners under validation

- Added an explicit internal original-frontend selection through the existing
  acquisition, keyed start, strict resume, LocalSpawner, local Host child/exec
  and ACP new/load owners. Pending work cannot fall back to ordinary recovery,
  metadata retry, reattachment or recreation. The accepted original Host
  transport carries prompt delivery through the existing turn journal and
  final SDK gate. See the [owner boundary](../../../plugins/agent-bridge/docs/acquisition-custody.md).
- Four isolated synthetic wire cases exercise the actual Host, child and ACP
  SDK for new/load and lost replies, with independent original-row readback.
  Source controls cover closure across scheduling and effects, exact live
  references, configuration effects and no downgrade. These do not constitute
  a native model conversation, attributed consumption or platform qualification.
- Wider source validation exposed stale permitted-venue and durable-row test
  fixtures; corrected those fixtures without disabling hosting policy or
  changing runtime denial behavior. The frozen preference authority vectors
  also require a genuine committed-source capture, not a replacement hash.
  Genuine committed-source capture of the three owned authority vectors and
  final combined validation now pass: 1,100 isolated controls, with the two
  existing collection skips. Normal review and merge remain pending.
  A concurrent lifecycle handoff is also excluded at the launch owner's
  actual native effect boundary.
- Source review identified two remaining replay paths: resync could replace
  an unresolved associated load, and capture failure after native acceptance
  could leave ordinary prompt admission unassociated. Resync now refuses
  before teardown and rechecks under its lifecycle lock; the original client
  and durable-session prompt/handoff paths remain fenced until capture.
  Accepted launch observations remain intact. Two additional real-Host
  synthetic cases close admission before capture, and lost-load recovery plus
  under-lock controls prove no replacement launch or prompt escapes.
  Expanded validation passed 1,273 controls; its remaining stale provider/
  durable-session fixture was corrected and the focused 104-control
  acquisition/resync selection then passed. No runtime denial was relaxed.
- Follow-up source review exposed lifecycle handoff treating the capture
  refusal as optional brief failure, plus a selected resume launching before
  rejecting a retained turn owner. Durable association now refuses handoff
  before either seed branch, and retained-owner resume refuses before launch.
  The isolated acquisition/handoff selection passes 224 controls, including
  both failed-capture handoff branches and selected create, stop/reap, then
  selected-resume refusal. No owner transfer or replacement launch is enrolled.
- Shutdown recovery now preserves the durable association's exclusion through
  insertion, stop, enable attempts and hydration, including legacy enabled
  flags. Startup and direct Host reattachment refuse associated history before
  client adoption. The isolated acquisition/reattach/dormancy/Host/manager
  selection passes 456 controls, including a selected launch, ordinary
  shutdown detach, closed epoch and fresh-manager hydration with no ordinary
  reattachment or prompt. The retained Host remains alive for its real owner.
- Direct ACP lifecycle entry points now require the retained original launch
  owner, so omitting it cannot select the raw SDK new/load path. Connected
  controls try omitted, copied and exhausted original owners after successful
  startup and failed capture; no extra native request is observed. The focused
  acquisition/ACP/preference selection passes 129 controls.
- Retained the original HTTP-50 capture checkpoint unchanged at
  `refs/tags/contract-evidence/http50-d5219b038a431a9579fed66a1c1ea71fdfad7a77`.
  Provider ref and commit-object reads confirm the original identity; the
  dependent source-preparation owner independently confirmed successful CI.
  No capture fact or historical provenance was rewritten.
- This source slice closes no full Phase 1/1b or Validation item. Other
  creation/background cohorts, reattachment/transfer, remote owners, native
  qualification, compatibility, guarded restoration and running adoption stay open.

### 2026-10-24 - Original-acquisition DB prerequisite merged
- #8750 merged as `a1c5a6ec30d2cf2fcf256e0317d253852eb78833` at
  17:06:29 PDT after exact-head approval `5640689283` on
  `2478f2cb3f9e79beeddae82fdd7bbc3783826975`, no open findings and
  passing required checks. Both review corrections have reproduced regressions:
  retired creation retains historical prior IDs without reviving them, and a
  new allocation refuses an incompatible resume before handoff retention.
- The final bounded coupled lane passes 844 controls. Genuine HTTP-50
  write/no-write capture from `d5219b038a431a9579fed66a1c1ea71fdfad7a77`
  covers both fixture installation modes and 72 outputs, preserving all 814
  fixture packets and historical provenance. These remain model-free
  ASGI/SQLite observations, not native lifecycle qualification.
- Reread the full Plan and Validation Plan after merge. Only the original
  acquisition DB prerequisite closes; Phase 1/1b, actual acquisition,
  creation/start/resume and host final-owner wiring, attributed consumption,
  target compatibility, restoration and installed/native qualification stay
  open. Guarded reconciliation verified all three surviving source commits
  were already published, with zero post-merge commits, retained the reviewed
  tip and advanced only the owned source venue.
- Next carry the original acquisition through the actual local ACP
  creation/start/resume owners and awaited preparation. Preserve unknown
  effects and explicit unsupported cohorts rather than enrolling defaults.
  No live enrollment, process stop or deployment is authorized by this merge.

### 2026-10-24 - Local native transport source merged; lifecycle custody next
- #8732 merged as `92215b6e389b5e4981c9f6d7c7446b4b367aa2ab` at
  16:15:25 PDT after exact-head approval `5640541999` on
  `f95987d38808c80aad94f461f3356b403565c326` and passing required CI.
  The reviewer corrections cover full-lifecycle enrollment exclusion and
  Python 3.10-compatible timeout handling. The addressed remaining thread
  was explicitly explained and resolved before normal managed completion.
- The final bounded coupled lane passed 645 controls. The two implementation-only
  source fingerprint classifications preserve all 814 fixture packets and
  complete capture-time provenance. No new HTTP capture, schema, generation
  or capability-floor change is claimed.
- Reread the complete Plan and Validation Plan after merge. This closes only
  the bounded internal local native-send source slice. Phase 1/1b, attributed
  consumption, complete cohort coverage, target compatibility, restoration and
  installed/native qualification remain open. Terminal observation settled the
  PR claim; guarded reconciliation retained the reviewed source and advanced
  only the owned source venue, with zero post-merge source commits.
- Next follow the existing acquisition, creation/start/resume owners and retain
  their original operation identities at actual effect boundaries, before
  background/host/remote coverage, target compatibility and installer convergence.
  This source merge authorizes no installed enrollment, model conversation,
  protected-owner action, process stop or deployment.

### 2026-10-24 - Explicit local native transport source controls
- Followed the actual SessionManager-to-AcpClient path through the prompt lock
  and scheduled SDK request, rather than treating the earlier DB primitive as
  live transport custody. Internal explicit selection retains the actual
  Session/client/Process/connection and target/cwd plus fresh incarnation and
  child/frontend birth observations. Construction and inbox negotiation do not
  select every client; ordinary defaults remain unchanged.
- New direct, queued-to-turn and keyed admission retain the original consumer
  atomically. Background prompt and synchronous turn authoring carry that
  attempt to the final SDK call. Rechecks after lock/task scheduling and after
  the full-durability begin refuse replacement, closure or changed context.
  No lock is held awaiting a reply. Actual reply acceptance, attributed
  consumption and semantic completion remain different facts.
- Failures/cancellation and lost begin replies retain uncertainty under the
  original turn; no resend or retrospective enrollment is granted. Selected
  clients refuse unretained direct sends, implicit resume and unenrolled
  lifecycle handoff. Host, remote, command/provider and private idle-continuation
  cohorts remain explicitly unsupported by this selection.
- The bounded coupled source lane passes 645 controls. Two cases use the actual
  ACP SDK and stdio to separate synthetic children: each independently reads
  the original committed SQLite handoff before observing the exact prompt;
  one exits without replying. The fixture advertises and verifies synthetic
  preferences instead of bypassing model-selection readback. No model
  conversation, installed consumer or native platform qualification is claimed.
- The wider regression lane exposed three stale handoff setups under the
  existing hosting-policy owner. Correct only those tests: put the stop inside
  the author callback, supply a real isolated directory, and explicitly isolate
  the single-checkout guard from its separately tested hosting precondition.
  Production hosting policy and successor/stop checks remain unchanged.
- Source review and merge remain owed. Next cover creation/start/resume,
  background and host/remote custody through their existing owners before
  target compatibility and installer convergence. Phase 1/1b, native
  qualification, restoration and running adoption stay open.
- The ACP source reached its shrink-only ceiling during integration. Extract
  prompt execution at its cohesive mixin boundary rather than raising the
  ceiling. The ordinary, remote and private-idle paths retain their semantics.
  The remote regression's old fixed acquisition count is updated to the
  actual contract: at least one outstanding admission blocks drain, and all
  nested admissions must release back to zero.
- Independent source review found concurrent enrollment could cross the
  entry-only lifecycle handoff check while hosting policy or successor launch
  awaited. Exclude enrollment for the full existing handoff scope, releasing
  it on failure/cancellation, and add both-boundary race controls. The
  synthetic lost-reply test catches `asyncio.TimeoutError` for the declared
  Python 3.10 minimum; the development-container run is not native 3.10
  qualification.

### 2026-10-24 - Original-turn DB foundation merged; native owner wiring next
- #8713 merged as `24f4d66d63a679f7bd8f6ee1e34e4fc66a40d863` at
  15:23:55 PDT after current-head approval `5640292804` on
  `c54f9ce01795525be32089a58dabfac04a28b5aa`, no open findings and
  passing required checks. Every captured execution source remained
  byte-identical after managed publication.
- Reread the complete Plan and Validation Plan. This lands the internal
  original-turn DB owner, not actual transport enrollment, Phase 1, full
  custody, native/platform qualification, restoration or running adoption.
  Terminal observation settled the PR obligation. Guarded reconciliation
  verified one published surviving commit and zero post-merge commits,
  retained the reviewed tip and advanced only the owned source venue.
- Next follow the existing native client owner through prompt and synchronous
  handoff-authoring send boundaries. Bind real owner observations before
  original turn insertion and fence the actual effect without auto-enrolling
  other cohorts or changing ordinary defaults. The receiving extension SDK
  remains its separate opt-in path; this is not a replacement receiver.
  No live START, protected-owner operation or deployment is authorized.

### 2026-10-24 - Original-turn native handoff DB foundation
- Schema 34 adds immutable handoff and separate started/accepted/consumed/unknown
  observations to the existing original turn, with prepared storage validation
  and full-durability mutation. Explicit internal turn admission retains the
  handoff in the same transaction as direct/keyed insertion or queued-row
  consumption; ordinary defaults stay unqualified.
- A focused negative control caught retrospective enrollment of an associated
  incomplete turn that might already have been sent. It fails before the
  correction. Handoffs now originate only with new turn insertion; observation
  and keyed retry cannot upgrade old history, and SQL guards enforce that rule.
- Original metadata, input and keyed receipts survive closure, native-context
  replacement, process exit and unknown outcomes. The final DB primitive is
  invocation-only; current DB context checks do not replace actual transport
  custody or establish native acceptance.
- The bounded combined selection passes 357 controls, including real SQLite
  begin/closure ordering, original consumer/input negatives, normal/adopted full
  commits, passive refusal, frozen legacy migration and one real child exit
  after final DB admission. No native SDK/ACP conversation is invoked.
- Both current HTTP-50 receiver fixture modes exposed the expected storage
  metadata change from 33 to 34, with all thirteen other observation groups
  unchanged. The current input now names 34 explicitly; actual write/no-write
  captures from committed source `1f2d7b6582d3032d40d361b3b36398fe86646e0b`
  execute both installation modes and reproduce 77 outputs. The existing owner
  refreshes only its current HTTP-50 packets; archived packets and complete
  prior provenance remain preserved. Two contracts and 814 fixtures pass the
  canonical checker. The closure includes all three new native-turn modules,
  but the observations remain ASGI/SQLite evidence, not actual native proof.
- Publication encountered only a compact-registry conflict with three disjoint
  upstream source-record updates. Preserve both sets through a strict
  three-way merge and retain the pre-rebase source backup. Native source/test
  bodies remain unchanged. Actual write/no-write capture is repeated against
  reconciled source `4a84dc2ba95185a43663895bcda46c05bb213285`, not relabeled
  from the earlier capture: both installation modes execute and reproduce 74
  outputs after the upstream nonsemantic classifications. The expanded coupled
  receiver/native-DB portfolio passes 433 controls with verified containment
  cleanup; current HTTP floors and native qualification limits are unchanged.
- Normal source review and merge remain owed. Actual SessionManager/native
  transport enrollment and wiring, other producer cohorts, cross-owner closure,
  target compatibility, native/platform qualification, restoration and running
  adoption remain open; no full Plan/Validation Plan checkbox closes here.

### 2026-10-24 - Creation/turn durability prerequisite merged
- #8695 merged as `89c5f0ffef012a5573f461e3896072ab3b46e100` at
  14:40:29 PDT after current-head Copilot approval `5640129914` on
  `a1c7bf2732131f2485fd45552c100f026477d4fb`, no open findings and
  passing required checks. The earlier source-coverage failure was repaired
  without changing historical fixtures or weakening the guard.
- Reread the complete Plan and Validation Plan. This closes only the retained
  session/turn commit-policy prerequisite, not native effect-owner mediation,
  Phase 1, full custody, native qualification, restoration or adoption.
  Guarded post-merge source reconciliation retained the reviewed tip and
  verified zero post-merge commits before advancing the owned source venue.
- Next extend the original turn's native handoff/one-shot final admission and
  outcome representation through its existing DB/native owners. Preserve
  unknown acceptance across interruption and keep unrelated producer cohorts
  explicitly unqualified. No new ledger, conversation, live experiment or
  deployment follows from this source merge.

### 2026-10-24 - Retained creation/turn durability prerequisite
- The existing session and turn owners still committed explicitly associated
  admissions under ordinary SQLite `NORMAL` policy. Focused failing controls
  reproduce this for session creation, direct turns, atomic queue-to-turn
  admission and keyed native turns on both normal and adopted connections.
- Reuse the existing runtime transaction owner with `FULL` durability only for
  associated admissions. Queue consumption remains in the same transaction;
  refusal or failed commit rolls it back. Restore the original connection
  policy, including stronger policies; leave ordinary admissions unchanged.
- The bounded combined regression selection passes 202 controls. A real child
  exits without close or reply after all four original admissions; reopened
  storage retains the same rows/association and the original keyed receipt
  remains recoverable after closure. This is process-exit and SQLite-policy
  coverage, not a power-loss, native-send or platform qualification claim.
- This is a prerequisite within Phase 1, not completion of creation/native
  effect-owner mediation. Normal source review and merge remain owed.
  Final native/start/resume fences, other custody cohorts, cross-owner closure,
  target compatibility, guarded restoration and running adoption remain open.
- The first required source-coverage guard exposed two stale registered source
  fingerprints. Refresh only the changed implementation-only session/turn
  records and explain their unchanged public wire behavior. Preserve every
  historical fixture and provenance record; no capture is regenerated or
  protocol floor changed for this durability repair.

### 2026-10-24 - Retained SDK source merged; continue remaining effect owners
- #8674 merged as `53b8cdb9f4aba9e162d0d81990cd9b9a41218f32` at
  14:07:33 PDT. Independent Copilot approval `5639977314` names exact head
  `70fdf8645a0ff2dd383757e27c9747770fe70ae8`; both reproduced recovery
  findings are resolved. All three refreshed required checks passed before
  normal managed completion. The earlier pending-check refusal was honored,
  not bypassed. The parent issue #8063 remains open.
- Reread the full Plan and Validation Plan. This lands the explicit local
  consumer source slice and its controlled process/recovery evidence, not
  complete Phase 1b, native Linux/Windows qualification, full runtime custody,
  guarded restoration or running adoption. Legacy defaults and installed
  settings are unchanged.
- Guarded source reconciliation verified the merged head and zero post-merge
  commits, preserved that reviewed tip in the managed backup, and advanced only
  the owned source venue to the provider's actual squash merge.
- Next continue the remaining creation/queued-native effect-owner mediation
  under the existing installation epoch and original operation owners. Preserve
  unknown native acceptance and explicit unsupported cohorts; no ordinary
  drain or partial inventory may qualify target restoration. Target-declared
  compatibility, continuing custody and installer convergence remain later
  source gates, not implied permission for a live experiment.

### 2026-10-24 - Wire the explicit retained SDK consumer
- The existing interactive extension selects retained delivery only through
  an explicit environment opt-in. It requires advertised recovery protocol 50,
  the actual SDK handle's native session ID and a matching metadata snapshot.
  Legacy defaults remain unchanged; selected retained delivery cannot use
  legacy ACKs, controls or interrupt/resend as a fallback.
- The serialized consumer preserves the enrollment predecessor across lost
  replies, polls one original handoff, and rechecks native context around
  one-shot final admission. SDK acceptance, attributed consumption and unknown
  outcomes remain distinct. Recovery uses the exact original consumer after
  reload/closure and lazy shared durable history, not bounded event caches.
- Controlled helper and actual-extension loading cases cover legacy/retained
  routing, unsupported SDK/protocol cases, original identity, reply loss and
  nonblocking event handlers. Independent Node consumers execute the actual
  ASGI/installation/SQLite owners across six process-loss boundaries in both
  installation modes; reopened storage retains the original work after closure.
  These are source controls with a synthetic SDK sink, not native proof.
- The preferred container image lacked Node and skipped its Node-dependent
  controls; that run is not execution evidence. The Node-equipped bounded
  host runner executes all 12 process-boundary cases and the combined Node
  helper regression wrapper. No dependency, runtime or installed setting changes.
- Normal source review and merge remain owed. Full Phase 1b, native Linux/Windows
  qualification, remaining custody cohorts, target compatibility, guarded
  restoration and separately authorized running adoption remain open.
- Independent review reproduced two source recovery gaps: an already-enrolled
  consumer did not reinitialize a restarted frontend, and history cooldown could
  advance unreconciled pages while older unknowns phase-locked later evidence
  out of every history read. Focused failing controls reproduce both.
  Recovery now checks the original binding and current/predecessor observations,
  repeats the original enrollment CAS, and retains a page cursor until its
  history can be reconciled. Foreign binding/consumer and lost-enrollment/restart
  negatives remain held. All 15 selected Python controls pass, including two
  actual-frontend restart cases and the combined 93-case Node wrapper.
  The 201-receipt control keeps 100 older unknowns outstanding while settling
  all 101 later receipts through three bounded shared history reads.

### 2026-10-24 - Recovery prerequisite merged; continue the actual SDK consumer
- #8661 merged as `7db62f243e91da9ad25d2f354b27914a686aa65f` at
  13:17:08 PDT after exact-head Copilot approval `5639791371` on
  `65008cc1cd8830073104c885463ca5620747758e`, no inline findings and
  all three required checks passing.
- Reread the Plan and Validation Plan. This closes only the original-handoff
  recovery source prerequisite: HTTP 50, metadata-only original observations,
  bounded polling and prepared indexing. No full Phase 1b or parent
  restoration/adoption item closes from it.
- Next bind the actual receiving extension to explicit opt-in enrollment,
  serialized one-message polling, invocation-only final admission and original
  outcome/consumption recovery. Preserve legacy defaults; do not use legacy
  ACK/interrupt-resend or bounded event caches as retained custody truth.
  Uncertain originals remain visible, never blind retries under new identities.
- The source worktree reconciled through the normal verified merged-PR
  operation, retaining its previous head in the managed backup. No runtime
  installation, rollout, native effect or platform qualification is inferred.

### 2026-10-24 - Qualify original-handoff recovery before SDK integration
- Source `50581e0f432c50c679d449ea3b0302c49a384d29` adds bounded,
  exact-native original-handoff observations through the existing receipt owner.
  Reads preserve the original consumer after replacement, closure and passive
  reopen; they return no message body, producer admission or send permission.
  The legacy receipt shape and omitted poll-limit behavior remain unchanged.
- HTTP 50 advertises recovery separately from transport 48 and producer 49.
  Optional one-message polling retains only the selected original before a
  crash. Schema 33 prepares a partial pending-handoff index through the normal
  writable migration owner; passive adoption and damaged current storage refuse
  without repair. No extension default or running installation changes.
- The coupled SQLite/ASGI cohort passes 303 cases. All three retained
  generation policies, current actual observations and version negotiation pass
  131 cases; collection-capability skips are outside the selected current
  observer. Actual source-bound write/no-write capture executes both
  installation modes and reproduces 78 outputs, preserving every older packet
  and provenance record. The canonical checker accepts two contracts and
  814 fixtures; scoped module, install and documentation contracts pass.
- Expanded historical/adoption replay exposed stale schema-version assertions
  and a checker that selected generation 50 from a synthetic older receiver's
  evidence window rather than its declared current protocol. The checker now
  follows the existing current-generation dispatch rule; old migration controls
  assert the actual prepared schema. All five focused regression cases pass.
  Source `8804bfac32c64e69705bf67b98d2203b454a649f` then passes genuine
  write/no-write recovery capture in both installation modes, preserving the
  same 78-output and historical-evidence boundaries.
- Normal review/merge remain owed. The receiving extension's actual SDK
  enrollment, delivery and durable attributed-consumption recovery are next.
  Keep retained delivery opt-in with legacy defaults unchanged; preserve
  unknown work without replay. Full Phase 1b, guarded restoration, native
  platform qualification and separately authorized adoption remain open.

### 2026-10-24 - Ordinary producer merged; continue at the actual SDK boundary
- #8569 merged as `09366035ad37b38a2091dd1ed2242ea0d5f8ab7b` at
  07:15:20 PDT after the independent current-head review
  `5638553814` reported no open issues on
  `8c20c050038110c018daf31e2cb6cb986ded3516`, all required checks passed
  and all eight concrete finding threads were resolved.
- Reread this effort's full Plan and Validation Plan. Only the ordinary
  retained-producer source dependency is landed; no full Phase 1b item, actual
  SDK/native effect or remaining restoration gate closes from this merge.
  HTTP 49 distinguishes producer custody from transport-only HTTP 48, with
  schema-32 prepared indexes and all historical evidence retained.
- Next connect explicit consumer enrollment, serialized retained polling,
  invocation-only final admission and original outcome/consumption recovery
  through the existing extension helpers. Settle activation and older-receiver
  compatibility before changing default delivery behavior. Keep event handlers
  nonblocking; bounded represented-event or in-flight caches must never become
  the only copy of custody or attributed consumption evidence.
- Preserve unknown original work rather than aborting/resending, manufacturing
  an SDK idempotency key or downgrading selected retained delivery to legacy.
  Target compatibility, guarded installation, native qualification, release and
  separately authorized adoption remain distinct open gates.

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
