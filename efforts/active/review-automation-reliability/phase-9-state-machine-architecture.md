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
one or more of the three machines' coupling, not a live integration test.
The driver
(`../../../plugins/agent-dispatch/src/agent_dispatch/simulation.py`) is a
generic `World` (a task `VersionedRecord`, a bridge one, and an optional
provider-approval one) plus `step_task`/`step_bridge`/
`step_provider_approval` helpers that resolve a named transition from the
owning declared table and apply it through `machine_coupling.apply_transition`
-- it declares no new machine behavior, it only drives the already-declared
tables. All ten scenarios below are now implemented and covered
(`../../../plugins/agent-dispatch/tests/test_simulation.py`,
`../../../plugins/agent-dispatch/tests/test_simulation_revision.py`, and
`../../../plugins/agent-dispatch/tests/test_simulation_deferred_scenarios.py`).
The final three landed after a design review corrected the earlier belief
that they needed new declared vocabulary:

- [x] Bridge caught mid-version-update while a task holds an active
  session against it. *(Corrected from the earlier "needs a version/EOL
  concept on the bridge machine" note: agent-bridge, not agent-dispatch,
  owns the actual session-host instances and their zero-downtime-deploy
  mechanics. agent-dispatch's job shrinks to noticing a transient
  connection/call blip and recovering by re-resolving the dynamic port
  binding before retrying -- no new `Liveness` tier or version dimension.
  Designed as a bounded-retry wrapper,
  `resolve_liveness_with_recovery(cache_hint, probe, discover_port,
  max_attempts)`, with `discover_port` **injected** (not called
  internally) so a fixture can deterministically drive a
  stale-port-then-fresh-port sequence; falls back to `COLD` -- never
  assumes `HOT` -- once attempts exhaust. Composes with the
  already-declared `PORT_CHANGED` bridge event.)*
- [x] Session host detached (network partition / process host restart)
  while a task believes its agent is running.
- [x] The bridge's discovered port/endpoint changes underneath an
  already-attached task.
- [x] The dispatch supervisor is about to end-of-life a bridge/runtime
  version while tasks are still attached to it. *(Designed as a pure
  predicate, `eol_safe_to_retire(active_lease_count) -> bool`, true only
  at zero -- no new bridge state. The supervisor's decision to stop
  routing new spawns to a retiring version is a dispatch policy, not a
  bridge-machine transition; existing `suspend`/`end`/`end_suspended`
  transitions already express graceful drain.)*
- [x] The provider's base revision moves (unrelated commits land) while a
  task's analysis is in flight, with and without the submitter's actual
  diff changing (base-only vs. substantive -- Phase 8's base-only
  detection candidate). Declared as
  `provider_state_machine.Revision`/`RevisionChangeKind`/
  `classify_revision_change`, folding Phase 8's base-only detection
  candidate directly into the provider machine rather than a standalone
  feature.
- [x] A verdict/response from a reviewer arrives after a newer PR update
  has already superseded the revision it was computed against (out-of-order
  delivery relative to a provider update). Guarded by
  `provider_state_machine.verdict_applies_to_current_revision`, which the
  evaluator consults before applying any verdict-driven approval
  transition.
- [x] Two provider events for the same task arrive out of order or
  duplicated (idempotent-replay proof for Phase 2's dedup requirement).
- [x] A steering input arrives while the evaluator is mid-transition on the
  same task (steer-vs-transition race). *(Corrected from the earlier
  "needs a declared steer transition on the task machine" note: reading
  `queue.py` found steering is already a real, implemented mechanism --
  `awaiting_steer` is an existing **boolean** flag orthogonal to `Status`
  (settable while `claimed`/`started`/`suspended`, not a new lifecycle
  state), `suspend` already refuses while an untaken steer answer exists,
  and a submitted steer already resolves to exactly one of two declared
  outcomes: resume-with-wake (interactive owner) or release-to-queued
  (headless re-embodiment) -- the same dual-outcome shape
  `TASK_TRANSITION_BRIDGE_CONFIRMATION["resume"]` already uses. No
  `VersionedRecord` payload change, no new task-machine transition, and no
  `SuspendReason` enum are needed -- that was scaffolding for a mechanism
  that turned out not to match the real system. Declared as
  `task_state_machine.SteerOutcome`/`STEER_OUTCOME_TRANSITION`/
  `resolve_steer_outcome`/`suspend_blocked_by_pending_steer` -- the
  existing `awaiting_steer`/steer-inbox contract made checkable data (the
  refusal-while-untaken-steer invariant, the dual-outcome resolution)
  rather than new vocabulary.)*
- [x] A resume is requested against a target whose liveness cache is
  empty, stale, or missing the entry entirely; the live hot/warm/cold
  check must still classify it correctly rather than the resume failing
  outright (the corrected bridge-machine model above).
- [x] A resume is requested against a target that is genuinely hot (a live
  interactive controller already attached); the resume must refuse absent
  an explicit force-takeover, never silently spawn a second controller.

Each fixture asserts: the correct terminal/next state is reached regardless
of interleaving order, the transition is idempotent under replay, and the
recovery mode taken matches the taxonomy above.

## Assignment: the reservation/allocation-fencing layer, coupled to the bridge machine

A fourth piece of durable state exists alongside the three machines above:
`agent_dispatch.queue.SpawnReservation` (`SpawnState`: `RESERVING` ->
`SPAWNED` -> `COLD`/`RELEASING` -> `SETTLED`/`FAILED`/`REARMED`/`DEFERRED`).
This is **not a fourth top-level machine** -- it answers a different
question than either the task or the bridge machine ("which attempt
currently owns the right to spawn a body for this task, so two bodies are
never dispatched for the same task"), and it belongs in the same
conceptual slot as `machine_coupling.py`: a declared **relation** between
an already-real allocation table and the bridge machine, not a new
lifecycle a caller has to separately learn.

### The single-assignment invariant (already enforced; now to be declared as checkable)

`SpawnReservation.reserve_spawn()` already atomically enforces, under one
write lock: no second reservation may be minted while one is
`SpawnState.ACTIVE` (`RESERVING`/`SPAWNED`/`COLD`/`RELEASING`) for the same
task, or -- when an `exclusive_key` is set -- for the same exclusive-key
group. This is a real, existing guarantee; the design work is declaring it
as a Phase-9-style checkable structural property (a fixture proving no two
reservations can simultaneously hold an `ACTIVE` state for the same task
or exclusive-key group) rather than leaving it as "true because the SQL
transaction happens to be written correctly."

### Intentional "letting go" -- a closed, reasoned vocabulary

Every exit from `ACTIVE` into `SpawnState.RELEASABLE`
(`SETTLED`/`FAILED`/`REARMED`/`DEFERRED`) already goes through one of a
small set of named operations (`fail_spawn`, `defer_spawn`, `settle_spawn`,
`retire_spawn`), each requiring a caller-supplied reason (`detail`) --
never a silent drop. The design work is naming this as the **contract** any
future consumer (an operator, or a separately-scoped monitor component --
see below) calls into, tagged with a `RecoveryMode`:

- `fail_spawn` -- the attempt is dead (exhausted retries, confirmed gone);
  `SAFE_RETRY` (a fresh attempt gets a new key).
- `defer_spawn` -- not a failure: a carried session was confirmed still
  live/busy; `SELF_RECOVERING` (the next cycle re-checks).
- `settle_spawn` -- the task reached a terminal outcome; no further
  spawning needed; `SAFE_RETRY`.
- `retire_spawn`/rearm -- an explicit, permission-gated operator override
  of a dead-lettered spawn history; `SELF_REPAIR`.
- **Preemption** (a different claim wins the task while this reservation
  is still `RESERVING`/unclaimed-`SPAWNED`) is its own reason, distinct
  from failure: the reservation agent-dispatch itself assigned is
  gracefully terminated and released with a `preempted` conclusion --
  never the winning claimant, which may be a different bridge-managed
  agent or a human's own CLI-driven session. `SELF_RECOVERING` (the
  system's own next state is simply correct once the winning claim lands).

### Reservation <-> bridge consistency: three tiers, not a strict pairwise map

A reservation's `SpawnState` is a claim about its bridge; the bridge's own
`Liveness`/`BridgeState` is the fresh, live-probed fact. The relation
between them has three tiers, not two, because **a bridge is not the only
thing that can drive a task** (a resolver can complete a suspended task
directly with no bridge ever spawned; a pool/fleet body can be live and
`RUNNING` while genuinely unbound to any specific reservation between
claims):

- **N/A** -- no reservation exists for the task, or the live body isn't
  bound to any specific reservation right now. Never an anomaly; the check
  is scoped per-reservation and simply doesn't apply.
- **Consistent** -- a reservation names a specific live process, and the
  fresh bridge read agrees:
  - `RESERVING` + `ABSENT` (about to spawn fresh; `worktree_ownership`
    `created`/`targeted`/`unknown`).
  - `RESERVING` + `RUNNING`, **only** when `worktree_ownership == "reused"`
    / `inherited_worktree` is set (deliberately carrying forward a
    still-live session).
  - `SPAWNED` + `HYDRATING`/`RUNNING`.
  - `COLD` + `SUSPENDED`/`ABSENT` (intentionally stopped; either paused or
    fully torn down are both legitimate).
  - `RELEASING` + any of `SUSPENDED`/`RUNNING`/`ABSENT`/`ENDED`
    (transitional).
  - `SETTLED`/`FAILED`/`REARMED` + `ABSENT`/`ENDED`.
  - `DEFERRED` + `RUNNING`/`HYDRATING` -- definitionally, since `DEFERRED`
    exists *because* the allocator already confirmed the carried session
    is still live/busy.
- **Anomaly** -- a reservation claims a specific live process and the
  fresh read contradicts it:
  - `SPAWNED` + `ABSENT`/`ENDED` -- the headline `reconcile_reserving`-class
    bug this effort exists to catch.
  - `RESERVING` (not reused/inherited) + `RUNNING` -- a live process
    already exists before this reservation ever spawned one.
  - `COLD` + `RUNNING` -- a "stopped" body that's actually still running.
  - `COLD` + `ENDED` -- claims resumability against a permanently-dead
    process; resume will fail.
  - `SETTLED`/`FAILED`/`REARMED` + `RUNNING` -- a leaked process under a
    concluded reservation's handle.
  - `DEFERRED` + `ABSENT`/`ENDED` -- the deferral's premise was wrong; a
    fresh attempt should trigger, not continued deferral.

### Explicitly out of scope: the launch-to-claim grace/recovery monitor

A separate concern -- monitoring a freshly-spawned body for evidence of
progress toward actually claiming its task (a bounded grace deadline, a
kill-and-resume-with-nudge recovery ladder before truly declaring an
attempt dead) -- is **deliberately not designed here**. It is a distinct
component with its own internal state (grace deadlines, evidence
counters, kill-attempt counts), consuming the reservation contract above
(`set_activity` already exists, already fenced to a reservation key,
already callable pre-claim) from the outside. Phase 9 declares the
contract that component calls into; it does not declare the component
itself.

## Phase 8 candidates, re-seated as required behaviors (not a parallel checklist)

Every Phase 8 candidate is *conceptually* an expression of one of the three
machines above, not an independent feature. The original mapping below
(kept for its rationale) was written before this phase's declared tables
existed; the **re-validation pass** (roster item 2, below) checked each
candidate against what the four modules actually declare today, not just
which machine it conceptually belongs to. Three of the eight are now
genuinely declared behaviors; five are still only a conceptual home
assignment, with no declared table or function backing them yet -- that
gap is real and is called out per candidate rather than left implied by
the original prose.

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

### Re-validation pass: declared today vs. still only assigned a home

| Phase 8 candidate | Assigned machine (above) | Actually declared? |
|---|---|---|
| Base-only/unchanged-substance detection | provider | **Yes** -- `provider_state_machine.Revision`, `RevisionChangeKind`, `classify_revision_change`. |
| Stale-approval-vs-current-head classification | provider | **Yes** -- `ApprovalStatus.STALE` + `revision_invalidates_approval` (already declared before this pass) is now concretely groundable in a real `Revision`, and `verdict_applies_to_current_revision` is the first-class scheduling guard the candidate asked for. |
| WIP/draft/hold and blocking-thread gating | provider | **Yes** -- `HoldReason` flag set + `merge_blocked_by_hold`. |
| Attempt-budget choke point (reserve/commit/cancel) | provider + task | **No.** Neither machine declares a budget counter, a reservation lifecycle, or a choke point any transition passes through. The task machine's `RecoveryMode`/CAS mechanism could carry a budget field on `VersionedRecord`, but nothing today declares one. |
| Event ledger with reason-code classification | provider + task | **No.** The `RecoveryMode` taxonomy classifies *transitions*, not *outcomes on an append-only per-round ledger* -- there is no ledger data structure or reason-code vocabulary declared anywhere in the four modules. |
| Official-vs-candidate approval-authority split | provider + task | **No.** `ProviderCapability.automated_identity_eligible_approver` is a per-provider default, not a per-approval-record distinction between an official/provider-native approval and a recipe-internal candidate-fenced one. |
| Worktree-pool force-clean/dirty-tolerance | bridge | **No.** `bridge_state_machine.py` has no notion of a worktree's clean/dirty status or a normalization rule (e.g. mode-only diffs) -- `BridgeState`/`Liveness` model process liveness, not checkout content state. |
| Relay/host liveness and health-fencing | bridge | **Partially.** `Liveness` (hot/warm/cold, always live-probed) covers *whether a controller is attached*, which is adjacent but not the same as bind-address/loopback-scope validation, an explicit health/live endpoint, or refuse-unsafe-startup -- those remain undeclared. |

Three candidates (base-only detection, stale-approval classification,
WIP/hold gating) needed no further work this pass -- they are real,
checkable behaviors today. The other five are unchanged from Phase 8:
real, evidenced, patched-downstream behaviors that still have no declared
expression in this repository, only a conceptual assignment to a machine.
Folding them in is design work (new dimensions/tables on the provider and
bridge machines, plus a genuinely new ledger concept), not a
re-validation-pass fix -- consistent with this effort's own coordination
gate, they are left as open follow-up work rather than authored ad hoc
inside this checklist item.

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
- [x] Declare the bridge/session state machine
  (`../../../plugins/agent-dispatch/src/agent_dispatch/bridge_state_machine.py`),
  deferring to the agent-bridge vision's verb work for the authoritative
  verb vocabulary once it lands (as of this slice
  `visions/plugins/agent-bridge/README.md` declares a task-shaped verb set
  but not yet the hot/warm/cold liveness tiers or the cache-is-never-
  authority rule, so this module does not duplicate or compete with it).
  Declares the lifecycle (absent/hydrating/running/suspended/ended), the
  corrected three-tier liveness model (hot/warm/cold, always a live
  observation, never cache-gated), and couples the one-universal-resume
  outcome to the lifecycle table so the two are checked together rather
  than as two independent tables. Structural tests
  (`../../../plugins/agent-dispatch/tests/test_bridge_state_machine.py`)
  prove reachability/exit-checking, that the live probe is always called
  regardless of a cache hint, and that every non-refusing resume outcome
  maps to a real declared transition.
- [x] Define the explicit control-flow coupling rules between the three
  machines (task-requests-bridge-action / bridge-event-as-evidence, as
  described above)
  (`../../../plugins/agent-dispatch/src/agent_dispatch/machine_coupling.py`),
  plus the generic CAS/generation primitive (`VersionedRecord` +
  `apply_transition`) all three machines' transitions share. Structural
  tests
  (`../../../plugins/agent-dispatch/tests/test_machine_coupling.py`)
  prove every bridge event names only real task transitions as evidence
  (never applies one), every task transition requiring bridge
  confirmation names a real bridge transition, and the CAS primitive's
  three outcomes (applied / lost-CAS / already-advanced-no-op).
- [x] Classify every declared transition into the recovery taxonomy.
  Already satisfied incrementally as each machine was declared: every
  transition in `task_state_machine.py`, `provider_state_machine.py`
  (both dimensions), and `bridge_state_machine.py` carries exactly one
  `RecoveryMode` tag, each proven by that module's own
  `test_every_transition_has_exactly_one_recovery_mode` (or dimension
  equivalent) structural test -- no transition was left unclassified to
  be discovered ad hoc.
- [x] Build the simulation/test track as deterministic fixtures, one per
  scenario above. All ten scenarios designed and implemented -- the final
  three (mid-version-update, EOL, steer-vs-transition race) landed in
  `../../../plugins/agent-dispatch/tests/test_simulation_deferred_scenarios.py`
  alongside their declarations in `bridge_state_machine.py`
  (`resolve_liveness_with_recovery`, `eol_safe_to_retire`) and
  `task_state_machine.py` (`SteerOutcome`, `STEER_OUTCOME_TRANSITION`,
  `resolve_steer_outcome`, `suspend_blocked_by_pending_steer`).
- [x] Declare the reservation/assignment allocation-fencing layer as a
  checkable relation coupled to the bridge machine (see "Assignment: the
  reservation/allocation-fencing layer" above)
  (`../../../plugins/agent-dispatch/src/agent_dispatch/spawn_reservation_machine.py`):
  the lifecycle transition table sourced from the real
  `agent_dispatch.queue.SpawnState`; the single-assignment invariant as a
  structural fixture (`violating_assignment_groups`); the closed "let go"
  reason vocabulary (`LetGoReason`: failed/deferred/settled/
  retired-rearm/preempted) tagged with recovery modes matching the
  sub-doc's classification exactly; and the three-tier
  (not-applicable/consistent/anomaly) reservation<->bridge consistency
  relation (`classify_consistency`), which defaults every undeclared
  pairing to anomaly rather than assuming consistency. Structural tests
  (`../../../plugins/agent-dispatch/tests/test_spawn_reservation_machine.py`)
  prove reachability/exit-checking (including that `FAILED` is releasable
  but not machine-terminal, since `rearm` is a real exit from it), every
  let-go reason naming a real transition and carrying the sub-doc's exact
  recovery mode, the single-assignment invariant across both task-id and
  exclusive-key groupings, and every whitelisted/documented-anomaly/
  undeclared consistency pairing. The launch-to-claim grace/recovery
  monitor remains explicitly out of scope (see "Explicitly out of scope"
  above) -- this module declares the contract that component would
  consume, not the component itself.
- [x] Re-validate each Phase 8 candidate against the declared machines;
  fold each into the relevant machine's spec rather than implementing it
  standalone. Done as the "Re-validation pass" table above: three
  candidates are genuinely declared today (base-only detection,
  stale-approval classification, WIP/hold gating); five remain only a
  conceptual home assignment with no declared table backing them
  (attempt-budget, event ledger, approval-authority split, worktree-pool
  dirty-tolerance, relay health-fencing) -- left as open follow-up design
  work rather than folded in ad hoc, since declaring them is new design
  subject to this phase's own coordination gate, not a re-validation fix.
- [x] Submit this phase's design as its own reviewed slice before any
  implementation begins, per this effort's coordination gate.
