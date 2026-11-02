# Effort Agenda Companion

- **Slug:** `effort-agenda-companion`
- **Repo:** copilot-extensions
- **Branch(es):** independent, reviewed per-slice PRs against `dev`
- **Created:** 2026-10-28
- **Status:** Done
- **Vision:** [efforts](../../../../visions/plugins/efforts/README.md)
  `optional-effort-agenda`, `nested-effort-continuations`,
  `cooperative-agenda-movement`; vision-extending
- **Umbrella issue:** [#9377](https://github.com/ThomasMichon/copilot-extensions/issues/9377)
- **Sub-issues:** carve bounded implementation issues after plan review

## Guiding Intent

Give agents an optional, efforts-owned process aide for following a substantial
effort without losing its agenda during long sessions, handoffs, or prerequisite
detours. A coarse graph describes the current work package, its exit condition,
and permitted advancement, branch, loop, or reset. A saved continuation makes a
child effort a detour with a return point, rather than a replacement objective.

The stateful agenda belongs to the worktree's continuing agent and lives near
its journal, accumulating assigned efforts, tasks and asides rather than
belonging to a single effort or session.

The companion belongs in the **efforts plugin**, not agent-dispatch. Optional
local CLI helpers may scaffold an agenda and inspect or maintain its cursor;
neither a scheduler nor another plugin is required. The effort README remains
the shared contract and completion authority. This is cooperative guidance, not
a claim to constrain every action an agent can take.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Planning author | Capture inception and reviewed planning amendments | This effort and #9377 |
| Implementation owner | Drive guidance, spike, helper decision and the full completion gate | #9377 and this effort's Plan/Validation/Journal |

## Coordination

- **Topology:** independent per-slice PRs after the inception plan is reviewed.
- **Host (owns PRs):** the participant claiming that slice on its issue.
- **Delegates:** none assigned; do not infer implementation ownership from this plan.
- **Handoff:** update the Plan and Journal with results, remaining gates and the
  next claimed slice; keep the canonical effort here.
- **Completed assignment:** guidance, paired spike, the helper decision and
  qualification are delivered as reviewed source. Normal `dev` -> `main`
  promotion remains with #3336; no installed rollout was authorized or claimed.
- **Adjacent owners:** [Worktree/Effort Railroad Binding](../../../active/worktree-effort-railroad-binding/README.md)
  and #3581/#3582/#3583 retain title, checklist-derived binding, and mid-session
  reminder work. No dispatch lifecycle, claims, or worktree-binding redesign is
  transferred into this effort.

## Context

- [Inception exchange](inception-transcript.md) preserves the initial request,
  substantive investigation proposal, and the ownership correction. The later
  operator direction supersedes the proposal's worktree-owned runtime placement
  and suggested dispatch integration.
- [GraphCode](https://github.com/scgopi/GraphCode/) supplies useful principles:
  explicit nodes and guarded edges, bounded cycles, and legible execution state.
  Its [composite state rollup](https://github.com/scgopi/GraphCode/blob/4088986d3e6d8ab4a938caae0d75f6b9f7e7d938/GraphcodeKit/Sources/GraphStore.swift#L1624-L1636)
  is not a saved parent continuation. Borrow principles, not its runtime or a
  dependency on it.
- The [efforts plugin](../../../../plugins/efforts/README.md) already supplies
  planning and continuity without a runtime service. Existing
  [effort-driven session loops](../../08/28%20effort-driven-session-loops/README.md)
  establish durable effort ownership, not graph or child-return semantics.
- Current worktree orientation uses a manually declared binding slice.
  Checklist-derived slice selection is intended by the vision and remains
  adjacent work under #3581; this effort must not assume it has shipped.
- Pattern fit: [architecture principles](../../../../docs/patterns/README.md),
  especially independent installation, graceful optional composition,
  right-sized surfaces, cross-platform parity and proportionate safeguards.
  No new daemon, broker, graph service or mandatory sibling dependency.

## Request

**Summary of settled intent:** extend the efforts plugin with a voluntary
structured agenda companion. Keep it a process aide, outside agent-dispatch;
consider optional CLI scaffolding and cursor support. Use the investigation as
the inception for this implementation effort. Once initial guidance is usable,
compare concurrent attempts at the same task in separate worktrees, with the
extra agenda layer present in only one arm.

The operator has now admitted execution of the whole goal, not another
planning-only slice. Agents should progressively complete assigned efforts,
bound detours and serendipity, understand their work stack and exit criteria,
and always derive their next concrete action.

Execution direction, verbatim:

> Great. Start working on this. Goal is that through these additions, agents will be able to focus on iterating down their assigned efforts, detouring in limited capacity when appropriate, and always be able to self-answer the question of "what do it do next?" For example, you keep stopping after each thing I say, which means we failed to assign an agenda based on my goal: taking inspiration from graph code, add a companion set of guidance, perhaps aided by lightweight CLI tooling, which helps an agent impsoe structure on itself when driving one or more efforts within its worktree lane, ensuring that agent progressively completes tasks, understands the exit criteria, and understands its "work stack" when following detours or dealing with serendipity.

Latest placement direction, verbatim:

> I would expect the stateful agenda to exist somewhere near the "worktree journal", as this is an agent's specific agenda, built from the efforts, tasks, random asides, etc. that it may wind up getting assigned during its existence.

The [full inception exchange](inception-transcript.md) distinguishes operator
requirements from agent recommendations and records publication-only omissions.

## Plan

The requested capability and ownership are fixed above. The decomposition and
specific mechanisms below originated as **agent-recommended** implementation
work, admitted by the operator and delivered through reviewed source. Current
usage belongs to the plugin's skill/docs; this effort records what landed.

### Phase 0 - Inception and reviewed scope
- [x] Land the canonical effort, inception exchange, active index and matching
      efforts vision extension through normal review.
- [x] Record the reviewed proposal and explicitly admit the next implementation
      slice on #9377 before starting implementation.

### Phase 1 - Companion contract and manual process aide
- [x] Define a small optional companion format for coarse nodes, stable
      Plan/Validation references, exit criteria, named outcomes and permitted
      transitions, including loop bounds and explicit exhaustion behavior;
      do not copy the plan or maintain a second completion checklist.
- [x] Separate reusable effort/board templates from the worktree-local mutable
      agenda, execution position, attempts, evidence and saved continuations. Define
      cursor discovery/resumption without requiring a managed worktree.
- [x] Keep the agent-specific agenda, cursor and assignment queue beside the
      worktree journal through an explicit owner/adopter locator; retain them
      across sessions and reference multiple canonical effort/task sources.
      Do not put the live agenda in each effort or invent a journal provider.
- [x] Specify nested call/return, blocked-child handling and return-time
      revalidation; distinguish a blocking child detour from parallel delegation.
- [x] Define guidance reconciliation so an adopted position or child detour is
      not redirected by default checklist/binding reminders, including when a
      bound worktree has no agenda projection. Preserve existing reminder
      ownership and standalone operation.
- [x] Ship efforts-owned authoring and usage guidance with a synthetic worked
      example. Ordinary efforts and manual agenda use stay first-class.

### Phase 1S - Paired guidance spike
- [x] After a reviewed initial guidance candidate is usable, prepare the
      [paired spike](paired-spike.md): identical task/starting revision,
      independent worktrees and sessions, treatment-only agenda guidance.
      A CLI toolset or completed Phase 1 rollout is not a prerequisite.
- [x] Predeclare the task, prerequisite detour, task-level acceptance checks,
      matched agent configuration, finite budgets and scoring before either
      arm starts; preserve the ordinary baseline's effort tools.
- [x] Run the two attempts concurrently, verify actual guidance exposure and
      collect independent outputs without cross-arm steering or publication.
- [x] Compare correctness, parent-task resumption, scope drift, false completion
      and available cost observations. Record the first pair as exploratory
      and use its findings to revise guidance or choose bounded replications
      before committing to a helper implementation.

### Phase 2 - Optional helper decision and bounded implementation
- [x] Evaluate whether a lightweight efforts-owned CLI adds enough objective
      cursor maintenance to justify it; record the decision and invocation/state
      ownership. Do not silently convert the plugin into a service.
      [Initial spike findings and the read-only helper decision](spike-results.md)
      admit next/check inspection only; no transition writer is needed for v1.
- [x] If admitted, implement only the agreed scaffolding, inspection and cursor
      operations, including explicit transition and child-return validation.
      Otherwise document the manual procedure and its weaker guarantees.
- [x] Handle duplicate operations, stale cursors, changed definitions and
      interrupted returns with proportionate revision checks and reconciliation;
      do not claim exactly-once external effects.
      The v1 helper is read-only: repeated inspections do not write, stale
      snapshots/definitions are diagnosed, and interrupted manual edits still
      require reconciliation rather than an invented transaction guarantee.

### Phase 3 - Qualification and adoption guidance
- [x] Qualify the standalone manual path and any admitted helpers using the
      Validation Plan; document unsupported guarantees explicitly.
      [Qualification and adoption boundary](qualification.md) records the
      measured cases and deliberately unclaimed guarantees.
- [x] Update affected plugin guidance and truthful documentation, add required
      changefiles for payload changes, and land implementation through review.
- [x] Record source qualification and provide opt-in adoption and recovery
      instructions. No live rollout was authorized or performed.
- [x] Deferred to `#3336 (existing dev-to-main promotion pipeline)`: Main release
      qualification/promotion. Source is merged and qualified; the observed
      release snapshot does not contain the helper. Normal publication is the
      pipeline's existing responsibility, not a new unowned agent task.
- [x] Resolve or explicitly transfer every Plan/Validation item before marking
      this effort Done; an inception PR is not capability completion.

## Validation Plan

These are **agent-recommended acceptance obligations**, not claims of tests
already run or a requirement to publish execution artifacts.

- [x] A standalone adopter can author and follow a coarse agenda with only
      efforts installed; no agent-dispatch, agent-worktrees, daemon or automatic
      turn driver is needed. Non-opted-in efforts keep their existing behavior.
- [x] A node identifies its canonical plan/validation references, objective,
      exit criteria and allowed outcomes without duplicating completion status.
      Evidence distinguishes measured results from attributed agent judgment.
- [x] Manual instructions explicitly reject advancement without exit evidence.
      The read-only helper reports criteria/declared moves without certifying
      them and implements no branch/reset writer; malformed references and
      exceeded budgets are refused. Any future transition writer needs its own
      exit-evidence gate, not permission inferred from a successful inspection.
      Failed, successful and no-progress cycles all honor declared bounds;
      exhaustion reaches an explicit stop/replan/steering outcome, not drift or
      silent completion.
- [x] A synthetic A -> B -> C -> B -> A scenario preserves the root objective
      and correct return points. A failed or blocked child remains visible; its
      PR/task/worktree ending does not satisfy its or its parent's effort gate.
- [x] Resumption reconstructs the agenda position and parent chain from durable
      records, not conversation history. Duplicate child returns cannot skip a
      parent through the helper, which does not write; manual interrupted/stale
      edits require rereading the retained stack and canonical source gates.
- [x] If helpers are admitted, qualify interrupted/steering snapshots, stale
      readers, definition/plan-reference changes and explicit path reconciliation.
      Journal-only edits do not unnecessarily invalidate execution position.
      No concurrent-writer or archive-path migration guarantee is claimed by a
      read-only inspector; locators remain explicit and manually reconciled.
- [x] Returning to a parent refreshes its agenda and relevant validation.
      Uncertain external effects are observed before repetition; a reset never
      resets Git, restarts processes or discards existing work or obligations.
- [x] Multiple participants retain distinct execution positions against one
      canonical effort; no repository-global current cursor or competing plan
      authority is introduced.
- [x] Any optional worktree projection reuses existing focus/claims owners and
      preserves the root completion gate; absence of that integration is safe.
      No dispatch integration is introduced as a prerequisite or deliverable.
- [x] Default checklist/binding guidance does not redirect an adopted position
      or child detour, including in an already-bound worktree without an agenda
      projection. Qualify guidance reconciliation without replacing the reminder
      owner or requiring sibling plugins for standalone use.
      The live implementation retained the same bound root with an older
      declared slice while its journal cursor selected qualification work;
      no automatic provider reminder/projection rewrite is asserted.
- [x] Guidance and any helpers are qualified on Windows and Linux/WSL. Helper
      tests use the repository's bounded test path; required CI remains scoped
      to the actual change and native qualification is reported honestly.
- [x] A paired spike follows [the protocol](paired-spike.md): both arms attempt
      the same task concurrently with matched configuration and ordinary
      planning tools; only the treatment receives the extra guidance layer.
      Task-level scoring does not require the control to produce agenda syntax.
- [x] The spike verifies intended guidance exposure and independently checks
      the prerequisite repair and parent outcome. Record invalid setup,
      intervention and unavailable metrics honestly; one pair alone is not
      evidence of general superiority.

## Proposal

The [historical proposal](inception-transcript.md#investigation-proposal)
contains the initial model. The current direction preserves its coarse graph,
evidence-aware movement, saved nested continuations and honest cooperative
limits, with the following explicit corrections:

- The efforts plugin owns the companion and any helper toolset.
- The basic process and optional helpers work independently of agent-dispatch
  and agent-worktrees; no background execution engine is part of this effort.
- The helper decision followed the actual spike: v1 adds read-only next/check
  inspection only, while scaffolding and transitions remain copy/edit and manual
  decisions. No writer or execution engine is implied.
- The cursor is an execution position, never a competing completion ledger.
  Non-opted-in slice behavior remains unchanged: the current worktree binding
  has a manually declared slice; automatic checklist derivation is separate,
  planned work, not an already-shipped prerequisite.
- The manual JSON convention, helper invocation and explicit journal/state-home
  boundary are documented in the skill/reference. They are payload-only
  capabilities, not a managed runtime or a private journal-provider API.

## Journal

### 2026-10-28 - Inception and ownership correction
- The operator confirmed `effort-agenda-companion` as the slug and requested
  preserving the investigation as this effort's inception.
- Captured the explicit efforts-plugin ownership, process-aide boundary,
  no-dispatch direction and optional helper consideration. The original
  worktree-owned runtime suggestion is retained only as historical inception.
- Opened #9377 for public coordination and the planning slice. Implementation
  remains unstarted; the plan and acceptance obligations are proposed.

### 2026-10-28 - Reviewed inception landed
- Merged [#9380](https://github.com/ThomasMichon/copilot-extensions/pull/9380)
  after Copilot's non-blocking owner review and green required CI. The effort,
  inception, index and matching vision extension are now reviewed source.
- Incorporated the three Low-severity review recommendations into this planning
  record: distinguish current declared slices from planned derivation, qualify
  bounds for successful/no-progress cycles, and qualify guidance reconciliation
  when the adopted position differs from default reminders.
- The inception delivery item is resolved. The next step is explicit admission
  of a bounded Phase 1 contract/manual-aide slice on #9377; no implementation,
  optional-helper decision or rollout is reported complete.

### 2026-10-28 - Paired spike requested
- Captured the operator's request to compare parallel attempts at the same task,
  with the extra guidance layer present in one worktree and absent in the other,
  once initial guidance exists.
- Added Phase 1S and a linked, agent-recommended protocol for comparable inputs,
  an observable prerequisite detour and parent return, independent scoring and
  honest interpretation of a first pair.
- This amendment plans the experiment only. Initial guidance, fixture creation,
  run admission and both agent attempts remain unstarted.

### 2026-10-28 - Execution admitted; retain the whole agenda
- The operator authorized implementation and explicitly identified repeated
  stopping after planning slices as a failure to retain the assigned goal.
- The implementation owner now drives all remaining Plan/Validation items,
  beginning with usable manual guidance and proceeding through the paired spike
  and evidence-based helper decision. No additional admission prompt is needed
  merely because a phase or PR finishes.
- Initial implementation adds an opt-in skill, manual JSON companion examples
  and a bounded continuous action/exit/detour/return procedure. Its limitations
  are explicit; no automatic executor or dispatch integration is introduced.

### 2026-10-28 - Worktree agenda placement clarified
- The operator placed the stateful agenda near the worktree journal: it belongs
  to the continuing agent and can accumulate several efforts, tasks and assigned
  asides over the worktree's lifetime.
- The initial guidance now keeps its agenda/cursor together in an explicit
  worktree journal/state home, referencing canonical sources rather than making
  the live agenda a file owned by one effort. Provider discovery remains an
  optional owner boundary; no staged journal API is presumed available.

### 2026-10-28 - Initial guidance merged; paired attempts started
- Merged [#9421](https://github.com/ThomasMichon/copilot-extensions/pull/9421)
  after current-head approval with no findings and green CI. The new skill,
  manual examples, journal-local placement and opt-in pointer are reviewed
  source; no local payload rollout is asserted.
- Prepared two managed fixture worktrees from the same commit with independent
  offline origins. Existing baseline checks pass, and the independent task
  acceptance packet fails before implementation as intended.
- Started concurrent general-purpose attempts with matching requested defaults
  and soft budgets. Only the treatment receives the reviewed guidance beside
  its worktree journal. Both must finish the parent task and assigned follow-up.
- The active slice is now Phase 1S. Keep the whole effort open; collect actual
  results before deciding whether lightweight helpers are justified.

### 2026-10-28 - Paired outcomes and narrow helper decision
- Both attempts passed the same eight independent task checks, returned from
  the parser prerequisite and completed the assigned counts follow-up. The
  treatment recorded an explicit journal-local stack/queue and completion
  history; the control used the ordinary effort/journal process.
- [Results and limitations](spike-results.md) distinguish the predeclared score
  from a post-measurement compatibility observation and control remediation.
  No general performance or efficacy advantage is claimed.
- Admitted a small read-only next/check helper to make the answer and format
  checks objective without implementing a cursor writer, dispatcher, journal
  provider or automatic completion engine. Manual state remains single-writer.
- Initial focused helper tests pass; qualification and source review remain
  open. Continue with Phase 2, then the remaining Phase 3 acceptance gates.

### 2026-10-28 - Read-only helper qualified
- The final complete relevant efforts packet passed **74 tests, 9 skipped**
  through the bounded source runner with cleanup verified. The skips are
  reported platform-specific producer controls, not falsely counted as passes;
  the helper's focused packet contains 19 passing cases.
- Native Windows and real Linux Python CLI reads cover active, three-frame and
  steering-with-queue snapshots plus stale expected revisions without writes.
  Host startup diagnostics were kept separate; no system configuration changed.
- Independently verified the three-frame witness's proof bytes, source gates,
  3/2/1/0 frame snapshots and no-write receipts; reran the current helper against
  the retained three-frame state.
- [Qualification details](qualification.md) make the manual/single-writer,
  read-only and source-completion boundaries explicit. The helper is deliberately
  not a cursor writer; no transactional or concurrency guarantee is smuggled
  into v1 acceptance.
- Code and qualification are ready for source review. Continue to merge,
  source/release reconciliation and final root/fixture obligation settlement.

### 2026-10-28 - Review diagnostic fix
- The helper review identified an uncaught filesystem error for NUL-bearing
  agenda paths. Text/path checks now reject that input before filesystem access
  and map invalid state-home resolution to the documented diagnostic.
- Added no-write regressions for active and queued NUL paths plus an invalid
  state-home case. The updated full packet passed **77 tests, 9 skipped** with
  cleanup verified; all 22 focused helper cases pass.
- The source-review gate remains open until the updated head is reviewed.
  Both offline fixture outcomes and journals have been retained outside
  cleanup-eligible worktrees, and their managed obligations are settled.

### 2026-10-28 - Source delivery complete and effort closed
- Merged [#9429](https://github.com/ThomasMichon/copilot-extensions/pull/9429)
  after fresh approval resolving the diagnostic finding, no open review issues
  and green required CI. The helper, 22 focused cases, actual spike findings
  and qualification/adoption boundaries are reviewed source.
- Post-merge [CI](https://github.com/ThomasMichon/copilot-extensions/actions/runs/40755004362)
  passed. The observed pinned
  [Main snapshot](https://github.com/ThomasMichon/copilot-extensions/tree/108c51551e0fd378940a5c8ca4635bf25b4490fa)
  reports efforts `0.1.6-dev1` but lacks the helper; no release or installed
  rollout is inferred from the Dev merge.
- Normal Main publication remains explicitly with #3336's existing pipeline.
  The source campaign has no unresolved implementation or qualification item;
  every Plan/Validation obligation is resolved or explicitly transferred.
- Both offline fixture outcomes were preserved in their separate local origins
  and raw journals retained in the machine-local results area. Their managed
  worktrees were finalized without deleting active directories or evidence.
- Archived as Done. The still-bound source worktree releases its effort focus
  and finalizes only after this closing change clears the normal review gate.
