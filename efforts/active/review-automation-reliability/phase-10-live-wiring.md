# Phase 10 - Wire the declared state machines into the live runtime

Linked from the effort [`README.md`](README.md) Plan. Read this only when
working this phase.

## Why this phase exists

Phase 9 deliberately declared four machines --
[`task_state_machine.py`](../../../plugins/agent-dispatch/src/agent_dispatch/task_state_machine.py),
[`provider_state_machine.py`](../../../plugins/agent-dispatch/src/agent_dispatch/provider_state_machine.py),
[`bridge_state_machine.py`](../../../plugins/agent-dispatch/src/agent_dispatch/bridge_state_machine.py),
[`spawn_reservation_machine.py`](../../../plugins/agent-dispatch/src/agent_dispatch/spawn_reservation_machine.py)
-- plus their coupling
([`machine_coupling.py`](../../../plugins/agent-dispatch/src/agent_dispatch/machine_coupling.py))
as pure data and pure functions, proven internally sound by deterministic,
in-process structural and simulation tests. That was the correct first
step (a design must be checkable before it governs anything), but it left
a real gap: **none of it changes what `queue.py`'s actual runtime code
does.** The declared tables and the executing code are today two
independently-maintained things that happen to agree, verified only by
tests that assert they still match (e.g.
`test_all_states_match_queue_status`). A future edit to a legal
transition in `queue.py` could silently drift from the declared table
without any test catching it until the structural-consistency test is
updated by hand to match -- exactly the kind of parallel-truth gap this
effort exists to close everywhere else.

This phase makes the declared tables **the single source of truth the
live code executes against**, one machine and one call site at a time,
never behavior-changing by design (the declared tables already match
today's real transitions -- that is precisely why wiring them in is safe)
except where wiring surfaces a genuine, previously-undetected discrepancy,
which becomes its own tracked fix rather than silently absorbed into a
wiring slice.

**Scope boundary:** this phase wires code to already-declared tables. It
does not redesign any machine's shape (that was Phase 9's job) and it
does not fold in the five still-conceptual Phase 8 candidates (a separate,
independent follow-up). If wiring a machine surfaces a case the declared
table does not cover, that gap is fixed in the declared table first (as
its own small design correction, following Phase 9's own conventions), not
patched directly in `queue.py`.

## Wiring order and rationale

Each of the four machines is wired independently, in the order that
minimizes risk and unblocks the most Validation Plan items fastest:

1. **Task machine into `queue.py`'s own transition call sites.** Every
   public method that calls `TaskQueue._transition(...)` today passes an
   inline, hardcoded `allowed=`/`to=` pair (e.g. `approve` hardcodes
   `allowed={Status.PROPOSED}, to=Status.QUEUED`). Confirmed by direct
   reading: every one of these hardcoded pairs already matches
   `task_state_machine.TRANSITIONS` exactly (`approve`, `start`,
   `suspend`, `resume`, `release_suspended`, `complete`, `abandon`, plus
   the `Status.HELD`-sourced `requeue_held`/`dead_letter_held` transitions
   used by liveness GC). Wiring replaces each hardcoded pair with a lookup
   against `task_state_machine.TRANSITIONS_BY_NAME[name]`, so the declared
   table becomes the actual governing data, not a parallel description of
   it. Lowest risk (behavior-preserving by construction) and unblocks the
   restart-at-any-boundary and duplicate-delivery Validation Plan items,
   which are fundamentally about the task machine's own transitions.
   **Correction found while wiring:** `TaskQueue.yield_task` (a worker's
   own deliberate, voluntary give-back on a recoverable snag -- distinct
   from `requeue_held`'s automatic owner-gone reconciliation, though both
   move `HELD -> QUEUED`) was entirely missing from the original
   declaration. Added as its own named transition rather than folded into
   `requeue_held`, since it has a different actor and a different
   recovery mode (`SAFE_RETRY`, not `SELF_REPAIR`).
2. **`machine_coupling.apply_transition`'s CAS primitive into `queue.py`'s
   concurrency fencing.** **Correction (found during slice 1's direct
   reading of `_transition`, which the original design here got wrong):**
   `queue.py` does **not** implement a `WHERE generation = ?`-conditioned
   `UPDATE` in the general path -- `_transition`'s SQL is
   `UPDATE tasks SET ... WHERE id = ?`, with no generation clause. Its
   real concurrency mechanism is `BEGIN IMMEDIATE` (SQLite's write-lock-
   now transaction mode), which serializes every `_transition` call
   against a given database, plus a fresh Python-level
   `task.status not in allowed_set` check read *inside* that lock. This
   is pessimistic locking, not optimistic CAS -- a different mechanism
   than `machine_coupling`'s declared generation-conditioned `UPDATE`
   primitive, though it achieves the same no-lost-updates guarantee by a
   different route. A `WHERE generation = ?` clause only appears when a
   caller explicitly supplies `expected_generation`, and a mismatch there
   raises `TaskError` immediately rather than resolving to
   `CASOutcome.LOST_CAS`/`ALREADY_ADVANCED` the way
   `machine_coupling.apply_transition` does.

   This means wiring item 2 is **not** a low-risk parity confirmation the
   way item 1 was -- it is a genuine design question that must be
   answered before any code changes: **does a replayed request against an
   already-advanced task (e.g. a duplicate `approve` call after the first
   one already succeeded) currently raise `TaskError`, and should that
   change to a silent no-op** (matching `machine_coupling`'s
   `ALREADY_ADVANCED` semantics and this effort's "duplicate delivery
   produces... at most one submission" Validation Plan item)? Today's
   answer, confirmed by reading every `_transition` call site: **yes, it
   raises** for every method except `suspend` (which has its own
   hand-written early-return for the specific same-owner-already-
   suspended case) and `complete_with_outcome` (which has its own
   hand-written idempotent-replay path for a result already recorded).
   No existing test locks in "raise" as required behavior for the other
   five methods, but changing it is an observable behavior change for any
   caller currently catching `TaskError` to detect "someone already did
   this" -- **this must not be decided unilaterally inside a wiring
   slice**. Before implementing, get explicit confirmation on which of
   these is wanted:
   - (a) Leave every method's raise-on-already-advanced behavior exactly
     as it is today, and only make the classification *observable*
     (e.g. a distinguishable audit-log note or exception subtype) without
     changing what callers experience, or
   - (b) Extend `suspend`'s and `complete_with_outcome`'s existing
     idempotent-replay pattern to the other five methods, making
     duplicate delivery a true no-op everywhere, which is a real behavior
     change validated by new tests, not merely "wiring."
   Whichever is chosen, the shared `machine_coupling.apply_transition`
   primitive is still the right vocabulary to express the outcome in
   -- this correction only changes what work item 2 actually requires
   before code is written, not the direction.
3. **Provider machine into a real provider adapter (or adapters).**
   Unlike the task machine, there is today **no existing provider-adapter
   code in this plugin** to wire against -- `provider_state_machine.py`'s
   capability table and revision/approval dimensions have no live GitHub/
   ADO/Gitea driver reading real PR state yet. This slice is additive, not
   a refactor: build the first adapter (GitHub, since this repo's own
   dogfooding runs there) that observes real PR state and drives it
   through the declared `ApprovalStatus`/`Mergeability`/`Revision`
   transitions rather than ad hoc polling logic. Higher-risk and larger
   than (1)/(2) -- expect this to be several slices, not one.
4. **Bridge machine into agent-bridge's live session/liveness reads.**
   `bridge_state_machine.py`'s hot/warm/cold liveness model and resume
   semantics need a live `live_probe` implementation reading real
   agent-bridge session state (this plugin depends on agent-bridge for
   this, per Phase 9's own note that agent-bridge owns the actual session-
   host instances). Coordinate with whatever the agent-bridge vision's own
   verb-vocabulary convergence has landed by the time this slice starts;
   do not duplicate or compete with it (same caution Phase 9's bridge
   module already carries).
5. **Spawn-reservation machine into `queue.py`'s real `SpawnReservation`
   lifecycle.** Same shape as (1): `spawn_reservation_machine.py`'s
   transition table is already sourced from the real
   `agent_dispatch.queue.SpawnState`, and its `implemented_by` fields
   already name the real methods (`record_spawn`, `record_cold`,
   `request_spawn_release`, `fail_spawn`, `defer_spawn`, `settle_spawn`,
   `retire_spawn`, `rearm_spawn`). Wiring here means confirming
   `violating_assignment_groups` and `classify_consistency` are actually
   *called* somewhere in the supervisor's real reconciliation loop (e.g.
   a periodic consistency sweep that logs/alerts on any classified
   anomaly), not just proven sound as declared functions nobody invokes.

Items 1, 2, and 5 are refactors/additions against **existing, real,
already-working code** -- they are the safe, tractable first slices.
Item 3 requires building genuinely new adapter code and item 4 depends on
agent-bridge's own convergence; both are larger, later slices, planned
here but not blocking the start of 1/2/5.

## Plan (this phase)

- [x] Wire the task machine into `queue.py`'s `_transition` call sites
  (item 1 above): replaced each hardcoded `allowed=`/`to=` pair (`approve`,
  `start`, `suspend`, `resume`, `release_suspended`, `abandon`, and
  `complete_with_outcome`'s local `allowed` set) with a lookup against a
  new `task_state_machine.TRANSITIONS_BY_NAME`, via a lazily-imported
  `_task_transition_spec()` helper in `queue.py` (a lazy import avoids the
  circular-import risk, since `task_state_machine` itself imports `Status`
  from `queue`). Wiring surfaced one real, previously-undetected
  discrepancy: the declared `complete` transition only named `started`,
  but `complete_with_outcome` has always also allowed completing a
  `suspended` task directly (a suspended task may resolve while no worker
  process is running). Corrected the declared table to match the real,
  already-working behavior, per this phase's own scope boundary ("the
  gap is fixed in the declared table first... not patched directly in
  `queue.py`"). Added
  `plugins/agent-dispatch/tests/test_task_transition_wiring.py` (9 tests)
  proving each call site genuinely *reads* the declared table --
  monkeypatching a transition's `from_states`/`to_state` and asserting the
  live method's behavior changes to match, not just that today's literals
  happen to agree with it.
- [ ] **Blocked on a design decision, not ready to implement.** Item 2's
  original plan assumed `_transition` already used a generation-
  conditioned CAS `UPDATE`; direct reading during slice 1 found it
  actually uses `BEGIN IMMEDIATE` serialization + a fresh in-lock status
  check, and every method except `suspend`/`complete_with_outcome` raises
  `TaskError` on a replayed request rather than no-op'ing. Get an
  explicit answer to the (a)/(b) question in the corrected item 2
  description above before writing any code here.
- [ ] Build the first live provider adapter (GitHub) driving
  `provider_state_machine`'s declared dimensions from real PR state (item
  3 above). Expect this to be split into its own sequence of slices as
  scope becomes clearer once adapter work starts.
- [ ] Wire the bridge machine's `resolve_liveness`/`resolve_resume` against
  a real agent-bridge liveness read (item 4 above), coordinated with
  agent-bridge's own verb-vocabulary convergence.
- [ ] Call `spawn_reservation_machine.violating_assignment_groups` and
  `classify_consistency` from the supervisor's real reconciliation loop
  (item 5 above), with a live-behavior test proving an actual anomaly
  (e.g. a `SPAWNED` reservation whose live bridge read is `ABSENT`) is
  detected and surfaced, not just classifiable in the abstract.
- [ ] Revisit this effort's Validation Plan once each of the above lands
  and check off whichever items each wiring slice actually makes
  checkable against live behavior.
