# Portable Agent Topology

## Goal

Deliver the portable topology-management concept, skill and schema in
copilot-extensions. An operator with a compatible harness and machine mesh can
assign a dispatcher that delegates rather than implements, records complete
role assignments before launch, remembers allocated native worktrees, relays
adjustments and status to the same owner, assigns temporary workers for
unrelated requests, and reconciles placement, load and minimum role coverage.

- **Slug:** `portable-agent-topology`
- **Repo:** copilot-extensions
- **Branch(es):** managed source worktree; sequential reviewed plan and implementation PRs
- **Created:** 2026-11-09
- **Status:** Done
- **Vision:** [Agent topology](../../../../visions/agent-fabric/agentic-factory/agent-topology/README.md)
- **Umbrella issue:** #11715

## Guiding Intent

Make the conversational allocator portable without inventing another control
plane. Durable assignment intent complements native agency, claims, task and
messaging records; it never replaces their custody or grants effect authority.
Private adopters supply topology and grants, not forks of the reusable concept.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Source owner | Design, implementation, validation and PR stewardship | Managed worktree codename `hinged-lantern`; #11715 |
| Repository reviewer | Independent plan and implementation review | Normal pull-request review |

## Coordination

- **Topology:** sequential plan and implementation PRs from one source owner.
- **Host (owns PRs):** source owner.
- **Delegates:** none; no fleet agents are created by this source effort.
- **Handoff:** preserve this objective, exact-source evidence, pending effects
  and PR obligations through native continuation; never reduce the objective to
  the last completed phase.

## Context

`agent-roles` already provides sustained skills and charters over optional native
adapters. The [factory vision](../../../../visions/agent-fabric/agentic-factory/README.md)
provides the conversational composition; [fleet loadouts](../../../../visions/machine-fleet/loadouts/README.md)
owns machine composition and capacity intent. Worktrees, Bridge and Dispatch
retain their native boundaries. Existing fleet-monitor goals and thread
registers are complementary supervision guidance, not an assignment allocator.

## Request

Provide a one-stop conversational dispatcher for an operator's compatible
harness and mesh. Persist role assignments and their complete charters, launch
through Bridge into appropriate native worktrees when authorized, retain the
assignment map for inquiries and changes, separate unrelated temporary work,
respect machine purpose and allowed assignments, balance eligible load and
describe the minimum topology.

## Scope Approvals

The operator's source assignment covers generic design, source changes,
bounded validation and normal issue/PR publication in this repository.
The portable request is recorded above; deployment-specific elaboration and
original private authority remain with their adopter, not on this public face.

This effort does **not** authorize live fleet launch, stop, move, takeover,
replacement, deployment, new recurring schedules, access changes or increased
spend. Schema validation, a reviewed plan and a merged implementation grant
none of those effects. Use isolated synthetic fixtures for behavioral proof.
Live adoption remains a separate explicit decision with its original owner.

## Plan

### Phase 1 - Reviewed intent and contract

- [x] Search prior art and claim #11715; select existing `agent-roles` as owner.
- [x] Land the topology vision and this proposal through normal review.
- [x] Bind the native source-worktree effort focus to this canonical effort.

### Phase 2 - Portable role and assignment document

- [x] Add a topology-dispatcher skill, fillable charter and native-adapter
  reference covering admission, discovery, allocation, follow-up, supervision,
  recovery and retirement through their existing owners.
- [x] Add a versioned schema and synthetic example for desired machine/role
  topology, complete assignment charters and native-owner associations.
- [x] Add a bounded offline validator/overview helper; reject invalid references,
  duplicate identities and inconsistent assignment/effect records. Report
  eligible placement and qualified role coverage without launching or mutating.
- [x] Preserve persist-before-effect, one-dispatcher custody, stable allocation
  identity, same-worktree head resolution and unresolved-effect recovery.

### Phase 3 - Consumer proof and delivery

- [x] Exercise the complete synthetic consumer flow, including restart,
  allocation collision, uncertain launch, same-owner follow-up, temporary work,
  placement constraints and minimum-role coverage.
- [x] Update plugin usage and file inventory, add the plugin changefile and
  complete exact-source checks and normal implementation PR stewardship.
- [x] Record release versus installed adoption honestly, and the explicit
  source-only/no-live-activation disposition.
- [x] Reconcile every Plan and Validation Plan item before marking Done.

## Validation Plan

- [x] Plan: validate changed Markdown links and diff hygiene; obtain normal
  independent review and required CI.
- [x] Unit/behavior: test malformed and duplicate references, charter changes,
  uncertain effects, current-head succession, temporary work separation,
  disallowed/over-capacity placement and qualified-versus-unknown coverage.
- [x] Simple E2E: invoke the actual payload helper as a subprocess on a fresh
  synthetic topology; persist proposed intent before simulating native results,
  reload and recover the same assignment, then derive the overview.
- [x] First-touch: an empty assignment map plus existing-owner discovery must
  produce an adoption/reconciliation proposal, not assume a fresh empty fleet.
- [x] Native compatibility: inspect the advertised Worktrees/Bridge contracts
  used by the runbook; qualify available read-only resolution paths. Report
  unsupported capabilities explicitly rather than guessing command syntax.
- [x] Live external venue: not exercised under this source-only authority;
  document that activation, effects and installed fleet acceptance remain with
  the adopter's separately authorized work, not claimed by synthetic tests.
- [x] Run affected plugin tests, skill/metadata checks and changed Python
  lint; complete required CI and actual merge for the implementation.
- [x] Audit public artifacts for private identifiers, paths and reverse links.

## Proposal

See [proposal.md](proposal.md) for the bounded schema/helper design and native
adapter flow. The plan PR is a review gate before implementation, not completion.

## Journal

### 2026-11-09 - Portable ownership and source claim

- The operator clarified that the reusable concept, skill and schema belong
  upstream, with private consumers retaining their own deployment and state.
- Prior-art review selected payload-only `agent-roles`; native lifecycle and
  machine owners remain unchanged.
- Claimed #11715 and prepared the canonical vision and implementation plan.
  No live fleet effects, deployment or new background work were performed.
- Bound the native worktree effort focus to Source owner / Phase 1.

### 2026-11-09 - Reviewed plan landed

- #11720 merged after an approving Copilot review at head `48d76d950c`,
  green required CI and native Maintainer completion.
- The source worktree synchronized onto `dev`; the implementation slice now
  begins with the portable role and assignment document. The plan merge does
  not close #11715 or qualify live activation.

### 2026-11-09 - Portable implementation and consumer proof

- Added the dispatcher skill, complete charter, native-adapter/recovery guide,
  version-1 JSON Schema and synthetic example in `agent-roles`.
- The bounded, read-only helper validates references and effect records, exports
  complete assignments, derives eligible placement and qualified coverage, and
  routes inquiries through the retained worktree and observed current head.
  An unaccepted or different assignment identity holds routing; a newer charter
  awaiting acceptance still permits an inquiry to the established owner.
- The contained plugin packet passed 102 tests (4.69 seconds of test execution,
  8.31 seconds including preparation and cleanup). Python F/E9 lint and skill
  validation passed. Filesystem-only behavior checks are T1; four real-helper
  subprocess scenarios are T2. Required unit-CI admission is unchanged.
- Synthetic subprocess evidence covers persist/allocation/launch/reload,
  uncertain effects, head succession, same-owner follow-up, first-touch
  reconciliation, and separate temporary work through native-exit evidence.
  Input bytes remain unchanged. This is offline consumer proof, not a live fleet.
- Inspected installed Worktrees creation/head-resolution/status and Bridge
  creation/send contracts. An actual read-only native head lookup resolved its
  existing worktree correctly. No native allocation, launch, send, retirement or
  external venue was exercised; those effects require adopter authority.
- Implementation review, required CI, actual merge and final release/adoption
  disposition remain open. No fleet activation or installed rollout is implied.

### 2026-11-09 - Implementation review corrections

- #11738's first CI run identified strict typing omissions. The corrected helper
  and tests pass the repository's strict Python 3.10 contracts for both Linux
  and Windows, including platform-specific headless subprocess flags.
- Review corrections retain immutable native-request envelope references and
  selected send modes before message effects, preserve historical charters
  after a role's scope changes, and use the repository's default patch bump.
- The affected plugin packet now passes 109 tests (9.18 seconds of test execution,
  14.67 seconds including preparation and cleanup); Python F/E9 lint passes.
  Five T2 subprocess scenarios include unknown-send recovery after saved-state
  reload and native-head succession without reconstructing the original prompt,
  caller, key or session. No live send or receipt lookup was performed.
- Current-head review, required CI and actual merge remain delivery gates.

### 2026-11-09 - Source delivery complete

- #11738 merged to `dev` at `5354412d9e` after an approving exact-head review,
  resolution of all review threads and green required CI. The usage inventory,
  patch changefile, 109-test plugin packet, five real-helper subprocess flows,
  strict Linux/Windows type checks and Python lint are complete.
- The final public-artifact audit found no private deployment identifiers,
  filesystem paths or reverse links. Native owners and the source-only authority
  boundary remain unchanged.
- The implementation was not yet on `main` at the closeout check. Normal release
  promotion, separately authorized installation and adopter-owned live acceptance
  remain distinct from this completed source delivery. No fleet agents were
  launched, reassigned or retired; no service, schedule or installed payload changed.
- Every source Plan and Validation Plan item is resolved. #11715 retains archive
  publication and native effort/worktree settlement until those steps finish.
