# Topology Dispatcher Integration

## Goal

Close the remaining source gaps between the portable topology dispatcher in
`agent-roles` and the native owners it composes. A dispatcher can report
liveness, progress, authority and uncertainty separately; join desired intent
with native standing-charter evidence without a second writable ledger; assign,
route and reconcile work through native owners; and admit eligible work while
preserving reserved capacity.

- **Slug:** `topology-dispatcher-integration`
- **Repo:** copilot-extensions
- **Branch(es):** managed source worktrees; one reviewed PR per phase
- **Created:** 2026-11-10
- **Status:** Active
- **Vision:** [Agent topology](../../../visions/agent-fabric/agentic-factory/agent-topology/README.md)
- **Umbrella issue:** #12009

## Guiding Intent

Integrate rather than replace. Worktrees, Bridge and Dispatch keep custody of
allocation, embodiment, messaging and task lifecycle. The topology helper stays
read-only and offline: it joins saved native evidence and proposes; it never
launches, sends, reconciles or grants effect authority on its own.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Source owner | Design, implementation, validation and PR stewardship | Managed source worktree; #12009 |
| Repository reviewer | Independent review of each phase | Normal pull-request review |

## Coordination

- **Topology:** sequential phase PRs from one source owner.
- **Host (owns PRs):** source owner.
- **Delegates:** none; this source effort creates no fleet agents.
- **Handoff:** preserve this objective, open phases, exact-source evidence and
  PR obligations through native continuation; a merged phase is not completion.

## Context

[Portable Agent Topology](../../2026/11/09%20portable-agent-topology/README.md)
delivered the dispatcher skill, schema and read-only helper.
[Dispatch Standing Charters](../../2026/11/10%20dispatch-standing-charters/README.md)
delivered native standing declarations, accepted materializations and
registrar-local policy. Coverage already counts only native accepted lanes, but
owner observations carry presence only, and several dispatcher flows remain
documented runbook steps rather than proven joins.

## Request

Integrate the dispatcher with native ownership, readiness and progress evidence;
keep proposed and adopted intent separate; route follow-ups to the retained
owner and unrelated work separately; reconcile vacancies and off-role drift; and
admit eligible dormant work without consuming reserved capacity.

## Scope Approvals

The operator's source assignment covers generic design, source changes, bounded
validation and normal issue/PR publication in this repository. Deployment-specific
elaboration and original private authority remain with their adopter.

This effort does **not** authorize live fleet enrollment, role appointment,
agent launch, stop, move, takeover or replacement, deployment, new recurring
schedules, access changes or increased spend. Behavioral proof uses isolated
synthetic fixtures. Live adoption is a separate explicit decision.

## Plan

### Phase 1 - Liveness and progress evidence

- [x] Add optional dated progress evidence to owner observations and report
  liveness and progress beside every coverage entry, with explicit unknowns and
  no effect on qualification or routing (#12009, #12010).

### Phase 2 - Desired versus native standing intent

- [x] Add a read-only view joining checked-in desired assignments with proposed
  and adopted Dispatch standing-charter evidence, keeping them separate and
  adding no writable ledger or reconciliation effect (#12016).

### Phase 3 - Native assignment flow

- [ ] Prove reserve, persist, start and accept through native owners, including
  the full charter seed and stale-generation rejection.

### Phase 4 - Same-owner routing and temporary work

- [ ] Route status and adjustments to the retained owner; allocate unrelated
  requests separately with bounded temporary-agent lifecycle, and prevent the
  dispatcher from taking over a worker role.

### Phase 5 - Vacancy and drift reconciliation

- [ ] Propose reconciliation for required-role vacancies and live off-role drift
  through monitor coordination, accepted reassignment and collision-safe
  replacement.

### Phase 6 - Eligible work admission

- [ ] Propose admission of eligible dormant work and prioritized backlog while
  preserving operator tasks, interactive reserve and review capacity.

## Validation Plan

- [ ] Unit/behavior for each phase with synthetic fixtures, including unknown,
  stale and contradictory evidence and unchanged input bytes.
- [ ] Simple E2E: invoke the real helper as a subprocess for each new read-only
  command or report.
- [ ] Clean-room: a fresh synthetic topology without optional evidence keeps
  legacy behavior.
- [ ] Live external venue: not exercised under this source-only authority;
  record that explicitly per phase.
- [ ] Affected plugin tests, Python lint, changefiles, required CI and actual
  merge for each phase; public artifacts audited for private identifiers.
- [ ] Reconcile every Plan and Validation Plan item before marking Done.

## Journal

### 2026-11-10 - Effort opened with Phase 1

- Gap review against `dev` found coverage already gated on native acceptance,
  but no progress evidence, no desired-versus-standing join, and no eligible
  work admission. Opened #12009 for Phase 1.
- Phase 1 adds an optional `progress` read, derived `liveness`/`progress` per
  coverage entry and per-role `progressCounts`. The agent-roles packet passed
  201 tests; Python F/E9 lint passed. No live fleet effect was performed.
- Phase 1 merged in #12010.

### 2026-11-10 - Phase 2 desired versus native standing intent

- Added a read-only `intent` command (#12016). Each standing assignment reports
  separate `desired`, `proposed` and `adopted` records, revision-only
  comparisons that are `null` when evidence is missing, stale, future-dated or
  desired-terminal, a lane state with explicit reasons, and native standing
  charters no assignment links. It copies no relationship receipt, writes
  nothing and does not qualify coverage.
- The agent-roles packet passed 216 tests, including a subprocess run proving
  unchanged input bytes; Python F/E9 lint passed. No live fleet effect was
  performed; live venue validation remains outside this source-only authority.

### 2026-11-11 - Phase 3 native assignment flow

- Implemented the read-only `flow` command (#12026): persist complete assignment
  and materialization, allocate through Worktrees, reserve through Dispatch,
  preserve the complete launch seed and original envelope, start the retained
  workspace through Bridge, then accept through Dispatch. The report names
  owning adapters and revision, generation and head fences without performing
  effects. Unknown effects require original-identity receipt recovery, never
  replay; stale generations and incompatible evidence hold the flow.
- Unit/behavior evidence: the agent-roles packet passed 236 tests and the
  Dispatch standing-admission/charter packet passed 174 tests. After correcting
  a test-helper annotation, the affected roles file passed 116 tests and the
  native assignment integration file passed both tests. Python F/E9 lint and
  all enforced type-check projects passed, with the corrected agent-roles
  project checked for both Linux and Windows.
- Simple E2E and fresh-state evidence: isolated native Dispatch admission and
  the launch subprocess consumer preserve the complete seed, reject a stale
  assignment generation and qualify coverage only after native acceptance.
  Synthetic topology fixtures also cover absent optional evidence and
  unchanged input bytes. Install-contract and documentation checks passed.
- No live external venue, fleet activation or installation was exercised:
  those require separate adoption and effect authority. Publication, review
  and merge remain pending; later phases and the parent objective stay open.

### 2026-11-11 - Phase 3 review corrections

- Corrected the pre-reservation join: only the exact workspace retained by a
  confirmed allocation is mapped before a Dispatch relationship exists.
  Unconfirmed, untargeted and unrelated owners still hold placement.
- Reject zero native head revisions before recommending acceptance, matching
  Dispatch's positive-revision fence.
- Added real Bridge workspace-consumer coverage using the saved complete seed
  and original keyed envelope. Normal delivery, a lost native-start response
  and a lost prompt-admission response each retain one native session and one
  prompt turn; recovery does not allocate or deliver again.
- The affected roles file passed 121 tests (24 flow cases); the Bridge
  workspace-creation file passed 53 tests with three existing skips, including
  all three saved-seed cases. Python F/E9 lint passed. No live fleet effects,
  installation or deployment occurred; review and merge remain pending.
- Final publication gates passed: Python lint, all 41 enforced type-check
  projects for Linux and Windows, and the install-contract check across all
  13 runtime plugins.