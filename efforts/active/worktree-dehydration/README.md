# Worktree Dehydration

## Goal

Deliver reversible worktree dehydration and rehydration, automatic stop/resume
integration, and complete operator controls. Preserve branch identity, agency
records, durable data and unfinished responsibility. The agenda-storage
prerequisite is complete; do not bypass its outstanding doctor hold.

Current operator assignment:

> Drive the PR, then make the fixes to the worktrees so that cleaning finalized worktrees won't clear the agendas. We'll want to ensure that the Agendas tool matches the expected storage location

Retained placement and doctor requirements:

> We need to move `.effort-agenda` out of the worktrees' actual footprints; it needs to be in ~/.<repo>/worktrees/<worktree-id>

> We'll need a "doctor" for that

Additional operator direction (private feed name generalized for publication):

> Fix the stub package version to fit within the seasoning; it should be something provided via [configured feed]

Latest dependency assignment (private feed name generalized for publication):

> Since this machine has to pull packages from the [configured feeds], help update the dependencies to use a set that can resolve from this machine

> This just means we have to work around with different dependencies.

Earlier inspection found the selected stub wheels younger than the seven-day
cutoff, and older pins did not fix preparation. The latest assignment permits
replacement of the dependency that requires those stubs, with the directly
dependent consumer changes needed to preserve behavior and type checking.
Preserve the configured feeds, seasoning and integrity checks; do not substitute
a public package source. Prove the selected set through the normal local checker
consumer, not only an isolated or range-only resolver result.

- **Slug:** `worktree-dehydration`
- **Repo:** copilot-extensions
- **Branch(es):** one reviewed design PR; later dependency-ordered delivery PRs
- **Created:** 2026-11-20
- **Status:** Active
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
| agenda-storage-driver | Owns #13790 source changes, consumer alignment, tests and PR delivery | This effort and #13790 |
| checker-dependency-driver | Owns the assigned configured-feed checker dependency follow-up | This effort's dependency follow-up |
| dehydration-driver | Owns reversible storage, automatic entry, Manager delivery and their dependency-ordered PRs | This effort and #13783-#13785 |

## Coordination

- **Topology:** one kickoff design PR; agenda placement/doctor precedes storage activation, then automatic entry and Manager integration.
- **Host (owns PRs):** dehydration-driver owns the assigned #13783-#13785 source delivery.
- **Delegates:** none; implementation proceeds in dependency order.
- **Handoff:** the canonical plan is here; keep the umbrella open until the complete operator flow is qualified.

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
The operator has now assigned the remaining implementation phases. The
reviewed architecture remains the starting point; implementation must qualify
unresolved mechanics rather than treat recommendations as settled API.

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
- **Superseding execution assignment:** the operator subsequently directed
  source fixes for agenda retention and Agendas-tool placement. This assigns
  #13790 implementation, relevant tests and normal PR delivery after the
  reviewed design. It does not assign automatic dehydration, a live operator
  agenda migration, installation or fleet rollout.
- **Checker dependency follow-up:** select a compatible dependency set that
  resolves through the configured feeds, with relevant local preparation,
  checks, directly dependent behavior-preserving consumer changes and normal
  source PR delivery. This includes replacing a dependency rather than only
  changing its version. It supersedes retaining the earlier failing stub pin,
  not the feed, seasoning or integrity requirements. It does not authorize an
  agenda migration, doctor workaround or fleet rollout.
- **Current implementation assignment:** the operator said, “Ah. Get to
  building that then,” after being told that reversible storage, automatic
  stop/resume and Manager controls were not implemented. This assigns Phases
  3-5, including source changes, directly relevant tests, documentation and
  normal PR delivery. Preserve the existing clean-Git, expected-branch,
  durable-data and ownership boundaries. This does not authorize live agenda
  migration, a doctor workaround, production installation or fleet rollout.

## Plan

### Phase 1 - Review the kickoff design

- [x] Capture the request and its superseded alternatives separately from recommendations.
- [x] Extend standing intent without presenting the feature as implemented.
- [x] Describe storage transitions, catalog recovery, branch preservation, entry ordering and qualification.
- [x] File #13781 with dependency-ordered work in #13790, #13783, #13784 and #13785.
- [x] Publish and merge the kickoff after the repository's review and CI gates (#13788).

### Phase 2 - Prove external agenda placement and doctor (#13790)

- [x] Put new managed agendas under `~\.<repo>\worktrees\<worktree-id>`, outside actual checkouts, through the existing data-placement owner.
- [x] Provide report-first doctor inspection and explicit lossless repair/migration for existing checkout-local lanes.
- [x] Prove history and store identity, single-writer relocation, target/locator conflicts and interrupted recovery before source retirement.
- [x] Align the Agendas tool with the same owner-published external placement and prove agenda retention across finalized-checkout cleanup.

### Checker dependency follow-up (completed)

- [x] Select a compatible checker dependency set from the configured feeds without weakening seasoning or hash checks.
- [x] Prove the selected set with normal local checker preparation and the relevant dependency consumer checks.
- [x] Deliver the bounded dependency change through the repository's normal review and CI gates (#14004).

### Phase 3 - Prove reversible storage (#13783)

- [ ] Implement the owner-mediated catalog and a real clean WIP round trip at the original path.
- [ ] Prove branch retention, interrupted-transition recovery and catalog-aware roster reconstruction.
- [ ] Integrate claims, paired worktrees, cleanup, Git GC and durable-data placement without semantic finalization.

### Phase 4 - Prove automatic stop and resume (#13784)

- [ ] Extend existing post-exit/resident work with actual retirement checks and a shared entry/removal fence.
- [ ] Hydrate before every path-dependent mux and Bridge entry; preserve prompts and session lineage.
- [ ] Prove repeated automatic cycles, concurrent requests, restart recovery and supported provider skew.

### Phase 5 - Prove the complete operator flow (#13785)

- [ ] Add `(D)`, visible hold/refusal reasons, auto/manual policy and manual Manager actions.
- [ ] Qualify native Windows and Linux/WSL flows, storage reduction and resume cost with disposable fixtures.
- [ ] Publish implementation and update reality docs only after their actual behavior is proved.

## Validation Plan

### Kickoff acceptance

- [x] Re-read Goal, Request, scope and proposal against each operator amendment.
- [x] Check changed effort/vision structure, whitespace, local links and public-surface sanitization.
- [x] Verify live issues, dependencies and published design links; preserve open implementation trackers.
- [x] Obtain the repository's required review and CI before merging the design PR.

### Implementation acceptance

- [ ] Clean CONVO, unpushed WIP, MERGED and FINAL retain their identity and classification with `(D)`.
- [ ] Staged, modified, untracked, detached/unexpected branch, unfinished Git operations and unknown evidence refuse.
- [ ] Ignored build output is disposable; projections and declared checkout settings are restored before entry.
- [x] Ignored legacy `.effort-agenda` history blocks removal until its owner preserves it and explicitly retires the source; unknown/partial homes and copy-only evidence do not bypass the hold.
- [x] Doctor report is non-mutating; explicit migration preserves complete agenda history, store/lane identity and one writer across restart, conflict, failed copy and locator cutover.
- [x] New agenda allocation and later discovery use the external per-repository home; a migrated agenda remains usable after real checkout removal and recreation.
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
- #13788 merged after approval and required CI. The operator then assigned
  #13790 source delivery and Agendas-tool alignment; later dehydration phases
  and live migration/rollout remain separate.
- Implemented the agenda prerequisite through the existing Worktrees and efforts
  owners. Durable data uses a separate `worktree-data` child beneath the required
  external worktree home, so launch-recovery sidecars remain independent.
  Existing external registrations remain readable; no live store moved.
- The targeted doctor preserves a complete export and the fenced original
  outside the checkout. Exact retry verifies accepted history after interrupted
  retirement, later target work and removal of the original checkout.
- Native Windows qualification passed 185 Worktrees tests, 51 efforts tests and
  one actual Agendas-consumer test. Thirteen platform-specific cases were
  skipped. Disposable fixtures prove first allocation, real Git checkout removal
  and recreation, and fresh reader discovery without a checkout or tracking
  record. This proves storage behavior, not cold-start latency, installed
  adoption, hardware power-loss behavior or a fleet rollout.
- Source implementation and local qualification are complete; delivery still
  requires the source PR's review, required CI and merge. The remaining
  dehydration phases stay unassigned.
- Review corrections fence launch-only containers during inventory, publish a
  legacy-path refusal before current-home allocation, and inspect published
  external agenda contents rather than treating an absent local source as
  proof of health. Focused regressions cover each correction.
- The follow-up native Windows packet passed 192 Worktrees tests (8 skipped),
  29 efforts tests (2 skipped) and the actual Agendas-consumer retention test.
  The earlier complete-history rescue and PowerShell evidence still applies.
- These are functional checks with a larger fixture budget, not latency results.
- The configured-feed dependency replacement merged as #14004. Marko 2.2.5
  passed normal checker preparation and the focused 121-test packet; this
  delivery does not qualify installed agenda behavior or implement dehydration.
- The operator assigned Phases 3-5: reversible storage first, then automatic
  stop/resume, then the complete Manager flow. Work on #13783 is claimed; later
  slices keep their existing issue and review gates.
- Required CI exposed whole-file strict typing errors in touched files. Added
  explicit types and preserved the runtime behavior. Diagnostic MyPy passes
  all 17 changed Python files; exact installer-consumer CI remains required.
  Local preparation is blocked by the configured feed's young stub artifacts.
  Older pins also refuse, so no ineffective downgrade or policy bypass is kept.
- A later review found that a healthy projection could hide a missing earlier
  accepted operation. External health now uses the existing bounded
  complete-export validator. The efforts packet, including the missing-history
  regression, passes 30 tests (2 skipped); 31 focused reminder tests also pass. Diagnostic typing
  passes the four changed files under both Linux and Windows stubs; required
  exact-head CI and a fresh review still gate delivery.
- Installer-consumer CI now passes strict typing on both platforms. Added the
  missing Manager qualification changefile. To keep maintenance below its
  module-size cap, moved the six unchanged targeted-doctor routes into their
  own module. Sixteen doctor tests pass, including success/failure no-fallthrough
  cases; AST equality confirms branch preservation. Source AST, runtime-path,
  marketplace baseline, module-size and effort-structure guards also pass.
- Clarified the first-export recovery boundary after a review summary raised
  partial-export retry. Unpublished partial files are retained; an unfenced
  matching source can use a newly selected empty export directory. Once fenced,
  recovery keeps the exact verified export and paths. Both refusal/recovery
  regressions pass in the 32-test efforts packet (2 skipped), without deleting
  partial evidence or allocating a second store.
- The assigned dependency follow-up used the canonical retained resolver to
  examine the complete compatible CommonMark-stub range, including the oldest
  available releases. Exact candidates still fail the seven-day cutoff because
  the configured registry dates their artifacts to 18 November 2026. A final
  full-graph resolution finds no eligible `types-commonmark>=0.9.0,<0.10`.
  The original pin and hashes remain unchanged; a range-only resolution is not
  accepted as proof that an exact locked set can be installed.
- The operator clarified that the follow-up should use different dependencies.
  Replaced the Python CommonMark dependency and its separate stub package with
  Marko 2.2.5, preserving source-comment grammar, literal examples and refusal
  behavior. The exact full checker graph resolves through the configured feeds,
  and normal SHA256-locked checker preparation succeeds. Other locked versions
  and hashes are unchanged.
- The focused GitHub/Gitea metadata and runtime-dependency packet passes all
  121 tests. An earlier parallel run timed out in the unchanged native Manager
  handoff guard; the complete serial rerun passes with the original limits.
  Whole-file strict checking passes both Linux and Windows targets. The Python
  declaration-age audit reports 772 declarations and zero violations; the
  13-plugin install contract, touched-Python lint and whitespace checks pass.
  These are source/test-environment results, not an installed runtime rollout
  or a repair of the separate agenda-doctor qualification blocker.
