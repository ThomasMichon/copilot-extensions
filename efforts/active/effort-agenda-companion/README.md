# Effort Agenda Companion

- **Slug:** `effort-agenda-companion`
- **Repo:** copilot-extensions
- **Branch(es):** independent, reviewed per-slice PRs against `dev`
- **Created:** 2026-10-28
- **Status:** Draft
- **Vision:** [efforts](../../../visions/plugins/efforts/README.md)
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

The companion belongs in the **efforts plugin**, not agent-dispatch. Optional
local CLI helpers may scaffold an agenda and inspect or maintain its cursor;
neither a scheduler nor another plugin is required. The effort README remains
the shared contract and completion authority. This is cooperative guidance, not
a claim to constrain every action an agent can take.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Planning author | Capture inception and submit the proposal | This effort and #9377 |
| Implementation owner | Claim the next admitted implementation slice | #9377 and its future sub-issues |

## Coordination

- **Topology:** independent per-slice PRs after the inception plan is reviewed.
- **Host (owns PRs):** the participant claiming that slice on its issue.
- **Delegates:** none assigned; do not infer implementation ownership from this plan.
- **Handoff:** update the Plan and Journal with results, remaining gates and the
  next claimed slice; keep the canonical effort here.
- **Current assignment:** land this inception and reviewed plan. It does not
  implement the companion, authorize rollout, or claim the effort is complete.
- **Adjacent owners:** [Worktree/Effort Railroad Binding](../worktree-effort-railroad-binding/README.md)
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
- The [efforts plugin](../../../plugins/efforts/README.md) already supplies
  planning and continuity without a runtime service. Existing
  [effort-driven session loops](../../2026/08/28%20effort-driven-session-loops/README.md)
  establish durable effort ownership, not graph or child-return semantics.
- Pattern fit: [architecture principles](../../../docs/patterns/README.md),
  especially independent installation, graceful optional composition,
  right-sized surfaces, cross-platform parity and proportionate safeguards.
  No new daemon, broker, graph service or mandatory sibling dependency.

## Request

**Summary of settled intent:** extend the efforts plugin with a voluntary
structured agenda companion. Keep it a process aide, outside agent-dispatch;
consider optional CLI scaffolding and cursor support. Use the investigation as
the inception for this implementation effort.

The latest operator request is preserved verbatim:

> In general I like the nuanced direction you propose. Help land an extension/companion for efforts (in the efforts plugin) that hels give agents this structured way through an effort. Note that I intend this to be a process aide, not something built into agent-dispatch. We could consider a helper CLI toolset to scaffold out one of these, and help maintain/identify a "cursor" in the agenda, of we want more objective driving through these. Take what you wrote so far and set it as the inception text for an effort to build this idea out.

The [full inception exchange](inception-transcript.md) distinguishes operator
requirements from agent recommendations and records publication-only omissions.

## Plan

The requested capability and ownership are fixed above. The decomposition and
specific mechanisms below are **agent-recommended**, pending reviewed design;
illustrative filenames or operations are not existing commands.

### Phase 0 - Inception and reviewed scope
- [ ] Land the canonical effort, inception exchange, active index and matching
      efforts vision extension through normal review.
- [ ] Record the reviewed proposal and explicitly admit the next implementation
      slice on #9377 before starting implementation.

### Phase 1 - Companion contract and manual process aide
- [ ] Define a small optional companion format for coarse nodes, stable
      Plan/Validation references, exit criteria, named outcomes and permitted
      transitions; do not copy the plan or maintain a second completion checklist.
- [ ] Separate the reviewed agenda from participant-local mutable execution
      position, attempts, evidence references and saved continuations. Define
      cursor discovery/resumption without requiring a managed worktree.
- [ ] Specify nested call/return, blocked-child handling and return-time
      revalidation; distinguish a blocking child detour from parallel delegation.
- [ ] Ship efforts-owned authoring and usage guidance with a synthetic worked
      example. Ordinary efforts and manual agenda use stay first-class.

### Phase 2 - Optional helper decision and bounded implementation
- [ ] Evaluate whether a lightweight efforts-owned CLI adds enough objective
      cursor maintenance to justify it; record the decision and invocation/state
      ownership. Do not silently convert the plugin into a service.
- [ ] If admitted, implement only the agreed scaffolding, inspection and cursor
      operations, including explicit transition and child-return validation.
      Otherwise document the manual procedure and its weaker guarantees.
- [ ] Handle duplicate operations, stale cursors, changed definitions and
      interrupted returns with proportionate revision checks and reconciliation;
      do not claim exactly-once external effects.

### Phase 3 - Qualification and adoption guidance
- [ ] Qualify the standalone manual path and any admitted helpers using the
      Validation Plan; document unsupported guarantees explicitly.
- [ ] Update affected plugin guidance and truthful documentation, add required
      changefiles for payload changes, and land implementation through review.
- [ ] Record source/release qualification and provide opt-in adoption and recovery
      instructions. Any live rollout is separately authorized and tracked.
- [ ] Resolve or explicitly transfer every Plan/Validation item before marking
      this effort Done; an inception PR is not capability completion.

## Validation Plan

These are **agent-recommended acceptance obligations**, not claims of tests
already run or a requirement to publish execution artifacts.

- [ ] A standalone adopter can author and follow a coarse agenda with only
      efforts installed; no agent-dispatch, agent-worktrees, daemon or automatic
      turn driver is needed. Non-opted-in efforts keep their existing behavior.
- [ ] A node identifies its canonical plan/validation references, objective,
      exit criteria and allowed outcomes without duplicating completion status.
      Evidence distinguishes measured results from attributed agent judgment.
- [ ] Manual instructions explicitly reject unsupported advancement; any helper
      rejects missing exit evidence and undeclared branch/reset operations.
      Repeated failure reaches an explicit replan/steering decision, not drift.
- [ ] A synthetic A -> B -> C -> B -> A scenario preserves the root objective
      and correct return points. A failed or blocked child remains visible; its
      PR/task/worktree ending does not satisfy its or its parent's effort gate.
- [ ] Resumption reconstructs the agenda position and parent chain from durable
      records, not conversation history. Duplicate child returns cannot skip a
      parent; a stale cursor cannot silently replace a newer one.
- [ ] If helpers are admitted, test interrupted child activation/return, concurrent
      stale updates, definition/plan-reference changes and archived effort paths.
      Journal-only edits do not unnecessarily invalidate execution position.
- [ ] Returning to a parent refreshes its agenda and relevant validation.
      Uncertain external effects are observed before repetition; a reset never
      resets Git, restarts processes or discards existing work or obligations.
- [ ] Multiple participants retain distinct execution positions against one
      canonical effort; no repository-global current cursor or competing plan
      authority is introduced.
- [ ] Any optional worktree projection reuses existing focus/claims owners and
      preserves the root completion gate; absence of that integration is safe.
      No dispatch integration is introduced as a prerequisite or deliverable.
- [ ] Guidance and any helpers are qualified on Windows and Linux/WSL. Helper
      tests use the repository's bounded test path; required CI remains scoped
      to the actual change and native qualification is reported honestly.

## Proposal

The [historical proposal](inception-transcript.md#investigation-proposal)
contains the initial model. The current direction preserves its coarse graph,
evidence-aware movement, saved nested continuations and honest cooperative
limits, with the following explicit corrections:

- The efforts plugin owns the companion and any helper toolset.
- The basic process and optional helpers work independently of agent-dispatch
  and agent-worktrees; no background execution engine is part of this effort.
- CLI implementation is a considered option, not a pre-decided requirement.
- The cursor is an execution position, never a competing completion ledger.
  The existing default checklist-derived slice remains unchanged without opt-in.
- Schema, helper grammar and storage location remain design decisions with
  named validation obligations, not an already-shipped runtime contract.

## Journal

### 2026-10-28 - Inception and ownership correction
- The operator confirmed `effort-agenda-companion` as the slug and requested
  preserving the investigation as this effort's inception.
- Captured the explicit efforts-plugin ownership, process-aide boundary,
  no-dispatch direction and optional helper consideration. The original
  worktree-owned runtime suggestion is retained only as historical inception.
- Opened #9377 for public coordination and the planning slice. Implementation
  remains unstarted; the plan and acceptance obligations are proposed.
