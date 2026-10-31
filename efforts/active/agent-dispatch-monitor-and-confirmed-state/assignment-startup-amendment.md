# Pool-owned assignment startup and retry eligibility

Status: **reviewed design; implementation and native qualification pending**.
The documentation/design slice merged in
[#9710](https://github.com/ThomasMichon/copilot-extensions/pull/9710).
Coordination: [#9709](https://github.com/ThomasMichon/copilot-extensions/issues/9709).
Parent: [monitor and confirmed-state effort](README.md).
Standing intent: [Dispatch vision](../../../visions/plugins/agent-dispatch/README.md#pool-owned-assignment-startup).
Reality: [current versus target architecture](../../../plugins/agent-dispatch/docs/spawn-supervisor.md#assignment-lifecycle-current-versus-target).

## Requested direction

**Operator-directed summary, 2026-10-30:** `CLAIMED` is allocator-owned
placement and startup, not just evaluation by an already-running worker.
Consider all pools in their defined order and choose the first filter match
with free capacity. If an agent/pool assignment already exists, wait for that
pool's slot rather than silently switching pools. Create or reuse its spawn
reservation and consume the slot in the `QUEUED -> CLAIMED` transaction;
workspace creation and Copilot startup follow.

Dispatch and the selected pool supervisor retain the reservation through a
bounded number of startup attempts. Once a worker is reachable, keep nudging
that same worker on the existing supervision cadence until it explicitly
accepts or declines. Exhaustion returns the task to `QUEUED`; startup failure
is a supervisor outcome, distinct from a worker's "not me".

A decline must carry objective machine-readable worktree, pool or machine
exclusions and an explanatory note. The declining worker must finalize its
own workspace before relinquishing the assignment so it can later be cleaned
up. Consider explicit Retry-After eligibility for unresponsive startup after
nudges and repeated same-pool rejection. Revise intent and architecture before
behavioral implementation; the mismatch is a repair hypothesis, not proof of
an operational root cause.

## Current foundations and discrepancy

`Supervisor._eligible` filters future `Task.not_before` values.
`QueueClaimQueriesMixin.claim_one` enforces the same due-time condition in its
write transaction, alongside routing and ownership checks. This is useful
existing scheduling authority, not a reason to add another timer.

`SpawnReservationMixin.reserve_spawn` is a distinct pre-worker operation:
it reserves queued, unowned work without transitioning the task to `CLAIMED`.
Fresh queued admission now enforces the stored `Task.not_before` in the write
transaction. Active/exclusive replay remains a non-spawning custody observation,
and suspended re-embodiment preserves its existing owner/workspace path.
This closes the selection-versus-reservation due-time gap without choosing a
new retry policy. Existing claim identity and per-lane capacity selection still
do not implement the target transaction. Source support is not installed proof.

Startup revisions, allocation identity, native attachment history, effect
custody and retained-reservation accounting are existing foundations. Preserve
them and their unknown outcomes. An active reservation does not prove a native
process exists; a launched process or delivered prompt does not prove acceptance.

## Implementation plan

The following sequencing and transaction means are **agent-recommended**;
they implement the requested guarantees without claiming these APIs exist.

1. Define one coordinator-owned supervised-placement operation. Recheck
   routing, retry eligibility, retained assignment and pool capacity at commit;
   atomically bind the selected pool, create/reuse its reservation, consume the
   slot and transition to `CLAIMED`. No network/process/workspace operation
   runs inside that transaction. Attach real workspace/native identities later
   under the existing assignment and startup fences.
2. Keep bounded startup retries within that claim episode and reservation,
   distinguishing the startup-attempt budget from task/recovery attempts.
   Record attempt outcomes and explicit acceptance/decline. Reuse existing
   coordinator/supervisor reconciliation for nudges; do not create an
   agent-owned timer or infer acceptance from a transport receipt.
3. On exhaustion, return the task to `QUEUED` with structured failure history.
   Retain accountable startup/effect cleanup and block new admission while
   outstanding work can still complete. Cancel or fence that work before
   freeing its slot or permitting replacement execution. An unknown late
   outcome is not permission to replay or an indefinite `CLAIMED` hold.
4. On worker decline, durably record its exclusion scopes and explanation
   while retaining the assignment/reservation. Finalize only its own correct
   workspace through Worktrees, then atomically apply the routing disposition
   and release the assignment to `QUEUED`. A narrowly task-aware closeout path
   must avoid deadlocking on its own inbound task/session, without bypassing
   foreign/shared-workspace, source, PR, claim or follow-up gates. Failed
   finalization keeps a visible closeout blocker and its owner. Physical
   reclamation still waits for the runner to stop and normal cleanup admission.
5. Advertise stable pool identity consistently and enforce declined
   worktree/pool/machine exclusions during selection, reservation and claiming.
   Recording an exclusion that a later boundary ignores is not conformance.
6. Resolve retry policy before implementation. Prefer composing a task-wide
   retry time with existing `not_before`, preserving any later scheduled due
   time, if the selected policy is task-wide. A pool-specific policy needs
   scoped eligibility without delaying other eligible pools; do not pretend
   the scalar timestamp can express it. Keep retry time separate from wake
   delivery backoff and monitored suspension.

## Open policy choices

- Startup-attempt cap and the acceptance-nudge interval; neither has a selected
  numeric default. Reuse the existing supervising owners and their cadence.
- Retry duration, fixed versus escalating backoff, reset conditions and
  task-wide versus pool-specific scope, including precedence with retained
  pool assignment. Unsatisfiable routing must be visible, not silently unpinned.
- Result rejection from `SUBMITTED`: returning work to its retained worker
  versus explicit fenced reassignment or assignment reconsideration.
- Terminal worktree closeout and whether submission should require closeout
  readiness. This amendment does not impose finalize-before-submission or
  reopen completed tasks for housekeeping.

These are not implementation defaults. The last two remain independent policy
decisions; neither is resolved by this amendment.

## Compatibility and ownership

Expand the owning coordinator/provider first, then migrate supervised callers
using runtime-advertised support, not payload/package-version assumptions.
Old reservation-plus-worker-claim clients cannot be treated as atomic placement
clients. Define mixed-cohort admission and rollback before switching a lane;
unsupported peers must not silently bypass required capacity/routing semantics.
Preserve direct interactive claims without inventing a supervised reservation.

Do not backfill legacy pool/native identity from labels or mark historical
startup effects settled. Preserve manual sessions, suspended work, attachment
history, existing holds and source claims. This slice does not take #6132's
pool-capacity, #2879's executing-worker nudge, #9255/#8051's startup-progress,
or the existing submission/conversation implementation ownership.

## Validation Plan

- [ ] Fake-clock selection and admission before, exactly at and after retry
  eligibility; direct reservation/claim bypass attempts refuse early work.
  Preserve later scheduled due times and cover the selected retry scope.
- [ ] Ordered all-pool selection, mismatching filters, no-capacity waiting,
  retained-pool waiting and unsatisfiable retained assignment are observable.
- [ ] Concurrent allocators cannot overconsume a pool or assign one task
  twice; reservation replay consumes one slot, not a second slot.
- [ ] Initial `CLAIMED` works before a workspace/native exists; attachment and
  `STARTED` require the actual current identities and explicit acceptance.
- [ ] Nudges reuse the same assignment, do not duplicate pending work, and
  distinguish delivery from a decision. Exhaustion returns `QUEUED`; delayed
  launch success cannot bypass startup fencing or free capacity prematurely.
- [ ] Worktree/pool/machine declines constrain every admission boundary.
  Finalize succeeds before release; failed closeout retains custody; no
  running, foreign/shared or otherwise protected workspace is reclaimed.
- [ ] Coordinator/supervisor interruption between each durable transition
  preserves history and either resumes the same operation or refuses replay.
- [ ] New/new, old/new, new/old and rollback cohorts preserve supported manual,
  supervised and suspended paths with truthful incompatibility errors.
- [ ] Separate source/unit acceptance from authorized installed/native
  forward-progress qualification. Source merge, release and running adoption
  remain distinct; no production root-cause claim follows from this proposal.

## Review and completion

The documentation proposal has passed normal review and merged.
Later source slices need their own eligible claims and current review gates.
The parent remains Active until its complete Plan and Validation Plan are
resolved or explicitly transferred, not merely because this amendment merges.
