# Inactive Worktree Relocation

- **Slug:** `inactive-worktree-relocation`
- **Repo:** copilot-extensions
- **Branch(es):** managed per-slice PR branches targeting `dev`
- **Created:** 2026-11-05
- **Status:** Active
- **Vision:** [agent-worktrees: identity-preserving-checkout-relocation](../../../visions/plugins/agent-worktrees/README.md#identity-preserving-checkout-relocation), [replay-safe-workspace-allocation](../../../visions/plugins/agent-worktrees/README.md#replay-safe-workspace-allocation)
- **Umbrella issue:** [#10879](https://github.com/ThomasMichon/copilot-extensions/issues/10879)

## Guiding Intent

Make an inactive managed checkout movable without making it a different unit
of agency or losing any checkout content. Deliver a complete, qualified flow
for stopped, clean, merged/final ordinary linked worktrees early; use
separately authorized adopter feedback before widening to inactive WIP,
retained sessions, pairs and other supported cases. A first usable flow is a
milestone, not completion of the broader inactive-worktree capability.

This is the canonical public source effort. It owns generic implementation
and qualification, not an adopter's actual migration, installation or cohort.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Source owner | Planning, implementation and source PR stewardship | #10879 and its linked managed PRs |
| Repository reviewer | Review of proposal and source deltas | Normal repository PR review |

## Coordination

- **Topology:** one source owner; dependency-ordered managed PRs against `dev`.
- **Host (owns PRs):** Source owner.
- **Delegates:** none; existing lifecycle and provider owners retain custody.
- **Handoff:** issue, canonical effort, current slice and unsettled effects;
  no new source-hosted authoring session.
- The source worktree remains needed and the effort remains Active after the
  proposal or first functional PR merges. Do not finalize it at either gate.
- #5293 is module/core decoupling, not relocation. #10863 owns standalone
  Driver extraction; consume its resulting seams, do not pre-patch or compete
  with that contributor's work.

## Context

The [owner and compatibility analysis](design.md) identifies current-root
assumptions separately from retained-path resolution. Allocation defaults are
not authority to invalidate an older checkout, and a recorded path alone is
not authority to mutate an arbitrary directory.

**Intent reconciliation:** extend the existing Worktrees vision with explicit
relocation and mixed-root guarantees; close its stable identity and replay-safe
allocation delta through existing owners. No new top-level vision, allocator,
session host, installer or background service is proposed.

Honor the existing [allocation/session acquisition pattern](../../../docs/patterns/workspace-allocation-and-session-acquisition.md),
[contract evolution pattern](../../../docs/patterns/service-contract-evolution.md)
and [execution-identity contract](../../../plugins/agent-worktrees/docs/execution-identity.md).
The plan changes intended behavior, not today's CLI documentation.

## Request

Public-safe capability request: support an identity-preserving move of
inactive worktrees, discover operational pointers across Worktrees tracking
and Copilot state, rebind to the new location, update Git, and transfer all
actual checkout content.

Supplied follow-up, verbatim:

> Great. Make an effort to support this and then practice on some of the oldest,
> merged or final worktrees, then work toward handling all inactive worktrees.

Scope separation: this effort provides the generic source capability and
qualification. Actual adopter practice is separately owned and authorized.
The ordered intent is retained: completed clean worktrees first, then wider
inactive cases, not a feature-complete monolith before the first useful flow.

The phase decomposition and technical safeguards below are
**agent-recommended realization**, not additional operator effect authority.

## Scope Approvals

Current admission is planning/coordination only: deduplicate and claim the public
issue, allocate one isolated source worktree, author this effort and needed
intent reconciliation, and drive the proposal through normal review/CI/merge.
Implementation requires the proposal gate and a subsequent explicit go-ahead.

No current permission to implement runtime code, move real worktrees, change
allocation defaults, install/update a runtime, stop owners/services, clear
claims, or perform an adopter pilot. Review and source merge grant none of
those effects. Fixture qualification in later source phases must use isolated,
authorized resources and the existing runner/lease/cleanup boundaries.

## Plan

### Phase 1 — First complete ordinary relocation flow
- [ ] After proposal review and execution admission, qualify the owner/pointer
  inventory and expose a read-only plan plus an explicit native move operation.
- [ ] Complete stopped, clean, merged/final ordinary linked-checkout relocation:
  fenced admission, all-content transfer, Git repair, authoritative rebinding,
  actual consumer verification and journaled interruption/recovery.
- [ ] Make old/new allocation roots coexist for this flow, retaining project,
  execution-space, Git-family and ledger authority; publish and qualify the
  complete source flow early through normal review/CI, not just scaffolding.
- [ ] Record precise supported/unsupported cases and hand the qualified source
  result to separately authorized adoption. Hold widening for initial pilot
  feedback; source approval itself never starts that pilot.

### Phase 2 — Broader inactive continuity
- [ ] Incorporate generic first-flow feedback; support inactive WIP with exact
  index and dirty/untracked/ignored preservation, not forced commit or discard.
- [ ] Qualify retained-session rebinding and actual resume; preserve conversation
  IDs/history and unrelated live session state through supported owner semantics.
- [ ] Support paired independent allocation roots and original interrupted
  creation/repair intent across default-root changes without replacement
  allocations, rewritten immutable receipts or authority weakening.
- [ ] Extend supported filesystem/provider cases through their owners and
  explicitly refuse those that cannot yet preserve identity and content.

### Phase 3 — Broad source acceptance
- [ ] Prove the [validation matrix](validation.md), including real native
  Windows cross-volume and POSIX flows, recovery, supported version skew and
  relocation followed by real resume/mutation/publication consumers.
- [ ] Publish accurate operating/recovery/support documentation with source
  changefiles for every changed plugin, then reconcile every Plan and
  Validation item or transfer it to a named tracked objective before Done.

## Validation Plan

The [focused validation matrix](validation.md) defines assertions and tier
boundaries. It is not a claim that runtime checks have already passed.

- [ ] Proposal: effort/vision structure, documentation consistency, public-safety
  audit, actual repository review verdict and required CI before merge.
- [ ] First flow: real linked Git checkout, stopped-clean acceptance,
  complete transfer and post-move consumers on same and different volumes;
  blockers and injected interruption preserve original/recovery custody.
- [ ] Mixed roots: existing resume/mutation/publication plus new allocations;
  foreign project/host/platform/Git-family/ledger and stale evidence still refuse.
- [ ] Broader cases: dirty bytes/index, retained-session resume, paired roots,
  original pending-intent recovery, path-sensitive artifacts and unrelated live
  records, with unsupported semantics explicitly gated.
- [ ] Native/compatibility: real Windows and POSIX filesystem semantics,
  version-skew/rollback controls and honest unexercised lanes; no installed
  rollout or real-data migration implied by fixture or source acceptance.

## Proposal

Review this effort, its linked design/validation obligations and the narrow
Worktrees vision extension before runtime realization. A merged proposal
satisfies only the source planning gate. The source objective remains open;
execution admission and adopter effects remain separate gates.

## Journal

### 2026-11-05 — Planning and coordination
- Rechecked public issue/PR title searches; no relocation claimant found.
  Created and claimed #10879; kept unrelated #5293 and #10863 separate.
- Inspected actual retained-path, receiving-namespace, source-publication and
  paired-allocation seams. Captured original-intent recovery, session-schema
  qualification and all-content preservation as explicit obligations.
- Captured and rechecked the supplied request and old-merged/final-first
  sequencing. Proposal is source-only; implementation and all live effects held.
