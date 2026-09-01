# Terminal Worktree Reclamation

- **Slug:** `terminal-worktree-reclamation`
- **Repo:** copilot-extensions
- **Branch(es):** serial PRs from one agent-dispatch worktree
- **Created:** 2026-08-31
- **Status:** Draft
- **Vision:** `visions/plugins/agent-dispatch` §Features/`terminal-worktree-reclamation`
  and §Behaviors/`allocator-reclaims-what-it-creates`
- **Umbrella issue:** #1488
- **Dependencies:** #1312 (`worktree-finality-and-obligations`)

## Guiding Intent

An embodiment allocator owns the workspace it creates through terminal
reclamation. Producers decide when their domain work is legitimately completed
or abandoned; agent-dispatch records and safely retires the sessions and
worktrees it allocated for that task.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| agent-dispatch maintainer | Vision, lifecycle design, implementation, and PR ownership | isolated copilot-extensions worktree |
| agent-worktrees maintainer | Review the claim-release, finality, and atomic removal contract | repository review |

## Coordination

- **Topology:** serial per-slice PRs from one worktree
- **Host (owns PRs):** agent-dispatch maintainer
- **Delegates:** none initially
- **Handoff:** keep this README current at phase boundaries; a successor resumes
  from the next unchecked Plan item

## Context

The supervisor already records spawn reservations and settles them when tasks
become terminal. It can also detect whether embodied workers are live or gone.
What it does not yet own is the final workspace obligation: worktrees allocated
for completed or abandoned tasks can remain registered indefinitely, forcing
each producer to invent cleanup logic or leaving stale workspaces behind.

The generic boundary is:

- the producer owns domain reservations, artifacts, external records, and the
  decision that a task is terminal;
- agent-dispatch owns the exact session/worktree allocation it created;
- agent-worktrees remains the authority for liveness, safe resolution,
  finalization, and removal.

This effort depends on #1312's worktree finality/obligation model. Dispatch must
release the terminal task's inbound claim before asking the ground layer to
re-derive prune eligibility, and the final liveness/claim/follow-up/upstream
check must remain atomic with removal. Dispatch records allocation provenance;
it does not create a parallel prune-eligibility store.

## Request

Build a cleanup daemon into agent-dispatch that removes worktrees from completed
or abandoned tasks it embodied, without making calling services reimplement
worktree lifecycle safety.

## Plan

### Phase 1 — Durable allocation and resolution contract
- [ ] Extend spawn/embodiment records with the exact created worktree identity
      and whether the spawn created it or targeted a pre-existing worktree.
      Missing legacy origin is `unknown`, never inferred as owned.
- [ ] Bind allocation ownership to the durable creating
      machine/environment. Retain the individual supervisor process id only as
      audit provenance so a same-host successor can reclaim across restarts.
- [ ] Require a terminal embodied task to carry a resolution outcome sufficient
      to distinguish landed work from explicit abandonment.
- [ ] Preserve version-skew behavior: older tasks without the new evidence are
      held for attention, and a newer supervisor facing an older ground layer
      without the required atomic lifecycle operation also holds visibly.
- [ ] Define the allocation record as dispatch-owned provenance that points at
      ground-layer claim/finality authority, not a second cleanup or
      prune-eligibility ledger.

### Phase 2 — Supervisor reclamation loop
- [ ] Reconcile terminal `completed` and `abandoned` tasks whose embodiments
      were created by this machine/environment; foreign-host allocations and
      pre-existing targeted worktrees are never evaluated for removal.
- [ ] Drive the supervisor-owned session to a terminal exit, or keep the
      allocation visibly pending/escalated until it exits; a terminal task with
      a live body is not silently forgotten.
- [ ] Release the task's inbound worktree claim, then delegate landed
      verification and exact-worktree reclamation to agent-worktrees.
- [ ] Before releasing the final claim, prove no other nonterminal dispatch
      allocation targets the same worktree, or include the complete inbound
      claim set in the ground-layer atomic eligibility check.
- [ ] Permit automatic abandoned reclamation only when the ground layer proves
      the workspace has no uncommitted or unmerged content. Dirty abandoned
      worktrees are held for operator attention, never reset by the daemon.
- [ ] Require the ground layer to re-check liveness, claims, follow-up state,
      and upstream safety atomically at removal so a concurrent resume or new
      claim cannot race cleanup.
- [ ] Persist cleanup state and errors so restart/retry is idempotent and a
      blocked cleanup cannot disappear from operator view.

### Phase 3 — Operations and existing backlog
- [ ] Add status/doctor output for pending, blocked, and completed reclamations.
- [ ] Provide a dry-run inventory for terminal historical embodiments and an
      explicit, safety-checked adoption path requiring operator attestation per
      worktree; never infer ownership or bulk-delete unproven legacy worktrees.
- [ ] Document the producer boundary and remove producer-specific worktree
      deletion where the generic owner now covers it.

## Validation Plan

- [ ] A completed task with landed work is reclaimed only after the worker is
      confirmed gone and agent-worktrees verifies the work is safe.
- [ ] An abandoned task is unwound through the explicit abandoned-resolution
      path only when it is already clean; dirty or unmerged abandoned work is
      retained for attention and never automatically reset.
- [ ] Started, suspended, queued, and live terminal-owner sessions are untouched.
- [ ] A terminal supervisor-owned session is driven to exit or remains visibly
      pending; it cannot accumulate invisibly.
- [ ] Unknown liveness, unmerged content, missing resolution evidence, and
      cleanup failures remain visible and retryable.
- [ ] A foreign-host allocation is never treated as locally absent or reclaimed
      by the wrong supervisor.
- [ ] An allocation created before a supervisor process restart is still owned
      and reclaimed by the successor in the same machine/environment scope.
- [ ] A pre-existing `--target-worktree` allocation and a legacy
      origin-unknown allocation are never removed by the reclamation loop.
- [ ] When two tasks share one worktree, terminalizing one leaves the worktree
      intact until every other inbound allocation is terminal and released.
- [ ] A concurrent worktree resume or new claim wins the atomic removal check
      and leaves the worktree intact.
- [ ] A headless body that allocated no worktree is a no-op.
- [ ] Repeated reconciliation and process restart do not repeat destructive
      work or lose cleanup state.
- [ ] Older task records and older ground-layer versions fail closed without
      minting unsafe compatibility behavior.
- [ ] Producer-owned domain records are neither inferred nor deleted by the
      generic reclamation loop.

## Proposal

Add a terminal-reclamation pass to the singleton supervisor. It consumes only
agent-dispatch-owned embodiment allocations created by that supervisor's host,
treats the task's terminal resolution as the producer's handoff, retires the
session it spawned, releases the inbound task claim, and delegates an atomic
workspace eligibility/removal decision to agent-worktrees. The pass is durable,
idempotent, and fail-closed: absence of exact ownership, terminal resolution,
definitive liveness, a clean abandoned workspace, or compatible ground-layer
capability leaves the worktree in place with a visible reason.

## Journal

### 2026-08-31 — Kickoff
- Captured the allocator-owns-reclamation boundary in the agent-dispatch vision.
- Opened #1488 as the public coordination token.
- Carved this effort to define the durable allocation contract, singleton
  reclamation loop, operational visibility, and safe historical adoption.
