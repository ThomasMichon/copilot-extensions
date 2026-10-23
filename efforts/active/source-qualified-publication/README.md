# Source-Qualified Publication

- **Slug:** `source-qualified-publication`
- **Repo:** copilot-extensions
- **Branch(es):** independent, dependency-ordered PRs against `dev`
- **Created:** 2026-10-22
- **Status:** Draft
- **Vision:** `visions/plugins/agent-worktrees/README.md`, resident authoritative live-state database; `visions/picker/README.md`, consumer presentation
- **Umbrella issue:** #7937
- **Sub-issues:** #7936 · #7946

## Guiding Intent

Refresh and successful maintenance must converge on the selected producer's
authoritative publication without regressing a newer row, confusing sources,
or rebuilding a fleet in the caller. Observation-only publications must not
become semantic row writes or changes.

This closes existing authority and bounded-consumer intent; it introduces no
new service, alternate authority, or operator-specific integration.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Plan author | Source-grounded proposal and plan PR only | This effort and #7936/#7937/#7946 |
| Projection implementer (unassigned) | F1 semantic storage and negotiated observation protocol | Separate managed source worktree after admission |
| Receipt implementer (unassigned) | F2 targeted owner observation and receipts | Separate managed source worktree after F1 contract |
| Manager implementer (unassigned) | F3 source store and F4 maintenance convergence | Separate managed source worktree after contract admission |
| Existing owners | #7938, #7974, #7788 and publisher/recovery work | Read-only issue/PR/task state; ownership retained |

## Coordination

- **Topology:** plan-only PR, then the explicit staged graph in
  [implementation.md](implementation.md); no functional edits in the planning PR.
- **Host (owns PRs):** plan author owns only the plan PR. Each admitted
  implementation participant owns its own PR and gates.
- **Delegates:** none assigned to functional work by this document.
- **Handoff:** coordinator admits exact nonoverlapping files before execution;
  report collisions to that coordinator without taking another owner's work.
- Do not commandeer, contact, inspect sessions of, or collaborate with
  pre-existing Bridge agents. Do not use agent-chat or dispatch remotely.
  Only effort-owned native sub-agents, or newly created owned local Bridge
  agents if separately needed and authorized, may execute this effort.
  Forge/task metadata is the collision-detection surface. If review requires
  an existing Bridge agent, record a blocker rather than contacting it.
- Preserve #3761/#6748/#2619/#6086 and existing publisher fixes. This is
  neither whole-daemon completion nor authority to recover deployed services.
- No shared deployment, installation, force reset, or destructive live test.
- The proposal is discoverable through its three issue claims. Avoid a
  concurrent rewrite of the shared effort index in this isolated plan PR.

## Context

The inspected source baseline is `22fa78f06922d48a2bd91e1feb834b535b0defee`.
The bounded trace and proposed contracts are in
[implementation.md](implementation.md). The test obligations and command-level
lanes are in [validation.md](validation.md).

Existing #6595 established bounded resident reads and atomic held feeds.
The rollback recovery in #7933 preserves election/generation fencing and
truthful publication failure. Retain those fixes rather than replacing them.
#6748 remains the publisher-stall recovery owner; targeted acknowledgement is
not permission to restart or replace it. #2619 owns broader event-driven
production and #6086 owns workload/spawn-budget qualification.

## Request

Public-safe capture of the requested scope (private coordination identifiers
and paths intentionally omitted):

> “source-qualified monotonic Refresh”, “owner publication receipt”,
> “semantic-only row deltas”.
>
> “Do NOT edit any functional code in this planning turn”.
>
> “Produce source mini-effort detailed implementation plan with explicit
> producer/consumer API schema, source-qualified identity, generation/revision
> ordering, pending/degraded receipt semantics, atomic publication/heartbeat
> separation, bounded effect waits/no caller fleet scan, test paths and
> command-level validation.”
>
> “Need exact file ownership and staged PR graph.”

The required regression cases are preserved individually in the Validation
Plan and its linked matrix. Proposed API names, bounds and implementation
partitioning below are **agent-recommended**, not claims of existing support
or additional execution authorization.

## Plan

### P0 — Reviewed plan
- [x] Trace the three issues through actual producer, feed, loader and screen.
- [x] Specify interfaces, compatibility, ownership exclusions and staged graph.
- [ ] Obtain plan review and merge through the source contribution gates.

### F1 — Semantic publications
- [ ] Implement the negotiated observation/semantic split and changed-row
  persistence from [implementation.md](implementation.md).
- [ ] Preserve atomic head, row, order, observation and deletion publication.

### F2 — Owner receipts
- [ ] Implement bounded targeted owner observation and post-effect receipt
  publication; no caller scan or replay of destructive effects.
- [ ] Preserve partial success, contention, supersession and pending outcomes.

### F3 — Monotonic source store
- [ ] Route local/remote Refresh to the selected source and commit through Inbox
  to the same store consumed by live ticks.
- [ ] Keep point-row revision floors separate from complete-feed cursors.

### F4 — Maintenance convergence
- [ ] Distinguish completed process effects from confirmed roster publication.
- [ ] Converge through the existing feed without source reconnect or full sweep.

### F5 — Integrated qualification
- [ ] Run the exact regression matrix and producer-to-screen integration.
- [ ] Record source, release and any separately authorized rollout states
  independently. No live rollout is part of this plan-only turn.

## Validation Plan

- [ ] Locked revision 10 → point unlocked 11 → 20 unchanged feed ticks never
  regresses the row; exercise actual screen/loader/Inbox path.
- [ ] Local and remote same-ID fixtures remain isolated; remote Refresh makes
  zero local point reads.
- [ ] Delayed/out-of-order replies, reload epochs and owner-generation reset
  preserve authoritative ordering and UI-thread mutation.
- [ ] Gated reclaim publication remains pending; releasing publication
  converges without repeating reclaim or reconnecting.
- [ ] Contention, partial bulk success and concurrent new owner are explicit.
- [ ] Ten real-provenance publications of 1,000 unchanged rows produce zero
  semantic replacements/deltas after the initial baseline; one title change
  produces exactly one semantic replacement/delta.
- [ ] Freshness remains truthful; heartbeat is not reobservation; reconnect,
  deletion, order and generation replacement remain atomic.
- [ ] Complete [validation.md](validation.md), including compatibility and
  command-boundary lanes, before asserting implementation success.

## Proposal

**Agent-recommended; awaiting source review.** Keep one owner and one
source-qualified consumer store. Add an explicitly negotiated bounded
observation/receipt contract, not an optimistic row cache or second writer.
Separate fact revisions, observation freshness and transport heartbeat.
Detailed design: [implementation.md](implementation.md).

The bounded [current-feed entry amendment](startup-entry-proposal.md) addresses
the pre-control CLI startup gate while preserving existing command semantics
and exact shared-file owners. It requires source review before implementation;
it does not expand F1 into launch, installer or owner-side effects.

## Journal

### 2026-10-22 — Bounded planning
- Read the three live issue bodies and their empty prior comment/assignment
  state; inspected open PR titles and task searches before scoped plan claims.
- Confirmed the overwrite and local-before-remote paths in source, the absent
  observation intent in current-read requests, and timestamp-driven full-row
  persistence/deltas. No latency or IOPS measurement is claimed.
- Preserved existing source owners and adopted the no-existing-agent-contact,
  no-agent-chat, no-remote-dispatch boundary.
- Captured request constraints and all specified regressions; API details are
  explicitly proposals. No functional source or installed state was edited.
