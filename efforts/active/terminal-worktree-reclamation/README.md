# Terminal Worktree Reclamation

- **Slug:** `terminal-worktree-reclamation`
- **Repo:** copilot-extensions
- **Branch(es):** serial PRs from one agent-dispatch worktree
- **Created:** 2026-08-31
- **Status:** Draft
- **Vision:** `visions/plugins/agent-dispatch` §Features/`terminal-worktree-reclamation`
  and §Behaviors/`allocator-reclaims-what-it-creates`
- **Umbrella issues:** #1488, #1918
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
The same ownership gap exists before task finality: a failed or yielded
embodiment attempt can settle while its created worktree is absent from the
spawn reservation, so repeated retries accumulate unattributed worktrees even
though the task remains queued.

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

Additional operator request:

> We need to ensure that these dispatched autopilot workers and tracked and
> attributed correctly. We also need to ensure they are garbage collected.

## Plan

### Phase 1 — Durable allocation and resolution contract
- [x] Extend spawn/embodiment records with the exact created worktree identity
      and whether the spawn created it or targeted a pre-existing worktree.
      Missing legacy origin is `unknown`, never inferred as owned.
- [x] Pre-create locally managed worker worktrees and durably bind them to the
      exact task, reservation, attempt, driver, and creating host before the
      worker body is allowed to run.
- [x] Publish that allocation provenance through `agent-worktrees list --json`
      so consumers never have to infer dispatch ownership from a session title.
- [x] Bind allocation ownership to the durable creating
      machine/environment. Retain the individual supervisor process id only as
      audit provenance so a same-host successor can reclaim across restarts.
- [ ] Require a terminal embodied task to carry a resolution outcome sufficient
      to distinguish landed work from explicit abandonment.
- [x] Preserve version-skew behavior: older tasks without the new evidence are
      held for attention, and a newer supervisor facing an older ground layer
      without the required atomic lifecycle operation also holds visibly.
- [x] Define the allocation record as dispatch-owned provenance that points at
      ground-layer claim/finality authority, not a second cleanup or
      prune-eligibility ledger.

### Phase 2 — Supervisor reclamation loop
- [x] Reconcile release-requested, failed, and yielded attempts before reserving
      a replacement: retire the exact session, delegate safe conclusion to
      agent-worktrees, and retain a visible retryable cleanup state until the
      created allocation is final or explicitly held.
- [x] Reconcile terminal `completed` and `abandoned` tasks whose embodiments
      were created by this machine/environment; foreign-host allocations and
      pre-existing targeted worktrees are never evaluated for removal.
- [x] Drive the supervisor-owned session to a terminal exit, or keep the
      allocation visibly pending/escalated until it exits; a terminal task with
      a live body is not silently forgotten.
- [ ] Release the task's inbound worktree claim, then delegate landed
      verification and exact-worktree reclamation to agent-worktrees.
- [ ] Before releasing the final claim, prove no other nonterminal dispatch
      allocation targets the same worktree, or include the complete inbound
      claim set in the ground-layer atomic eligibility check.
- [x] Permit automatic abandoned reclamation only when the ground layer proves
      the workspace has no uncommitted or unmerged content. Dirty abandoned
      worktrees are held for operator attention, never reset by the daemon.
- [x] Require the ground layer to re-check liveness, claims, follow-up state,
      and upstream safety atomically at removal so a concurrent resume or new
      claim cannot race cleanup.
- [x] Persist cleanup state and errors so restart/retry is idempotent and a
      blocked cleanup cannot disappear from operator view.

### Phase 3 — Operations and existing backlog
- [ ] Add status/doctor output for pending, blocked, and completed reclamations.
- [ ] Provide a dry-run inventory for terminal historical embodiments and an
      explicit, safety-checked adoption path requiring operator attestation per
      worktree; never infer ownership or bulk-delete unproven legacy worktrees.
- [ ] Document the producer boundary and remove producer-specific worktree
      deletion where the generic owner now covers it.

## Validation Plan

- [x] A completed task with landed work is reclaimed only after the worker is
      confirmed gone and agent-worktrees verifies the work is safe.
- [x] An abandoned task is unwound through the explicit abandoned-resolution
      path only when it is already clean; dirty or unmerged abandoned work is
      retained for attention and never automatically reset.
- [x] Started, suspended, queued, and live terminal-owner sessions are untouched.
- [x] A terminal supervisor-owned session is driven to exit or remains visibly
      pending; it cannot accumulate invisibly.
- [x] Unknown liveness, unmerged content, missing resolution evidence, and
      cleanup failures remain visible and retryable.
- [x] A foreign-host allocation is never treated as locally absent or reclaimed
      by the wrong supervisor.
- [x] An allocation created before a supervisor process restart is still owned
      and reclaimed by the successor in the same machine/environment scope.
- [x] A pre-existing `--target-worktree` allocation and a legacy
      origin-unknown allocation are never removed by the reclamation loop.
- [ ] When two tasks share one worktree, terminalizing one leaves the worktree
      intact until every other inbound allocation is terminal and released.
- [x] A concurrent worktree resume or new claim wins the atomic removal check
      and leaves the worktree intact.
- [x] A headless body that allocated no worktree is a no-op.
- [x] Repeated yielded or failed attempts do not accumulate one worktree per
      attempt; created worktrees are concluded while targeted, reused, dirty,
      live, foreign-host, and origin-unknown worktrees are preserved.
- [x] Repeated reconciliation and process restart do not repeat destructive
      work or lose cleanup state.
- [x] Older task records and older ground-layer versions fail closed without
      minting unsafe compatibility behavior.
- [x] Producer-owned domain records are neither inferred nor deleted by the
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

### 2026-09-03 — Attempt-level reclamation
- Opened #1918 after confirming that a yielded or failed nonterminal attempt can
  lose its created worktree identity and leak one allocation per retry.
- Extended the effort through the attempt boundary: provenance is recorded
  before execution, and replacement remains blocked until exact safe cleanup is
  complete or visibly held.
- Implemented immutable task/reservation/attempt provenance in both dispatch and
  worktree records, with opaque retention for newer or malformed provenance.
- Added the restart-safe `releasing` reservation state and routed failed,
  yielded, confirmed-gone, and terminal reservation-created allocations through
  exact session teardown plus exact-ID managed removal before retry eligibility.
- Preserved targeted, reused, dirty, locally committed, live, foreign-host,
  provenance-unknown, obligated, and lifecycle-raced worktrees.
- Reconciled onto the concurrent exact-ID managed-removal implementation and
  made dispatch use that fresh-liveness-checked deletion path directly.
- Closed pre-submit fencing gaps by making the release transition atomic,
  treating `releasing` as active for exclusive-key uniqueness, counting live
  releasing bodies against process capacity, and making one-shot spawning run
  its own exact cleanup attempt.
- Validation: 228 focused agent-dispatch tests, 203 focused agent-worktrees
  lifecycle/tracking tests, 22 worktree guards (2 skipped), and repository
  lint/install/version/payload/docs gates passed.
