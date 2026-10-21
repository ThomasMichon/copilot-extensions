# Remote ACP Host Ownership

- **Slug:** `remote-acp-host-ownership`
- **Repo:** copilot-extensions
- **Branch(es):** Serial proposal and implementation PRs targeting `dev`
- **Created:** 2026-10-19
- **Status:** Active
- **Vision:** [externally-hosted-acp-agents](../../../visions/plugins/agent-bridge/README.md#externally-hosted-acp-agents)
- **Umbrella issue:** #7237

## Guiding Intent

A managed worktree that opens an externally hosted ACP conversation owns the
conversation relationship as a durable responsibility. The bridge owns its
connection and conversation records, not the peer's process, workspace or
service. Host succession must preserve the obligation without allocating a
development workspace merely to obtain tracking.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| welded-piston | Proposal, integrated implementation, validation and publication | Public issue #7237 and its claimed managed worktree |
| grimy-gizmo | Writer-qualification amendment review only; no runtime implementation | Public issue #7237; bounded proposal worktree under the coordinating participant |

## Coordination

- **Topology:** Reviewed proposal first, then dependent implementation PRs.
- **Host (owns PRs):** welded-piston.
- **Delegates:** None currently; any bounded delegation retains one integration owner.
- **Handoff:** Update this contract and transfer the public claim explicitly.
- #7218/#7231 retain discovery, binding, authentication and remote transport
  scope. Implementation builds on that reviewed contribution, not a parallel
  connector. #7238 retains the separate conversation-domain graduation.
- Preserve #6076 dispatch acquisition, #3443 orphan adoption and #6934
  authoritative head-resume ownership. Do not replace their state machines.

## Context

The normal bridge CLI/client carries `caller_owner_ref`. The remote session
route does not forward it to the remote attachment, and that attachment does
not retain it on its `SpawnTarget`. Caller affinity and remote history identity
therefore do not establish a host-worktree obligation.

The worktree layer already owns qualified references, `bridge` resource
claims, active/at-rest/released dispositions, finalization gates and accepted
claim handoffs. The bridge owns remote session persistence and lifecycle
serialization. Compose these owners through their supported command/provider
boundaries; do not write another plugin's private store.

The existing `resource-producer` ABI explicitly describes disposable-owned
allocation and verified absence. A retained external conversation is not
ownership of a disposable peer service. Do not adopt that ABI merely because
its reserve/bind verbs look convenient; the implementation must establish
compatible relationship semantics without weakening existing producers.

The Phase 2 authority check found that the public legacy `claims add` allowlist
does not include bridge/session claims, ordinary add transactions may reopen a
finalized owner, and reverse-owner lookup is explicitly best-effort diagnostic
data. None is a substitute for the required retained-relationship transaction.
Any shared claim-contract extension must be explicitly designed and reviewed;
do not work around this gap with private-store writes or disposable-peer
absence claims.

## Request

**Public-safe summary of the operator request:** Allow agents to open and
interact with external ACP sessions that become owned and bound to the
originating host worktree for tracking. Preserve that relationship through
reconnect and host-session succession.

The operator confirmed the slug `remote-acp-host-ownership`. Concrete consumer
systems and private deployment details are omitted without changing the
ownership requirement. The lifecycle design and validation decomposition below
are **agent-recommended**.

**Source scope follow-up:** Necessary shared worktree claim-authority source
changes are permitted, but the compatible retained-relationship contract must
clear a separate design review before implementation. No wire shape is selected
by that scope permission. Preserve existing producer/lifecycle guarantees and
all release, runtime, credential and live-request holds.

**Execution constraint:** Review and merge this proposal before implementation.
Neither this plan nor a `dev` merge authorizes promotion, installed-runtime
updates, daemon restarts, credential grants or live external requests.

**Writer-qualification follow-up, verbatim selection:**

> Prepare a source-only writer-exclusion design for review before enabling transfer (recommended)

The [writer-qualification amendment](writer-qualification.md) is
**agent-recommended design**. It must clear review before its new exclusion
mechanism is implemented. This selection does not authorize runtime changes or
relax the existing transfer, rollout or live-request gates.

**Validation-policy follow-up, verbatim:**

> Yes, give protocol-validation tests greater timeouts, and create separate perf-focused validation flows.

**Settled validation direction (summary):** Protocol correctness and startup
performance use separate flows. The operator selected measure-then-baseline;
the controlled blocking performance baseline remains deferred. This permits no
production-deadline change, containment/review/CI waiver or invented perf pass.

## Plan

### Phase 1 - Reviewed relationship contract

- [x] Deduplicate and claim #7237; confirm the effort slug and native adoption.
- [x] Review and merge the effort and the focused vision clarification.
- [x] Sync onto the reviewed plan and merged target-contribution prerequisite.

### Phase 2 - Durable owner and claim integration

- [x] Review and merge the separate
  [retained relationship authority proposal](relationship-authority.md) before
  implementing shared claim changes.
- [ ] Execute the authority proposal's ordered source and acceptance slices.
- [ ] Carry the qualified host owner through ordinary CLI/client/API creation
  into the existing remote session record, separately from caller-session
  attribution, the bridge handle and target-qualified remote identity.
- [ ] Integrate an idempotent relationship claim with existing claim authority
  and inspection; reject unresolved, frozen or conflicting managed owners
  before remote creation. Never infer ownership from a bare caller string.
- [ ] Journal pending/confirmed/uncertain acquisition through the bridge's
  existing persistence and reconcile claim outcomes before reporting success.
  Define compensating failure and cross-owner transfer behavior explicitly.
- [ ] Preserve standalone programmatic use without a worktree: absent managed
  ownership is reported as unowned. Supplied but invalid ownership is a refusal,
  not an unowned fallback.

### Writer-qualification proposal

- [ ] Review and merge the source-only
  [writer-qualification amendment](writer-qualification.md) before implementing
  a new old-writer exclusion mechanism.
- [ ] Implement and qualify its Bridge/Worktrees writer closure and lifecycle
  activation/rollback boundary before advertising retained transfer.

### Phase 3 - Lifecycle and continuity

- [ ] Preserve the claim through stop, transport loss, reconnect, daemon
  rehydration and same-worktree session succession. Refuse implicit takeover
  by another worktree; use accepted handoff for explicit ownership transfer.
- [ ] Make explicit end settle the bridge relationship only after durable
  local retirement; retain remote history and never terminate the peer service.
  Interrupted or unknown settlement remains visible and retryable.
- [ ] Reconcile failed creation, response loss, concurrent reuse/load/stop/end,
  startup cancellation and stale owners without duplicate creation or silent
  obligation loss.

### Phase 4 - Reviewed delivery and closure

- [ ] Document relationship inspection, finalization blocking, explicit end,
  ownership transfer, standalone policy and older-daemon capability refusal;
  update affected plugin changefiles and contract evidence.
- [ ] Pass the validation matrix and review/CI gates; merge implementation to
  `dev`, then journal exact evidence and archive the resolved effort.
- [ ] Transfer release/rollout or live acceptance to named tracked objectives
  where separately authorized; do not equate a merge with deployment.

## Validation Plan

All detail below is **agent-recommended**.

- [ ] Unit tests prove distinct owner/caller/bridge/target-native identities,
  qualified owner validation, project/binding identity and old-record behavior.
- [ ] Actual ordinary CLI/client/API consumer tests show the relationship in
  session inspection and `claims show`/reverse-owner inspection; unresolved
  active conversations block host finalization.
- [ ] Real command-boundary tests cover successful and refused claim writes,
  already-applied/lost claim responses, pending acquisition recovery, duplicate
  requests and host freezing concurrently with creation.
- [ ] Deterministic races cover start/load/reuse/stop/end and cancellation.
  Known accepted remote IDs reconcile without creating another conversation;
  an unidentifiable remote outcome remains explicitly uncertain without replay.
- [ ] Persist/restart tests preserve owner and claim with the native ID.
  Same-worktree successors retain responsibility; different owners cannot
  steal it. Accepted transfer and terminal settlement retain an audit trail.
- [ ] A contained synthetic TLS peer exercises create/send/stop/reconnect/end
  with temporary worktree authority, normal claim commands and a fresh bridge
  database; assert zero workspace allocation, process launch or peer termination.
- [ ] Standalone bridge tests retain explicit unowned operation; a managed
  request refuses when required claim authority or daemon capability is absent.
- [ ] The writer-qualification amendment's old/new, already-open connection,
  preparation, quiescence and rollback matrix passes without claiming a schema
  stamp or one compatible command excludes every participating writer.
- [ ] Existing remote transport/auth/session, worktree obligations/handoff and
  contract suites pass through canonical runners under the authorized separate
  protocol/performance policy, without changing production deadlines or gates.
- [ ] Record clean-room and live-venue tier availability with explicit skip
  reasons. No unauthorized endpoint, grant, deployment or restart is a test.
- [ ] Public artifacts contain generic fixtures and masked source attribution;
  documentation-impact review covers the final implementation diff.

## Proposal

Use one bridge-owned relationship record and the existing worktree-owned claim,
not a new session manager, remote workspace or ownership service.

The qualified worktree reference is the stable owner; a caller session is
provenance, not an ownership epoch. Validate the current host authority using
existing worktree commands. Keep target/binding identity and native session ID
separate so URL/name aliases cannot fork a claim and identical native IDs at
different targets cannot collide.

Persist a pending local relationship and obtain a confirmed host claim before
consequential remote creation. Serialize reconciliation with the existing
remote lifecycle lock. If the claim result is unknown, inspect/reconcile it
before proceeding. If the remote accepts creation but its reply is lost, retain
an uncertain active obligation; ACP does not promise an idempotent creation
key, so do not invent exactly-once creation or blindly retry it. When a native
ID is known, preserve and reuse it under the current binding policy.

Stop/disconnect retain responsibility. Explicit end retires the local bridge
relationship, not the remote service or history; finalize remains blocked
until settlement is confirmed. Remote reachability is not a prerequisite for
retiring an idle local relationship, but an uncertain in-flight outcome must
remain disclosed. An accepted worktree claim transfer must update the bridge's
owner relation or remain pending; neither side silently declares a handoff done.

Review the exact claim namespace, capability generation and reconciliation
protocol during Phase 2 against current owners. Any required change to the
generic disposable-producer contract is a separate reviewed design decision,
not an implicit dependency of this effort.

The [Phase 2 authority proposal](relationship-authority.md) selects the
agent-recommended compatibility boundary for that separate review: a distinct
retained-relationship command using the existing protected claim machinery,
provider-verified local retirement and acknowledged accepted transfer.
It does not change the available version-1 disposable ABI or authorize coding
before the proposal clears review.

## Journal

### 2026-10-20 - Propose writer exclusion before retained-transfer activation

- The operator selected a source-only writer-exclusion design for review.
  Added the focused amendment and explicit implementation/activation gate.
  This proposal-only worktree does not publish the pending transfer code.
- The proposed boundary combines Bridge-owned SQLite mutation exclusion with
  the existing lifecycle's confirmed quiescence and independent Worktrees
  writer qualification. Persistent database protection alone cannot stop an
  already-active peer client or qualify a different plugin's ledger writers.
- New production exclusion code, public transfer/provider activation,
  installed updates, restarts, release work, credentials and live requests
  remain outside this change. Unknown writer/lifecycle proof keeps the feature
  disabled. Implementation follows only after the amendment clears review.

### 2026-10-20 - Separate protocol timeouts from performance qualification

- The operator approved greater protocol-test timeouts and separate performance
  validation, superseding the earlier combined five-second cold-bootstrap gate.
  Native bootstrap now has 15 seconds and its test module a 60-second item
  watchdog. Existing post-connect shared deadlines and the deliberate deadline
  refusal assertion are preserved; group and process containment are unchanged.
- Added a separate five-sample real CLI startup flow, with fresh isolated state,
  exact process identity, real HTTP readiness, source fingerprints and explicit
  startup/cleanup watchdogs. No fixture-module import stands in for production
  startup. Windows redirector identity and transient publication observation
  conflicts are handled without relaxing owner identity or discarding samples.
- The broader full-daemon boundary initially observed starts around 6-13 seconds
  and one beyond 15 seconds, unlike the earlier import-plus-IPC boundary. The
  operator selected measurement first, then a reviewed baseline on controlled
  capacity. Report mode cannot claim qualification; explicit provisional budget
  checking remains separate. No new required CI or release gate is introduced.
- This source-test policy change does not authorize promotion, installed
  updates, shared restarts, credential actions or live external requests.
- The final report flow completes all five samples, with a 5.54-second median
  and 13.21-second maximum, explicitly `target_met: false` and baseline
  unestablished. Its measurement, refusal and observer unit cases pass.
- A mixed native batch exceeded its existing 300-second aggregate before
  producing complete failure diagnostics. The runner now uses its existing
  independent-group mechanism for the ten affected native/startup files;
  selection tests prove no selected file is lost or duplicated. The complete
  default grouped run passes 230 cases with one deliberate opt-in benchmark
  skip in 195.67 seconds, including the original 32 native transfer cases.
  Six focused runner-selection tests pass; no group/plugin deadline is raised.

### 2026-10-20 - Reconcile independent generation and queue-removal race

- Retained the upstream attention-boundary generation 42 and all its evidence.
  The unpublished ownership capability moves to generation 43, explicitly
  refusing attention-only daemons. Current re-attestations derive from the
  independent upstream generation-42 vectors, not the divergent unpublished
  ownership vectors.
- Atomic turn admission distinguishes a removed queue row from a changed row
  or other database failure. Drain advances only after a typed missing-row
  refusal, so deleting the selected prompt during authority inspection does
  not strand a second queued prompt. Cancellation and authority failures
  retain their existing non-retry behavior.
- This is source reconciliation and a review correction, not release,
  deployment or live acceptance. The effort and transfer work remain open.

### 2026-10-20 - Retain the cold native gate while narrowing imports

- The rebased Windows native lane still intermittently exceeds the original
  five-second cold-start-plus-IPC gate. Current qualification is not green;
  earlier passing native runs are not substituted for this source.
- Failure-side import profiling observed one complete native test-module import
  taking 8.27 seconds before connection, including 7.27 seconds under the app
  import. Other samples completed; the underlying host or filesystem cause of
  that variance is not established.
- Production preparation now defers ordinary registry composition, optional
  worktree-cache restoration and candidate serving/relay constructors to their
  existing use boundaries. Strict decoding, custody and activation fences remain
  unchanged. The native fixture and its timeouts are untouched; temporary
  profiling instrumentation is removed.
- The latest focused run passes 128 cases, including the two-row removal race,
  optional-worktree restoration, provider startup and passive restoration, but
  two native cold-start cases still fail. This remains an explicit publication
  qualification blocker, not a reason to widen timeouts or retry into a pass.
- Generation-43 evidence now identifies exact committed source
  `4cee06ed296deec2b17fa9bbcfed7c9b488cfe61`: 52 registered sources and 45
  current vectors, with all upstream generation-42 bytes retained. The registry
  checker passes both contracts and 353 fixtures. The guard run passes 315
  cases with eight platform/optional skips; its one stale current-generation
  provider consumer is corrected to the independent generation-43 vector and
  passes its real HTTP producer/consumer case. A further focused run passes
  109 cases, including queue, schema, reconciliation and attention behavior,
  before that same provider assertion was corrected. No native acceptance is
  inferred from those results.

### 2026-10-20 - Optimize production cold startup, not the native deadline

- Broader validation found cold native transfer deadline failures. Failure-side
  child stacks showed eager ACP SDK/model imports before native connection;
  subsequent profiles also found ordinary route/cache imports in passive
  preparation. The source optimization lane was selected explicitly over a
  separate fixture-bootstrap allowance.
- Production import changes defer ACP construction to real ACP use, preserve
  the exact public client class through its lazy facade, and keep annotation
  imports out of the startup path. Ordinary serving route composition and
  roster/worktree cache dependencies now load at their existing factory or
  lifecycle boundary, not merely by importing passive runtime support.
- The native transfer fixture and every timeout remain unchanged. Its full
  32-case native portfolio now passes. A separate cold subprocess checks real
  app/manager/passive import composition, ordinary factory construction and
  exact public client identity within five seconds. Required snapshot access
  to the existing worktree locks is retained; it is not replaced with a
  parallel cache/lock owner.
- Temporary import profiling and child-stack probes have been removed.
  No installed payload, service, fixture setup allowance, protocol deadline,
  item/group budget or activation behavior is changed.
- The integrated queue/schema/session/native/startup portfolio passes all 394
  cases in 136.77 seconds. The additional first-use ACP client, authenticated
  WebSocket, roster, restored serving/control and ordinary startup consumers
  pass 173 cases in 46.40 seconds. This includes another complete pass of the
  original native transfer suite, not a filtered retry or timeout waiver.
- Source commit `2a150b33ee33c1caf758f3a498149a27ff286e2c` qualifies the repair.
  Only three affected current contract vectors are re-attested; exact database
  admission/preparation dependencies are registered without changing historical
  expected bytes. Registry validation passes two contracts/311 fixtures, the
  current/historical HTTP contract tests pass 33, full Bridge guards pass 304
  with eight existing platform/optional skips, and all 13 install contracts
  pass. The 16 changed source modules retain their existing size ceilings.

### 2026-10-20 - Atomic queue recovery and an explicit schema boundary

- The next current-head review identified the abrupt-exit boundary left by
  cancellation-only repair: process death cannot run a recovery handler after
  queue deletion. Admission now peeks without consuming; exact queue deletion
  and turn creation commit in one SQLite transaction. Insert failure rolls back
  queue consumption, an explicitly removed prompt cannot be admitted, and the
  dequeue event follows the commit. Ordinary cancellation no longer re-enqueues
  or changes FIFO position.
- A real separate daemon process exits directly during managed claim
  inspection, bypassing every exception/finalization handler. Before the fix
  the queued follow-up disappears. After the fix a fresh manager/connection
  recovers the original queue ID, caller and prompt, resumes the known native
  conversation and persists exactly one turn with one claim and no new
  conversation. Transaction refusal cases cover duplicate turn insertion and
  explicit queue removal; passive connections cannot consume work.
- Database schema 27 distinguishes the additive relationship tables/authority
  from the preceding schema 26. A frozen prior-schema core projection is
  refused without migration by passive preparation. Ordinary writable startup
  preserves its session, creates the relationship structures/authority and
  stamps schema 27; subsequent migration-free adoption preserves that identity
  and starts no writer. No candidate-side bootstrap or fallback is added.
- Initial focused validation passes 22 cases, including the actual child exit,
  prior-schema preparation and existing queue behavior. The two reported
  behaviors fail before their repairs. Release, rollout and live requests
  remain separately held.

### 2026-10-20 - Cancellation-safe queued prompt admission

- Re-reading the complete review overview identified a separate concrete
  cancellation defect: queue drain removes its row before awaiting managed
  claim inspection. Cancellation bypassed the ordinary exception recovery,
  losing the follow-up before a turn existed.
- Queue recovery now handles cancellation and checks the durable prospective
  turn before restoring a prompt. A failure after turn persistence, including
  host notification, cannot enqueue an already-admitted duplicate. Cancellation
  propagates after synchronous recovery; turn numbering advances only after
  successful persistence.
- Four bounded managed-session cases pause real claim admission or post-turn
  notification, then cancel or raise an ordinary error. Before the fix, the
  cancellation-before-persistence and error-after-persistence cases fail.
  Afterward all four pass, preserve caller attribution and one claim, and
  recover exactly one turn. The focused queue and prompt regression selection
  passes 19 cases.
- The combined session-manager, managed relationship, remote-session and
  reconciliation suites pass 293 cases; the full Bridge guard portfolio passes
  304 with eight existing platform/optional skips. Python lint and diff checks
  pass. Exact-source contract evidence is refreshed after the source commit.
- This source correction does not authorize release, rollout or live requests.

### 2026-10-20 - Persist accepted identity before cancellable cleanup

- A current-head review again reported no posted issues while describing an
  unresolved cancellation defect. Direct tracing found the concrete boundary:
  the SDK sets its accepted native ID before awaiting remote adoption, but the
  attachment failure path previously waited for shutdown before persisting it.
  Cancellation during that cleanup could lose a known identity on restart.
- Accepted identity is now persisted before that await. Failed managed startup
  transitions to stopped in its finalization path even after another cancel,
  retaining its client reference for verified teardown. The ordinary standalone
  state transition remains after cleanup.
- Two fail-first cases pause cleanup after ordinary cancellation, verify both
  durable identity copies before releasing it, exercise repeated cancellation,
  then restart and load the original conversation with one claim and no new
  conversation. Both pass after the ordering fix.
- Current-generation re-attestation checks retain every independent historical
  expected byte and capability floor while validating each exact source hash
  and registered capture identity, rather than requiring unrelated captures to
  have one common commit. Earlier-generation evidence remains untouched.

### 2026-10-20 - Preserve unknown outcomes and bound command retirement

- Reconciliation refusal no longer converts a lost reserve/bind/close response
  into definite refusal. Tests apply each transaction, lose its reply, refuse
  read-back, then recover and settle the original claim.
- Exact subject lookup uses a SQLite JSON-expression index and still refuses
  ambiguous matches. The query-plan regression requires index use, not a
  timing estimate. Relationship inspection uses the existing public DB accessor.
- The authority subprocess now establishes native custody before receiving
  transaction stdin, reuses the plugin's identity-checked group primitive and
  native Job retirement, and bounds subsequent pipe collection. Cancellation
  waits for the intrinsically bounded native retirement before closing its
  handle; unknown cleanup is logged and remains unknown, never successful.
- A bounded delegate found that the general async spawn API alone does not
  promise strict custody. The finite Worktrees front door's stdin barrier
  provides the narrower integration point: unavailable custody refuses before
  owner resolution or mutation, without a shared-library change or thread-
  abandoned command. Linux and Windows are the supported authority hosts;
  macOS lacks the required identity backend and is explicitly refused.
- Native Windows cases verify Job membership, zero remaining processes and
  inherited-pipe cleanup both with a live parent and after parent exit. Parent
  and console descendant each report no console or acquired foreground across
  two observation cycles. Deterministic cases cover hung cleanup, native
  retirement failure and repeated cancellation without premature handle close.
- The command/receipt/reconciliation/index selection passes 54 cases. The
  suggested dictionary normalization is not an implemented transport case:
  both supported transports use the declared task set, so no speculative
  alternate task-container adapter was added.
- The complete ownership/native/drain portfolio passes 127 cases, with its
  Linux-native companion explicitly skipped on Windows. The added pre-input
  incompatible-Job-API case and focused portfolio pass 55 cases plus that same
  platform skip. No shared process library or unrelated renderer behavior changed.

### 2026-10-20 - Opaque claim storage cannot prove absence

- A further review found that malformed collections/items become conservative
  `metadata_opaque` claims without producer metadata. Checking only malformed
  producers could therefore report authoritative absence from unreadable
  storage. Explicit absence inspection now rejects either condition; new
  reservation uses the same refusal rather than appending into that storage.
- Six fail-first cases cover malformed mappings/scalars, non-mapping items,
  invalid states and explicitly opaque released entries. Each verifies that
  neither absence nor a new reservation is reported and original bytes remain
  unchanged. This does not relax existing protected-claim or teardown rules.
- The complete retained-authority and claim-codec selection passes 98 cases,
  including native CLI/provider and finalization checks. The Bridge ownership,
  native consumer and ordinary drain selection passes 116 cases; its full
  guards pass 304 cases with eight optional/platform skips. The independent
  managed-drain capture brings the registry to 311 fixtures.

### 2026-10-20 - Recheck turn drain after claim inspection

- The current-head review found one remaining admission race: an idle session
  awaiting claim inspection is not an active acquisition, so drain can finish
  cleanly before that inspection returns. Turn admission now rechecks the
  existing drain gate immediately afterward, before turn-count or storage
  changes. Answers that settle already-admitted work keep their existing path.
- The fail-first regression pauses real authority inspection, runs actual
  manager drain to a clean result, then releases inspection and requires the
  existing drain refusal with zero new turns. It and the existing drain
  portfolio pass 33 cases. An independently authored generation-42 vector
  records this boundary; unrelated current-source captures remain unchanged.

### 2026-10-20 - Bound callback capacity and preserve teardown authority

- The next current-head review identified three remaining boundary defects.
  The claim's recovery already retains the complete approved binding, so bound
  identity now adds only a fixed-size fingerprint of the opaque native ID.
  Bridge checks the eventual bound subject's size against the unchanged callback
  limit after confirmed reservation and before contacting the peer. Full native
  identity remains in its journal; capacity failure leaves a closable pending
  claim rather than an accepted but unusable remote conversation.
- Failed managed stop retains the original client. Resume cannot replace it
  before local transport quiescence, and end/provider verification still sees
  that retained teardown reference. No parallel task tracker or reaper was added.
- A CLI owner declaration now prevents an unowned fallback when both caller-ID
  and owner probes fail, including empty or malformed declarations. Truly
  standalone callers retain explicit unowned behavior.
- Seven fail-first cases reproduced the three defects. The deterministic
  callback-limit, CLI ownership, teardown, recovery and retention selection
  passes 94 cases; local-session/caller-selection/remote compatibility passes
  297 cases. The actual command/provider/TLS matrix passes five cases under
  unchanged limits. Current-source contract re-attestation remains required
  before publication.

### 2026-10-20 - Retire without losing native or reservation evidence

- The next current-head review confirmed the earlier five fixes and identified
  two retirement variants. Direct end now reconciles the persisted accepted
  native ID before retiring, rather than requiring resume to preserve it.
  The restart regression checks both resume and direct end and then deletes
  the closed session row to prove terminal audit remains complete.
- End no longer tries to reserve when the original reservation was unconfirmed.
  Worktrees offers an explicit read-only missing-request mode under its exact
  owner-record lock; existing claims return complete receipts and malformed,
  ambiguous or foreign authority cannot establish absence. Older commands
  reject the extra field, and ordinary missing inspection keeps its refusal.
- Bridge retains an exact absence receipt only after local quiescence and when
  neither an accepted native ID nor an uncertain remote attempt exists. This
  uses the existing journal authority-result slot, not another ledger or a
  schema migration. Unknown reservation outcomes remain retryable and blocking.
- The real finalized-owner case now covers failed start followed by successful
  local end without a reservation or peer request. The complete native matrix
  passes five cases under unchanged limits. An earlier combined run timed out
  while another plugin's native suite ran concurrently; isolated deterministic
  cases and the complete serial native run pass. No timeout was widened and
  the concurrent timing cause is not claimed fixed.
- The final integrated Bridge repair selection passes 91 cases. Retained
  Worktrees authority tests pass 61 existing cases plus twelve explicit
  absence cases, including actual CLI inspection; strict receipt tests reject
  numeric booleans and wrong identity, and new acquisitions require the
  advertised absence capability. Install and documentation gates remain green.

### 2026-10-20 - Preserve recovery across aging and interrupted admission

- PR #7364's current-head review identified five concrete recovery/authority
  gaps. Nonclosed relationships now preserve the original session handle
  through aging and startup cleanup, so pending settlement remains retryable
  rather than leaving only an unreachable journal and blocking obligation.
- The retained authority adapter disables the legacy PATH fallback while
  preserving it for older unrelated callers. A missing owning runtime must
  refuse even when a foreign compatible command is available.
- Native collision and reuse checks include the journal's requested/accepted
  identities after failed load and restart. Recovery reconciles the native ID
  committed in the session row before validating a journal-bound retry.
- Admission captures the live client and status around authority I/O and
  refuses if stop/replacement changed either before a prompt or answer acts.
  The fail-first cases reproduced duplicate attachment, lost recovery and
  post-stop prompt/answer admission rather than merely testing helper output.
- The combined strict adapter, retained aging, native reconciliation, managed
  lifecycle and actual-command/provider/TLS selection passes 75 cases.
  Local-session, caller selection, remote attachment and retained aging
  compatibility passes 316 cases. The module ceilings and touched Python lint
  pass unchanged. Current-source re-attestation and another current-head review
  are required before delivery; no operational hold changed.

### 2026-10-20 - Generation-42 compatibility evidence

- Captured the exact Bridge source commit
  `f6af0cce96f262cbffa22465df5384c24bd5135a`, preserving all historical
  generation-41 and earlier evidence. Re-attested its 42 independent vectors
  with only advertised-version changes; added independently authored managed
  owner negotiation and retained inspection expectations.
- The complete Bridge guard selection passes 303 cases with eight platform or
  optional-path skips. Contract-checker regressions pass 29 cases. The affected
  provider response and ordinary catalog/native transport selection passes
  49 cases, including both named and URL transport paths.
- Contract failures exposed outdated synthetic session shapes and current
  response projections. Corrected those consumers to assert the additive
  relationship field explicitly while retaining frozen historical expectations.
  No item, group or containment limits were increased.
- The module-size check rejected growth in the session route registry.
  Extracted the cohesive relationship HTTP adapters without changing route
  order or public imports, then re-attested generation 42 to that committed
  source. The extracted route, managed lifecycle and contract selection passes
  380 cases with two platform skips; the module ceiling remains unchanged.
- Publication rebasing encountered the concurrently merged exact-workspace
  owner evidence. Preserved that source and immutable generation-41 vector,
  included its unchanged expectations in generation 42, and re-attested
  against the combined committed source instead of retaining a stale capture.
- Documentation impact covers remote ownership operating guidance and immutable
  contract evidence. Existing transport, authentication, install and lifecycle
  ownership remain unchanged. Publication and review remain pending; release,
  runtime and live external acceptance are not authorized by these results.

### 2026-10-20 - Bridge acquisition and retirement source

- Implemented ordinary owner forwarding, a durable Bridge acquisition journal,
  exact Worktrees command receipts and registered local-retirement verification.
  The source retains uncertain remote outcomes and refuses blind creation replay.
  Stop and restart retain responsibility; explicit end retires only the local
  relationship and keeps terminal evidence available for inspection.
- The managed recovery selection passes 25 cases, and the strict command adapter
  passes 15. The five complementary native cases pass through actual Worktrees
  commands, the registered Bridge callback and a synthetic TLS peer; they retain
  the existing item/group limits. Separate database, protocol, session and
  transport selections pass, as do the ordinary HTTP routes.
- Instrumentation located the combined native case's poor deadline margin in
  repeated command/process boundaries. Removed redundant confirmed-bind
  round trips, then separated admission/retirement and send/resume scenarios
  without removing assertions or replacing the native boundaries. HTTP/2
  transport coverage remains separate from the integrated TLS WebSocket lane.
- The new HTTP generation requires independently recorded generation-42 contract
  evidence. That evidence and the publication gates are still pending; the
  source is not a merged delivery. Acknowledged cross-worktree transfer remains
  unsupported and explicitly refused. Runtime, credential and live-request
  holds are unchanged.

### 2026-10-20 - Worktrees authority source merged; Bridge integration next

- #7356 merged as `1e1c929caa1766ff1ca75f9d48e6287916f33d2b` after a clean
  current-head Copilot approval, resolved feedback and green required checks.
  The normal managed merge used no review bypass. The reviewed typed-JSON
  correction had already been pushed when the initial old-head finding
  arrived; the subsequent review confirmed it resolved.
- Re-read the effort after merge. The first authority-proposal source slice is
  complete; owner forwarding, Bridge acquisition/retirement, acknowledged
  transfer and the integrated synthetic peer matrix remain open.
- The ordinary sync helper aborted its multi-commit squash rebase unchanged.
  Preserved a backup, verified all twenty owned paths were byte-identical to
  the confirmed merge, then rebased away only those already-merged commits
  onto the current contribution branch. No source work was discarded.
- Next is the ordinary Bridge CLI/client/API owner path and durable relationship
  acquisition/retirement integration against this authority, retaining the
  explicit refusal of unimplemented transfer. No runtime deployment or live
  external acceptance is inferred from the source merge.

### 2026-10-20 - Retained authority design reviewed; first source slice

- #7345 merged as `18c88f1e6b2fb213c8bbf906c89beb5133f363ec` after a clean
  current-head Copilot approval and green required checks; the normal managed
  merge used no review bypass. Reconciled only the owned effort-index row
  against concurrent updates, then synced onto the reviewed design.
- Began the Worktrees metadata/transaction/preservation slice. A fail-first
  focused regression confirmed the missing retained authority. The current
  61 retained-lifetime cases pass, including an actual interpreter CLI, a real
  registered provider process, final cleanup and an OS-sidecar race.
- Disposable allocation, absence, transfer recovery, projection, token and
  legacy preservation selections pass. Command-boundary coverage found and
  fixed a missing project-free route; legacy fixtures found and fixed an
  unintended dependency on record status for non-relationship preservation.
  Slow compatibility files used complementary bounded selections, without
  changing deadlines or dropping cases.
- Exact replay compares canonical JSON rather than Python object equality;
  fail-first boolean/integer/float and nested-value cases prove that a changed
  recovery subject cannot alias the original request.
- Fresh temporary ledgers provide first-touch API proof. No installer or
  provisioning behavior changed; full synthetic peer integration belongs to
  the next source slice. Live external acceptance remains separately held,
  not claimed by these source tests.
- The effort remains Active. This design merge and source work do not grant
  runtime update, shared restart, credential or live-request authority.

### 2026-10-20 - Separate retained-relationship authority design

- Prepared the focused Phase 2 source-contract proposal after the operator
  permitted needed shared claim changes subject to separate design review.
  The wire/lifetime choices are agent-recommended, not attributed to the
  operator's scope permission.
- Kept the version-1 disposable allocation/absence contract intact and named
  the new-lifetime preservation, old-writer refusal, uncertain acquisition,
  local retirement and acknowledged-transfer validation obligations.
- This delta is planning only. No ownership runtime code, promotion, installed
  update, shared restart, credential action or live request is included.

### 2026-10-20 - Connection prerequisite merged; relationship integration next

- #7231 merged as `28db8c9a2c700317824e96e1880fdcb5bd4df478` after a passing
  current-head owner review, required checks and resolved threads. Its generic
  target/auth source acceptance is complete; this effort accepts the next
  host relationship and separately authorized adopter acceptance boundary.
  Release promotion remains with #3336 and does not authorize rollout.
- Preserved the unpublished reviewed-plan status commit, rebased it onto the
  merged prerequisite and reconciled only the host-ownership index row while
  retaining newer unrelated entries. The source is now ready for Phase 2.
- The exact claim authority, capability and reconciliation contract must be
  checked before implementation. A retained conversation is not a disposable
  remote service: existing reserve/bind/delete semantics cannot be assumed
  compatible. No ownership code, runtime update, grant or live request has
  been performed by this transition.
- Source inspection confirmed the public legacy claim kind/terminal-owner
  mismatch and diagnostic-only reverse lookup. Establish the reviewed retained
  relationship authority boundary before coding; preserve existing disposable
  producer behavior and older-daemon refusal.
- Source-only scope now permits needed shared claim-contract changes after
  separate design review. Next is the bounded authority contract proposal,
  not implementation against an assumed disposable or best-effort interface.

### 2026-10-19 - Reviewed proposal merged

- #7258 merged to `dev` at `2026-10-19T20:32:24Z`, commit
  `06638e155269bcdd3d9498c5d6cfce6e85ed0e64`. Copilot approved the proposal
  and its narrow index-conflict reconciliation; all observed final-head checks
  passed before the managed merge.
- Synced onto the merged plan and re-read this contract. Ownership code has not
  begun: #7231 is still undergoing review of its production source-registration
  repair and remains an explicit implementation prerequisite.
- The effort remains Active; plan merge is neither ownership acceptance nor
  permission to promote, deploy, restart or acquire live credentials.

### 2026-10-19 - Proposal

- Claimed #7237 and confirmed the effort slug. The native read-only adoption
  probe returned the exact version-1 compatible efforts capability.
- Traced the missing qualified-owner forwarding and existing claim/disposition
  authority. Distinguished external relationship retirement from disposable
  resource deletion and peer-service ownership.
- Prepared the plan and focused vision clarification before implementation.
  No runtime, deployment, grant or live external request is part of this delta.
