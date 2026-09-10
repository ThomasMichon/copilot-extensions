# Phase 9 - Foundational state-machine architecture

Linked from the effort [`README.md`](README.md) Plan. Read this only when
working this phase.

## Why this phase exists

Phases 1-8 accumulated a real, working reviewer recipe by patching concrete
failures as they were found -- terminal-reservation races (Phase 5), a
supersession incident (Phase 6), environment-drift credential failures
(Phase 7), and a mature downstream deployment's own independent hardening
(Phase 8). That downstream deployment's file-by-file comparison (Phase 8)
confirms the pattern: the same classes of problem (attempt-budget
bypass, unlabeled cancellation, base-only re-trigger, stale approval,
dirty-worktree false positives) recur because there is no single, explicit
model of **what state a review, a task, or a session is in and what the
valid next move from that state is**. Each patch fixed one symptom without
naming the state machine it belonged to.

This phase defines that model directly, before any more point fixes land,
so Phase 8's candidates -- and any future contribution -- are **expressed as
behaviors of the state machines below**, not bolted on as independent
patches.

**Scope boundary:** this phase designs the model. It does not implement it
end to end; implementation is broken out per sub-phase below once the
design clears this effort's review gate.

## The three state machines, and why three (not one)

A review-loop deployment is a distributed system with no ambient
transaction: a pull-request provider, a task ledger, and a session host all
move independently, and none of them can block the others mid-transition.
Naming three separate machines -- rather than one combined state -- is what
makes that tractable: each has its own authority, its own failure modes,
and its own observation source, and most of the "weird state" incidents
this effort has hit (Phases 5-8) are really a **coupling** bug between two
of these three, not a bug in any single machine.

1. **Provider/PR-target state machine** -- the reviewable thing itself:
   its revision identity, mergeability, approval status, and any
   provider-side hold (draft, WIP, blocking review threads). Observed by
   polling or webhook from the provider; never assumed from local task
   state.
2. **Dispatch task state machine** -- the unit of work this effort's
   lifecycle already partially defines (Phase 1's requested / claimed /
   analyzing / awaiting-steer / ready / submitted / failed / abandoned).
   Owned by the dispatch supervisor's ledger.
3. **Bridge/session state machine** -- whether an agent process backing a
   task is absent, hydrating, running, suspended (worktree/session held
   cold), or ended, plus its host/transport health. Owned by agent-bridge.
   This effort depends on -- and must not duplicate -- the verb/state
   vocabulary the companion agent-bridge vision work is currently
   clarifying; treat that vision as the source of truth for bridge verbs and
   reference it rather than re-defining bridge states here once it lands.

   **Liveness is a live, three-tier read -- never gated by a cache.** A
   downstream deployment's operator design conversation (2026-09-10) found
   the load-bearing bug class here: a target-existence or liveness cache
   (a discovery index, a session-record table) is a **performance
   shortcut only** -- it must never be the *authority* on whether a target
   exists or what state it's in. A cache miss, a stale entry, or an
   unpopulated crawl must fall through to a **live, authoritative check**,
   never a hard failure. The three tiers, each observed live at resume
   time:
   - **Hot** -- a live interactive controller is actually attached to the
     target right now. Resuming refuses unless the caller explicitly
     force-takes-over (that would spawn a second controller on the same
     target).
   - **Warm** -- no live interactive controller, but the backing process is
     actually alive. Resuming reattaches it; conversation/session history
     intact.
   - **Cold** -- neither of the above. Resuming always succeeds here: it
     spawns a fresh session bound to the *existing* checkout/target (never
     a new one).

   This reframes the bridge machine's verb surface: **one universal
   "resume", keyed by either a worktree handle or a repo/agent name**,
   returns whichever of hot/warm/cold is actually true as an **observed
   result**, never a precondition the caller must satisfy or guess first.
   A narrower "resume this exact session id" remains available for
   targeting one specific, possibly non-head session. A distinct "create
   fresh" path exists only where a genuinely new, additional checkout is
   possible; where the target's own registry class has exactly one head
   (no second checkout to make), "create fresh" is a declared error and
   "resume" is the only correct verb -- collapsing what would otherwise be
   a second, competing recovery mechanism (an explicit reclaim escape
   hatch alongside create) into the one correctly-observed resume path. A
   separate "discard and roll forward in place" gesture (deliberate
   handoff under context pressure or a runaway agent) stays distinct from
   resume's "give me whatever's there, however it stands."

Each machine's states and legal transitions are declared independently.
**Control flow couples them explicitly**, never implicitly: a task-state
transition that requires a bridge action (spawn, resume, suspend, end)
issues that action and waits for the bridge machine's own transition to
confirm it: no task transition assumes a bridge effect happened just
because it was requested. Symmetrically, a bridge-side event (a session
ending, a host detaching) is only ever *evidence* fed into the task
machine's next evaluation -- never a direct mutation of task state from the
bridge side. This is the general fix for a recurring bug class observed
downstream: a task cancelled mid-flight without its steering-wait flag
being cleared, leaving it permanently un-resumable, because the
cancellation transition partially applied without a defined "what confirms
this transition completed" rule.

**Mechanism, concretely** (this effort has no cross-system transaction, and
this doc does not pretend otherwise):

- Each machine's record carries a monotonic **generation/version** number.
  A transition is a **compare-and-set**: read current generation, compute
  the next state, write conditioned on the generation being unchanged.
  A losing CAS means someone else moved the state first; the loser
  re-reads and re-evaluates rather than retrying blindly.
- Every transition derives its next state from **freshly observed** current
  state (provider poll/webhook payload, task ledger row, bridge status
  query) -- never from an assumption about what a prior action must have
  done.
- Every transition is **idempotent and resumable**: replaying the same
  transition request against an already-advanced generation is a no-op,
  not an error and not a duplicate effect. This is what makes restart-at-
  any-boundary (Phase 4's validation goal) actually provable instead of
  merely tested-by-hand.

## Provider-capability model (why one provider machine isn't enough)

The provider/PR-target machine's **states** are the same shape across
GitHub, Azure DevOps, Gitea, and any future provider (revision identity,
mergeability, approval, hold). What differs, and must be declared
per-provider-and-per-repository-policy rather than hardcoded, is:

- **Who may approve/merge**: required-reviewer counts, CODEOWNERS-style
  gating, branch-protection rules, and whether an automated identity is
  itself an eligible approver under that repository's configured policy.
- **Notification fidelity**: whether state changes arrive as push
  (webhook), must be polled, or are not observably signaled at all for a
  given event type (a provider may notify on new commits but not on
  review-thread resolution, for example). The task machine's evaluation
  cadence for a given transition must be driven by the actual fidelity
  declared for that provider/event pair -- a machine that assumes push
  fidelity where only polling is available will silently miss the
  transition it was waiting for.
- **Conflict-handling authority**: see below.

This is a **declarative capability table**, keyed by provider and
overridable per repository, consulted by the task machine before it
assumes any provider capability -- not a hardcoded per-provider branch in
the driver.

### Conflict handling -- resolved as a policy-gated mode, not a flat choice

Phase 8 left this as an open, two-sided design conflict: this plugin's
existing conflict-resolution recipe rebases and force-pushes the pull
request's own branch; a downstream deployment's hardened scheduler instead
never creates a conflict-resolution worker at all, hands the blocker back
to the submitter, and reviews only their own corrected head.

**Resolution:** the default is **hand-back** -- never mutate a
contributor's branch on their behalf. Branch-mutating conflict resolution
(rebase + force-push) becomes an explicit **policy-gated mode**, enabled
only where the provider/repository's declared policy and configuration
permit an automated identity to push to a contributor's branch (this is
already true today for some providers' own native auto-merge/auto-update
behavior, which some hosts perform for the submitter without any agent
involvement at all). The existing rebase/force-push recipe is **demoted to
that mode**, not deleted: a repository that has explicitly configured for
it keeps it; every other repository defaults to hand-back. The capability
table above is where this policy is declared and read.

## The board-game contract

An agent consuming this runtime should be able to ask two questions and
get a direct answer, without inferring either from raw ledger rows:

1. **"Where am I?"** -- the current state of my assigned task, across all
   three machines' relevant projection (task state; the bridge state of my
   own session; the provider state of the target I'm reviewing).
2. **"What are my valid next moves?"** -- the set of legal transitions
   from the current state, given the declared provider capability and any
   pending steering input.

**Ownership split** (this is the piece that resolves whether "task" is the
right unit -- it is, provided ownership is split this way):

- The **dispatch supervisor** owns creating, resuming, suspending, and
  ending an agent via the bridge. It is the only actor that spawns or
  tears down a process.
- The **agent**, once attached to a task, plays its turn: read the task's
  current state, perform the objective, evaluate its own completion
  status, and request the task move to its next state. The agent never
  moves itself off the board, resumes itself, or declares its own
  suspension -- it requests; the machine decides.
- **Evaluators and emitters** own actually moving a task to its next state:
  taking it off the board (ending the agent), adjusting and re-queuing it
  (resuming the agent, generation bumped), suspending it (bridge told to
  stop the agent, worktree/session left cold), or resuming a suspended one.
  An emitter can also **steer** an agent by updating an already-started
  task's input mid-flight -- this is a distinct transition from resume, and
  must be modeled as one (Phase 3's steering contract is the existing
  partial version of this; it generalizes here rather than being
  superseded).

A task remaining the right unit-of-work label, despite changing state
across an agent's attachment, follows directly from this split: the task
*is* the board position, not a fire-and-forget request.

## Recovery taxonomy

Every declared transition -- across all three machines -- is classified
into exactly one recovery mode, recorded alongside the transition
definition itself (not left to be discovered ad hoc when it fails):

- **Self-recovering**: the system's own next evaluation cycle reaches the
  correct state without external action (e.g., a transient bridge health
  check flaps but the next poll confirms liveness).
- **Safe-retry**: idempotent replay of the same transition is the
  correct remedy (e.g., a lost provider response; re-issue the same
  CAS-guarded transition).
- **Self-repair**: the system detects an inconsistent intermediate state
  and must actively reconcile it before proceeding (e.g., a carried
  session whose bridge status disagrees with the task's assumed bridge
  state -- Phase 5's `reconcile_reserving` gap, generalized; and the
  cache-vs-live-check gap above -- a stale/missing liveness cache entry is
  never treated as "target absent," it always falls through to a live
  hot/warm/cold check before any transition proceeds).

No transition is left unclassified. A transition whose recovery mode is
"none of the above" is a design defect in this phase, not an acceptable
gap.

## Simulation and test track

Each scenario below is a deterministic, unit-testable fixture exercising
one or more of the three machines' coupling, not a live integration test:

- [ ] Bridge caught mid-version-update while a task holds an active
  session against it.
- [ ] Session host detached (network partition / process host restart)
  while a task believes its agent is running.
- [ ] The bridge's discovered port/endpoint changes underneath an
  already-attached task.
- [ ] The dispatch supervisor is about to end-of-life a bridge/runtime
  version while tasks are still attached to it.
- [ ] The provider's base revision moves (unrelated commits land) while a
  task's analysis is in flight, with and without the submitter's actual
  diff changing (base-only vs. substantive -- Phase 8's base-only
  detection candidate).
- [ ] A verdict/response from a reviewer arrives after a newer PR update
  has already superseded the revision it was computed against (out-of-order
  delivery relative to a provider update).
- [ ] Two provider events for the same task arrive out of order or
  duplicated (idempotent-replay proof for Phase 2's dedup requirement).
- [ ] A steering input arrives while the evaluator is mid-transition on the
  same task (steer-vs-transition race).
- [ ] A resume is requested against a target whose liveness cache is
  empty, stale, or missing the entry entirely; the live hot/warm/cold
  check must still classify it correctly rather than the resume failing
  outright (the corrected bridge-machine model above).
- [ ] A resume is requested against a target that is genuinely hot (a live
  interactive controller already attached); the resume must refuse absent
  an explicit force-takeover, never silently spawn a second controller.

Each fixture asserts: the correct terminal/next state is reached regardless
of interleaving order, the transition is idempotent under replay, and the
recovery mode taken matches the taxonomy above.

## Phase 8 candidates, re-seated as required behaviors (not a parallel checklist)

Every Phase 8 candidate is an expression of one of the three machines
above, not an independent feature:

- Attempt-budget choke point, event ledger with reason-code
  classification, stale-approval classification, official-vs-candidate
  approval-authority split, WIP/hold gating -> properties and transitions
  of the **provider/PR-target** and **task** machines.
- Base-only/unchanged-substance detection -> a provider-machine observation
  rule the task machine consults before treating a provider event as a
  substantive revision (see simulation scenario above).
- Worktree-pool force-clean/dirty-tolerance -> a **bridge/session** machine
  concern: what "session ready for reuse" means before task resume runs its
  self-repair.
- Relay/host liveness and health-fencing -> a **bridge/session** machine
  property (the host substrate's own state must be observable and
  fenced independently of any single task).

## Plan (this phase)

- [x] Declare the dispatch task state machine as a checkable data table
  (`../../../plugins/agent-dispatch/src/agent_dispatch/task_state_machine.py`),
  reconciling Phase 1's original reviewer-flavored state list against the
  real `agent_dispatch.queue.Status` states and the board-game ownership
  split above, with structural tests
  (`../../../plugins/agent-dispatch/tests/test_task_state_machine.py`)
  proving every state reachable, no non-terminal state without an exit,
  and every transition classified under exactly one recovery mode.
- [x] Declare the provider/PR-target state machine and its per-provider
  capability table (approval authority, notification fidelity, conflict
  policy)
  (`../../../plugins/agent-dispatch/src/agent_dispatch/provider_state_machine.py`),
  with structural tests
  (`../../../plugins/agent-dispatch/tests/test_provider_state_machine.py`)
  proving both dimensions (approval status, mergeability) fully reachable
  and exit-checked, every provider's capability declaring fidelity for
  every event type, and every provider defaulting to the hand-back
  conflict policy unless a repository explicitly overrides it.
- [ ] Declare the bridge/session state machine, deferring to the
  agent-bridge vision's verb work for the authoritative verb vocabulary
  once it lands; coordinate rather than duplicate in the interim.
- [ ] Define the explicit control-flow coupling rules between the three
  machines (task-requests-bridge-action / bridge-event-as-evidence, as
  described above).
- [ ] Classify every declared transition into the recovery taxonomy.
- [ ] Build the simulation/test track as deterministic fixtures, one per
  scenario above.
- [ ] Re-validate each Phase 8 candidate against the declared machines;
  fold each into the relevant machine's spec rather than implementing it
  standalone.
- [x] Submit this phase's design as its own reviewed slice before any
  implementation begins, per this effort's coordination gate.
