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

- [x] Prove reserve, persist, start and accept through native owners, including
  the full charter seed and stale-generation rejection (#12026, #12030).

### Phase 4 - Same-owner routing and temporary work

- [x] Route status and adjustments to the retained owner; allocate unrelated
  requests separately with bounded temporary-agent lifecycle, and prevent the
  dispatcher from taking over a worker role (#12050, #12067).

### Phase 5 - Vacancy and drift reconciliation

- [x] Propose reconciliation for required-role vacancies and live off-role drift
  through monitor coordination, accepted reassignment and collision-safe
  replacement (#12084).

### Phase 6 - Eligible work admission

- [ ] Propose admission of eligible dormant work and prioritized backlog while
  preserving operator tasks, interactive reserve and review capacity (#12137).

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

### 2026-11-11 - Reconciliation delivered; admission source implemented

- Phase 5 merged in #12084. The read-only reconciliation report preserves
  uncertain ownership and original effects, coordinates retained owners and
  proposes new demand only after discovery and placement holds clear. Its
  focused packet passed 268 tests; affected strict Linux/Windows type checks
  and Python F/E9 lint passed. No live reconciliation or installation occurred.
- Phase 6 (#12137) adds an optional offline `admission --work` report with a
  versioned saved-evidence schema. Priority ranking consumes only per-invocation
  headroom; general work cannot borrow interactive or dedicated review reserve.
  Dormant work keeps its native owner, generation and exact head. Operator
  tasks, waits/holds, stale or conflicting evidence and unsettled effects remain
  held. Native owners still perform all claiming, resumption and allocation.
- The affected topology packet passed 308 tests, including a real subprocess
  with unchanged input bytes, empty-work/fresh-state compatibility and explicit
  help/version behavior. Python F/E9 lint, documentation consistency and
  install-contract checks passed. The affected strict type-check project passed
  for Linux and Windows; the final exact-source check is refreshed before
  publication.
- Clean-room scope is a fresh synthetic topology with no installed services or
  optional admission evidence required by legacy commands. No install or
  provisioning behavior changed. Live external-venue validation remains
  excluded by source-only authority and requires separate adoption. Phase 6
  review, required CI, merge and source finalization remain outstanding.

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
- Final publication gates passed: Python lint, all enforced type-check
  projects for Linux and Windows, and the install-contract check.

### 2026-11-11 - Phase 3 ownership-conflict correction

- Moved the retained owner's assignment-conflict check ahead of launch
  preparation. The flow now holds incompatible ownership before recommending
  launch persistence, workspace start or acceptance.
- The affected roles file passed 130 tests, including the nine-case
  ownership-conflict regression matrix. Python F/E9 lint, documentation
  consistency and all enforced Linux/Windows type-check projects passed.
- This correction uses isolated synthetic evidence only. No live fleet effect,
  installation or deployment occurred; corrected publication, fresh review and
  required CI remain the next gates before merge.

### 2026-11-11 - Phase 3 bookkeeping-revision and positive-fence correction

- Saving a prepared allocation or launch can advance the topology document
  revision without changing the effective charter. `flow` now proposes a
  source-only standing-charter reconciliation for one prepared allocation on an
  unowned task, or one prepared/admitted/consumed launch on a claimed task with
  a matching generation. It proves the old source digest differs only by a
  bounded `topologyRevision` advance; changed identity, effective content or
  unrecognized digests remain held, and started tasks keep their
  owner-reconciliation hold. Effects, envelope, creation key, reservation and
  generation are retained; nothing is replayed.
- Native standing revisions and claimed/started task generations must now be
  positive before a fenced operation is proposed.
- The affected roles file passed 146 tests. The native assignment integration
  file passed 3 tests, using real Dispatch reconciliation that preserves task
  identity, owner, generation, session and relationship while advancing the
  standing revision, then accepting with unchanged launch-seed bytes. Python
  F/E9 lint, documentation consistency and all enforced Linux/Windows
  type-check projects passed.
- Isolated source evidence only; no live fleet effect, installation or
  deployment occurred. Corrected publication, fresh review and required CI
  remain the next gates before merge.

### 2026-11-11 - Phase 3 merged

- Phase 3 merged in #12030, closing #12026 after the native assignment,
  ownership-conflict and bookkeeping-revision corrections. Later phases and
  the overall validation gate remain open.

### 2026-11-11 - Phase 4 same-owner relay and temporary work

- Added the read-only `relay` report (#12050), separating complete follow-up
  persistence, native charter reconciliation, immutable envelope/effect
  persistence, exact-head delivery and response/acceptance settlement.
  Delivery does not imply acceptance; settlement requires positive current
  evidence and respects owner/effect holds. Source-only relay bookkeeping
  reconciliation preserves the original owner, generation and envelope.
- Temporary work is reported separately from standing minimum coverage.
  Dispatcher/worker self-ownership is rejected; completion does not bypass
  native retirement or obligation settlement.
- Both focused topology files passed 218 tests, including stale, missing and
  contradictory evidence, immutable-envelope fences, unresolved effects and
  unchanged subprocess input bytes. The native Dispatch assignment file passed
  4 tests; its new relay regression invokes the real helper and native
  reconciliation/acceptance while preserving the original owner, generation,
  session, relationship and envelope bytes. Acceptance uses the complete
  retained workspace, including its creation nonce and path.
- Python F/E9 lint, all enforced type-check projects, documentation consistency,
  install-contract and version-consistency checks passed. After the final
  workspace correction, both focused test packets and the agent-roles strict
  Linux/Windows type-check project passed again.
- All evidence is isolated and synthetic. No live fleet effect, installation,
  deployment or external-venue validation occurred under this source-only
  authority. Fresh temporary fixtures exercise the helper without prior
  enrollment; this change adds no installer or provisioning path. Publication,
  independent review and merge gates remain outstanding; Phase 4 and the parent
  objective are not complete.

### 2026-11-11 - Phase 4 review corrections

- Settled, accepted assignments retain standing coverage, routing and accepted
  flow after proven bookkeeping-only source revision drift. Effective charter
  differences, missing acceptance, unresolved effects and ownership conflicts
  remain held; the intent report still exposes exact source-declaration drift.
- Delivered adjustments can propose reacceptance at a monotonically newer
  observed head revision of the same retained session. Prepared delivery,
  another session, regressed or missing revisions, and head drift after
  current-revision acceptance remain held.
- Both focused topology files passed 222 tests. All 5 native Dispatch
  assignment tests passed, including unchanged and advanced same-session head
  revisions and restored coverage, routing and accepted flow after settlement.
  The regressions preserve native owner, generation, session, relationship and
  immutable envelope bytes.
- Python F/E9 lint, all enforced type-check projects, explicit agent-roles
  Linux/Windows strict checks, documentation consistency and whitespace checks
  passed. The existing install-contract and version-consistency evidence is
  unchanged. These remain isolated synthetic checks; no live external venue,
  enrollment, installation or deployment was exercised. Updated publication,
  fresh independent review and required CI remain gates before merge.

### 2026-11-11 - Phase 4 aligned-source performance correction

- Skip historical bookkeeping-digest searches when the current source digest
  already matches the materialized declaration. A real source mismatch still
  requires the existing bounded proof; acceptance and ownership guards are
  unchanged.
- The new regression reproduced 256 unnecessary historical hashes for one
  aligned assignment before the fix. Both aligned and drift cases now pass:
  the former performs no historical hashes, while the latter proves the prior
  revision normally.
- All 158 tests in the affected topology-dispatch file passed, along with
  Python F/E9 lint, strict agent-roles checks for Linux and Windows, and
  whitespace checks. Earlier integration evidence remains applicable; this
  correction adds no native operation or provisioning path.
- Validation remains isolated and synthetic, with no live enrollment,
  installation, deployment or external-venue effects. Publication, fresh
  independent review and required CI still gate merge.

### 2026-11-11 - Phase 4 merged

- Phase 4 merged in #12067 after independent review and required CI. The final
  correction requires persisting the exact observed native worktree before
  relay preparation; incompatible ownership remains held.
- The final focused topology packet passed 225 tests, including the real CLI
  unbound/persist/relay sequence and unchanged input bytes. All five native
  Dispatch assignment cases passed, preserving the original seed, worktree,
  relationship and exact-head acceptance fences. Python F/E9 lint and strict
  Linux/Windows type checks passed.
- All evidence remains isolated and synthetic. No live external venue,
  enrollment, installation or deployment occurred. Later phases and the parent
  objective remain open.

### 2026-11-11 - Phase 5 vacancy and drift report

- Added read-only `reconcile` reporting for #12079. It combines qualified
  coverage with eligible placement, retaining unqualified assignment identities,
  original pending effects and owner evidence. Off-role drift proposes
  coordinated in-place reassignment; uncertain owners never become replacement
  slots. Unmapped owners/tasks propose in-place reconciliation.
- Completed assignments remain held until release and retirement evidence
  settle. Dispatch-linked release additionally requires a fresh retired task,
  null native ownership and a closed or absent original relationship.
  Only unobstructed uncovered demand proposes new standing assignments, with
  complete charters, stable identities and serialized native admission.
- Both focused topology files passed 255 tests. Behavioral cases include stale,
  dormant, off-role and unknown owners, original-effect identity, original
  assignment continuation, aliases, custody/discovery/capacity holds and native
  retirement. A real CLI subprocess proves unchanged input bytes; fresh
  synthetic legacy/native fixtures exercise optional-evidence boundaries.
  Python F/E9 lint passed.
- The skill, recovery reference and plugin entry point document the new report
  and its advisory-only limits. No installer, runtime service or provisioning
  path changed. Live external-venue validation, enrollment, installation and
  deployment remain outside this source-only authority. Strict agent-roles
  type checks for Linux and Windows, documentation consistency, version
  consistency and whitespace checks passed. Publication, independent review
  and required CI still gate merge; Phase 5 is not complete.