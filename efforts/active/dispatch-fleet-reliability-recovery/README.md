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
- **Umbrella issue:** #6466
- **Sub-issues:** #6467, #6470, #6471, #6793; coordinate #6185 and #6407.

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
- **Lifecycle owners:** Compose #5301 and #5655; do not add a competing watchdog,
  cleanup daemon, reservation store or repository registry.

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

- [ ] Review this bounded amendment before runtime implementation of #6793.
- [ ] Accept the adapter's verified, standalone claimed snapshot after an
  in-place edit; preserve author, schema, occurrence and release-conflict
  validation. Do not infer relinquished ownership from a missing local task.
- [ ] _(Agent-recommended implementation detail)_ Add explicit GitHub-only
  `forge.auth: repository`, retaining omitted/`ambient` standalone behavior.
  Resolve the optional agent-worktrees sibling through its existing invocation
  helper, select the repository account and obtain credentials through its
  existing command boundary, then run GitHub operations with a child-only
  environment. Refuse missing siblings, mismatches and credential fallback
  before mutation. Never log or persist token output.
- [ ] Renew scoped credentials on demand; preserve immediate pre-mutation
  identity verification and avoid process-global environment changes.
- [ ] Document the opt-in and unavailable-integration failures; add a changefile,
  land through normal review/CI, and retain release/adoption as separate gates.
- [ ] Leave task transfer (#6189), PR-ground-truth reconciliation (#2798),
  broader account-aware operations, and unrelated recovery owners unchanged.

## Validation Plan

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
- [ ] Round-trip an actual provider comment edit through fresh discovery and
  selection, including a second empty coordinator; a trusted claimed snapshot
  remains excluded and is not released merely because its task is absent.
- [ ] Keep malformed/untrusted markers rejected and conflicting releases from
  clearing a claim. Preserve ordinary reserved/claimed/released histories.
- [ ] Exercise scoped account selection, renewal, unavailable sibling, mismatched
  account, failed/degraded token retrieval and child-environment isolation
  without exposing credential bytes. Include a real subprocess contract.
- [ ] Run the same declaration's ambient mode without the optional sibling.
  Record native Windows results and available Linux CI separately from live
  adopter proof; a mock or source pass is not installed-consumer evidence.
- [ ] Resolve or explicitly transfer every plan/validation item before Done.

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

### 2026-10-14 - Bounded source-safety proposal

- #6793 records two demonstrated source blockers. In-place marker editing
  removes the predecessor that the current reducer requires for a claimed
  snapshot; the GitHub adapter also has no source-scoped authentication opt-in.
- This amendment closes existing dispatch ownership and agent-fabric
  account-aware-operation intent. It adds no coordinator, credential store,
  supervisor, transfer mechanism or mandatory sibling dependency.
- Implementation remains gated on this amendment's review and merge. Existing
  recovery, release and broader identity-contract ownership is unchanged.

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
