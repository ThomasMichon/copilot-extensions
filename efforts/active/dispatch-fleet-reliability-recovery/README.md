# Dispatch fleet reliability and recovery

- **Slug:** `dispatch-fleet-reliability-recovery`
- **Repo:** copilot-extensions
- **Branch(es):** Independent implementation worktrees; sequential merges.
- **Created:** 2026-10-12
- **Status:** Active
- **Vision:** agent-dispatch durable results, task/session attachment history and
  recoverable goals; plugin-services bounded work and single-owner lifecycle;
  registry-resolved configuration.
- **Umbrella issue:** #6466
- **Sub-issues:** #6467, #6470, #6471; coordinate #6185 and #6407.

## Guiding Intent

Keep dispatch work navigable without discarding audit evidence, bound supervision
at its real blocking boundaries, and recover terminal housekeeping separately
from completed business work. Repository-owned declarations identify registered
repositories and relative resources rather than duplicating machine-local roots.
Preserve the owning ground-layer, installer and configuration authorities.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Coordinator | Own the plan, integration, merge order and completion gate | Plan and integration worktree |
| Category implementer | Task metadata, producers and category-specific views | Dedicated implementation worktree for #6466 |
| Supervisor implementer | Bounded receipt reconciliation and progress recovery | Dedicated implementation worktree for #6467 |
| Registry implementer | Named repository/resource bindings and validation | Dedicated implementation worktree for #6470 |
| Existing scalar-repair owner | Preserve ownership of #6407 and its existing #6421 PR | Existing PR and its current source worktree |

## Coordination

- **Topology:** Independent per-slice worktrees and PRs; merge serially and
  reconcile actual content conflicts before publication.
- **Host (owns PRs):** Coordinator; implementation participants may prepare
  their own slice PRs, but do not merge concurrently.
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
  - [ ] Integration coordinator and category owner: resolve the original native
    category failure's disposition before accepting the integrated source and
    normally publishing PR #6542. No timeout increase, fixture change, accepted
    flake exception or publication bypass has been taken.

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
