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
