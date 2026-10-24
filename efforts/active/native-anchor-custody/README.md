# Native Anchor Custody

- **Slug:** `native-anchor-custody`
- **Repo:** copilot-extensions
- **Branch(es):** reviewed proposal, then bounded claims implementation against `dev`
- **Created:** 2026-10-24
- **Status:** Draft
- **Vision:** [Worktree Lifetime & Agency State](../../../visions/plugins/agent-worktrees/README.md): Claims, leases, and obligations; finalization-joins-durable-obligations
- **Umbrella issue:** #4488, bounded explicit claim-adoption slice
- **Related work:** #5997 and contributor-owned PR #6009

## Guiding Intent

A live controller operating from a registered repository's anchor must be able
to record current custody of unfinished resources in the existing native
claims ledger. Current custody is not historical creator provenance. A shared
parent session does not make an unrelated finalized worktree the resource's
owner, and a presentation-layer anchor handle is not a canonical claim ref.

Reuse the reserved `@anchor` identity and existing receiving-side writer.
Do not grant anchor code-write permission, take over another controller,
release unsettled resources, or turn session death into settlement evidence.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| `welded-piston` | Proposal, claims implementation, review and source delivery | Managed source worktree |
| Existing anchor controller | Accept and verify its own bounded current-custody repair after supported delivery | Existing admitted owner channel |
| PR #6009 contributor | Retains main-checkout session-registration implementation | Original contribution thread |

## Coordination

- **Topology:** one reviewed plan followed by one independently reviewable
  claims slice; no shared checkout or implementation branch.
- **Host (owns PRs):** `welded-piston`.
- **Delegates:** none for the initial cohesive claims trace.
- **Handoff:** preserve the canonical effort, unresolved gates, native repair
  refusal, resource custody and exactly one next action.
- **Overlap boundary:** PR #6009 retains registration, activation, session
  binding and its related lifecycle guard changes. This effort does not copy,
  replace or supersede that contribution. Broader linear session lineage and
  dispatch/handoff scope remain with #4488, not silently completed here.
- **Safety prerequisite:** do not broaden anchor-ledger materialization into
  worktree-class repositories until the contributor-owned ledger isolation
  and lifecycle protections are reviewed and merged. Preserve the original
  contribution and cooperate on its thread rather than reimplement its guards
  in a competing PR.

## Context

The source already defines canonical `<machine>/<project>/@anchor` references,
atomic anchor-ledger creation and conservative anchor claimant-liveness.
Singleton run journaling can lazily create that ledger. The explicit
`claims add --owner-ref` path, however, requires the resolved tracking record
to exist. A live registry's `anchor-<project>` presentation handle is treated
as an ordinary missing worktree, not as the reserved identity.

At the inspected source pin, ordinary `list_records` includes every ledger
and the finalize/cleanup entrypoints have no reserved-anchor guard. PR #6009
proposes those protections and reader enrollment alongside registration.
Therefore reusing a reserved identifier alone is not enough to safely enable
a cold ledger for worktree-class anchors. Its isolation/lifecycle contribution
is a genuine prerequisite, not evidence already delivered by the singleton
claim implementation.

The motivating owner confirmed a live anchor session and a child with an open
PR but no creator `owner_ref`. A different finalized worktree shared that
session; it finalized before the child existed. Rewriting that retired
worktree's history would therefore be an incorrect repair. The actual
requirement is an explicit, durable current-custody claim.

This closes the existing claims-accountability intent, not a new session
hosting architecture. Preserve the existing single-writer, receiving-project,
resource-settlement and anchor hygiene contracts.

## Request

The operator requested the following; private repository/worktree identifiers
are redacted without changing the requested behavior:

> Fix the claims ref; [private control-repository] worktree [retired vessel]
> should not have been allowed to finalize if it owns a copilot-extensions
> worktree with an open PR

After the native writer refused the live anchor's presentation handle, the
operator selected:

> native-anchor-custody

The selected option was: "Add native anchor-custody adoption support upstream,
preserving historical creator provenance." Investigation corrected the
historical-ownership premise; it did not waive the requested accountability
or completion gates.

## Plan

The implementation decomposition below is **agent-recommended**.

### Phase 1 - Reviewed bounded contract

- [x] Coordinate the explicit claims slice on #4488 without taking PR #6009's
  contributor-owned registration work.
- [ ] Land this proposal through current-head review and required CI before
  implementation.
- [ ] Cooperate on the original PR #6009 and wait for its required ledger
  isolation/lifecycle protections to merge before enabling worktree-class
  anchor materialization. Do not copy or supersede its implementation.

### Phase 2 - Native current-custody claims

- [ ] Reuse the reserved `@anchor` ledger and canonical owner ref for explicit
  native claim production; diagnose presentation aliases without guessing a
  managed worktree or changing historical creator fields.
- [ ] Resolve the receiving repository and local execution identity before
  initialization or mutation. Refuse unavailable, foreign, ambiguous or
  unsafe owner paths without a success-shaped deferred local repair.
- [ ] Materialize a missing anchor ledger through the existing atomic native
  creation/writer machinery, then journal the explicitly adopted obligation.
  Preserve an existing ledger's claims and sessions under contention.
- [ ] Make forward and reverse claims discovery report the durable anchor
  custody. Keep resource settlement distinct from session/controller liveness
  and retain all active child/PR obligations.
- [ ] Preserve historical `owner_ref`, parent-session provenance, finalized
  unrelated worktrees and existing handoff/producer reservations.
- [ ] Document the canonical invocation and its current-custody meaning;
  include the shipping owner's changefile.

### Phase 3 - Qualification and source delivery

- [ ] Exercise focused unit and real CLI controls below with existing test
  containment and unchanged deadlines.
- [ ] Land the claims slice through current-head review and required CI.
- [ ] Record source, release and installed states separately. Perform any live
  adoption only through supported, authorized delivery and the existing
  controller's affirmative acceptance; never execute unreviewed source against
  the live tracking store or patch installed files.

### Phase 4 - Conclusion

- [ ] Resolve or explicitly transfer every Plan and Validation item to a named
  tracked objective. Do not claim all of #4488 complete from this slice.
- [ ] Journal the actual native adoption result or exact remaining delivery
  blocker, archive this effort and finalize through the native owner.

## Validation Plan

- [ ] A cold registered worktree-class anchor can explicitly journal a child
  claim through the real CLI without a pre-existing ledger or a managed anchor
  checkout; repeated calls are idempotent.
- [ ] Concurrent first use preserves independent claims and existing session
  data through the native receiving writer.
- [ ] Forward/reverse discovery identifies canonical anchor custody while the
  child's original creator and unrelated finalized records remain unchanged.
- [ ] Invalid aliases, foreign execution spaces, wrong repositories, unsafe
  paths, unavailable registration and ambiguous writes fail visibly without
  changing other ledgers.
- [ ] An open PR and unsettled child remain active; no session-death, missing
  ledger, age or incomplete inbound lookup is treated as settlement proof.
- [ ] Existing anchor write, finalize and reclamation protections are not
  broadened by this slice; singleton run claims and ordinary managed claims
  retain their behavior.
- [ ] Prove the prerequisite ledger isolation/lifecycle protections are present
  in the actual implementation base, not merely described in an open PR.
- [ ] Focused lint, receiving-writer/claims tests, documentation impact and
  install contract pass. Required CI remains change-scoped.
- [ ] Record native Windows and available genuine POSIX/clean-root tiers,
  explaining unavailable lanes rather than substituting compatibility shells.
- [ ] Verify released/installed native repair through the existing owner, or
  explicitly retain its named delivery blocker without reporting repair done.

## Proposal

Extend explicit claim production to materialize the already-reserved anchor
ledger through the existing native authority, using its canonical ref rather
than a live-session display handle. An adopted outbound resource claim records
current responsibility without changing the child's historical creator.
Keep this separate from PR #6009's session-registration contribution and from
the rest of #4488's linear-lineage/handoff contract.

## Journal

### 2026-10-24 - Native repair refusal and bounded source proposal

- The existing owner verified current anchor custody and attempted the native
  explicit claim operation. The presentation handle resolved to a missing
  managed record, so no claim was written and all child/PR obligations remain.
- Reconciled the established `@anchor` implementation and #4488 with the
  operator's selected native-custody option. PR #6009 is open and belongs to
  its original contributor; it is not a delivered dependency or replacement
  target.
- Inspected ordinary ledger listing and finalize/cleanup routing at the source
  base. The proposed #6009 isolation/lifecycle protections are genuinely
  required before broader cold-anchor materialization; recorded that
  prerequisite rather than pre-patching the contributor's open change.
- Created an isolated source workspace after transient allocation contention
  cleared through the normal retry. No lock was removed or bypassed.
- The request capture was checked against the operator's literal request and
  selected option. Only private identifiers are redacted. No implementation,
  live claim repair or installed rollout has occurred.
