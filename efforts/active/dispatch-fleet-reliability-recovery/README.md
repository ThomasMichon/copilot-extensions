# Dispatch fleet reliability and recovery

- **Slug:** `dispatch-fleet-reliability-recovery`
- **Repo:** copilot-extensions
- **Branch(es):** Independent implementation worktrees; each PR merges after
  its own required gates pass.
- **Created:** 2026-10-12
- **Status:** Active
- **Vision:** agent-dispatch durable results, task/session attachment history and
  recoverable goals; plugin-services bounded work and single-owner lifecycle;
  registry-resolved configuration.
- **Historical category issue:** #6466 (closed; not a live claim token).
- **Continuing supervision coordination:** #6467 and this Active Plan,
  Validation Plan and Journal; bounded source issues retain existing owners.
- **Sub-issues:** #6467, #6470, #6471, #6793; proposed Phase 7: #7486;
  startup communication/process clocks: #8051;
  coordinate #6185 and #6407.

## Guiding Intent

Keep dispatch work navigable without discarding audit evidence, bound supervision
at its real blocking boundaries, and recover terminal housekeeping separately
from completed business work. Repository-owned declarations identify registered
repositories and relative resources rather than duplicating machine-local roots.
Preserve the owning ground-layer, installer and configuration authorities.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Coordinator | Own the plan, integration, dependency ordering and completion gate | Plan and integration worktree |
| Category implementer | Task metadata, producers and category-specific views | Dedicated implementation worktree for #6466 |
| Supervisor implementer | Bounded receipt reconciliation and progress recovery | Dedicated implementation worktree for #6467 |
| Registry implementer | Named repository/resource bindings and validation | Dedicated implementation worktree for #6470 |
| Source safety implementer | Trusted ownership snapshots and optional scoped GitHub authentication | `jittery-latch`, #6793 |
| Existing scalar-repair owner | Preserve ownership of #6407 and its existing #6421 PR | Existing PR and its current source worktree |
| Ordinary startup admission proposer | Prepare the proposed Phase 7 contract and bounded regression evidence; implementation remains review-gated | Coordinator and the existing #7486 public claim |
| Startup observation implementer | Own the reviewed Phase 3 startup-clock slice without taking over lifecycle, hydration or active-turn control | `sluggish-beacon`, #8051 |

## Coordination

- **Topology:** Independent per-slice worktrees and PRs may merge concurrently
  after their own gates pass; reconcile actual content conflicts.
- **Host (owns PRs):** Coordinator; implementation participants may prepare
  and merge their own slice PRs after their required gates pass.
- **Delegates:** The three implementation slices above. No shared-checkout Git
  operations and no nested delegation without explicit authorization.
- **Handoff:** Return source changes, exact validation results, PR state and
  unresolved acceptance criteria. The coordinator retains the parent objective.
- **Existing ownership:** Do not supersede #6421. Feed relevant evidence to its
  owner and compose its released repair capability.
- **Retained maintenance/recovery intake:** Keep #2207 and #2395 acceptance
  directly below after tracker supersession, with #6467's live bounded
  integration and all existing source/service/installer custodians unchanged.
  Use this Plan/Validation/Journal and eligible open bounded source issues for
  concrete overlap; no closed issue is a live ownership token, new assignment
  or permission for cleanup, restart, native execution or rollout.
- **Lifecycle owners:** Compose #5301 and #5655; do not add a competing watchdog,
  cleanup daemon, reservation store or repository registry.
- **Retained registry/deferral intake:** Keep the complete #8778/#8779
  original/refined/inherited contracts in the
  [direct Phase 3 Plan and Validation](retained-registry-deferrals.md).
  Existing coordinator/supervisor, Bridge registry/acquisition, retained-start
  and terminal-capacity custodians remain. Uncertain/live allocations are
  retained or flagged without positive existing release authority; no new
  cleanup actor, source appointment or resource transfer is introduced.
- **Proposed Phase 7:** Coordinate the bounded ordinary native-startup admission
  amendment through #7486. Preserve #6467's master-supervisor scope and #6793's
  source-safety scope, plus acquisition #6076, head-resume #6934 and remote
  relationship #7237. Represented admission #1453 and remote-carrier response
  loss #5843 remain separate owners. No implementation is authorized before
  this amendment clears normal public review and merge.
- **Startup observation:** #8051 owns only the Phase 3
  [startup-clock amendment](startup-progress-and-timeouts.md). Preserve
  #6467's master-supervisor scope, #3194's complete hydration acceptance,
  #4028/#6934/#7337 lifecycle ownership, #4171 active-turn control and
  #6812 updater observability. No production rollout or uncertain-turn resend
  follows from plan approval.

## Context

Recurring maintenance occurrences are ordinary tasks today. Newer rows can fill
query limits before ordinary work is rendered. Results, events and attachment
history are valuable and must remain intact; presentation separation is not a
retention policy.

The master supervisor resolves declarations and companion receipts before
creating its shared reconcile deadline. Receipt withdrawal can synchronously
validate and hash a runtime tree while holding a root-wide lock. Process-alive
checks therefore do not establish loop progress. This is distinct from the
inner-lane conclusion timeout in #6185.

Completed tasks can retain held terminal cleanup after capped retries.
Correcting the underlying serialization or timeout does not itself resume that
cleanup. Historical retirement flags must not be confused with active exclusive
fences or proof that a process remains alive.

Named repository references currently work inconsistently across declarations.
Evaluator argv has no explicit execution root, and several path fields interpret
repository names as relative directories. Merely replacing an absolute argument
with a relative string is not a safe portability fix.

Relevant existing plans are `supervised-lane-process-discipline`,
`versioned-singleton-manager`, `agent-dispatch-monitor-and-confirmed-state` and
`agent-dispatch-tasks-pane-ux-overhaul`. This effort owns the bounded repairs and
category delta, not their broader uncompleted campaigns.

## Request

Public-safe summary of the requested behavior:

- Separate recurring maintenance tasks into a category that does not clutter
  ordinary work, while retaining statistics, logs, results and agentic
  task/worktree/session audit associations. Defer pruning.
- Identify and fix supervision stalls with observable, bounded progress and
  lifecycle-owned recovery that preserves durable ownership.
- Repair malformed worktree serialization through its existing owner and harden
  terminal cleanup without restarting completed work or relaxing deletion gates.
- Make repository-owned registrations use registered repository names and
  relative resources. The global repository registry is the concrete checkout
  root authority; do not hardcode another machine's root.

Operational task recovery and deployment placement remain adopter-owned.
No private host, checkout, task or session identifiers are needed upstream.

### Bounded source-safety amendment

Public-safe summary of the subsequently authorized repair:

- Preserve ownership recorded by a trusted comment that the adapter updates in
  place. An absent predecessor comment or task on another coordinator is not
  evidence that the issue is unclaimed.
- Allow a source to explicitly select existing repository-scoped GitHub
  authentication, without switching global accounts, persisting credentials or
  exporting one account into the shared supervisor environment.

### Proposed ordinary native-startup amendment

Public-safe settled request summary, coordinated in #7486: preserve one ordinary
startup turn across a lost response, including a response lost after the turn
settled. Retain original allocation/session identity, distinguish historical
receipt reads from permission for fresh work, and leave intentional unkeyed
follow-up turns unchanged. The bounded design and validation matrix are in
[ordinary-startup-admission.md](ordinary-startup-admission.md).
This is a proposal, not an implementation or rollout authorization.

### Startup communication/process-clock amendment

The shared service/tool contract is reviewed in #8022 and
[`docs/patterns/progress-and-timeouts.md`](../../../docs/patterns/progress-and-timeouts.md).
The [Phase 3 adoption slice](startup-progress-and-timeouts.md) retains the
operator's literal request and separates required behavior from proposed
implementation sequencing. It qualifies the composed Dispatch-to-Bridge
startup path first; it does not claim every suite entry point already conforms.

## Plan

### Phase 1 - Review intent and retain existing owners

- [x] Deduplicate category, supervisor and registered-resource scopes and claim
  #6466, #6467, #6470 and #6471.
- [x] Identify #6421 as the existing scalar writer/selected-repair implementation.
- [x] Review and merge this proposal before runtime implementation.
- [x] Reconcile category-specific presentation to the existing dispatch vision;
  land any needed standing-intent extension before realizing it.

### Phase 2 - Separate task categories without changing retention

- [x] Add an orthogonal category with compatible ordinary-work defaults and
  explicit all-category API/CLI retrieval.
- [x] Carry explicit category through task creation and schedule templates without
  altering labels, routing, sources, deduplication or managed-create replay.
- [x] Filter before SQL limits across list/search/sweep and delegated presentation.
- [x] Make ordinary Tasks request work and provide a separate Maintenance
  contribution with failures, progress, results and attachment access.
- [x] Provide preview-first, exact-provenance, audited historical classification;
  leave ambiguous rows unchanged and never infer maintenance from worker kind.
- [x] Preserve IDs, timestamps, statuses, results, logs, events and associations.

### Phase 3 - Bound supervision and preserve recoverable operations

- [ ] Establish the budget before declaration/companion resolution and isolate
  expensive receipt integrity work from the master reconcile thread.
- [ ] Keep one tracked operation per receipt and revalidate exact selection and
  generation under the owning lock before changing state.
- [ ] Reduce root-wide lock occupancy without weakening integrity validation.
- [ ] Expose cycle/phase/operation progress separately from process existence.
- [ ] Integrate an independent progress watchdog with the existing lifecycle owner;
  unknown outcomes retain authority and never authorize duplicate embodiment.
- [ ] Bound post-timeout pipe draining as well as initial subprocess execution.
- [ ] Compose #6185's conclusion boundary with terminal-cleanup recovery rather
  than replaying completed task work.
- [ ] Complete all directly retained #8778
  [finite deferral-episode Plan rows](retained-registry-deferrals.md#plan),
  preserving budget/backoff/visible-exhaustion and release-or-flag alternatives,
  historical evidence and original custody.
- [ ] Complete all directly retained #8779
  [typed registry-to-reservation Plan rows](retained-registry-deferrals.md#plan),
  preserving finite transient versus definitive-absence classification,
  distinct task/reservation accounting and original caller uncertainty.

### Phase 3a - Startup progress and timeout adoption

- [x] Review the [startup-clock amendment](startup-progress-and-timeouts.md)
  before runtime implementation through #8051.
- [x] Implement and qualify the actual composed consumer: incremental nested
  target progress, sliding communication silence and an independent long process
  budget, without replay or replacement authority.
  Source consumer proof is merged in #8177; actual native-agent performance,
  containing release and authorized adoption remain separate acceptance.
- [ ] Resolve source/release and authorized adoption separately; preserve the
  parent Phase 3 and all unrelated owners' open acceptance.

### Phase 4 - Resolve repository resources from one registry

- [x] Define an explicit registered-repository binding for script resources,
  execution cwd and effort state-root identity.
- [x] Resolve roots through the owning global registry at use time; reject missing,
  ambiguous or incompatible registrations without PATH/ancestor guessing.
- [x] Separate local repository identity from forge/task-lane coordinates.
- [x] Avoid persisting a second authoritative checkout root in repo discovery
  pointers or repository-owned declarations.
- [ ] Preserve standalone queue use and stage adopter migration deliberately;
  old peers must fail explicitly when required resolution support is unavailable.

### Phase 5 - Recover held cleanup through existing ground-layer gates

- [ ] Cooperate with #6421 through release; validate its selected repair interface
  without implementing a competing schema repair.
- [ ] Add preview-first, audited held-cleanup recovery fenced to the exact
  reservation/generation and current allocation ownership.
- [ ] Preserve prior failures, task completion, attempts, results and attachment
  history; reset only eligible housekeeping progress.
- [ ] Treat explicitly incomplete paired cleanup as retryable, not complete merely
  because an intermediate action is primed.
- [ ] Keep live, dirty, unlanded, open-PR, unfinished, held and ownership-ambiguous
  work protected by existing conclusion and cleanup authorities.

### Phase 6 - Preserve source ownership and scoped authentication

- [x] Review this bounded amendment before runtime implementation of #6793.
- [x] Accept the adapter's verified, standalone claimed snapshot after an
  in-place edit; preserve author, schema, occurrence and release-conflict
  validation. Do not infer relinquished ownership from a missing local task.
- [x] _(Agent-recommended implementation detail)_ Add explicit GitHub-only
  `forge.auth: repository`, retaining omitted/`ambient` standalone behavior.
  Resolve the optional agent-worktrees sibling through its existing invocation
  helper, select the repository account and obtain credentials through its
  existing command boundary, then run GitHub operations with a child-only
  environment. Refuse missing siblings, mismatches and credential fallback
  before mutation. Never log or persist token output.
- [x] Renew scoped credentials on demand; preserve immediate pre-mutation
  identity verification and avoid process-global environment changes.
- [x] Document the opt-in and unavailable-integration failures; add a changefile.
- [ ] Land through normal review/CI, and retain release/adoption as separate gates.
- [x] Leave task transfer (#6189), PR-ground-truth reconciliation (#2798),
  broader account-aware operations, and unrelated recovery owners unchanged.

### Phase 7 - Ordinary native-startup keyed admission

- [x] Review and merge the [bounded contract](ordinary-startup-admission.md)
  before runtime implementation; preserve every existing owner above.
- [x] Persist a stable invocation and exact fully rendered prompt with ready-agent
  intent before first send; retain original allocation lineage across attempts.
- [x] Reuse the existing unique turn-delivery key and atomically persist immutable
  binding facts and the admission receipt with the turn, without a new ledger or
  delivery state machine.
- [x] Separate historical receipt reads from fresh permission: a queued g0
  admission remains historical truth after a legitimate g1 claim.
- [x] Add capability-gated keyed native send without unkeyed fallback; retain
  existing creation and protocol-floor preflight, not a new CREATE route.
  These implementation checks describe the local candidate, not publication,
  release, adoption or permission to activate an adopter.
- [ ] Preserve the exact one-turn counterexample assertion, unkeyed follow-ups,
  source/native safeguards and bounded supervision; qualify the reviewed fix
  through the sibling validation matrix.
- [ ] Keep normal publication, containing release and authorized rollout separate;
  plan approval alone does not authorize deployment or clear acceptance.

### Spawn-attempt exhaustion reporting and single-task recovery from #1145

<!-- backlog-reconciliation:7556:transfer:1145:to:efforts/active/dispatch-fleet-reliability-recovery/README.md -->

Place this direct unchecked acceptance under Phase3 supervision/recovery and its
Validation Plan. It is still-open work, not a completed feature or a new authority.
Preserve existing coordinator/supervisor, startup/ground-owner, scalar-repair and
held-cleanup source owners, claims and effect gates; no reservation, resource or
implementation custody is transferred.

- [ ] Reconcile handleless/stale RESERVING through a bounded race-safe original-startup
  check. A missing handle, expired timestamp, failed probe or UNKNOWN verdict is not
  absence; require the existing exact owner/operation evidence and prevent a late launch
  before freeing capacity. Preserve live/unknown/unreachable and captured-history
  blockers.
- [ ] Do not consume the hard failed-attempt budget for EXPLICITLY retryable
  dependency/capacity unavailability. Distinguish safe pre-admission refusal from
  uncertain accepted work; never use this classification as permission for duplicate
  effect replay or clearing held startup authority.
- [ ] Make generic task/list/health/status report the SAME exhausted-spawn blockage,
  count and supported recovery, rather than only a loop log while presenting an ordinary
  runnable queue. Preserve retired task-status DEAD_LETTER compatibility/migration
  separately; do not restore that status or confuse queued spawn diagnostics with
  terminal task retirement.
- [ ] Provide one-task explicit permissioned/audited retry/reset without globally
  raising attempt limits. Keep current
  owner/generation/native-history/startup/cleanup/exclusivity fences; coherent
  configured attempt bounds must not strand a task below an unrelated recovery minimum.
- [ ] Preserve append-only diagnosis and all original reservation keys, outcomes, errors
  and lifecycle events. Audited current-state transitions must not erase historical
  failure or synthesize missing launch/completion evidence.
- [ ] Qualify daemon restart, transient dependency outage, stale/handleless reserving,
  exhausted budget, queued-versus-blocked views and manual single-task recovery,
  including probe/write/late-start races. Preserve ordinary queued behavior and UNKNOWN
  conservatism; no all-host/model or whole-effort completion is added to this finite
  repair.

Delivered atomic rearm and positive-absence recovery are baseline, not all1145
acceptance. Keep assigned terminal-startup capacity and other active source scopes with
their existing owners; this block neither closes them nor sweeps held rows. Normal
source review/current test-portfolio routing applies, not a new ACK rendezvous. Close
duplicate1145 ONLY after this COMPLETE named Plan/Validation block is merged/read back
in this existing reviewed Active effort; link-only journal text or a closed/Draft
receiver is insufficient. Preserve first-pass history and retain1145 if delivery or
custody conditions fail.

### Released exclusive key versus held cleanup from #2207

<!-- backlog-reconciliation:7556:transfer:2207:to:efforts/active/dispatch-fleet-reliability-recovery/README.md -->

This direct Plan/Validation subcase remains at the existing Phase3 supervision/Phase5
housekeeping boundary, preserving the whole current effort,1145 acceptance, Phase7
native-admission fences and existing owners. The title-only report is not a verified
universal release defect; classification remains open. Current6542 recovery is credited,
not authorization to sweep held rows.

#### Plan

- [ ] Classify/reproduce the exact alleged held-cleanup/released-key fence with task,
  allocation revision, active reservation state and actual logical release distinguished
  from diagnostic cleanup metadata. Do not infer a failure or repair authority from a
  Summary heading.
- [ ] A genuinely released logical key must not fence an otherwise safe DIFFERENT task
  solely because historical cleanup metadata remains. Preserve
  SAME-task/workspace/allocation-revision, live body, retained ownership and uncertain
  native-send/effect reconciliation fences; logical key release is not allocation
  disposal or permission to duplicate admitted work.
- [ ] Preserve full settled/pending/held cleanup history and visible reasons. Require
  existing exact confirmed-absence/conclusion evidence for safe settlement; RELEASING,
  unknown/live holder, missing handle or elapsed age never implies release permission.
- [ ] Recovery remains exact
  reservation/generation/creating-host/provenance/newer-allocation/lease/preview/audit
  under existing authorities. Terminal housekeeping recovery does not reset nonterminal
  attempt/business work or change results/attempt/history; retired old failures do not
  become new allocation authority.
- [ ] Keep assigned #1918 per-attempt/no-handle/old-held custody, #1488 task-terminal
  resolution, #1666 ground liveness and scalar/native-startup owners separate. No
  unassignment, expiry, blanket force clear, new orphan collector or competing recovery
  actor is introduced.

#### Validation

- [ ] Exercise positive valid-release/different-task and negative
  same-task/workspace/revision, live/unknown/RELEASING, pending native effect and racing
  newer-allocation cases through the actual existing reservation/recovery consumers.
  Preserve exact historical flags/metadata while asserting the correct current safety
  outcome.
- [ ] Restart, repeated/lost-response recovery, cleanup failure and concurrent attempt
  cases keep one allocation/effect, full failure history and truthful held state. Credit
  existing confirmed-absence/preview recovery without claiming all title symptoms
  complete or requiring whole-parent/native-program completion.
- [ ] Close duplicate2207 only after normal reviewed ACTUAL merge and CURRENT FULL
  direct Plan/Validation/custody readback in this existing Active effort. Until then
  retain2207. This proposal performs no cleanup, reservation release, test or native
  action.

Sources: https://github.com/ThomasMichon/copilot-extensions/issues/2207 ;
https://github.com/ThomasMichon/copilot-extensions/pull/6472 ;
https://github.com/ThomasMichon/copilot-extensions/pull/6542 ;
https://github.com/ThomasMichon/copilot-extensions/pull/3264 .

### Retained Phase3 acceptance — temporary per-unit watchdog maintenance from #2395

<!-- backlog-reconciliation:7556:transfer:2395:to:efforts/active/dispatch-fleet-reliability-recovery/README.md -->

This tracker-only amendment requires normal review of the full existing effort,
successful required checks, ACTUAL MERGE and readback of the complete current Active
Plan/Validation/Coordination before superseding #2395. A title link, unmerged patch,
generic maintenance state or body presence is not delivery. If any original owner/gate
cannot be preserved, KEEP THE SOURCE OPEN.

#### Coordination and scope

Preserve the existing Coordinator/Supervisor roles and #6467's live bounded
supervision/progress integration; #5301/#5655 and each service/installer/other-watchdog
owner keep their existing source, custody and rollout. Record #6466 as closed historical
category work, not a live claim token. This accepted future slice introduces no
implementation assignee, source handoff, parallel daemon, reservation store or service
restart authority. Normal review/concrete overlap coordination applies, not a new
generic ACK ceremony or takeover of the broader lifecycle programmes.

#### Unchecked Plan — per-unit restart inhibition

- [ ] Inventory the actual dtssh companion_gate/supervise restart boundary and
  applicable other watchdog consumers. Reuse their existing supervision/lifecycle
  authority for an attributable, narrowly addressed temporary interval; do not introduce
  another watchdog or mandate one filesystem marker/backend for all services.
- [ ] Enter the interval before the targeted install/update/maintenance stop. Restart
  decisions observe it before relaunch, preventing the one target's immediate revival
  while unrelated units keep their existing desired-state, health and progress behavior.
  Do not kill or disable the whole supervisor tree to create a replacement window.
- [ ] Integrate actual install/update success, failure and interruption. Use owned
  cleanup and/or safe expiry/recovery so an abandoned operation cannot leave the unit
  permanently disabled. A finally-only happy path or an unbounded stale flag is not the
  interrupted-operation proof; preserve concurrent live-owner safety and existing
  selection/readiness/recovery fences.
- [ ] Support explicit scoped temporary operator use through the same documented
  authority, without mutating persistent operator-off overrides or clearing another
  updater's maintenance. Handle overlap/reentrancy, stale-versus-current state and
  invalid/foreign inputs explicitly; absence of a local task or elapsed agent-claim age
  is not ownership release.
- [ ] Expose entered/held/expired/released/recovery/error and restart-resumption
  outcomes truthfully. A timeout, process-exit receipt, maintained status word or
  permission sidecar is not completed maintenance or safe rollback. Keep failed primary
  installation and inhibitor cleanup failures distinguishable.
- [ ] Preserve all applicable consumers and the original dtssh/Dispatch causal case,
  with exact source/service/installation identity and no unrelated-unit disruption.
  Installation-context maintenance authorization, task categories, optional Manager
  enrollment, default-only no-expiry killswitch and per-installer watchdog deadlines
  remain distinct mechanisms.

#### Unchecked Validation Plan

- [ ] Prove the actual target stops without immediate unwanted restart during
  maintenance and resumes normally after the interval safely ends; other supervised
  units remain live/progressing.
- [ ] Exercise automatic success/failure/interruption/owner-loss and manual temporary
  paths, concurrent holders, delayed/stale observations and failed cleanup/expiry.
  Preserve current owners and ensure neither a stale flag permanently disables a unit
  nor one participant clears another's live interval.
- [ ] Verify actual native dtssh/Dispatch restart and applicable other-watchdog
  integration, plus meaningful supported-platform positives/negatives and bounded
  process/pipe cleanup. Source flags, a category test or mocked successful stop alone is
  not the effect evidence; do not invent an all-host or full-parent completion
  requirement.
- [ ] Preserve persistent operator overrides, source/configured-runtime/readiness
  identity, existing child ownership/health/backoff and retained failure history. Do not
  weaken another lifecycle or maintenance contract to obtain a green result.
- [ ] Keep every old Phase3/#6467 progress/uncertain-operation item and #5301/#5655
  responsibility intact. This future acceptance is not proof of complete independent
  custody or whole-update rollback and authorizes no current native/service/test
  operation.
- [ ] After actual reviewed merge, read back all rows, Active status, live coordination
  and unchanged owners. Close #2395 only as duplicate tracking after complete accepted
  delivery, never as an implemented maintenance capability or expired claim.

No host process, service, marker, override, watchdog, permission, credential or runtime
is changed by this consolidation.

## Validation Plan
- [ ] Complete every directly retained #2395 Plan/Validation clause above at its original evidence tier and bounds; preserve current owners, holds and unfinished acceptance. Tracker consolidation is not implementation.

- [ ] Complete every directly retained #2207 Plan/Validation clause above at its original evidence tier and bounds; preserve current owners, holds and unfinished acceptance. Tracker consolidation is not implementation.

- [ ] Complete every directly retained #1145 Plan/Validation row above at its
  original evidence tier and bounds, preserving existing owners and holds.
  Tracker consolidation is not implementation or native acceptance.

- [ ] Request read-back preserves category-only scope, no pruning, audit retention,
  registry authority and existing schema-fix ownership.
- [ ] Old databases and omitted categories remain compatible; filters precede
  limits; list/search/sweep/local/remote views agree.
- [ ] Managed-create replay, results, events, attachment history and reservations
  survive category introduction and repeatable historical classification.
- [ ] Fault-inject slow hashing, root-lock contention and retained pipe handles;
  bound cycle latency and prove unrelated units continue progressing.
- [ ] Restart/lost-response tests preserve one operation and exact generation
  ownership; watchdog recovery does not duplicate active or cold workers.
- [x] The same named-repository declaration resolves under two distinct registered
  roots; missing and ambiguous registrations fail explicitly.
- [ ] Scalar repair remains with its existing PR and is exercised after release.
- [ ] Held-cleanup retry never restarts business work or bypasses newer ownership.
- [ ] Partial pair races and interrupted progress remain resumable under exact
  pairing evidence rather than guessed ownership.
- [ ] Run targeted unit and actual CLI/RPC first-touch integration checks. Record
  clean-room/live-venue evidence or a concrete unavailability reason per slice.
- [ ] Add required changefiles, lint/install-contract gates and documentation-impact
  review; drive implementation PRs through merge and promotion.
- [ ] Authorized rollout has running-version and real consumer evidence; merge
  alone is not deployment.
- [x] Round-trip an actual provider comment edit through fresh discovery and
  selection, including a second empty coordinator; a trusted claimed snapshot
  remains excluded and is not released merely because its task is absent.
- [x] Keep malformed/untrusted markers rejected and conflicting releases from
  clearing a claim. Preserve ordinary reserved/claimed/released histories.
- [x] Exercise scoped account selection, renewal, unavailable sibling, mismatched
  account, failed/degraded token retrieval and child-environment isolation
  without exposing credential bytes. Include a real subprocess contract.
- [x] Run the same declaration's ambient mode without the optional sibling.
  Record native Windows results and available Linux CI separately from live
  adopter proof; a mock or source pass is not installed-consumer evidence.
- [ ] Resolve or explicitly transfer every plan/validation item before Done.
- [ ] Phase 7 proposal read-back preserves the g0/g1 distinction, original lineage,
  exact prompt, atomic turn receipt, capability refusal and existing owners;
  execute the [bounded matrix](ordinary-startup-admission.md#validation-matrix)
  after this plan amendment clears review and merge, and before the runtime
  repair lands. Retain the scoped passing control and honest failing
  counterexample without publishing a known failing test as a completed repair.
- [ ] #8051 satisfies every row of the
  [startup observation matrix](startup-progress-and-timeouts.md#validation-matrix),
  including unknown updates, target versus local keepalives, both silence
  cases, independent process expiry, nested flush, retained identity and
  uncertain acceptance. Qualify the actual consumer, not only clock helpers.

- [ ] Complete every matching #8778/#8779
  [direct Validation row](retained-registry-deferrals.md#validation-plan)
  against its full original/refined/inherited Plan scope and evidence tier.
  Preserve all original OR/AND choices, retained allocations and current
  custodians; tracker intake is not behavior completion or resource release.

## Proposal

The initial proposal changes no runtime or deployment configuration. Category is
orthogonal to execution kind and routing. Registered resources resolve through an
optional adopted repository integration rather than introducing an unconditional
dependency for standalone dispatch. Housekeeping recovery cannot change business
completion or weaken allocation ownership.

_(Agent-recommended)_ Add explicit capability reporting for category filtering and
named-resource resolution so an older peer cannot silently accept but ignore a
requested contract.

## Journal

### 2026-10-28 - Direct retained registry and deferral contract

The complete #8778/#8779 original/refined/inherited outcomes are retained in
the direct Phase 3 Plan, matching Validation and continuing Coordination
through [their per-origin ledger](retained-registry-deferrals.md).
The bounded current-contract study verified actual whole text and complete
nested eligibility under stable actor/roster guards, with the current
immutable receiving ROOT read separately from implementation evidence.

Budget, backoff, exhaustion and release-or-flag alternatives remain open at
their original scope. Existing acquisition, retained allocation, native-history
and supervisory ownership are unchanged; no retry, release, rearm, cleanup or
runtime operation occurred. Normal review, actual merge and complete current
receiving readback precede any supported source-tracker supersession. This is
neither technical completion nor whole-effort closeout.

### 2026-10-23 - Startup observation source merged; release and adoption remain open

- #8177 merged as `c0b0dbba0f639344c5e4b384ccffe7b7ede1cc02` after the
  exact published head received a clean owner-authored Comment review with no
  open findings, every substantive thread was resolved and required CI passed.
  The resolved Maintainer completion path exercised its ordinary review-rule
  exception; no failing CI or blocking review was bypassed.
- Review corrections cover actual text/JSON protocol refusals, supervised
  read-only preflight and late-contact expiry, incomplete/wrong-session resume
  acknowledgements, canonical ACP-to-Bridge identity, valid lifecycle status
  acknowledgements and accepted delivery followed by receipt-settlement failure.
  Unknown outcomes retain the original operation and never authorize replay.
- Current HTTP-46 operation/registry replay passes 103 cases; the unchanged
  committed-diff checker passes two contracts and 515 fixtures. The 456 prior
  fixture records and existing capability floors remain unchanged. Current
  attestation uses actual source `fc439fc4b4e4c6e160868e94450c4bed57eddac0`.
  Linked predecessor families remain historical, not new native captures.
- The affected coordinator cohort passes 50 cases. Twenty targeted admission
  cases preserve all eight lifecycle values and turn index zero while rejecting
  invalid statuses. Actual CLI/HTTP controls exercise distinct canonical/native
  IDs and retain one resume with zero prompts on invalid resume acknowledgement.
  Earlier corrected-source Linux proof remains pinned to its recorded commit;
  it is not relabelled as a run of every later integrated source byte.
- Whole-interpreter timing assertions and cold route imports in the synthetic
  service produced recorded fixture failures during qualification. The controls
  now measure the selected observation clock and prepare the service before
  observation; no production clock was widened.
- The merged worktree was preserved after ordinary sync auto-aborted a conflict
  replaying its squash-merged history. A backup retained the exact published
  tip; an empty post-merge rebase pulled the continuing source workspace forward.
  Release proof and any authorized adoption continue under #8051. Phase 3,
  full native-agent performance, other suite entry points and broader owners'
  acceptance remain open.

### 2026-10-23 - Corrected startup consumer qualifies on Windows and offline Linux

- Corrected executable source `6dca21f9fd450d98e94f631504c80dac4d7c73ad`
  (tree `919ea4cfedf76cbf2387433491bf5f0ee608a5b0`) passed the real controlled
  route, native CLI/HTTP/SSE and actual Dispatch-parent witnesses. The focused
  Windows Bridge cohort passes 46 cases and CI repair passes 48; earlier
  Dispatch startup/diagnostic qualification passes 128 plus the added local
  unknown-outcome wrapper regression.
- The same corrected source passes 46 Bridge and 177 Dispatch cases on an
  exact-source, pinned, nonroot/NNP1/cap0 Linux box, with zero failures, errors
  or skips. Networks were removed before testing; no model, host credentials,
  home, socket or ports were supplied. Final census and exact-resource absence
  were independently checked, and the resource obligation was settled.
- Narrow independent follow-up review confirmed all three original findings
  resolved, with no significant issue in the corrections. Carrier-only CRLF
  and caller/report-path errors remain recorded; they did not change the
  source under test or license a repeated mutation.
- This qualifies the bounded source consumer, not every suite entry point,
  real-agent startup performance, release, deployment or retained-task
  acceptance. Normal publication and those subsequent gates remain open.

### 2026-10-23 - Correct actual subscription and uncertain delivery contracts

- Independent review rejected the first local Phase 3a candidate before
  publication: its synthetic HTTP fixture missed the real controlled stream's
  caller/continuity requirements; truncated admission acknowledgements could
  escape as ordinary failure; and an existing coordinator timeout catch could
  release a possibly active CI body.
- Retain the authoritative cursor snapshot and separate observer audience,
  including the existing empty-log bootstrap. Add conformance through the real
  service route, not only a permissive synthetic endpoint.
- Normalize incomplete/invalid admission responses to unknown without replay,
  and retain coordinator custody after transport or receipt-settlement
  uncertainty. Known pre-delivery failures keep their existing cleanup path.
- Current-source Windows/Linux qualification, focused re-review and normal
  publication remain required; the earlier candidate's passes are not
  relabelled as corrected-source acceptance. No deployment or retained-task
  resend occurred.

### 2026-10-22 - Startup-clock proposal reviewed; implementation begins

- #8058 merged after an exact-head approving review with no findings and green
  required checks. The source worktree was pulled forward before implementation.
- Phase 3a now proceeds under #8051. Existing phase budgets, target event streams
  and progress writers are the starting seams; no runtime adoption, new numeric
  default, replay or production action follows from this plan milestone.

### 2026-10-22 - Propose runtime adoption of the shared timeout contract

- #8022 establishes the service/tool-wide design contract; it does not implement
  the current Dispatch/Bridge consumer. Worktrees already emits incremental
  phase and waiting output, while intermediate captured calls and fixed outer
  deadlines still prevent that signal from composing end-to-end.
- Added the bounded #8051 Phase 3 amendment, preserving the operator request,
  independent clocks, existing operation custody and all broader owners.
  Implementation awaits this proposal's normal review and merge.
- #8015/#8046 remains the separate caller-discovery pipe-ownership correction;
  its unchanged legacy budget is not startup-clock adoption or a new numeric
  default. Source qualification, release and authorized rollout remain distinct.

### 2026-10-22 - Complete retained acceptance from #2395

- Retained the complete #2395 scope directly in the Plan and Validation,
  without declaring feature completion or transferring source/resource custody.
  Normal reviewed merge and current full readback precede administrative
  supersession; all substantive acceptance remains unfinished.

### 2026-10-22 - Complete retained acceptance from #2207

- Retained the complete #2207 scope directly in the Plan and Validation,
  without declaring feature completion or transferring source/resource custody.
  Normal reviewed merge and current full readback precede administrative
  supersession; all substantive acceptance remains unfinished.

### 2026-10-22 - Complete retained acceptance from #1145

- Materialized the complete #1145 residual directly in the Plan and
  Validation, preserving existing source/resource custody and partial delivery.
  Normal reviewed merge and current full readback precede administrative
  source supersession; no behavior, native run or rollout is declared complete.

### 2026-10-21 - Phase 7 local review corrections

- The local candidate now reads historical g0 admission after a real g1 claim
  without readiness/allocation writes or fresh sends. One-shot and supervisor
  entry points share the canonical original-lineage, lane-aware seed.
  Created/resumed storage retains immutable intent transactionally, and
  changed requested agent/charter selection conflicts before any delivery.
- The focused Dispatch caller/intent portfolio passes 140 cases with one
  native concurrency case deliberately outside this in-process batch.
  The original preamble, finite two-poll settled-response-loss case and Unicode
  real-stdio case pass together: three cases, no skips, one inbound prompt each.
  Their identities remain compatibility-only and native-source unqualified;
  queued task state is not task completion or verification.
- Corrected semantic HTTP-45 families and the owned current-authority recapture
  pass 218 focused Bridge cases. The registry audit passes with 456 fixtures;
  historical HTTP vectors and capability floors remain unchanged. Shared new
  operation evidence is explicit, not relabeled independent runtime capture.
- The inspected offline UV-loop fixture now uses existing bounded process-tree
  and pipe ownership with its original 30-second limit. Its exact six-library
  order includes worktree identity. Both local and canonical-source cases pass
  in separate targeted runs; earlier timeout and stale-five-library failures
  are retained, not waived. No production installer/deadline changes were made.
- Broader portfolio approval, independent OS-isolated qualification, final
  code review, normal publication, containing release and authorized rollout
  remain separate and outstanding. No adopter activation is claimed.

### 2026-10-21 - Phase 7 proposal reviewed

- Plan-only PR #7522 merged to `dev`; the ordinary startup admission contract
  cleared normal review and required checks. This closes only the plan-review
  gate, not implementation, validation, release or adoption.
- The local test-only passing control and honest settled-response-loss
  counterexample remain the starting evidence for #7486. Preserve the exact
  one-turn assertion and original owner boundaries during the authorized
  implementation slice; no creation-intent recovery or native-guard repair is
  included.

### 2026-10-16 - Bounded complete-suite CI sharding

- The approved source-safety repair's hosted dispatch job completed seven
  passing batches totaling 730.53 seconds of pytest time before the unchanged
  12-minute job deadline cancelled the remaining work. Its changed test files
  were in the completed sixth batch; no credential-test stall was established.
- The authorized fix-forward partitions the runner's authoritative sorted
  test-file selection deterministically across four CI smoke matrix jobs for
  dispatch only. Existing file validation, isolated fixtures, environment
  admission, process-tree containment and per-test/per-group limits remain.
  Empty shards never fall back to full-directory collection.
- Required `PR gate` still depends on the complete fail-fast-disabled smoke
  matrix; either shard's failure or cancellation remains blocking. Other
  plugins are single-job, while manual full and promotion full/coverage
  validation remain unsharded. This closes an execution-distribution gap,
  without changing standing intent or introducing selective coverage.
- Native runner/workflow/release-policy contracts pass 209 tests with one
  platform skip; the final focused shard portfolio passes 35, including a real
  contained pytest fixture and CLI forwarding. A real source manifest proves
  202 files partition into 101 + 101, with no omission or duplicate membership.
  Fatal lint, module ceilings and whitespace guards pass.
- No complete hosted shard run, new exact-head review, publication, merge,
  promotion or adoption is claimed by this local evidence. The new full-shard
  commands and supported manifest inspection are documented in TESTING.md.
- The initial two-shard native full run did not complete: shard 1 exhausted
  the unchanged 720-second aggregate budget during its third subsuite after
  two passing subsuites, with no observed assertion failure. This is not a
  passing full-suite result. The authorized measured refinement is four
  dispatch smoke shards, preserving the deadline and aggregate gate. The
  current 202-file source set partitions into 51, 51, 50 and 50 files;
  independent complete execution of all four remains required.
- Four-shard refinement validation: 32 count/completeness/matrix-gate contracts
  pass in a separate tooling interpreter; actual manifests have no omissions or
  duplicate memberships. A broader focused check passed 54 contracts but could
  not execute the unchanged real-fixture test because that interpreter lacks
  pytest-timeout. No dependency was installed and the managed dispatch venv
  was left unused for independent complete-shard validation.
- A targeted native diagnostic then isolated an assertion in the cutover
  fixture, reproducible with both candidate and pristine HEAD runners. Its
  deliberately aged passive-reap record was not current transition authority.
  The fixture now derives positive promotion proof from each actual published
  endpoint and verified process pair, checks increasing routing generations,
  and separately proves stale evidence is refused without changing the active
  route or process identities. Production ZDD and identity safeguards are
  unchanged. Both real native lifetime modes plus existing promoted-launcher
  and fixture PID/token guards pass: five tests in 100.85 seconds with the
  candidate runner and 124.09 seconds with the pristine HEAD runner.
- Separate bounded per-file timing, without a partition rerun, measured
  `test_supervisor.py` at 139.87 seconds (270 passed),
  `test_install_ps1_build_snapshot.py` at 29.77 seconds (14 passed, two skipped),
  `test_installer_engine_adoption.py` at 100.33 seconds (28 passed, nine skipped),
  and `test_task_worker_cli.py` at 99.83 seconds (55 passed). The latter's real
  HTTP-process case alone took 62.58 seconds. These observed costs total 369.80
  seconds before the separately measured native cutovers and remaining files;
  they establish accumulated work, not a claim that one repaired assertion
  explains the earlier 600-second limit. Complete-shard validation remains open.
- Post-repair complete native four-shard validation stopped at its first limit,
  without rerunning successful tests. Shard 1 covered all 51 files: 1,204 passed,
  six existing skips, and 295.26 seconds total wall time. Its three pytest
  subsuites reported 196.97, 55.01 and 12.23 seconds.
- Shard 2 reached the unchanged 600-second subsuite bound in its first group,
  returning runner exit 124 (outer exit 1), 610.17 seconds total wall time.
  The last completed case was the managed-companion real readiness/rollback/stop
  case; the next emitted case was `test_mcp_server.py::test_create_find_show`,
  which had no outcome before termination. No assertion failure was observed.
  Twenty-two files had been traversed, with 982 passing and 12 skipped reports,
  but there was no completed pytest subsuite summary. The remaining 29 files
  in that shard and both later 50-file shards remain unvalidated.
- Retained verbose timestamped logs show report-span lower bounds of 210.059
  seconds for managed companion, 132.810 for coordinator, and 100.225 for
  installer-engine adoption. These are not complete pytest file durations:
  interruption prevented the pending subsuite's duration summary. Exact
  completed-file lists, available duration sums and all remaining manifests
  are retained with the local validation logs. Native Windows-only lifetime
  cases remain legitimately inapplicable to hosted Linux; neither that platform
  distinction nor prior focused successes turn this native run into a pass.
  Hosted shard count remains four, and no budget, skip, production guard,
  publication, retry or rollout changed.
- Authorized native functional-coverage continuation reused the completed
  51-file first shard and derived disjoint positive `--test-file` batches for
  the second shard: managed companion (52 passed, one skipped, 190.65 seconds
  pytest / 198.38 wall), coordinator (139 passed, 122.44 / 129.18 seconds),
  installer-engine adoption (28 passed, nine skipped, 90.59 / 97.43 seconds),
  and the other 48 files. Their planned union is exactly the original 51-file
  shard, without overlap or omission.
- The remainder stopped on an actual assertion failure in its first subsuite:
  `test_companion.py::test_gated_launch_runs_only_after_receipt_and_stops_tree`
  found no `started.txt` after its existing five-second poll; the receipt
  existed. The completed failing subsuite reported one failed, 1,052 passed
  and 11 skipped in 389.32 seconds (399.90 seconds invocation wall time).
  The second subsuite and original shards 3 and 4 were not run. No assertion
  rewrite, retry, budget increase or further partition change was attempted.
- Only the reused first shard and three successful single-file batches count
  as green complete functional receipts: 54 of 202 files, 1,423 passed and
  16 existing skips. Earlier partial passing reports and the failed remainder
  do not establish complete native coverage. The original four-shard timing
  receipt remains failed/incomplete; fresh hosted Linux four-shard CI is still
  required separately. Logs, positive manifests, exact argv, failure trace and
  the full fixture patch are retained as local validation artifacts.
- The exact companion assertion received one focused diagnostic run per runner,
  with the original five-second observation and all existing containment limits.
  Candidate passed in 2.04 seconds and pristine HEAD under the same in-memory
  instrumentation passed in 1.42 seconds. Recorded receipts existed before
  gate release and matched the live creation token; the gate leader was alive
  before stop and exited afterward, and its receipt was removed. Captured child
  stdout/stderr were empty in both runs.
- These isolated passes do not explain or waive the earlier failed batch.
  The original failure retained no child receipt/stderr snapshot. The diagnostic
  marker probe mistakenly used the separate process cwd, not the fixture's
  plugin root, so its marker fields were excluded from conclusions; no valid
  marker-creation timestamp was captured. No extra diagnostic run or speculative
  production/test fix followed. No exact accepted-flake exception is known.
  Further fixture-versus-runtime action needs failure-context observability
  with the actual marker path and unchanged timing, rather than a timeout raise,
  expectation relaxation or retry-until-pass. Native coverage remains incomplete.
- The authorized diagnostic-only fixture now receives the actual marker object
  from the test and preserves the original five-second polling/assertion body.
  It captures receipt/gate events atomically, owned child stdout/stderr, PID
  creation tokens, descendant metadata and exceptions before teardown. It is
  explicitly opt-in; production companion code, containment, budgets and
  expectations are unchanged.
- Exactly one original 48-file remainder run under those diagnostics passed:
  1,053 passed and 11 skipped in 348.98 seconds, then 880 passed and one skipped
  in 351.04 seconds; 716.42 seconds invocation wall time under the unchanged
  720-second plugin bound. Reported files match all 48 positive manifest entries.
  The focused case itself took 3.97 seconds. No reproduction retry or later
  shard run followed.
- Valid evidence matches the marker parent to the receipt's declared plugin
  root, records the receipt before gate release, and observes the marker
  1.2475 seconds after release with content `started`. Receipt and live leader
  creation tokens match. Stop retired the leader and removed the receipt;
  child streams and exception lists are empty. Four descendant identities
  were recorded; one birth token remained queryable immediately after stop,
  which is not treated as a liveness/leak verdict without an exit-handle sample.
- The earlier contextual failure is still unexplained, not fixed or accepted
  as a flake. The passing diagnostic run adds observed functional execution but
  does not turn the earlier native four-shard timing failure into a pass or
  authorize merge. Shards 3 and 4 and fresh hosted Linux four-shard CI remain
  separate outstanding gates.
- Bounded static tracing confirms the fixture marker is absolute, independent
  of the separate process cwd. This unmanaged declaration has no provider,
  managed snapshot or health probe: launch synchronously records identity,
  atomically publishes the receipt, releases the byte pipe and performs one
  startup poll. The real gate/service interpreter bootstrap remains asynchronous
  relative to the fixture's five-second observation; no managed monitor is
  involved. No concrete unrestored mutation was found in the searched immediate
  test predecessors, which does not exclude every possible order-dependent effect.
- One specifically recorded stdio hypothesis received the sole controlled
  experiment: remove only the diagnostic file-stream overrides at the real
  Popen, restoring original inherited native stdout/stderr. The exact focused
  case passed in 6.20 seconds (5.97 call), with marker creation 2.4239 seconds
  after release and the original observation unchanged. This disproves a
  deterministic requirement for redirected streams, not an intermittent
  historical fault. CPython also explicitly preserves valid stdio handles
  alongside the gate handle whitelist. The unused dedicated stream files in
  this control are not evidence of empty child output.
- Read-only, birth-verified zero-wait handle sampling classified the current
  gate leader as matched-and-exited and its four recorded descendants as gone.
  No diagnostic process signal was sent. The earlier run's descendant birth
  tokens lacked exit-handle samples, so their historical liveness remains
  unknown; queryable creation identity is not a leak verdict.
- No causal fix is justified by these later passes. The earlier failure still
  cannot be classified as delayed bootstrap versus early child exit without
  failure-context execution/error evidence. One remaining decision is whether
  to preserve original-inherited-stdio diagnostics during the next required
  validation, not another speculative standalone retry or an exception. No
  portfolio run, budget/expectation change, publication or rollout followed.
- The next authorized diagnostics revision preserves original child
  stdin/stdout/stderr, argv, cwd and handle inheritance. It removes stream
  redirection and process inventory, records only the borrowed owned process
  handle's receipt-bound creation identity and zero-wait exit state, and adds
  an opt-in first-statement bootstrap breadcrumb without replacing the original
  marker or five-second assertion. It remains measurement, not a readiness or
  import workaround. Syntax, fatal lint and whitespace checks pass.
- Required original native shard 3 then passed all 50 files: 446 passed/nine
  skipped in 333.08 seconds and 665 passed/four skipped in 206.26 seconds,
  566.52 seconds total wall time. Original shard 4's first 25 files passed
  468 tests with ten skips in 154.93 seconds.
- Validation stopped at the first failure in shard 4's second subsuite:
  `test_repository_issue_source_safety.py::test_repository_probe_timeout_reaps_real_stalled_descendant[account]`
  asserted that the readiness file existed, but it was absent under the existing
  0.8-second probe timeout. The readiness prerequisite failed before any
  surviving-descendant assertion. The trace does not establish why readiness
  was absent and is not called a production leak or accepted flake. That
  subsuite reported one failed and 240 passed in 48.87 seconds; shard 4 returned
  exit 1 at 230.67 seconds wall time. No retry, budget or count change followed.
- The conditional original 48-file inherited-stdio context was not reached,
  so the revised companion diagnostics were not exercised in that case and
  the prior companion failure remains unexplained. All-202 native coverage,
  exact-four timing and fresh hosted Linux validation remain incomplete.
  The required-validation logs, remaining manifests and diagnostic review diff
  are retained locally; no publication or exception authority is claimed.
- Bounded stalled-probe analysis distinguishes policy from fixture input:
  production authentication commands retain 30-second timeouts; the regression
  imposes 0.8 seconds and then assumes the descendant became ready inside that
  interval. The real capture helper correctly charges spawn and identity-query
  work against its deadline. The old child stderr was deliberately discarded,
  so missing readiness alone cannot identify startup delay versus early exit.
- One causal experiment used the exact actor script and argv, observed real
  preparation separately, then handed the actual Popen object once to the
  unchanged capture/reaping path with timeout 0.8. Readiness was observed at
  0.734 seconds; identity-module import took 0.063 seconds separately. The
  sample did not cross 0.8 and does not demonstrate the historical failure's
  cause. Real timed capture returned no result, retired the known root, and
  stopped the real descendant heartbeat. No communicate/cleanup or identity
  was fabricated. This controlled in-memory fixture experiment is not a pass
  of the unmodified regression.
- No versioned probe fix followed an unproven cause. A remaining design decision
  is whether to explicitly separate real readiness/custody preparation from
  timed communication in the fixture, preserving 0.8 while pinning matching
  child identity and proving unknown/foreign owners cannot be signaled. That
  would be a fixture-contract correction, not a claimed production timeout fix;
  otherwise failure-context evidence is needed during a separately authorized
  necessary validation. No additional standalone attempt, full shard or retry
  occurred. Companion failure remains unresolved and its original-stdio
  48-file context remains unrun.
- The operator explicitly authorized fixture readiness/timed-capture separation,
  without accepting a historical-cause claim. A controlled old-fixture attempt
  with a real one-second child bootstrap delay failed its original readiness
  assertion with the actual 0.8-second argument. The child had not entered the
  delay before expiration in that attempt, so it is recorded only as the old
  structure's failure, not proof of why the historical failure occurred.
- The corrected test-only fixture prepares the real interpreter tree using
  existing suspended-spawn/Job custody on Windows or the existing POSIX session
  contract, waits for actual atomic descendant readiness, and pins native
  identity/membership before the timed call. A guarded spawn adapter hands
  over the same actual Popen object; real communicate and tree retirement are
  not replaced. Pinned child exit and heartbeat cessation are asserted before
  fixture custody closes and before the actor's finite watchdog can expire.
  Unknown/foreign identity and mismatched argv/cwd/environment requests refuse
  without releasing the timed phase or signaling foreign processes.
- The cold-bootstrap regression records an actual delay exceeding 0.8 before
  real readiness and timed communication. Account, credential, identity and
  forge-operation cases remain. Production authentication timeout stays 30s;
  only the intentionally stalled capture receives 0.8. Fixture preparation uses
  the existing ten-second readiness pattern inside the unchanged 30-second
  item default; an initial five-second preparation iteration failed and is
  retained, not called green. Actor active-watchdog, group 600s and plugin 720s
  limits remain unchanged. No skip or production implementation change was made.
- Further fixture validation exposed a mismatched pre-arm watchdog: preparation
  could wait ten seconds while the actor still ended preparation after five.
  Both now use the same ten-second readiness constant; active watchdog stays
  five. Parent-only dispatch/ZDD harness imports were also moved out of actor
  startup; actors use the existing lightweight native identity/publication
  helpers. Intermediate readiness/ownership failures are retained, not waived.
  Success is recorded only after error-free fixture teardown, and the regression
  explicitly observes the real communicate TimeoutExpired, not merely no output.
- Final native validation of the entire 43-test source-safety file plus directly
  coupled capture/identity guards passes with both runners: candidate 48 passed,
  two existing POSIX skips in 39.16s; pristine HEAD runner executed in memory
  48 passed, two skips in 23.80s. Fatal lint, module ceilings and whitespace
  checks pass. Complete fixture patch and all RED/iteration/final logs are
  retained locally. No portfolio/shard rerun followed; companion failure is
  unresolved, original-stdio 48-file context is unrun, fresh hosted Linux four-
  shard CI/review and publication remain separate gates, and no exception or
  historical root-cause authority is claimed.
- Independent full-file validation is real counterevidence to those earlier
  focused successes: 42 passed and one failed in 53.19 seconds, identity
  parameter, because the pinned child's zero-wait result was nonzero before
  fixture custody closed. That run did not retain the exact native return,
  LastError or exit code, so it is not classified as a live escape, async
  termination race or invalid handle, and no flake exception is accepted.
- Failure-preserving fixture diagnostics now retain exact wait/error,
  handle PID/birth match, exit code, heartbeat, stop/custody state and remaining
  active-watchdog margin before the strict exit assertion. The same recorded
  zero-wait value must still be zero; no confirmation-wait fix or production
  change has been made.
- The one authorized focused identity experiment passed one test in 13.13
  seconds. Taskkill returned zero; matching pinned-child waits at taskkill
  return, assertion entry and after the original assertion were all zero,
  exit code one. Custody remained open and stop was absent. The first observed
  exit was 1.937 seconds after arm, 3.063 seconds before the earliest natural
  watchdog, with stable heartbeat metadata. No diagnostic signal or bounded
  confirmation wait was used. This establishes a valid exit in that sample,
  not an explanation or waiver of the independent failure.
- The independent failure remains blocking and its native state remains
  unclassified. Exact diagnostics are staged for a next operator-authorized
  necessary validation; this slice made no additional standalone attempt,
  portfolio run, timeout/expectation relaxation or publication. Companion
  failure stays unresolved and original-stdio 48-file context stays unrun.
- The operator supplied an independent observation-contract basis, not a
  historical-cause claim: Microsoft's TerminateProcess contract says external
  termination is asynchronous and callers requiring completed exit must wait
  on the process handle. Source verification confirms the Windows capture
  cleanup issues taskkill and waits only for its root Popen; it neither owns
  nor promises a waited descendant kernel object. POSIX explicitly waits for
  group disappearance and remains unchanged.
  Reference: https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-terminateprocess
- The authorized fixture-only correction permits one wait only after a known
  matching WAIT_TIMEOUT, on the same pinned handle, capped at 1,000ms and by
  the existing five-second active window with one full second reserved for
  heartbeat/diagnostics. WAIT_FAILED, unknown exit/identity, changed custody or
  requested stop remain fatal; a second pending result is fatal, with no loop.
  Confirmed identity/exit, open custody, absent stop and stable heartbeat are
  rechecked, and the final clock check remains strictly before the natural
  watchdog and before fixture Job close. No signal, PID lookup substitution,
  production change or deadline extension was added.
- Observation-policy tests first failed 14 cases before implementation; all 15
  final policy cases pass, including timeout-to-signaled, failed/unknown refusal,
  persistent pending/error, identity/custody changes, insufficient margin and
  rejection of an exit observed after the watchdog. These are pure policy
  states without PID/creation claims; the real root/descendant capture cases
  remain separate integration evidence.
- The complete 58-test source-safety file and coupled capture guards pass once
  per runner: candidate 63 passed/two existing POSIX skips in 34.99s, pristine
  HEAD runner in memory 63 passed/two skips in 33.02s. Each produced five actual
  preclose native receipts with matching identity, confirmed exit, open custody
  and no stop. After heartbeat/diagnostics the minimum natural-watchdog margin
  was 3.063s and 3.25s respectively. Native pending-confirmation was not observed
  in those successful samples; that branch has focused policy regression
  coverage, not a fabricated historical reproduction.
- This corrects the unsupported synchronous-observation assumption independently
  of the earlier failure, whose actual nonzero return remains unclassified.
  No exception authority, portfolio run or publication follows from these
  successes. Companion failure remains unresolved, original-stdio 48-file
  context remains unrun, and parent-owned complete validation/review/CI gates
  remain open.
- Independent native source-safety validation then passed all 58 tests in
  32.48s. TESTING.md now explicitly separates real readiness/custody from the
  forced 0.8-second timed capture and documents bounded same-handle exit
  confirmation before the existing watchdog; production authentication remains
  30 seconds. The four-shard aggregate wording now says any shard.
- The next required native shard 4 completed all 50 files: 1,069 passed and ten
  existing skips, with subsuites of 179.73s and 268.83s and 480.07s invocation
  wall time. This includes the corrected complete source-safety file. Original
  shard 1, shard 3 and the three heavy shard-2 single-file receipts were reused,
  not rerun.
- The exact original remaining 48-file selection then ran once with original
  child stdio/cwd/argv/gate semantics and failure-preserving diagnostics. Its
  first contained 25-file subsuite completed 1,053 passed and 11 existing skips
  in 480.26s; the original five-second gated-companion case passed there.
  The second subsuite reached the unchanged aggregate plugin deadline with
  234.594s remaining at admission, during the first worker-CLI case, after 732
  observed passes and no observed assertion failure. Inner exit was 124,
  wrapper exit was 1, and invocation wall time was 728.65s including cleanup.
  Execution stopped at that first cutoff; there was no retry.
- The authoritative planned union remains 202 files exactly once with no
  overlap or omission. Completed invocations cover 154 files; the additional
  completed 25-file subsuite makes 179 green files and 4,656 passed/50 skipped
  across valid completed summaries. The final 23-file subsuite is incomplete;
  its partial reports are not credited as green coverage. First-subsuite
  per-file duration sums include procutil 250.44s, queue 41.81s and the installer
  build snapshot 31.13s. Retained manifests, exact argv, timestamped logs,
  complete-file duration tables and preclose diagnostics preserve this failure.
- Native functional coverage is therefore still incomplete, not a native FOUR
  performance pass. The historical companion failure remains unexplained:
  this successful original-stdio observation is not a demonstrated fix or an
  accepted exception. Native Windows cutover/Job coverage is platform-skipped
  on Linux, so these timings cannot establish hosted Linux geometry. Hosted
  FOUR remains unchanged and still needs fresh CI and review before publication
  or merge. This fixture/documentation slice changes no production bootstrap;
  no new clean-room installer or external Linux proof is claimed. No budgets,
  skips, production guards, publication or rollout were changed.
- The authorized remaining 23-file positive batch was checked against the
  current 202-file discovery: it is disjoint from the 179 green files and
  completes the exact-once planned union. All changed fixture inputs, including
  the new real-probe helper, were verified tracked/staged without working-copy
  drift and their hashes retained with the invocation.
- Existing complete evidence for this same 23-file subsuite was 880 passed/one
  skip in 351.04s, with 343.07s summed case durations. That measured evidence
  supported one batch inside the unchanged 600-second subsuite bound; no new
  shard count, grouping, test exclusion or budget was introduced.
- The single new attempt nevertheless hit that 600-second subsuite cutoff:
  811 partial passes, no observed assertion failure, inner exit 124/wrapper
  exit 1, and 605.72s invocation wall time. The contained native cutover case
  passed; its production-lifetime parameter was active at the cutoff. The final
  worker-instance and session-guidance files had no completed case reports.
  The 811 partial passes are not credited, and there was no retry.
- The valid green receipt remains 179 files and 4,656 passed/50 skipped; the
  23-file batch remains incomplete. Exact argv, unchanged 720/600/default-30
  limits, snapshot-input hashes, full timestamped output, partial case states
  and the complete remaining manifest were retained. Native full coverage and
  exact FOUR performance are not passing. Historical companion cause remains
  unknown with no exception authority; source remains unpublished, and fresh
  hosted FOUR CI/review remain integration-owner gates. Only this journal was
  changed after the observation; no further test, implementation, publication
  or rollout was performed.
- A subsequent independent focused check reproduced the original companion
  failure with original stdio and the unchanged five-second assertion: receipt
  publication and release completed with matching root birth, but neither the
  script-entry breadcrumb nor started marker existed before stop. This is valid
  entry-unconfirmed failure evidence, not an accepted flake or a wrong-path
  diagnostic; absence does not exclude blocking in the first file open/write.
- One authorized opt-in test-only phase observation also failed: one failed/
  25 deselected in 6.64s. It preserved argv/cwd/stdio, handle options, gate
  protocol, containment and the five-second assertion. A private sitecustomize
  observer recorded child import/gate/spawn phases and a non-signaling stack
  dump; this intentionally adds diagnostic import/profile overhead, so its
  measured durations do not purport to be uninstrumented historical timings.
- In that experiment, package initialization took 4.078s, including the eager
  queue import closure. The four-second stack was inside package version
  resolution importing importlib.metadata/resources. The gate then read the
  released byte immediately, 4.500s after parent release, without a captured
  gate wait. Service Python reached its startup observer at 4.984s; script
  bootstrap appeared only after the failed five-second observation. Existing
  Job-member handles were sampled before stop with actual creation tokens and
  membership; pending zero-wait results were recorded as exit-unconfirmed,
  never inferred to be leaks or used to signal foreign processes.
- Specific upstream comparison at dev be1ab20e995845c78454b49b73b21de62d782867
  found the shared procutil changes add direct-Python selection and move daemon
  implementation behind a lazy boundary; the current gated launch does not use
  those new paths. No gate/package-bootstrap repair is present there.
- Crucially, existing open PR #6487 already contains this exact root repair:
  its _launch_gated uses the same stdlib-only companion_gate.py by absolute
  script path instead of `-m agent_dispatch.companion_gate`, avoiding package
  initialization without replacing the gate protocol. Its native gate test
  poisons package initialization and checks the command remains gated, runs
  after release and exits correctly on both platforms. That live owner owns
  this launch/native-gate slice; no production fix was copied or prepatched
  here. Integration coordination and constructive repro feedback remain with
  the primary coordinator.
- Complete phase logs, stack evidence, identity observations, exact invocation,
  the full diagnostic-only delta and the specific upstream/PR patches were
  retained. Fatal lint and embedded-observer syntax checks passed. No second
  diagnostic run, candidate fix validation, portfolio retry, publication or
  rollout followed. The 179-file green receipt remains historical baseline
  evidence; the 23-file batch remains incomplete. Integration of the existing
  repair/new native test requires fresh discovery and validation, not a claim
  that this baseline's 202 files prove the future integrated snapshot.
- Precision retained after that observation: service site initialization was
  recorded before the deadline, but the observer did not profile the service's
  first file open/write. Its absent breadcrumb therefore cannot distinguish
  not-yet-entered script from a pending first write. The captured gate read
  completed within timestamp resolution and the import overhead is measured,
  but neither proves every historical cause or that the existing owner's
  repair completely eliminates this five-second failure. The one experiment
  is consumed; no second run or production change was made to erase that
  uncertainty. The primary coordinator posted repro feedback on the existing
  PR, and the exact owner hunk/regression were verified at its published head.
- Authorized isolated validation then pinned owner PR #6487 head
  b0ebd1e93af5529771aadac12e034feeea715f31 and applied only its verified
  direct-script launch delta to an ignored disposable source snapshot. The
  delta's SHA256 is
  3c7f117ccdbec63b6566a050d5f3f5389498650021192e9f58761111936a815b.
  Its dependency-free native poison-package test was reconstructed from the
  pinned API patch and verified against Git blob
  400afc2f524f35cb77e23f7f9cddde04c2089912. No other owner production changes or
  unvalidated capture/reconciliation dependencies were imported.
- The existing runner's in-memory plugin-root/prepared-venv seams reused its
  original venv admission lease and unchanged real run_contained boundary.
  Snapshot-first import paths, module __file__/hash assertions and observed
  actual Popen argv proved execution used the patched snapshot, not a warmed
  original module. Only companion.py differed among production snapshot
  sources; tracked production hashes were unchanged before/after.
- The original focused companion case and the owner's native poison-package
  regression each ran once: two passed/25 deselected in 5.59s, 20.234s runner
  wall time. Original stdio/cwd, headless/handle options, real gate protocol
  and the five-second marker assertion were retained. No child sitecustomize/
  profile hook was enabled. Receipt and matching root birth preceded release;
  bootstrap/marker were absent before release, and marker completion was
  observed 2.432s after it.
- Additional read-only proof reused the existing native fixture helpers to
  pin five actual same-Job process handles with matching creation tokens.
  Real controller.stop ran unchanged; every pinned object signaled exit in
  0.078s within the existing 15-second stop budget and before outer runner
  teardown, not through natural actor expiry or a fixture cleanup signal.
  The owner's poisoned initializer never ran, and its contributed command
  remained gated until release. Plugin/subsuite/default-item limits remained
  720/600/30 seconds.
- This is Windows validation of the isolated owner entrypoint delta only,
  not full PR #6487/current-dev validation, Linux evidence or proof of every
  historical cause. Tracked production remains unrepaired/unpublished; the
  existing owner and primary coordinator retain landing/integration authority.
  The earlier native 179-green/23-incomplete baseline and failed outer-context
  receipts remain unchanged. Exact code/provenance/hashes, source assertions,
  actual argv, stop proof and logs were retained. The operator's subsequent
  lifecycle-priority freeze preserves the ignored snapshot and all unpublished
  work without cleanup/deletion. The bounded check is complete; source writes
  and test commands are quiesced for the primary coordinator. No portfolio,
  retry, production patch, publication or rollout followed.

### 2026-10-15 - Phase 6 review repairs

- PR #6800's two Medium findings reproduced before correction: a conflicting
  release discovered before a live claim hid that claim, and the scoped
  subprocess timeout left a real descendant running. A third regression
  confirmed the actual scoped forge operation also bypassed tree containment.
- Same-occurrence conflicting releases no longer defeat a claim in either
  comment order, including retained snapshots with a previous label.
  The fresh review's differing-label variant and a matching-task/different-label
  variant each failed before the follow-up correction. Only matching-task,
  matching-label releases retain precedence in both orders;
  author, schema, issue, label and occurrence admission remain unchanged.
- Scoped production commands now reuse the existing captured process-tree
  runner for account selection, token retrieval, identity verification and
  forge operations. Injected runner seams and ambient mode remain compatible.
  Child environments stay isolated and failures do not echo credential output.
- The four targeted native Windows consumer suites pass 320 tests, including
  real stalled descendants at each of those four command boundaries. Fatal
  lint, module ceilings, headless-launch, install-contract and whitespace gates
  pass. This repairs the reviewed Phase 6 behavior, not standing intent.
- Validation tiers: unit/adapter coverage and simple real-process integration
  passed. No install/bootstrap change requires a fresh-machine scenario.
  Hosted Linux evidence awaits this exact push; no separate live external
  venue or rollout is authorized for this source-only repair. macOS is not
  claimed as natively exercised. Review, merge, promotion and adoption remain
  separate pending gates.

### 2026-10-14 - Source-safety implementation and native contracts

- Proposal #6794 merged as `1fbc4ce34effc3a5d72f7534a97461cc24e42401`
  after exact-head Copilot approval with zero findings and passing CI.
- The marker reducer now accepts the trusted in-place snapshot, preserves
  newer occurrences and rejects conflicting same-occurrence releases.
  It moved into the existing marker-helper module to respect the shrink-only
  module ceiling; the prior import remains compatible.
- GitHub-only `forge.auth: repository` composes the existing optional sibling
  invocation/account boundary. Each operation captures a fresh credential only
  in memory, verifies the resulting child identity and refuses degraded
  retrieval before mutation. Ambient mode is unchanged.
- The focused native Windows portfolio passes 307 tests, including the real
  provider edit/read/selection round-trip against an empty coordinator and a
  real subprocess child-environment check. Fatal lint, module-size, headless
  launch and install-contract gates pass.
- Authorized read-only integration exercised the candidate provider against
  the installed optional credential owner without changing parent credentials.
  No source was copied into an installed runtime. Actual production mutation,
  worker outcomes and activation remain adopter-owned acceptance gates.
- No installation/bootstrap path changes, so a separate first-install clean-room
  run is not applicable; fresh missing-sibling and synthetic credential cases
  exercise the new opt-in boundary. Native Linux execution remains with
  required CI. Review, source merge, normal promotion and authorized deployment
  are still outstanding, as are the broader effort's unrelated objectives.

### 2026-10-14 - Bounded source-safety proposal

- #6793 records two demonstrated source blockers. In-place marker editing
  removes the predecessor that the current reducer requires for a claimed
  snapshot; the GitHub adapter also has no source-scoped authentication opt-in.
- This amendment closes existing dispatch ownership and agent-fabric
  account-aware-operation intent. It adds no coordinator, credential store,
  supervisor, transfer mechanism or mandatory sibling dependency.
- Implementation remains gated on this amendment's review and merge. Existing
  recovery, release and broader identity-contract ownership is unchanged.

### 2026-10-14 - Native fixture-composition source validation merged

- #6629 merged to dev at `2026-10-14T05:31:30Z` as
  `697df793f896516733dab0823a9eb4492e8ed4ec`, from exact approved head
  `c8b0a67221b945b67cf0d0608e63a6d256716bd7`. Actual Copilot review
  `5513377617` approved that head with zero findings; all runnable checks,
  including required CI and native Linux/Windows, passed, with no review threads.
- [x] Resolve the native category fixture's aggregate budget-composition
  validation: four independently scoped real CLI view cases, one-boundary
  guard, controlled unchanged-budget experiment and semantic/evidence
  preservation are source-validated and merged.
- The original uninstrumented timeout's process/root cause remains unproven
  and its diagnostic/integration gate stays open with the coordinator and
  existing cleanup integration owner. This is not accepted-flake disposition,
  original-hang resolution, release, deployment, adoption or overall Done.
  The parent effort remains Active.

### 2026-10-14 - Native category contract budget composition

- The native category fixture put four serial cold CLI calls, each permitted
  20 seconds, inside one 30-second item plus real HTTP/setup/teardown. That is
  a contract-composition mismatch, not evidence of the original timeout cause.
- A measured normal run recorded CLI durations of 3.81, 4.39, 4.23 and 4.20
  seconds and an 18.28-second item interval. A contained nine-second cold-start
  delay left completed CLI calls within 20 seconds but exhausted the old
  aggregate item during the third call. The real modules and HTTP remained
  unmocked; neither timeout budget changed.
- Separate function-scoped view items preserve every selector, pivot, result,
  progress, statistics and attachment/session/worktree assertion. Under the
  same controlled delay all four real CLI calls passed in 12.27, 18.77, 14.06
  and 14.74 seconds, with each item below 30 seconds.
- Original failed-integration process/socket evidence is absent and the cause
  remains unproven. Its tracking and diagnostic disposition remain open with
  the coordinator; this fix does not declare the unknown hang resolved or
  accept it as a flake. No production lifecycle, pool, schema or rollout change
  is part of this test-contract correction.

### 2026-10-13 - Category source completion and inherited cwd admission

- #6543 merged to dev as `e45220dc726b22c5aad147a23094796b582fe4b3`
  after clean exact-head review, required CI and complete thread resolution.
  Its standing-intent extension #6482 had already merged before realization;
  the Phase 1 checkbox was stale. Phase 2 implementation items are resolved,
  not their release or installed-consumer obligations.
- Shape-only expansion introduced a coupled provenance gap: an inherited
  relative script cwd could pass admission when its command was named, then
  lose its defining-repository context in the generated held-tick declaration.
  Six validator, expansion and real recipe-document regressions failed first.
- The existing relative-cwd refusal now runs before the shape-only return,
  without registry or execution-file lookup. Positive coverage preserves named
  inherited cwd and directly declared leaf-relative cwd.
- The affected resource, script-forge, issue-loop and registrar-context suites
  pass 296 tests. This pure admission correction changes no installer, runtime
  selection or provisioning contract. Fresh temporary recipe files exercise
  the document boundary; native CI remains required before merge.
- No historical classification, adopter installation, native watchdog custody
  or scalar repair is claimed complete.

### 2026-10-13 - Category standing-intent prerequisite

- Proposal #6472 and category vision revision #6482 are merged. The dispatch
  vision now states orthogonal category metadata, compatible defaults,
  category-selection-before-bounds and evidence-based historical classification;
  its Tasks-pane child names independent full-fidelity Maintenance presentation.
- Category implementation proceeds in its isolated slice. Serial merge, release,
  integration and the parent completion gate remain with the coordinator.

### 2026-10-12 - Generic proposal and ownership boundaries

- Read-only evidence identified category/filtering gaps, a whole-cycle deadline
  gap, synchronous retention validation and capped held-cleanup recovery gaps.
- Existing #6407/#6421 already owns multiline scalar preservation and selected
  legacy repair; cooperation replaces a competing implementation.
- Opened the distinct coordination issues above. No runtime implementation,
  retention change, service update or worktree removal is part of this proposal.

### 2026-10-13 - Reviewed proposal and implementation ownership

- Proposal #6472 merged after an actual owner-authored clean `COMMENTED`
  Copilot review and passing required CI. The full agentic review lane reported
  a runner-start timeout; the available Lite review reported zero findings.
- Began isolated category-intent reconciliation, bounded-supervisor and
  registered-resource slices. Each implementation uses its own source worktree;
  the coordinator retains serial merge and deployment obligations.
- Existing #6421 scalar-repair ownership and the lifecycle owners remain intact.
  Category realization remains gated on standing-intent reconciliation.

### 2026-10-13 - Registered-resource review corrections

- #6531's first implementation review identified separate root lookups across
  validation fields, pre-lease emitter resolution and recipe identity/document
  reads. Five failing regressions reproduced those boundaries under a moving
  registry before the fixes.
- One transient resolution context now binds each validation/invocation and
  recipe chain to cached roots and a shared lookup budget. A standby emitter
  performs no lookup before acquiring its lease. Preserved public recipe and
  declaration seams while extracting focused modules, without raising size
  ceilings.
- Finite metadata probes now hold native process-tree custody through captured
  pipe EOF. A real Windows test exercises a leader that exits while its
  descendant retains the pipes; timeout closes the non-breakaway Job and proves
  the descendant has exited. This is finite-command custody, not independent
  master-watchdog delivery.
- Validation includes 421 dispatch compatibility tests, 19 repository-owner/
  clone tests and 42 resource/capture tests after the finite-probe correction.
  Native headless, module-size and fatal-lint gates pass. Review, promotion,
  authorized rollout and actual adopter migration remain outstanding.

### 2026-10-13 - Registered-resource provenance and custody follow-through

- The next #6531 review exposed five remaining boundaries: derived recipe cwd
  lost its repository class, reference effort state was treated as execution,
  builtin loading/standby validation still resolved resources, the field
  provenance walk used different roots, and POSIX retirement could signal a
  reaped leader's reused PID.
- Added failing regression coverage before correction. Named recipe directories
  now survive relative nesting and delayed placeholders as named resources.
  The field walk and merge share roots; cached class metadata is checked per
  read/execution use. Builtin spec loading and standby ticks validate shape,
  deferring identity and filesystem resolution to the held tick.
- POSIX captured-pipe handling retains the original leader unreaped until group
  retirement. Kernel child custody fences signaling without an external identity
  query; reaped or unknown custody refuses cleanup. Pipe EOF alone does not
  prematurely terminate a still-running leader.
- The affected consumer suites pass 418 tests on Windows; three native POSIX
  tests await the existing Linux CI lane. Native Windows containment remains
  exercised. Fatal CI lint, module-size, headless and install-contract checks
  pass. This is still source validation, not release, adoption or the separate
  master-watchdog completion gate.

### 2026-10-13 - Same-root implicit owner preservation

- Review of #6531 identified implicit checkout-derived owners changing during
  identity-only pointer migration when the checkout and registered names differ.
  The new regression failed before correction; migration now materializes the
  existing effective owner without changing explicit owner overrides.
- Registrar/resource consumer coverage passes 137 tests on Windows with three
  native POSIX skips; fatal lint and the install contract pass. The preceding
  revision's native Linux agent-dispatch CI job passed its complete suite.
- Required CI remains blocked by the independently tracked Windows launch
  failure (#6266, owned by #6274) and a distinct MCP routing-publication failure.
  Existing owners have been contacted before any overlapping implementation.
  No merge, release, deployment, or recovery-record mutation is claimed.

### 2026-10-13 - MCP cutover CI readiness prerequisite

- Traced #6548 to the integration fixture accepting `active.json` existence.
  Routing intentionally writes conservative custody history before the new
  active route, so file existence does not prove control-endpoint publication.
- The fixture now waits within its original startup deadline for the exact
  spawned daemon's routing endpoint. Real cutover, predecessor exit, handle
  flip, and data-plane assertions remain unchanged; production routing and
  the separate #6533 orphan-watchdog investigation remain untouched.
- Windows cutover coverage passes 27 tests, with eight native POSIX skips;
  fatal lint passes. Native Linux CI must exercise the changed real-process
  test before merge. No installation behavior changes, so a fresh-machine
  installer scenario is not applicable. No admitted external Linux venue is
  available; the existing native Linux CI lane is the integration witness.
- The non-blocking platform documentation finding now distinguishes native
  Windows and Linux evidence from unverified macOS custody. Missing unreaped
  child primitives refuse before spawning; no unsafe fallback is promised.

### 2026-10-13 - Discovery class and strict metadata boundaries

- A registered read-only pointer lost its class when discovery expanded relative
  executable fields against a bare path. Discovery now checks the registered
  active-source class before expansion; reference and knowledge registrations
  remain usable for recipe/state reads, never execution. This chooses explicit
  source refusal rather than inventing path-based provenance after expansion.
- Strict owner resolution now rejects non-string remotes before returning the
  ready response. Empty or omitted string remotes and legacy non-strict readers
  retain their prior behavior.
- Three dispatch and three metadata regressions failed before the source fixes.
  Final consumer suites pass 144 dispatch tests and 20 owner tests, including
  cached read-only checks, inherited recipe/state reads and exact negative CLI
  responses with unchanged registry bytes. Fatal lint, module ceilings and the
  install contract pass.
- Corrected MCP native Linux CI and the Windows launch check passed on the
  preceding head. No unconditional retry or bypass was used; the tracked
  independent Windows launch investigation remains owned separately.

### 2026-10-13 - Registration validation remains pre-admission

- Review identified eager callers of emitter validation at registration and
  declaration loading. Nine validator/registration/declaration regressions
  reproduced registry probes before any schedule lease.
- Emitter validation now defaults to shape-only; held builtin ticks retain their
  execution-time validation and resource resolution. Context forwarding covers
  both shape-only and explicit resolution without changing cwd provenance.
- The affected registration, declaration, resource and emitter contracts pass
  273 tests; fatal lint and whitespace checks pass.

### 2026-10-13 - Direct resource grammar admission

- Twenty emitter/evaluator loader, validator, registry and declaration cases
  reproduced accepting invalid named-resource grammar before execution.
- A shared syntax-only helper now covers direct emitter argv/cwd/side-load,
  evaluator registry argv/cwd, inline evaluator registration and existing
  script-forge shape validation. No registry or filesystem lookup is added.
  Positive admission coverage preserves valid not-yet-local named resources.
- Final script/resource/registration consumer coverage passes 346 tests,
  including positive not-yet-local admission. Fatal lint, module ceilings,
  install-contract and whitespace checks pass.

### 2026-10-13 - High-level expansion defers execution resources

- Actual repository/effort loop expansion still called execution validation
  before lease admission. Nine regressions reproduced the eager probe through
  direct expansion, decoded-document expansion and trusted registrar discovery.
- Both expansion callers now use shape-only validation. Generated specs retain
  the original loop declaration, including named execution/state resources,
  script-forge context and deferred worker identity. Held execution remains the
  resource-resolution boundary.
- Adjacent loop, resource, declaration and registrar contracts pass 350 tests.
  This is source evidence, not release or deployment; adoption remains gated on
  the reviewed promoted contract and actual consumer readiness.

### 2026-10-13 - Remaining master blocking boundaries

- Continued the preserved implementation after an interrupted worker rather than
  creating a competing source branch. Optional status collection/publication,
  receipt inventory and provider resolution now use independently tracked
  bounded workers; four slow providers cannot occupy authoritative discovery.
- Receipt inventory and its child operations remain in shutdown custody. Pending
  mutation outcomes are not replayed, and adopted registrations remain fenced
  while inventory is delayed. Observer errors remain explicit and confer no
  recovery authority.
- Two deterministic regressions failed before correcting an idle-return producer
  race and old-summary/new-cycle coalescing. A third failed before fixing inline
  singleton release while a prior-generation status writer was still running.
  Inline handoff now defers without spawn cooldown until publication settles.
- Updated asynchronous fixtures to observe actual tracked completion instead of
  assuming metadata publication or worker submission happens before a master
  return. Master latency bounds and single pending-write assertions remain.
- Final affected supervisor lifecycle, CLI, health, receipt and publication
  coverage passes 164 tests. Fatal lint, module ceilings, install contract and
  whitespace checks pass. This remains source validation, not deployment or
  independent native watchdog custody.

### 2026-10-13 - Retirement finalization and transient reconciliation failures

- Four failed-before regressions reproduced a second runtime hash after
  irreversible retirement, a permanently poisoned legacy receipt future, and
  healthy-looking observations with a future cycle start.
- Withdrawal now carries the prevalidated selection into an exact metadata-only
  under-lock recheck. Final pin withdrawal/readback share a separate one-second
  bookkeeping budget; no partial integrity result is accepted. Changed or
  foreign metadata preserves the pin and cannot cross the mutation boundary.
- Completed legacy futures clear on every outcome, preserving only pending work.
  Worker-raised timeouts remain visible failures. Future cycle starts return an
  unknown observation and never grant recovery authority.
- Retention, bounded receipt, health and native lifecycle coverage passes 144
  tests with two platform skips. Final supervisor, CLI, publication and metadata
  fence coverage passes 146 tests. No production state or lifecycle ownership
  changes are claimed.

### 2026-10-13 - Registered resources merged; adopter gate retained

- #6531 merged to dev as `c88d004c36e4b8d06aa56349a0f80bcfa205be1c`
  with clean exact-head review, required CI and no unresolved threads.
  Registered-resource source and two-root validation items are resolved.
- Standalone compatibility and deferred admission are proven in source.
  Promotion, installed runtime evidence and deliberate adopter migration remain
  open; merged is not released or live.
- Integrated the resolver into this supervisor slice, retaining both effort
  journals. Native independent watchdog custody and scalar repair still remain
  with their existing unresolved acceptance gates.

### 2026-10-14 - Cold discovery and pre-shutdown handoff corrections

- Fresh review of #6487 reproduced an unobserved initial registry being treated
  as empty, stopped units during a deferred inline handoff, and healthy-looking
  future completion timestamps. The fail-first selection reported five failures
  and two already-correct invalid-time cases.
- The store snapshot is explicitly absent until its first successful read.
  A pending initial read raises an indeterminate result before any unit removal
  or receipt withdrawal; completed snapshots retain the existing retry behavior.
  The persisted-receipt regression crosses the real inventory boundary and
  consumes the same completed registry future on the next cycle.
- Inline handoff drains optional status publication within the existing grace
  budget before stopping units. A pending writer retains the singleton and
  running units without arming cooldown. The existing serve-loop contract also
  passes; merely checking an unfinished future without bounded draining did not
  preserve that near-completion behavior.
- Completion observations reject future, non-finite, boolean and nonnumeric
  timestamps as unknown, without granting recovery authority. Missing completion
  retains the unfinished-cycle contract.
- The combined supervisor, CLI/progress, bounded receipt and health selection
  passes 176 tests. Fatal lint, module ceilings, documentation, headless-launch,
  install-contract and changefile-presence gates pass with unchanged budgets.
  These are source/consumer results, not rollout or independent keeper proof.

### 2026-10-14 - Receipt fairness, cold declarations and native startup

- Five fresh review findings reproduced starvation behind unknown receipt
  readbacks, missing unmanaged exit confirmation, cold configured declarations
  being treated as empty, an unresolved unmanaged provider losing its receipt
  fence, and accumulated completed probe references. The local corrections
  retain unknown operations, rotate admission without destructive replay, and
  preserve unfinished work in shutdown accounting.
- Actual contained startup profiling found that invoking the stdlib-only gate
  with `-m` initialized the coordinator package first. A native shadow-package
  regression failed before switching to the gate's pinned script path. The
  real readiness/repeated-health/rollback/stop path subsequently completed in
  24.297 seconds within its unchanged 30-second test limit.
- A separate fail-first regression showed that process creation was not charged
  against captured execution time. The command now computes the remaining wait
  from a deadline established before creation, preserving the one-second pipe
  drain limit. The actual inherited-descendant-pipe test completed in 5.047
  seconds against its unchanged 4.8-to-5.8-second assertion.
- State-machine fixtures now drain owned work before restoring mocked process
  identities and use controlled receipt executors with their simulated clock.
  Real concurrent receipt and native tests retain their actual executors.
- **Validation remains incomplete.** The latest exact subset passed six cases
  before the existing transition-group rollback test exceeded its unchanged
  30-second limit inside retention validation/publication. That timeout is not
  waived or classified as a proven flake. Full combined coverage, remaining
  gates, publication, fresh review and required CI remain open. No deployment,
  independent keeper custody or fleet processing proof is claimed.
- Subsequent current combined coverage passes 136 tests with one existing
  platform skip, including actual inherited-pipe timeout at 5.062 seconds,
  transition-group rollback at 12.968 seconds, and native readiness, repeated
  health, rollback and stop at 25.187 seconds. The state-machine fixture also
  controls its known provider responses so a 50ms validation test does not
  accidentally test an unresolved provider's different authority policy.
- Coupled companion/retention/progress coverage then passes 132 tests with one
  platform skip and one failure: the real materialization-process lock fixture
  did not publish its build marker within the existing ten-second admission
  wait. Its diagnostic `communicate(timeout=1)` timed out against a still-running
  worker that the fixture had not yet released, so it does not establish which
  startup phase was delayed.
  This newly exposed native boundary remains unresolved; no timeout waiver,
  publication or release is authorized by the earlier combined pass.
- Added actual child-entry, runtime-import, fixture-import and materialization
  phase evidence without changing that ten-second wait or the worker/cleaner
  release protocol. The exact native lock test passes; subsequent complete
  coupled coverage passes 133 tests with one existing platform skip. Actual
  child-entry-to-build time was 5.954 seconds in the combined run. The original
  missing-marker observation remains unexplained rather than retroactively
  labeled a pass or a proven flake. The latest two complete selections now
  cover 136 and 133 passing cases, respectively; publication and review gates
  remain separate and no live deployment is claimed.

### 2026-10-14 - Isolate damaged receipt metadata and invalidate withdrawn providers

- The next exact-head review identified four gaps: unsupported process-receipt
  versions could reach retirement; withdrawn provider futures could be reused;
  malformed operation metadata escaped the indeterminate contract; and one
  corrupt receipt or operation could prevent valid sibling scheduling.
  Nine new regression variants failed against the published source before
  correction. No completed earlier phase was treated as final approval.
- Receipt generations now validate schema and identity before journaling or
  retirement, and unavailable metadata remains an explicit indeterminate fence.
  Inventory, operation scheduling and progress isolate and log failures by
  path or identity while preserving damaged files and processing valid siblings.
  Withdrawn providers lose cached authority immediately; uncancellable work
  remains tracked but cannot supply a reintroduced declaration's launch result.
- Combined validation exposed a native Windows operation-file replacement
  failure. A separate controlled held-reader regression reproduced the exact
  `WinError 5` at atomic replacement. The controller's existing reentrant lock
  now serializes its metadata readers and operation writes. This is not a
  retry, filesystem-error waiver or broader lifecycle-authority change.
- Current bounded receipt/discovery/provider coverage passes 94 cases with
  unchanged deadlines, including the new corruption, withdrawal and real
  held-file regressions. The earlier owner-probe regression now asserts the
  intended per-identity error log and retained fence instead of a whole-pass
  exception; its retry, thread, process and receipt-preservation assertions
  remain. Coupled consumer validation, normal publication, fresh review and
  required CI remain separate gates. Independent native keeper custody and
  live fleet processing remain open.
- Coupled coverage then exceeded the unchanged 30-second rollback-test limit.
  Narrow phase instrumentation confirms the test executor was running real
  retention scans synchronously on the master: its first scan took 4.547
  seconds; the restarted master's second began at 24.640 seconds and was still
  inspecting cells at expiry. A test-only alternative routed both real scans
  asynchronously and passed once, but failed its unchanged total deadline in
  subsequent combined coverage. That alternative and its temporary tracing were
  removed in full; neither lifecycle fixture nor its deadline has changed.
- Exact companion/gate files pass 27 cases. Broader coupled coverage exceeded
  its existing 600-second aggregate limit. Disjoint managed-companion coverage
  reached the native readiness/rollback/stop case, which exceeded its existing
  30-second limit in the real stop subprocess. Neither result is a proven
  flake or permission to publish/merge past incomplete validation. The native
  boundary remains unresolved; no process, cleanup pass, hash or assertion is
  skipped, and no production or test timeout has been raised.
- Disjoint supervisor/retention files subsequently pass 180 cases with one
  existing platform skip. The bounded selection's 94 cases and exact companion
  files' 27 cases are separate completed evidence, not a completed native suite.
  The existing `windows-coverage-rotation.yml` supports an immutable candidate
  ref and native full-suite validation without workflow edits. Normal publication
  of this same shipping PR is needed to exercise that external lane; it does
  not waive the local failures or authorize merge. The selected rotation date
  must actually include agent-dispatch, and the run's pinned SHA and plugin
  outcome must be recorded before treating it as acceptance.

### 2026-10-15 - Bound availability reads and retain withdrawal suppression

- Fresh review found that the newly shared metadata lock could let one blocked
  read hold unrelated master-side launch checks. Two real-executor regressions
  demonstrate the original two-second stall; metadata locks are now bounded,
  per path and reference-counted. Availability I/O uses the existing provider
  executor, retains unfinished futures in shutdown accounting, and never
  requires an unbounded master-side lock or metadata read.
- Successful availability observations expire after their consuming cycle;
  withdrawn observations cannot be reused. An additional fail-first test
  commits an unknown operation and loses the write response, proving that
  availability must be invalidated before the write attempt, not after its
  return. Both the original record and unknown-outcome fence remain authoritative.
- A delayed scoped registry read reproduced a withdrawn declaration being
  started again after publication readback. The publisher now retains the
  suppression tombstone until a scoped snapshot started after the confirmed
  withdrawal observes absence. Older results are rejected by withdrawal epoch.
  The two direct publisher tests now explicitly exercise that extra readback
  boundary, without adding destructive retries.
- Valid JSON operation states containing a list or object now fail as
  indeterminate corruption, not an escaping `TypeError`. Both cases failed
  before adding the state-type guard.
- Current combined bounded coverage passes 101 cases with unchanged limits.
  The injected descendant in the pipe fixture now uses only stdlib process
  flags: it still creates a real inherited-pipe descendant, without measuring
  an unrelated plugin import before readiness. The original four-second
  execution, pipe-drain and elapsed assertions remain unchanged.
- Required CI exposed an upstream guard added after this branch's old base.
  Backed-up managed synchronization succeeded normally. The one-shot captured
  helper's existing exemption moved with its extracted function; no exemption
  count or launch authority increased. The new launch guard and its 67 tests
  pass.
- Hosted Windows run `38661141587` actually checked out the earlier published
  candidate at both selection and test jobs. Its first seven deterministic
  file groups completed with 4,953 passes and 44 skips, including the managed
  companion, receipt and master groups. Its unchanged 30-minute job limit
  interrupted the final group: the full suite did not pass. Local failures and
  incomplete whole-portfolio coverage remain recorded, not relabeled flakes.
  Fresh candidate review, required CI, release and live native keeper/worker
  acceptance remain open.

### 2026-10-15 - Preserve cancellation fences and incomplete native evidence

- Coupled master, companion and gate coverage passes 115 cases on the
  bounded-availability and withdrawal-epoch candidate. Subsequent fail-first
  cases expose cancellation of queued availability, provider and registry
  reads escaping reconciliation. All three now pass: canceled reads retain
  indeterminate admission, release completed future references and preserve
  the existing cached-registry or cold-start barrier. Cancellation does not
  become an empty successful inventory or permission to launch.
- The isolated real managed-companion readiness, rollback and stop case passes
  within its unchanged 30-second test cap. Its fixture uses stdlib-only native
  child flags and retains the real subprocess, health, rollback, stop and
  unchanged-marker assertions. The subsequent complete managed cohort exceeds
  that same test cap during concurrent governed runtime provisioning. That
  overlap is correlation only; the full cohort is not green or an accepted
  flake.
- Whole-candidate bounded and native revalidation, source publication and fresh
  review remain gated on coherent receiving-runtime evidence. No deadline was
  enlarged, cancellation swallowed as success, live service repaired by this
  source slice or incomplete hosted run reported as a full pass.
- Independent native keeper custody and production adopter integration remain
  separate open obligations under the canonical `versioned-singleton-manager`
  effort and #5655. A bounded supervisor, a progress snapshot or the existing
  Linux library does not close Windows custody or stalled-master recovery.

### 2026-10-17 (UTC) - Complete current bounded and native consumer validation

- Preserved the existing review corrections in local commit `c1f66c729` before
  resuming validation. Current bounded receipt, scheduling, blocking-boundary
  and recovery coverage passes all 104 cases, including the three cancellation
  regressions. Full managed-companion coverage passes 52 cases with one platform
  skip, including real native readiness, rollback and stop under the unchanged
  item and aggregate deadlines.
- Coupled master, health, companion, retention and native-gate tests initially
  reported 216 passes and one skip, but the runner correctly returned 125 after
  detecting a persistent machine-environment value change. No source reference
  to that value or direct persistent-environment write was found in the selected
  test files; the writer and cause remain unknown. No environment value was
  printed, restored, ignored or added to an exemption.
- Revalidated the complete affected consumer inventory in disjoint contained
  groups: native gate one pass, master/health 97 passes, companion/retention
  118 passes and one skip. Each invocation exited zero with the host-state guard
  still enabled. These are fresh disjoint passing results, not a claim that the
  earlier combined invocation passed or that its environment drift was explained.
- Current install contract, module ceiling, focused F/E9 lint, headless-launch
  guard, effort structure and five-library vendor parity pass. The existing
  one-shot helper exemption remains moved with its extracted function; no
  exemption count, lifecycle authority, test assertion or deadline increased.
- Publish through the existing owned PR, obtain a fresh resulting-head review
  and complete CI, and resolve the addressed findings with their actual
  dispositions. Source validation does not establish deployment, independent
  native custodian/adopter recovery or the parent fleet objective's completion.

### 2026-10-17 - Bound active removal and preserve shutdown child accounting

- Fresh exact-head review `5555329271` confirms both previous High findings
  resolved but identifies two Medium boundaries: active managed removal still
  performs selection integrity synchronously after stop, and a parent inventory
  completing between child sampling and pruning can hide a pending child.
  New regressions reproduce both defects before production changes.
- Managed removal now updates adoption through the existing tracked inventory
  and generation-fenced receipt worker. A changed adopted set can queue fresh
  inventory in the same cycle; no additional executor or destructive retry is
  introduced. Selection and retention pins remain until verified withdrawal.
  A blocked integrity scan does not delay an unrelated unit, and expiry keeps
  the operation explicitly unknown with the selected bytes preserved.
- Shutdown prunes completed parents before sampling receipt children, retaining
  a late child in the same grace budget. The controlled parent-completion
  interleaving can no longer report a clean drain with an untracked child.
- Six focused removal, disable and shutdown controls pass. The full current
  bounded/managed contract selection passes 158 cases with one platform skip,
  including real native readiness/rollback/stop. State-machine removal controls
  now share the real monotonic clock with real integrity deadline checks;
  timeout and cleanup assertions are retained rather than enlarged.
  Fresh publication/review/complete CI remain required. Native keeper/adopter
  and live deployment acceptance are still separate open gates.

### 2026-10-17 - Recover failed progress workers without extending active budgets

- Fresh review `5555601881` exposes two High boundaries. An unexpected optional
  callback exception survives in its future and is rethrown on the next master
  phase; inventory called without an active cycle receives an already-expired
  deadline. Three fail-first cases reproduce both defects.
- Completed progress-worker failures now use the existing sanitized, throttled
  warning path before the latest frame starts another worker. Consumed callback
  work is not replayed. All receipt adapters share a forward call budget only
  when no cycle deadline exists; an existing deadline is preserved, even when
  already expired. The five focused recovery/deadline cases pass.
- The integrity extraction also has a real import-order defect: importing
  `managed_integrity` first fails with a partially initialized module. Isolated
  fresh module imports reproduce it. Runtime dependencies are now resolved only
  when their functions execute, preserving the exported helpers, digest checks
  and exact timeout exception class. Both import orders pass.
- Registry-wide publication readback is retained deliberately. A new cross-scope
  regression proves an unknown write's immutable old machine/environment target
  is read back before a changed target is published. Applying current daemon
  filters could mistake that old write or a cross-scope ID collision for absence.
  One bounded publication worker still owns this read, off the master.
- Removed the unconditional passing native-lock trace print; failure diagnostics,
  subprocess evidence, lock assertions and deadlines remain unchanged.
- Integrated bounded receipt/scheduling, supervisor boundary/recovery and managed
  runtime/retention/companion coverage passes 297 cases with two platform skips
  in 561.47 seconds. The contained wrapper exits zero with its host-state guard
  enabled, including actual native materialization, readiness, rollback and stop.
  Import-contract isolation covers both fresh module orders; no install/bootstrap
  behavior changed, so a separate fresh-box provision was not run for this fix.
  Publication, fresh review and complete CI remain required. No source result
  closes independent native recovery or rollout.

### 2026-10-17 - Fence native progress and clear failed-cycle authority

- Exact-head CI catches `supervise_cli.py` exceeding the hard module cap at
  1,005 lines. Extracted only progress-record I/O into a focused leaf module;
  the CLI entrypoints and their path-resolution seam remain compatible.
  Validation now runs the exact provider base/head diff scope instead of
  relying only on the earlier unscoped local result. No baseline was widened.
- Fresh review confirms the previous five dispositions, then identifies
  native PID reuse, exceptional cycle cleanup and missing inventory being
  returned as confirmed empty. Eight new cases fail before fixes. Optional
  progress publication now requires both the PID and confirmed native start
  token; missing/changed tokens preserve the original file. A real current
  process generation successfully publishes through the same atomic path.
- A `finally` boundary clears cycle deadline/token references after either
  success or failure, retaining the actual failed phase for diagnosis. The
  observer returns unknown for a missing receipt inventory and preserves
  explicitly supplied empty or nonempty inventories. All eleven focused
  defect cases pass, as does the positive native-generation writer case.
- The new review's missing-`sys` assertion is not applicable: the published
  retention test already imports it at module scope, and the actual cold
  import-order cases have executed successfully. Removed six passing fixture
  timing prints and their unused timers; service health protocol output and
  native rollback/readiness/stop assertions remain unchanged.
- Initial coupled validation records 327 passes, one platform skip and one
  failed removal observation: a snapshot started before adoption withdrawal
  can complete asynchronously after the first master pass. The test now drives
  the real caller's bounded polling within the same original one-second
  observation budget and keeps every master pass below the original 0.5-second
  limit. No production deadline, selection-pin assertion or unknown-state
  requirement is relaxed. The focused removal/current-writer/error controls
  pass all four cases; complete resulting-candidate validation remains required.
- Complete resulting master, health, worker-instance, bounded receipt/scheduling,
  recovery, native companion and cold integrity-import coverage passes 328 cases
  with one platform skip in 207.64 seconds, contained exit zero with host-state
  protection enabled. F/E9 lint, the exact CI base/head module-ceiling scope
  and whitespace checks pass. The original failed coupled attempt remains
  recorded separately. Publication, fresh verdict and all current provider CI
  still gate merge; no runtime/native custody or parent completion is implied.
### 2026-10-13 - Held housekeeping implementation slice (#6471)

- Reconciled to `agent-dispatch`'s existing **terminal-worktree-reclamation**
  intent: failed cleanup remains retryable and the ground layer retains disposal
  authority. This closes existing intent rather than extending the vision.
- Reproduced missing exact held recovery, incorrect completion of explicitly
  incomplete priming, and the crash window between sibling and primary unlink.
- Added preview-first control-authorized recovery, transactional request replay
  and retained prior failure envelopes, exact generation/provenance/allocation
  fences, and consumption through existing cleanup leases. Business task state,
  results, timestamps, logs and attachment history remain unchanged.
- Added atomic reciprocal progress receipts before sibling unlink. Receipt
  recovery requires exact current provenance and unchanged terminal sibling
  evidence; ordinary liveness/Git/PR/effort/claim guards still apply.
- Contained targeted dispatch validation exercised actual native CLI subprocesses
  against a loopback fixture coordinator and a fresh database, including
  preview/apply/lost-response replay/history. No production records were changed.
- Installer/bootstrap clean-room and external deployment validation are not
  claimed: this slice changes neither installers nor provisioning, and no live
  deployment venue is allocated to its implementation worker. Fresh-fixture
  first-touch integration is covered; release, authorized rollout and real
  adopter recovery remain coordinator-owned gates after implementation review.
- Implementation is not the effort's completion. Scalar repair stays with its
  existing owner; category, supervision and registry slices retain their owners.

### 2026-10-13 - #6542 review correction

- Reproduced the four actual review findings before changing runtime code:
  incomplete retained allocation identity, component-free structured envelopes,
  malformed retained pair values, and the wrong cleanup-history diagnostic.
- Fenced every immutable allocation field consumed by cleanup, including
  project/path/originating reservation and the exclusive key, at lease claim,
  validation and outcome publication. Logical creation and inherited-allocation
  identity are retained too.
- Required structured envelopes to carry supported incomplete housekeeping while
  retaining legitimate flat legacy progress. Validated pair receipt values
  before reconstruction; malformed receipts use existing preservation reasons
  without widening exception catches or changing provenance authority.
- Added focused regression coverage for each field/boundary, component-free
  envelopes, malformed receipt values and both CLI error paths. Existing native
  first-touch and partial-pair checks remain part of validation.
- Serial merge, release, rollout and adopter recovery remain coordinator-owned.

### 2026-10-13 - #6542 actual-consumer correction

- Reproduced both fresh-review blockers: newer worktree publication after apply
  left a permanent pending loop, and an accepted unlabeled/unflagged retired row
  was never consumed by an actual supervisor.
- Kept generation/provenance checks before claim decisions, while routing newer
  worktree ownership through the existing durable held transition. Full
  allocation validation remains mandatory before a real lease grant, lease use
  and outcome publication.
- Required pre-existing release authority; recovery does not create it. Missing
  or malformed obligations leave the row, events and recovery history untouched.
- Extended native CLI/loopback HTTP validation through the actual supervisor
  consumer, protected lease and conclusion publication. Verified one successful
  retry, no replay after completion, no business spawn, and unchanged task state.
  Fault injection covers newer active and subsequently settled allocations:
  selected once, preserved held, never repeatedly selected as pending cleanup.
- Merge, promotion, rollout and adopter operations remain coordinator-owned.

### 2026-10-13 - #6542 lease-envelope and raw-pair correction

- Reproduced session-only recovery reaching a session end without a cleanup
  token, and a raw false pair value permitting primary unlink after normalization.
- Required the normal producer's worktree component for every component-shaped
  envelope. Kept genuine flat legacy progress supported; complete-worktree /
  incomplete-session recovery still follows claim, validation, then session end.
- Required raw sibling YAML to contain none of the pair keys before receipt
  reconstruction. The raw absence and digest checks share one parsed document;
  this does not alter the owning record writer or infer repair authority.
- Added actual consumer regressions for rejected session-only shapes, a positive
  leased session retry, and post-unlink raw false/empty/null/numeric/unsupported
  fields preserving both records and the unchanged receipt.
- Existing release authority, immutable allocation fences, one-shot newer-owner
  preservation, native consumer proof and coordinator deployment custody remain.

### 2026-10-13 - #6542 owned-receipt lifetime correction

- Reproduced successful managed/daemon removal leaving terminal-pair receipts,
  missing-record removal acknowledging unproven ownership, and failed deletion
  losing the staged seed or not surfacing receipt-cleanup errors.
- Extended the existing authoritative launch-seed/record removal transaction to
  remove only the exact record-owned receipt after YAML deletion. Under its
  existing record lock, caught deletion errors restore retained raw UTF-8 state
  via the existing atomic writer and surface the failure for a normal retry.
- Covered seed-only paired-record audit retention, unknown/rejected authority,
  symbolic ambiguity, record/receipt failure and retry, real daemon success /
  repeated removal, and native managed reap success/live/failure paths.
- No task history is pruned and no schema writer is replaced. Unrelated CI repair
  remains with its existing owners; integration, review and deployment custody
  remain with the coordinator.

### 2026-10-13 - #6542 authority-last teardown correction

- Reproduced abrupt interruption before/after each owned removal boundary;
  deleting YAML before its receipt stranded retry without authorization.
- Kept the existing removal transaction and error rollback, but ordered owned
  seed and receipt deletion before deleting YAML authority last. Interrupted
  sidecar removal retains YAML for supported retry; interruption after the final
  deletion has no owned sidecars left to strand.
- Added six crash-window cases and seed/receipt/YAML error injection. Retries
  finish only the exact old record while newer worktree identities and foreign
  artifacts remain byte-for-byte unchanged. Native authority-removal, pair and
  leased session-consumer contracts remain validation gates.
- No teardown marker, actor takeover, schema change, timeout increase or process
  killing is introduced. Review, CI, integration and release remain coordinator
  custody.

### 2026-10-13 - Reviewed source integration and host/protocol correction

- #6531 source binding merged at `c88d004c`; #6543 category source merged at
  `e452`. #6580's follow-through merged at `f0bb92bed` after actual approval,
  required CI and zero unresolved threads. Its external source witness exercised
  90 owner and eight admission checks with unchanged fixture state.
- Marked only the first four Phase 4 source-binding items and the two-root source
  validation complete. Standalone/adopter migration, release/live evidence,
  native master custody and selected schema recovery remain open.
- The independently owned obligation fixture #6588 merged at `117476aa6`.
  Those fixtures and the promotion/release lane are not reimplemented here.
- Reproduced recovery leases accepted by foreign/unknown hosts through actual
  claim/validate/outcome RPC and foreign supervisor session/worktree consumers.
  Carry the existing resolved supervisor machine and require the retained-host
  match, without hostname guessing or changing ordinary legacy conclusions.
- Required/advertised removal protocol version 4. Known pre-v4 monitors refuse
  before dialing or direct fallback; real old-daemon tests preserve all owned
  state. No monitor restart, actor takeover or manual unlink is added.
- The prior Git/pipe timeout remains causally unproven, tracked separately in
  #6471, and is not declared an accepted flake. Review, CI, migration, promotion
  and authorized rollout remain coordinator-owned.
- A separate native-CLI observation captured cold import-file waits before any
  recovery RPC. The former single item performed five cold CLI startups inside
  one 30-second item budget. Those five real CLI boundaries now have independent
  items with the original 15-second child / 30-second item limits; HTTP and
  actual supervisor consumption remain real in every case. No timeout is raised
  and no cached fake transport or in-process CLI substitute is introduced.

- **Unknown-component review correction (PR #6542):** Fresh review confirmed
  the claimant-host and removal-v4 corrections, then identified an unsupported
  state-bearing cleanup component that could be ignored by completion
  aggregation. Eight preview/apply regressions reproduced the missing explicit
  rejection; recovery now rejects any top-level state-bearing component other
  than `session_end` and `worktree_cleanup`, before scheduling or recording
  recovery history. Exact reservation and business-task state remain unchanged.
  The coupled recovery/conclusion portfolio passed 107 tests. This is a source
  correction only; adopter release, migration, live validation and schema
  custody gates remain open.

- **Rebased integration validation remains open (PR #6542):** The category and
  recovery client mixins now compose, with shared error re-exports retained.
  The bounded cleanup/native-consumer portfolio passed 20 cases and ordinary
  completion/category compatibility passed two. The additional native category
  fixture timed out at its second CLI list call under the existing 30-second
  item budget. Its original leader/descendant and socket evidence was not
  retained, so the cause remains unproven, separate from the earlier Git timeout
  tracked on #6471. One observation-only, unchanged-budget reproduction passed
  in 24.79 seconds: the second leader and Python child stayed live until a
  successful request, then exited with complete stdio and no descendants. That
  reproduction does not settle the original failed gate. The exact pre-rebase
  source does not contain this category fixture; the runner has no alternate-ref
  execution mode, so a same-test comparison against that source is unavailable
  without synthesizing a different baseline.
  The category owner corroborated earlier second-list timeouts and the already
  integrated smoke reduction to four native startups (commit `2d7c6f30b8`,
  evidence comment `6153092854` on PR #6543). Its earlier unchanged-budget pass
  retained neither original process nor socket instrumentation and does not
  clear this integration gate or establish a cause. No additional fixture
  reduction is proposed by the cleanup owner.
  - [ ] Integration coordinator and category owner: retain causal disposition
    of the original native-category failures as a separate tracked diagnostic
    objective. The validated source correction below does not establish their
    original cause. No timeout increase, accepted flake exception or publication
    bypass has been taken.

- **Coupled thin-client import correction (PR #6542):** Direct source tracing
  proved that `client_verification` eagerly imported `coordinator_auth` solely
  for the pure sender-proof HMAC helper, loading FastAPI/Pydantic/native core in
  every client-only process. Three fresh-process regressions reproduced this
  contract violation after actual client/CLI list and cleanup-history HTTP
  calls; pre-fix HMAC vectors and real HTTP event-note proof remained correct.
  Move the unchanged helper into the standard-library-only `control_token`
  module, retaining the old server export and all auth policy. The corrected
  import graph, helper compatibility and event-note portfolio passed 14 tests.
  This is a below-altitude dependency-boundary fix, covered by this PR's existing
  `agent-dispatch` patch changefile, not a competing capability or version bump.
  Original native/Git uncertainties and diagnostic-only crash contamination
  remain explicitly unproven; the original uninstrumented native integration
  portfolio and normal publication gates are still owed.

- **Thin-client source validation after correction:** The original 25-case
  integration selection passed uninstrumented, with unchanged 15/20-second child
  and 30-second item limits. After safely integrating current `dev`, the only
  conflict was the pure-helper relocation alongside already-merged task-scoped
  server auth; retain that auth policy unchanged. The revalidated HMAC/import,
  event-note and task-scope portfolio passed 32 cases, then the same original
  uninstrumented native selection passed all 25 again. The source contract is now
  demonstrated: real client/CLI calls do not import server frameworks, sender
  proofs and the old export remain identical, and actual leased cleanup consumers
  finish within their existing deadlines. This closes the changed source's
  native-validation criterion, not the original uncertain timing/crash cause.
  Clean-room provisioning and live rollout were not exercised: this correction
  changes neither installation nor runtime deployment, and this worker has no
  rollout authorization. Review, CI, merge and release remain coordinator-owned.

- **Ordinary-client identity correction (PR #6542):** Exact published-head CI
  run `38541143072`, job `115784120648`, caught a genuine regression: globally
  wrapping the supervisor client changed the ordinary label-route snapshot's
  client identity. Keep the existing assertion unchanged. Four fail-first
  cases reproduced it, including actual claim/validate/outcome RPC host proofs
  for local, foreign and unknown hosts. The supervisor now retains its supplied
  client and uses its existing host-binding adapter only at housekeeping
  conclusion boundaries; no client lifetime, allocation or auth policy changes.
  The combined ordinary-Bridge/recovery portfolio passed 160 cases, legacy
  supervisor conclusion flows passed 85, and seed/removal protocol tests passed
  48. Host, generation, outcome, session, exact-pair and removal-v4 guards remain
  intact. The additional combined identity/lifetime, auth and real native
  portfolio passed 84 cases under unchanged budgets. This coupled correction
  uses the same PR and existing patch changefile. Original native/Git
  uncertainties remain separate and unproven;
  source publication, fresh review/CI and any release remain coordinator custody.

- **Reviewed cleanup-boundary corrections (PR #6542):** Reproduced an
  equal-clock active/settled competitor that escaped the durable held
  transition, the actual foreground supervise constructor omitting its host,
  and native managed GC deleting reciprocal proof before primary retry.
  Align the inclusive reservation fence and carry the existing registration
  scope alias through the real constructor. Receipt-bearing siblings remain
  protected until exact durable primary unlink; a primary-side guard prevents
  collecting that proof record first. This defines sibling-before-primary GC,
  including crashes after either unlink, without inferring missing proof.
  Existing malformed/raw-pair, owner, identity and legacy-receipt preservation
  remains fail-closed. No supplier/registrar discovery files or adoption state
  are changed. The combined dispatch contracts passed 187 cases, final
  constructor/identity/auth checks 67, authoritative seed/removal checks 58,
  pair fault recovery 11, and ordinary GC 23. An overbroad worktree selection
  exceeded its 300-second aggregate limit; remaining families were then tested
  separately within the unchanged budgets, with no test expectations weakened.
  Existing per-PR patch changefiles cover both plugins. Original timing/crash
  uncertainties remain separate and unproven; fresh publication/review/CI and
  authorized rollout remain coordinator custody.

- **Claim snapshot and reciprocal teardown corrections (PR #6542):** Nine
  fail-first RPC/race cases plus five GC cases exposed stale retry counters after
  recovery, unhashable/accepted non-string envelope fields, receipt-before-YAML
  rollback collection, and well-typed contradictory pair proof. Retired cleanup
  now claims and validates a due reservation before deriving retry state from
  that authoritative snapshot. Legacy and structured counters restart at one,
  while host/generation/outcome guards and business task state stay unchanged.
  Primary GC waits for both sibling artifacts; both conclusion and GC share
  exact reciprocal IDs, complementary roles, physical identities and forward/
  back references. Malformed kind/action values yield the standard typed4xx
  rejection. Targeted dispatch compatibility passed 260 cases, authoritative
  seed/GC/rollback tests 63, and exact pair faults 11 under unchanged budgets.
  No other owner's reconciliation/daemon/supplier files or credentials changed.
  Existing patch changefiles remain; original UNKNOWN diagnostics are not
  reclassified. Fresh source review, CI, merge and deployment remain coordinator
  custody.

- **Default identity and full-poll component ownership (PR #6542):** Two
  fail-first cases reproduced a housekeeping adapter bound before default local
  identity resolution and legacy disposable reconciliation overwriting a pending
  session component. Bind the existing lease-only adapter after resolution and
  leave release-requested component envelopes to the component-aware consumer.
  The actual constructor/full-poll and ordinary client/host-proof checks passed
  five cases, without weakening unknown-host refusal or raw client identity.
  Combined compatibility produced 257 passes and one real failure:
  `test_native_cli_first_touch_preview_apply_and_history[apply]` exhausted its
  unchanged 15-second subprocess budget. This uninstrumented run supplies no
  process-tree or HTTP-stage evidence, so the cause remains UNKNOWN; no rerun,
  waiver or passing-shaped inference clears that gate. The exact failed gate
  and prior diagnostic evidence are retained separately. A previously aborted
  supported upstream sync also has actual conflicts in `launch_seed_state.py`
  and its tests; the original source and framework recovery references remain
  preserved. This below-altitude correction uses the existing patch changefile.
  Publication, conflict resolution and the outstanding native validation remain
  blocked under coordinator custody; original timing/crash uncertainties, review,
  merge and deployment obligations remain open.

- **Additive source integration and retained native uncertainty (PR #6542):**
  A bounded observation inside the actual contained native fixture passed all
  258 selected contracts, using only built-in child import timing and temporary
  parent-side creation, HTTP and pipe telemetry. Five real children completed
  in 3.234–10.734 seconds; import self time was 2.801–9.957 seconds, creation
  31–250 milliseconds and native HTTP handling about 15–16 milliseconds. Both
  captured pipes reached EOF and each child exited successfully. This is fresh
  current evidence, not a retroactive pass, accepted flake or causal explanation
  for the original 15-second timeout. Temporary instrumentation was restored;
  the original UNKNOWN remains tracked.
  Integrating reviewed `dev` additively preserves the published and local source
  ancestors and composes the existing launch-admission guard with YAML-last
  receipt teardown. A real admission-only v4 daemon regression failed first:
  it could accept removal without implementing receipt teardown. Keep the v4
  admission contract for all seed mutations and require v5 for the extended
  removal contract, refusing earlier daemons before dispatch or direct fallback.
  The first full launch-seed selection hit its unchanged 300-second aggregate
  limit; bounded exhaustive families and remaining post-integration validation
  are still owed, with no budget expansion or assertion weakened. Existing
  plugin patch changefiles remain. Publication, fresh review/CI and authorized
  release remain coordinator custody; no deployed state is changed.

- **Explicit-file post-integration validation (PR #6542):** All 73 launch-seed
  cases passed, including the real v5 receipt-removal daemon, unchanged v4
  admission guards, mixed-version refusal, rollback and GC provenance checks.
  Complete cleanup/ordinary-Bridge files passed 151 cases, complete queue and
  supervisor conclusion files passed 24, the legacy supervisor selector passed
  77, complete thin-client/task-scope/category files passed 84, and nine additional
  audited supervisor-selector cases passed. These 418 passes include actual
  uninstrumented native first touches under unchanged budgets; they are fresh
  source evidence, not retroactive clearance of the original UNKNOWN timeout.
  Fatal lint, module ceilings, install contracts, documentation, whitespace,
  large-file and existing changefile gates passed.
  The terminal-conclusion file collected 64 cases but exceeded its unchanged
  300-second runner limit. An exhaustive disjoint plan identified 42 module
  cases and two complementary 11-case reciprocal slices. The first 42-case
  slice again exceeded the runner deadline and containment reported
  user-environment drift during execution, returning exit 125 without rollback.
  Attribution is UNKNOWN; no key exemption, host repair, budget expansion or passing-shaped
  inference is made. The reciprocal slices did not execute, and this 64-case
  gate remains open. No further base chase, publication, review request or
  deployed-state change was attempted. The additive source ancestors and
  backups remain intact; coordinator disposition of the concrete containment
  blocker and unfinished coverage is still required.

- **Same-guard stale lifecycle recovery (PR #6542):** A bounded recorded
  capture exposed the reused-PID fixture exhausting its unchanged 0.1-second
  deadline after stale unlink. Deterministic fail-first regressions then proved
  a source defect: releasing/reacquiring the guard after successful stale proof
  can spend the remaining budget before exclusive claim, and an expired owner
  probe could still unlink the old token. Retain the same per-path guard for
  proven stale cleanup and exclusive-create, check the existing monotonic
  deadline before discharge/claim, and keep PID/birth verification and exact
  release-token fencing unchanged. A reappearing foreign token is preserved.
  No timeout, liveness/ownership permission or containment policy is relaxed.
  Five focused deterministic/native cases passed; the exact independent
  reciprocal `None` crash case passed. Per-case records and exhaustive disjoint
  groups proved all 64 original terminal cases plus three new regressions
  passed exactly once, with every setup/call/teardown successful and each outer
  contained runner returning success. No old quiet dots were credited; the
  longest remaining group took 68.18 seconds within unchanged bounds.
  Shared publication/creation lifecycle callers passed 31 cases, and all 73
  coupled launch-seed/admission/receipt cases passed after the correction.
  The prior 418 passes remain their own exact source evidence. The original
  native/Git timing, old aggregate-deadline and host-environment uncertainties
  are not retroactively cleared or declared flakes. This below-altitude fix is
  covered by the existing plugin patch changefile. Fatal lint, module ceilings,
  install/documentation/whitespace/large-file and changefile gates passed.
  An ancestry-preserving publication path and fresh review remain owed, with
  merge and rollout coordinator-owned.

- **Claimed legacy counters and stale rendezvous owners (PR #6542):**
  Full-poll fail-first cases reproduced both listed attempt 11 and 12 being
  published as held after an intervening operator recovery. Legacy terminal
  reconciliation now obtains and validates its authoritative claimed snapshot
  before retry/exhaustion decisions, retaining the original cleanup policy,
  lease identity and completed business state. Old removal rendezvous entries
  are treated as absent only after positive dead-PID or PID/birth mismatch
  evidence; live, unknown, missing/malformed identity and missing birth remain
  refused. Positive stale-owner cases failed first while four preservation
  controls already passed; eight owner variants are now covered.
  Changed dispatch compatibility passed 177 cases, legacy supervisor selection
  77 and all seed/admission/removal cases 81. Recorded terminal revalidation
  completed 50 unique cases before the next group's first case,
  `test_pair_progress_rejects_raw_pair_keys_after_unlink`, hit its unchanged
  30-second item timeout. Setup passed; the call did not complete. Its captured
  stack was in current-branch Git work and a subprocess reader-thread join.
  Source context identifies `git rev-parse --abbrev-ref HEAD`; actual child
  PID/argv/cwd and exit/pipe custody were not retained, so causality remains
  UNKNOWN. Execution stopped without retry, budget change or publication.
  Seventeen terminal cases and fresh shared-caller revalidation remain owed;
  prior accepted 67/31 and 418 evidence are not relabeled. Separately, published
  source CI run 38662415065 cancelled its agent-index job at the workflow's
  eight-minute limit: two batches completed (298 and 328 passes) before a third
  reached 54%, with no last test ID retained. No CI retry, exception or change
  to another source owner's code is made; coordinator inspection remains open.
  Fatal lint, module ceilings, install/documentation/whitespace/large-file and
  changefile checks passed for this source slice. These static results do not
  discharge the retained runtime failure or authorize publication.

- **Raw-pair phase capture and reject-first ordering (PR #6542):** A bounded
  capture of the exact case, with unchanged execution and 30-second item bound,
  observed sibling/primary fixture creation at 10.203/16.672 seconds and the
  initial valid interrupted conclusion completing at 22.812 seconds. Invalid
  raw retries issued zero Git calls, but primary liveness ran before canonical
  rejection; the later timeout occurred in lifecycle-lock release/import
  scanning. This does not explain or clear the older initial Git-reader wait.
  A deterministic valid-receipt fixture failed first at the forbidden liveness
  call. Read-only reciprocal-shape refusal now precedes native lifecycle work
  and is repeated under the primary record lock; eligible paths retain every
  existing provenance, liveness, ancestry and mutation-time check. All 64
  early/locked raw-value cases passed, preserving all three record/receipt
  byte snapshots, then the unchanged native raw-key case passed in 13.72
  seconds. No earlier timeout is relabeled as passed or an accepted flake.
  The next affected clean-pair control stopped at its original 30-second item
  limit inside native process creation during sibling ancestry verification.
  Setup passed; the call did not complete. No child PID/exit evidence exists,
  so native-cause attribution remains UNKNOWN. The execution sequence stopped:
  remaining pair cases and shared-caller checks are still owed. No broad
  replay, timeout increase, user-environment exemption/repair, role/update
  operation or source publication occurred. All earlier evidence and backups
  remain retained.
  Standard source/module/install/documentation/whitespace/changefile gates
  passed. Further latency-sensitive validation is paused for a stable host
  window; temporal overlap with provisioning is not proven failure causality
  and grants neither a flake designation nor a validation waiver.

- **Current complete cleanup consumer validation (PR #6542):** Resumed the
  same preserved source candidate under coordinator ownership. The original
  affected native clean-pair case passed within its unchanged item deadline.
  All remaining reciprocal-pair cases then passed: 85 cases in 239.13 seconds,
  with the settled clean-pair case excluded rather than counted twice.
  Complete remaining terminal and launch-seed coverage passed 126 cases in
  260.78 seconds. These disjoint contained runs cover both changed Worktrees
  test files without raising item, suite or resource budgets.
  Full changed Dispatch cleanup/ordinary-Bridge/conclusion files plus the
  affected legacy-supervisor selector passed 254 cases in 165.92 seconds.
  Shared lock and creation-request callers passed 48 cases with three platform
  skips. Every invocation returned zero with containment and host-state guards
  enabled; the earlier UNKNOWN timing and environment observations remain their
  own recorded failures, not retroactively explained or called accepted flakes.
  Current install contract, exact three changed source-module ceilings, F/E9
  lint, headless guard, five-library vendor parity and effort structure pass.
  The two pending review corrections preserve authoritative claimed retry
  counts, positive stale-owner proof and all live/unknown/foreign ownership
  refusals; raw reciprocal-shape rejection remains before native work and
  repeated under lock. No business work is restarted and no held reservation
  or production record is changed by this validation.
  Proceed only through normal publication of this existing PR, fresh
  resulting-head review, complete CI and the repository's merge profile.
  Source merge remains separate from release, adopter operation and the
  canonical recovery effort's full completion gate.

- **Current upstream conflict integration (PR #6542):** After normal incremental
  publication, the provider reported a genuine conflict with newer dev. A named
  backup retains the exact published tip and all earlier history. Supported
  synchronization auto-aborted without changing the branch; its documented
  owned-worktree conflict fallback then replayed the existing commits.
  Integration retains newer launch admission and native-transport guards,
  stage/take protocol floor 5, restore/finish floor 4, and cleanup removal
  floor 5. The refusal matrix covers the union of older protocol versions,
  including removal version 4, without dialing an incompatible owner.
  The sender-proof helper remains client-neutral while preserving upstream's
  lazy import, and both source-validation journals remain. No wholesale
  ours/theirs replacement, stash, hard reset or manual remote push was used.
  Pre-integration passing results above are not resulting-head proof;
  the integrated seed/client/supervisor and pair contracts must pass again
  before normal publication, fresh review and complete CI.

- **Integrated candidate consumer proof (PR #6542):** Real daemon validation
  caught removal still advertising version 4 after conflict integration.
  Restored the existing removal client/server floor 5, preserving the newer
  upstream stage/take floor 5 and restore/finish floor 4. The unchanged real
  daemon assertion and complete seed suite then passed all 83 cases.
  Integrated Dispatch cleanup/ordinary startup/conclusion/legacy and thin-client
  proof coverage passed 263 cases. Complete reciprocal terminal coverage passed
  86 cases, and remaining terminal/shared creation/lock coverage passed 93 with
  three platform skips. These disjoint contained selections cover 525 passes
  on the integrated behavior, all within unchanged item/suite/resource limits.
  F/E9 lint, exact changed-source module ceilings, install/headless contracts,
  vendor parity, staging and whitespace gates pass. Earlier pre-integration
  results are not substituted for these current runs. Normal existing-PR
  publication, fresh resulting-head review and complete CI still gate merge;
  no updater, task replay, record repair or deployment is implied.

- **Preserve published cleanup ancestry without guard bypass:** The receiving
  publication guard rejected a rewritten source series containing an older
  additive merge, despite the earlier dry-run accepting the local plan.
  Retained the fully tested candidate and restored the published ancestry on
  a separate local branch in the same owned worktree, merging only the exact
  tested dev base. Composing four owned conflict/document paths from the
  validated candidate made the entire merge index and worktree byte-identical
  to that candidate, with the published tip retained as an ancestor.
  The ordinary merge commit then exposed a real hook limit: 897 Python paths
  and the full staged set exceeded Windows child argv limits.
  The narrowly coupled hook correction batches all four staged guards in
  groups of 32, preserves every path and option separator, and fails the
  commit if any batch fails while still checking later batches. Three real-hook
  regressions fail before the fix; the hook/large-file integration selection
  passes all 41 cases afterward. No hook disablement, no-verify, forced push,
  history/provenance override, dropped validation or deadline increase is used.
  Only this hook, its focused tests and directly related documentation differ
  from the already validated runtime/cleanup tree.

- **Fresh resulting-head cleanup review:** Review of the published additive
  candidate exposes a remaining equal-timestamp gap in the shared legacy
  allocation helper, duplicate claimant keywords in the bound client and
  UTF-8 decoding of rollback evidence. Two actual lease cases, nine explicit
  claimant cases and three raw-byte rollback cases fail before corrections.
  The allocation claim, validation and publication paths now share the same
  ambiguous-or-newer predicate. The host-bound adapter accepts omitted/matching
  claimant values and explicitly rejects a foreign override before I/O.
  An omitted legacy claim body gets a fresh model per request.
- **Byte-exact teardown evidence:** Authorized removal snapshots raw bytes and
  restores them through the existing atomic writer, preserving its per-path
  serialization and replacement retry. Failure at each seed/receipt/YAML
  boundary must retain the original bytes, including invalid UTF-8, before a
  normal retry removes the exact authorized artifacts. Documentation now
  correctly states removal protocol floor 5; stage/take stay at 5 and
  restore/finish at 4. The shared reciprocal identity projection is an explicit
  public helper rather than a cross-module private import.
- **Current validation boundary:** Complete Dispatch cleanup and reservation
  coverage passes all 262 cases in 170.45 seconds. A combined worktree selection
  reaches its existing 300-second aggregate cap without a test assertion failure;
  it is not a pass. Remaining complete worktree consumer groups are being run
  disjointly under their unchanged budgets rather than enlarging the cap.
  Module ceilings, install contract, headless-launch guard and F/E9 lint pass.
  Publication, new exact-head review and complete CI still gate merge; no result
  grants rollout, replay, record-repair or lifecycle-owner authority.
- **Resulting-fix consumer proof:** Complete seed/real-daemon coverage passes
  86 cases in 250.17 seconds, reciprocal terminal-pair consumers pass 82 in
  284.45 seconds, and the disjoint remaining terminal/shared-lock/atomic-writer
  selection passes 51 in 230.77 seconds. Together with the 262 Dispatch cases,
  these are 481 unique current-candidate passes, each contained invocation
  exiting zero under unchanged limits. They do not relabel the earlier combined
  timeout or incomplete provider CI as a pass. Raw-byte rollback exercises each
  actual filesystem failure/retry boundary; real daemon floor/admission and
  reciprocal/GC preservation assertions remain intact. Installer/bootstrap is
  unchanged, so no new full fresh-box provisioning is claimed; hosted Linux and
  complete resulting-head CI remain the external lane, not a production rollout.
- **Validate successful lease envelopes before use:** A subsequent review
  identifies claimed responses with missing or malformed tokens being passed
  directly into validation. Six real-consumer cases fail before correction.
  All three claim consumers now share a protocol check for a nonblank string
  token and authoritative reservation snapshot, with a sanitized warning and
  typed protocol error before validation or effects. The complete Dispatch
  cleanup/reservation selection passes 268 cases in 216.07 seconds.
  The receipt-path projection, like reciprocal identity, is now an explicit
  public module API. Its exact daemon/sidecar/paired-GC consumers pass 23 cases
  in 163.65 seconds. An unnecessarily combined seed/GC run hit the unchanged
  aggregate cap; it remains incomplete, not a pass. No cleanup assertion,
  authority check or deadline was relaxed. Resulting-head review and CI remain
  required, and the parent/native/deployment gates remain open.
- **Canonical partition integration:** Candidate `38443344bb` received a clean
  current-head Copilot review and complete green CI, but normal merge was refused
  because reviewed PR #7144 had independently replaced the smoke scheduling
  contract. With explicit coordination approval, retain that merged canonical
  three-partition workflow and helper, remove this branch's competing two-shard
  runner surface and preserve every cleanup/runtime fix and original published
  commit additively. The managed sync attempt aborted cleanly; its backup and
  recovery references retain the pre-integration candidate.
- **Preserved qualification refusal:** Five actual mutations against the adopted
  canonical step reproduce acceptance of collection/filter flags, failure masking,
  altered partition identity and bypassed file selection. Qualification now
  checks the complete canonical execution step while retaining the upstream
  literal-data inventory and workflow/helper receipt digest. Complete integrated
  qualification, promotion-workflow, runner and partition contracts pass 204
  cases with two platform skips. Cleanup/reservation/supervisor/thin-client
  consumers pass 508 cases in contained execution. Changed reciprocal-terminal
  contracts pass 84 cases; changed seed/daemon/guard contracts pass 44, and the
  disjoint remaining seed contracts pass 42. A combined full native selection
  reached the unchanged 300-second aggregate ceiling and is not counted as a
  pass; disjoint consumers preserve all original item and aggregate limits.
  Commit-hook/large-file consumers pass 35 cases. Fresh resulting-head review,
  all current CI and actual merge still gate source completion; no deployment,
  native custodian or recovery authority follows from these source checks.
- **Complete disjoint native inventory:** The remaining terminal contracts
  pass 56 cases in 131.37 seconds. Thus all 86 seed and 140 terminal items
  execute exactly once across four successful contained selections, rather than
  claiming the earlier combined aggregate timeout passed.

### 2026-10-16 - Focus PR6800 on publication and its own gates

- Preserved the complete bounded investigation in a local commit and backup
  before current-base integration. The publishable follow-up excludes the
  exploratory companion observers; the original companion assertions and
  five-second window remain unchanged.
- Retain complete-set CI sharding and the verified real-process fixture
  contract corrections. The prior native record remains 179 green files with
  23 incomplete; no aggregate timeout, partial pass, or later isolated pass
  becomes a full-suite success.
- The existing PR6487 owns the direct gate-entry repair. Its pinned entrypoint
  delta passed two isolated snapshot checks, not full-PR or current-dev
  validation. No production change from that PR was copied here, and its
  required checks and review remain independent gates.
- Continue with supported current-dev integration, the runner and affected
  source contracts, then this PR's fresh hosted CI and review. No merge
  bypass or runtime rollout is authorized by this publication work.

### 2026-10-16 - Current-dev integration and runner contract proof

- Rebased the owned PR onto current dev with recovery refs and local backups
  retained. Conflict resolution preserves upstream documentation and its
  plugin-relative, unlinked-file selector validation alongside the complete
  file-sharding contract.
- The current runner contracts pass 111 tests with two platform skips. The
  scoped-source, issue-loop and default GitHub-adapter contracts pass 292
  tests through the contained native runner.
- The real nested-pytest regression exposed an undeclared timeout-plugin
  dependency. Both CI contract jobs now install `pytest-timeout` explicitly,
  with a regression checking that declaration; no timeout option was removed.
- These are targeted current-base results, not a complete native portfolio
  claim. The rewritten PR still requires fresh hosted CI and a current review
  before merge.

### 2026-10-16 - Repair the fresh review and CI consumers

- Review4300767243 exposed discarded matching-release evidence in a
  three-snapshot fold. Reduction now retains both claims and releases by full
  ownership identity within the selected occurrence. A matching release closes
  only its owner, never another unreleased claim.
- The 138 added permutation cases cover unrelated, missing and null task IDs,
  two claims with one release, all matching releases, differing labels and
  older occurrences. The coupled selection passes 475 tests; independent full
  source-safety verification passes 196.
- Hosted runner contracts exposed an outdated exact dependency-command guard.
  Its expectation now includes the required timeout plugin without dropping
  the other installer requirements.
- Promotion qualification now derives every sharded smoke row from the runner's
  single literal policy without executing candidate code. Unknown policies or
  recipes fail closed, each required shard must have succeeded, and legacy
  unsharded inventory remains supported. The qualification suite passes 64
  tests; the combined affected CI contract bundle passes 294 with five platform
  skips and 22 exhaustive-lane deselections.
- These repairs require a new published head and fresh CI/review. No cancelled
  duplicate run, partial report, stale approval or baseline observation is a
  merge waiver; runtime rollout remains separately authorized.

### 2026-10-16 - Address current-head Medium review findings

- Each public GitHub provider operation now resolves one fresh scoped child
  environment and explicitly passes it through its subcommands. No provider or
  global token cache is introduced; nested operations cannot replace the
  caller's credentials, and each mutation retains live user/repository checks.
- Scoped command errors expose only an allowlisted command group and numeric
  exit code, never arguments, query/body content, stdout, stderr or credentials.
  Fifteen regressions failed before these changes and pass afterward; the
  complete source-safety/provider selection passes 404 tests through the
  contained runner. Independent targeted verification passes all 15 cases.
- Qualification locates its literal shard default only within the unique
  `_ci_matrix` function. Unrelated policy readers no longer break inventory,
  while missing or ambiguous matrix/default declarations still refuse
  qualification. All 69 qualification tests pass.
- The Low CLI-validation observation does not identify duplicate facts: early
  validation checks the explicit argument mode, while later validation checks
  the resolved target set after exclusions. Three added regressions prove that
  excluding the only target refuses file, manifest and shard requests before
  preparation. The relevant CLI selection passes 18 tests.
- Fatal lint and whitespace checks pass. These repairs still require a new
  published head, fresh review and hosted CI; no merge or rollout is claimed.

### 2026-10-16 - Close the late command-adapter review

- The scoped command adapter now explicitly forwards working directory as well
  as environment and timeout. Compatibility options must match the contained
  runner's fixed capture/headless contract; unknown or conflicting options
  fail before execution rather than being silently discarded.
- A real child verifies the forwarded working directory. Five rejection cases
  cover incompatible capture, text, check, process flags and input options.
  Complete contained source-safety/provider coverage passes 410 tests without
  changing timeouts or process-tree custody.
- CI matrix help now refers to runner policy instead of hard-coding a shard
  count. The actual CLI help and 15 relevant matrix/selection tests pass;
  affected Python lint and whitespace checks pass.
- The previous published head's hosted CI succeeded. That result does not
  validate these later edits: the next published head still needs fresh CI and
  a passing review before merge.

### 2026-10-16 - Make active ownership selection deterministic

- The current review identified insertion-order selection among multiple
  unreleased claims. Selection now uses greatest marker timestamp, then task
  ID, label and canonical payload order. Repeated snapshots of the same claim
  retain the same maximum rather than overwriting it by discovery order.
- The 96 new permutation cases reproduced 52 failures before repair. The
  complete issue-loop/source/CLI/recipe selection now passes 556 tests,
  retaining matching-release and selected-occurrence protection.
- Qualification accepts either annotated or single-target unannotated literal
  shard policy. It rejects nonliteral, chained and duplicate declarations,
  including an annotated declaration followed by a plain reassignment. All 73
  qualification tests pass.
- Native validation exposed one cold readiness failure. Finite file-only
  actors now avoid unrelated process-runtime imports; a poisoned-module
  regression proves readiness without that dependency under unchanged
  deadlines. Import-profiler observations with undrained stderr were discarded
  as observer interference, not presented as causal timing evidence.
- Shared-helper validation also exposed malformed JSON from one actual Windows
  process census. The inventory now uses explicit UTF-8 direct stdout rather
  than host rendering; strict JSON parsing remains unchanged. The original
  offending code point was not captured, so its precise cause is not claimed.
  The native transport round-trips a greater-than-400,000-character field with
  Unicode/control characters. All 12 census, durable-launch and real cutover
  consumer cases pass after the transport change.
- No deadlines, skips, production ownership checks or process-tree custody were
  weakened. Fresh published-head CI and review are still required before merge.

### 2026-10-16 - Preserve schema rejection and explicit fixture encoding

- The proposed claim-key coercion is intentionally not applied: GitHub and ADO
  marker parsing and script-provider decoding already require finite numeric
  timestamps and nonempty string claim IDs/labels before reduction. Coercing
  malformed ownership evidence would weaken that admission contract.
- Fifteen new boundary cases verify null, container, nonnumeric, boolean and
  nonfinite sort fields are rejected before they reach ordering. All pass.
- Workflow and generated-text fixture reads in the runner contract tests now
  explicitly use UTF-8 rather than the host locale. All 17 affected workflow,
  preparation and native artifact-reader cases pass; fatal lint passes.
- The current review's three encoding observations share this one repair.
  Publication still needs a fresh current-head review and CI before merge.

### 2026-10-17 - Preserve safe authentication-stage diagnostics

- Contained command failures surface as `RuntimeError`. Authentication requests
  now wrap that specific runner failure with the same sanitized stage context
  already used for OS/subprocess failures, rather than losing the failed stage.
- Ten regression cases cover injected runtime errors and the default adapter's
  unavailable, OS-error and subprocess-error paths at account selection,
  credential retrieval and identity verification. Each error suppresses its
  original context and stops before any forge mutation.
- The complete source-safety suite passes 339 tests in 22.70 seconds; fatal
  lint and whitespace checks pass. No timeout or fail-closed behavior changed.
- The separate Copilot agentic-review startup check timed out; that advisory
  failure is not a required-CI verdict. Current-head CI and a passing review
  remain separate gates; neither is bypassed.

### 2026-10-17 - Sanitize forge capture failures at the adapter boundary

- The command adapter also sanitizes expected runtime failures from contained
  capture during a forge operation, outside authentication probes. Three
  regressions verify runtime, OS and subprocess errors cannot expose their
  original detail or exception context.
- Provider operations and credential probes now share the same existing
  30-second constant. A real adapter-boundary assertion retains that exact
  value; no timeout becomes configurable or increases.
- Complete source-safety/provider coverage passes 535 tests in 66.42 seconds;
  fatal lint and whitespace checks pass. Publication still owes fresh
  current-head CI and review.

### 2026-10-18 - Resume only the bounded source-safety PR

- The operator released the waiting-only hold for the original #6800 and
  authorized current-base repair, focused source/real-child validation, current
  CI and genuine full review. Phase 6 remains the only active slice here;
  the overall effort stays Active.
- Current dev's coverage-selection and CI partition policy supersedes this
  PR's older four-shard implementation. By explicit operator choice, remove
  only that obsolete CI delta and retain current dev's workflow, runner,
  selection and qualification policy without changing coverage or budgets.
- Preserve the prior source and recovery refs, ownership snapshots and optional
  child-only repository authentication. Release/adoption, fleet operations and
  runtime changes remain outside this authorization.
- Current-base issue-loop/source/CLI/recipe coverage passes 584 tests, including
  real child-authentication and timeout custody contracts. Native census,
  durable-lifetime and exact cutover consumers pass 12 tests. Fatal lint,
  module-size and install-contract checks pass; CI workflows, tooling and
  pipeline policy are byte-identical to the current dev base.
- Managed publication preview retains the original PR and its exact recorded
  head lease. Neither the older successful CI attempt nor the earlier
  agent-start-timeout Lite review replaces current-head CI and a genuine full
  review after this rebase.

### 2026-10-19 - Resume the original bounded supervisor PR

- The original #6487 source owner resumes its retained, clean privacy-fix
  commit rather than opening a competing branch or changing a live runtime.
  Command creation/communication errors now expose a fixed unavailable message;
  declaration discovery logs only its error type, without raw text or traceback.
  Eight fail-first controls retain cold indeterminate and confirmed-cache
  behavior. Existing timeout, unknown-outcome and non-replay fences remain.
- Preserve both the published tip and the private fix through a local backup
  and additive integration of reviewed dev `834dfd278dc24ff53f58a0d96243f50c9266f416`.
  Retain both journals at their append-only conflict and preserve upstream's
  published/degraded catalog assertions in independent cold-CLI items. Every
  item still executes one actual cold process within the unchanged 20-second
  subprocess and 30-second item ceilings. Current dev's canonical CI partitions
  and workflow policy are retained, not replaced by another scheduling patch.
- The integrated bounded-receipt, blocking-boundary, supervisor master/health
  and worker-instance cohort passes 280 cases in contained execution. Source
  module ceilings and install contracts pass. Full resulting-head review,
  every current CI gate and actual managed merge remain required. Source
  publication grants no host repair, deployment, role/admission, native
  custodian/adopter or queue-recovery authority.
- Remaining scheduling, bounded recovery, managed companion, native gate,
  companion and retention consumers pass 228 cases with two platform skips
  in 229.28 seconds. Together, the two disjoint contained selections execute
  508 affected tests successfully. Headless-launch contracts pass 93 cases
  and the actual guard retains all 13 adopters; the only contribution delta
  in its allowlist follows the existing one-shot helper to its extracted module.
  Contribution-scoped whitespace is clean. Neither these tests nor an older
  Lite review substitutes for the new full review and all current hosted CI.

### 2026-10-19 - Full-review managed selection and privacy follow-through

- Full Balanced review `5576387524` on #6487 identifies four actual Medium
  defects. All four reproduce first through the real consuming boundary:
  process-receipt availability discloses its path, cold selected-state fallback
  blocks a sibling after the deadline, malformed selected state escapes as
  `ManagedRuntimeError`, and the outer serve log discloses a chained declaration
  error despite the inner warning being sanitized.
- Process-receipt availability now uses the common sanitized metadata reader.
  Provider and selected-state observations share bounded, authority-keyed
  future handling in the existing provider pool. Pending selected reads stay
  in shutdown accounting; withdrawal and authority changes invalidate their
  result. Managed metadata errors withhold only that companion. Expected
  indeterminate serve failures log a fixed category without a traceback while
  preserving exception causality for callers.
- Four fail-first consumer cases and two real pending-future authority controls
  pass. Complete affected master/health/receipt/blocking/worker-instance
  consumers pass 286 cases; remaining native lifecycle, scheduling, retention
  and recovery consumers pass 228 with two platform skips. All selections
  retain their original containment and deadlines.
- Required CI also exposes the existing #7020 native POSIX PowerShell purge
  timeout. Authorized native investigation reproduces a successful unchanged
  test, then distinguishes actual type compilation from millisecond native
  identity reads in diagnostic fixtures. A separate local post-body filesystem
  wait is not the hosted identity-stall cause. No production repair is justified
  by that different signature.
- The existing fixture now frames the actual module-qualified `Add-Type`
  invocation, preserving all source behavior, physical-home refusal,
  sentinel/lock/stage assertions and the original twenty-second child bound.
  Its real native test and original-exception diagnostic control both pass.
  This is additional phase evidence for #7020, not a claimed fix, accepted
  flake, retry permission or CI waiver. Fresh resulting-head full review and
  every required hosted gate remain blocking; native recovery and adoption
  obligations are unchanged.

### 2026-10-19 - Address body-only full-review findings

- Review `5577100006` on #6487 labels its inline list empty but contains four
  Medium findings in its overview body. Treat those as blocking, not as a
  clean verdict. Two raw operation/scheduling log cases, an unmanaged orphan
  alongside an unrelated malformed transition group, and the second cold
  selected-state read all fail before correction.
- Every receipt reconciliation log now exposes only its exception type.
  Generations with no managed selection or process-snapshot evidence do not
  discover unrelated managed groups before orphan retirement; all managed
  selection validation, root-pin checks, exact generations and unknown-outcome
  fences remain. Managed recovery carries the accepted persisted snapshot
  instead of synchronously reading it again. Later cold reads use the bounded
  observation path.
- The integrity-blocking fixture now publishes a real managed selection before
  blocking validation, rather than presenting an unmanaged receipt and mocking
  its selected state. All original progress/deadline/unknown assertions remain.
  The materializer-authority test still proves rejection and preservation of
  the existing process, while expecting the intentionally classified error log.
  Initial coupled failures remain failed runs, not passing evidence.
- Complete disjoint affected cohorts pass 343 receipt/master cases in
  80.11 seconds and 175 native lifecycle/retention cases with two platform skips
  in 201.59 seconds: 518 successful cases with unchanged containment budgets.
  The PR description now names the actual installer/stable-launcher owner,
  singleton-handoff exception, managed-write barrier, bounded probe shutdown
  and missed-grace/native-custody limitations. It links the owning lifecycle
  contract and records the graceful-cutover checklist assessment.
- Natural predecessor CI passes the native #7020 guard independently. That is
  not a root-cause fix or acceptance of the earlier failure; the authorized
  native investigation remains open. New source review and all resulting-head
  CI still gate merge, with no runtime rollout or broader recovery authority.

### 2026-10-19 - Preserve late readback and incomplete observation semantics

- Full Balanced review `5577661133` contains three Medium findings in its
  overview despite the empty-open-issues banner. Eight fail-first cases
  reproduce incomplete receipt observations, publication starvation after a
  slow read, and raw withdrawal errors; a known-empty observation passes.
- Any invalid or unreadable operation record now makes the whole optional
  progress observation unavailable, rather than reporting empty or partial
  inventory as complete. Per-identity scheduling and immutable evidence remain.
  Withdrawal warnings retain pending custody and expose only the error type.
- A detached publication ledger retains completed readback rows across the
  existing deadline. A later batch consumes them before another RPC, invalidates
  changed targets, and consumes each observation before mutation. An unknown
  old target settles before a changed declaration. Deterministic four-second
  reads under a three-second budget prove eventual publish/withdraw/readback;
  an event-blocked real executor proves bounded polling and single-writer harvest.
- The no-second-selection-read regression now asserts the exact single-read
  contract during real recovery, rather than putting unrelated complete
  filesystem recovery beneath a half-second observation timeout. Production
  call deadlines and separate blocking-boundary timing assertions are unchanged.
  Initial coupled failures are recorded as failures, not passing evidence.
- Current dev integration preserves reviewed #7126 task-authority ownership
  alongside queued native-generation/cycle progress. The receiving authority
  fixture now implements the real queue interface and checks both identities.
  The combined consuming-boundary cohort passes 321 cases; preceding managed
  recovery coverage passes 296 with one platform skip, and independent receipt,
  health and native-lifecycle consumers pass 84 with one platform skip. These
  overlapping selections are not summed as unique coverage.
- Install contract, contribution ceilings, fatal lint and whitespace pass.
  This is source-level behavior, not a new installer or provisioning path.
  Existing native Windows subprocess/filesystem coverage remains distinct from
  the still-open native POSIX #7020 cause and production adoption. Fresh full
  resulting-head review and every required CI check remain merge gates.

### 2026-10-19 - Close concurrent metadata and authority-reversal defects

- Full predecessor review `5578395994` identifies concurrent receipt/selection
  publication, submission rejection after a pending milestone, interrupted
  receipt-only root-pin withdrawal and outer inventory logging. Current-head
  review `5578499365` additionally identifies list-valued generation metadata
  and A-to-B-to-A authority reversal. These are real blocking findings,
  including the predecessor's body-only issues.
- A native Windows held-reader control reproduces `WinError 5` during atomic
  replacement. Read/write/delete sharing alone still fails with `os.replace`;
  canonical shared readers plus native `ReplaceFileW` for an existing target
  succeed. The same binary reader covers strict metadata hashing. Parser,
  digest, inode and concurrent-change rejection remain intact. Actual managed
  publication preserves its ready process with either a held parser or hash
  reader; the stale reader reports indeterminate metadata.
- The canonical plugin-activation library owns this portability seam. Its one
  physical customizing-copilot source copy is synchronized; all eight consuming
  plugins/standalone packages have patch changefiles, including the existing
  Dispatch entry. Windows state, restoration, installed-inventory, permission,
  bounded retry and native subprocess tests remain in force. Fault fixtures
  inject at the explicit replacement/read boundary, not an unused platform API.
  Project fixtures use the receiving platform's actual key, including WSL.
- A submission error cancels the separate execution admission before recording
  an exact unstarted generation as superseded. A callback queued before a late
  submission failure cannot subsequently mutate. Already-started execution
  remains tracked with its original durable outcome. Interrupted managed
  process-receipt-only generations withdraw their previously validated root pin
  after retirement; corrupt metadata or an unrelated pin is never accepted.
- Operation generation receipt/selection values must be objects or null even
  with a matching digest. A changed uncancellable observation loses its retained
  authority immediately, so A-to-B-to-A cannot reuse the first result. Outer
  receipt polling logs only the exception type, preserving the underlying cause.
- Two natural current-head CI jobs fail before tests because the coverage
  baseline names the prior unparameterized headless-pool CLI case. That real
  default case now retains its exact node; the central/local cases remain
  separately parameterized through the same assertions. No selection item,
  coverage policy, threshold, guard, retry or deadline is removed.
- Disjoint contained Dispatch cohorts pass 442 master/receipt/authority/catalog
  cases, 205 managed/native/retention cases with three platform skips, and all
  three CLI cases: 650 successful cases. The complete canonical shared library
  passes 139 cases on native Windows and the same 139 on native Ubuntu/WSL.
  Install contract, exact vendored-source sync, fatal lint and contribution
  ceilings pass. Initial failing controls remain documented failures.
- First state publication and native subprocess consumption run in fresh
  test-owned directories. No installer/provisioning flow changes or system
  installation are performed; a separate full installation clean-room run is
  not claimed. Hosted current-head CI and a fresh full review remain required,
  and #7020's independent native causal investigation remains unresolved.

### 2026-10-19 - Verify exact future and deadline interleavings

- An actual inventory future now fails between receipt priming and the second
  poll, proving the outer handler preserves classified logging at that precise
  boundary. A submission error after real worker entry retains the running
  execution future in receipt/shutdown accounting until its original operation
  completes.
- The held-read publication control explicitly waits until the original
  monotonic deadline before releasing the RPC and verifies a deferred readback
  with no write. A caller wake alone is not used as proof that the read crossed
  the deadline. This strengthens the test precondition without extending the
  production or test-operation budget.
- The complete two-file receipt/recovery cohort passes 113 cases; both new
  future controls are additional to the preceding 650-case implementation proof.
  Fatal lint and whitespace pass. No production code changes in this follow-up,
  and fresh full resulting-head review and all hosted CI remain outstanding.

### 2026-10-19 - Remove partial replacement and denied-scan ambiguity

- Full predecessor review `5578929112` identifies two further real Medium
  defects: documented `ReplaceFileW` error 1176 can remove the destination
  before caller cleanup deletes the staged candidate, and `Path.glob` can turn
  denied directory enumeration into a successful empty receipt observation.
  Both failures reproduce before correction; missing/empty controls pass.
- Windows publication now uses one `FileRenameInfoEx` atomic rename with
  replace-if-exists and POSIX retained-reader semantics, not multi-step
  `ReplaceFileW`. Refused native 1175/1176/1177 outcomes retain the candidate
  and expose its path on the original exception, without retrying the write.
  Unsupported atomic capability fails before mutation. Actual retained-reader
  and UTF-16 path cases include supplementary and unpaired native code units.
- Progress and inventory use directory enumeration that propagates scan errors.
  Missing and empty remain known empty; denied access is indeterminate with
  classified logging and unchanged operation bytes. The same contract applies
  across every newly introduced receipt scan.
- The coupled accepted-queue cancellation case also reproduces a raw
  `CancelledError` and stranded pending record. A captured local execution
  generation now survives until its proven-unstarted settlement is recorded.
  Failed settlement retains that proof; a committed response loss is read back
  without another write. A cancelled readback or newer operation cannot gain
  unstarted authority. Abrupt owner loss still retains the unknown fence.
- Direct updater consumers expose a real standalone-import regression in the
  extracted native helper. The helpers remain in the existing stdlib-only
  `state.py`, below its source cap; the two newly introduced helper modules are
  removed. The original shipped-source loader test is unchanged and passes.
  Its restoration fault fixture now injects at the actual replacement boundary,
  retaining command non-replay, retry, timeout and permission assertions.
- Complete affected Dispatch implementation passes 455 master/receipt cases,
  205 managed/native/retention cases with three platform skips and three CLI
  cases: 663 successful cases. All 74 direct updater consumers pass. After the
  standalone relocation, cold import order and both actual held-reader phases
  pass; the complete canonical library passes 146 cases on native Windows and
  142 on native Ubuntu/WSL with three Windows-only skips.
- Canonical and actually shipped state files also run in separate fresh
  `python -I` processes from empty test-owned directories, with no package
  dependencies available through the working directory or user site. Both
  perform real first publication/readback under the original child bound.
  This is isolated first-touch evidence, not a system installer or live rollout.
  Fresh full resulting-head review, all current CI and actual merge remain
  outstanding; #7020's independent native cause and adopter gates remain open.

### 2026-10-19 - Retain receipt fences on denied selection-pin scans

- Full exact-head review `5579503395` finds one further Medium defect:
  `Path.glob` suppresses a selection-directory `PermissionError`, so a surviving
  durable root pin can be mistaken for confirmed withdrawal. Both real
  readback and retirement consumers reproduce the incorrect completion before
  correction. Generic I/O failures and genuine empty/missing controls are
  retained alongside the denied-access cases.
- The metadata-only pin scan now uses explicit directory enumeration under
  the existing root lock and deadline. Only a genuinely missing directory is
  accepted without completing a scan; access and I/O errors propagate, retain
  unknown operation authority and prevent receipt availability. Surviving pins
  and cells are untouched. This is a below-altitude correction to the existing
  fail-closed contract, with no new recovery or deployment authority.
- The complete bounded receipt, scheduling, managed retention and managed
  companion cohort passes 243 cases with two platform skips in the contained
  Windows runner. These include actual journal, pin and availability consumer
  assertions, not just a directory helper mock. No install/bootstrap code
  changes, so no additional installation clean-room or external rollout is
  claimed; native library first-touch evidence remains unchanged. Fresh full
  resulting-head review, current hosted CI and actual merge remain required.

### 2026-10-20 - Invalidate selection observations before changed provider resolution

- Full exact-head review `5579673540` identifies a further body-only Medium:
  pending managed selection A can survive inactive provider B because B never
  asks for a selection. Returning to indeterminate A then consumes the pre-B
  result. Four desired-state regressions reproduce stale recovery through the
  actual provider/future path, covering inactive and invalid B plus read
  completion before and after returning to A.
- Declaration processing now invalidates changed-authority selection reads
  before provider resolution, independent of its result. Cancellation never
  implies that a running read stopped; unfinished futures retain shutdown
  accounting. A later fallback waits for the old read to finish and requires a
  fresh observation. No deadline, restart or ownership authority is broadened.
- The complete blocking-boundary, bounded-recovery, supervisor-daemon and
  managed-companion cohort passes 254 cases with one platform skip. This is an
  incremental correction, not an installer or provisioning change. Fresh full
  resulting-head review, current CI and managed merge remain required.
- The bounded #7020 physical-home test/diagnostic repair handoff is now
  explicitly accepted by `greased-cylinder` in issue comment `6280945017`,
  before edits. That work excludes this PR's supervisor/state/receipt hunks.
  Native workspace commands and actual phase evidence are recorded in comment
  `6281119961` after direct and relay transport refused delivery. Acceptance
  names the separate repair owner; it does not claim the native cause fixed,
  permit a timeout increase or waive any failing check.

### 2026-10-20 - Keep optional progress admission failures off the master path

- Full exact-head review `5579836787` identifies a body-only Medium:
  optional progress-worker submission can raise during discovery or after unit
  mutation, aborting reconciliation before its summary. Four fail-first
  regressions reproduce actual construction refusal and real executor thread
  start refusal at both boundaries.
- Progress admission now retires the failed pool without waiting, cancels its
  queued work, clears its active reference and emits the existing classified,
  throttled warning. Latest progress/final-status frames remain coalesced for
  a later healthy retry. Discovery and completed unit mutations still reach
  the caller; optional I/O does not acquire lifecycle authority.
- The tests inspect the actual executor's queued futures after start failure,
  prove they are cancelled, and verify fresh-pool progress and final-status
  publication plus the next desired-state cycle. The complete blocking,
  bounded-recovery, daemon and health cohort passes 207 cases. No installer,
  provisioning or external rollout behavior changes. Fresh full resulting-head
  review, all current CI and managed merge remain required.
