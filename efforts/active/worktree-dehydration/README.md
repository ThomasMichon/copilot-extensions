# Worktree Dehydration

## Goal

Capture the agreed worktree dehydration idea as a canonical effort, extend the
agent-worktrees vision, develop an architectural proposal, and file the delivery
work. This kickoff is design and planning, not runtime implementation or rollout.

Latest operator requirement:

> We need to move `.effort-agenda` out of the worktrees' actual footprints; it needs to be in ~/.<repo>/worktrees/<worktree-id>

> We'll need a "doctor" for that

- **Slug:** `worktree-dehydration`
- **Repo:** copilot-extensions
- **Branch(es):** one reviewed design PR; later dependency-ordered delivery PRs
- **Created:** 2026-11-20
- **Status:** Draft
- **Vision:** [checkout hydration](../../../visions/plugins/agent-worktrees/checkout-hydration/README.md)
- **Umbrella issue:** #13781
- **Sub-issues:** #13790 (agenda placement/doctor), #13783 (storage/recovery), #13784 (automatic lifecycle), #13785 (Manager/qualification)

## Guiding Intent

The unit of agency must outlive its checkout. When its agent has actually
stopped, a clean worktree should release reproducible checkout storage without
losing the local branch, conversations, relationships, obligations or pending
work. Ordinary resume should recreate the checkout before the agent needs it.
Disk reclamation must not require pretending that unfinished work is complete.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| design-driver | Owns the initial vision, architecture proposal, tracking and design PR | This effort and #13781 |
| implementation-owner (unassigned) | Future storage, lifecycle and Manager delivery | Assign through the linked issues after design review and execution assignment |

## Coordination

- **Topology:** one kickoff design PR; agenda placement/doctor precedes storage activation, then automatic entry and Manager integration.
- **Host (owns PRs):** design-driver owns only the current planning publication.
- **Delegates:** none; no implementation worker has been launched.
- **Handoff:** the canonical plan is here; assign later execution explicitly.
  Keep the umbrella and implementation issues open after the design PR merges.

## Context

- [Architecture proposal](architecture.md): proposed mechanics, recovery and qualification.
- [Agenda placement and doctor](agenda-placement-doctor.md): external data home,
  explicit migration, interrupted recovery and legacy-history acceptance.
- [Public request capture](inception.md): original public-safe wording and amendments.
- [Current lifecycle](../../../plugins/agent-worktrees/docs/worktree-lifecycle.md):
  displayed classification, finalization and terminal collection are distinct.
- [Worktree data](../../../plugins/agent-worktrees/docs/worktree-data.md):
  durable domain data already has checkout-independent placement.
- [Continuous reclamation](../../../visions/plugins/agent-worktrees/continuous-reclamation/README.md)
  remains the owner of terminal collection, not a way to conclude unfinished work.
- Adjacent work retains its owners: #10968 derived-state hygiene, #10879 checkout
  relocation, #11298 roster authority and #6750 deletion throughput. None is
  replaced or declared complete by this effort.

The initial source inspection found removal and branch deletion as separate Git
helpers, a new-branch-oriented creation helper, and path-dependent resume
preflight. These are integration seams, not evidence that dehydration exists.
No matching open issue was returned for either "dehydrat" or "rehydrat" before
the umbrella was filed; a bounded worktree/disk search identified the related
owners above.

## Request

**Summary of settled operator intent:** provide reversible dehydration and
rehydration, ultimately automatic after confirmed agent-process retirement.
Use live Git checks, preserve the expected local branch, treat ignored output
as disposable, retain recovery state beside the checkouts, include it in roster
reconstruction, and hydrate before mux or Bridge resume. Show `(D)` as a
sub-state. Consider manual policy, a per-worktree hold and Manager actions.
Start with this effort, vision changes, architecture and tracking.

The public-safe original words and amendments are in [inception.md](inception.md).
The latest publication request is generalized there to exclude private
provenance destinations. No public artifact links back to private material.

## Scope Approvals

- **Operator-assigned now:** capture the discussion; publish the canonical public
  effort, vision extension and architectural design; file tracking items.
- **Settled design:** ignored files are disposable; normal status must have no
  staged, modified or untracked content; keep the local branch; use a nearby
  dehydration catalog and account for it during periodic roster reconstruction.
- **Proposed options, not final API decisions:** exact configuration keys, hold
  UX, CLI syntax and capability names remain design recommendations.
- **Existing integration constraint:** retain the worktree-data owner's hold
  for legacy checkout-local durable agenda history until supported preservation
  and explicit source retirement. This does not add a per-build-artifact policy.
- **Latest operator placement requirement:** move `.effort-agenda` out of the
  checkout footprint into the per-repository worktree data home
  `~\.<repo>\worktrees\<worktree-id>`. Plan the existing-lane migration before
  enabling automatic dehydration; do not treat a path change as proof of
  preserved history or authority to discard the original.
- **Latest operator recovery requirement:** provide a doctor for the agenda
  placement transition. Report-first inspection and explicit owner-mediated
  repair must precede retiring legacy checkout-local data.
- **Not assigned by this kickoff:** runtime implementation, automatic deletion
  on this host, shared-worktree cleanup, live migration, installation or rollout.
  Review of these documents does not grant those effects.

## Plan

### Phase 1 - Review the kickoff design

- [x] Capture the request and its superseded alternatives separately from recommendations.
- [x] Extend standing intent without presenting the feature as implemented.
- [x] Describe storage transitions, catalog recovery, branch preservation, entry ordering and qualification.
- [x] File #13781 with dependency-ordered work in #13790, #13783, #13784 and #13785.
- [ ] Publish and merge the kickoff after the repository's review and CI gates.

### Phase 2 - Prove external agenda placement and doctor (#13790; execution not assigned)

- [ ] Put new managed agendas under `~\.<repo>\worktrees\<worktree-id>`, outside actual checkouts, through the existing data-placement owner.
- [ ] Provide report-first doctor inspection and explicit lossless repair/migration for existing checkout-local lanes.
- [ ] Prove history and store identity, single-writer relocation, target/locator conflicts and interrupted recovery before source retirement.

### Phase 3 - Prove reversible storage (#13783; execution not assigned)

- [ ] Implement the owner-mediated catalog and a real clean WIP round trip at the original path.
- [ ] Prove branch retention, interrupted-transition recovery and catalog-aware roster reconstruction.
- [ ] Integrate claims, paired worktrees, cleanup, Git GC and durable-data placement without semantic finalization.

### Phase 4 - Prove automatic stop and resume (#13784; execution not assigned)

- [ ] Extend existing post-exit/resident work with actual retirement checks and a shared entry/removal fence.
- [ ] Hydrate before every path-dependent mux and Bridge entry; preserve prompts and session lineage.
- [ ] Prove repeated automatic cycles, concurrent requests, restart recovery and supported provider skew.

### Phase 5 - Prove the complete operator flow (#13785; execution not assigned)

- [ ] Add `(D)`, visible hold/refusal reasons, auto/manual policy and manual Manager actions.
- [ ] Qualify native Windows and Linux/WSL flows, storage reduction and resume cost with disposable fixtures.
- [ ] Publish implementation and update reality docs only after their actual behavior is proved.

## Validation Plan

### Kickoff acceptance

- [ ] Re-read Goal, Request, scope and proposal against each operator amendment.
- [ ] Check changed effort/vision structure, whitespace, local links and public-surface sanitization.
- [ ] Verify live issues, dependencies and published design links; preserve open implementation trackers.
- [ ] Obtain the repository's required review and CI before merging the design PR.

### Implementation acceptance (future, not claimed by this kickoff)

- [ ] Clean CONVO, unpushed WIP, MERGED and FINAL retain their identity and classification with `(D)`.
- [ ] Staged, modified, untracked, detached/unexpected branch, unfinished Git operations and unknown evidence refuse.
- [ ] Ignored build output is disposable; projections and declared checkout settings are restored before entry.
- [ ] Ignored legacy `.effort-agenda` history blocks removal until its owner preserves it and explicitly retires the source; unknown/partial homes and copy-only evidence do not bypass the hold.
- [ ] Doctor report is non-mutating; explicit migration preserves complete agenda history, store/lane identity and one writer across restart, conflict, failed copy and locator cutover.
- [ ] New agenda allocation and later discovery use the external per-repository home; a migrated agenda remains usable after real checkout removal and recreation.
- [ ] The retained local branch protects commits through Git GC; external branch movement is detected without reset.
- [ ] Failure injection at each persisted transition is recoverable, including a failed final catalog removal.
- [ ] Removing operational tracking in an isolated fixture permits catalog-based reconstruction without fabricated authority.
- [ ] Passive reads never hydrate; no claim, PR, effort, session or pair is concluded by checkout absence.
- [ ] Concurrent dehydration and real mux/Bridge entry cannot remove a live checkout or start duplicate agents.
- [ ] Fresh native fixtures, supported mixed providers and rollback produce truthful results; measure bytes and cold/warm latency.

## Proposal

Extend the existing Worktrees authority, Git helpers, resident maintenance and
entry fencing. Do not add a cleanup daemon, independent roster, mandatory extra
ref, or terminal-finalization shortcut. The nearby catalog is durable recovery
input, not permission to replay stale external effects.

The [architecture proposal](architecture.md) marks agent-recommended mechanics
and decisions still to qualify. The initial publication gate is the reviewed
kickoff; the full capability remains future work under #13781.

## Journal

### 2026-11-20 - Kickoff

- Captured three design rounds and the request to publish the planning foundation.
- Confirmed Git worktree removal preserves branches and inspected existing source
  seams without implementing or deleting any worktree.
- Filed #13781, #13783, #13784 and #13785. Implementation remains unassigned.
- Review identified the existing ignored checkout-local agenda-history hold;
  retained that guard and added a preservation/refusal qualification case.
- The operator then required the external per-repository worktree data home and
  a doctor. Added #13790 as the placement/migration prerequisite; no live
  migration or runtime implementation was performed.
