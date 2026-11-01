# Pool-owned assignment startup and retry eligibility

Status: **reviewed design; due-time admission source merged; atomic placement
and native qualification pending**.
The documentation/design slice merged in
[#9710](https://github.com/ThomasMichon/copilot-extensions/pull/9710).
The existing due-time reservation check merged separately in
[#9755](https://github.com/ThomasMichon/copilot-extensions/pull/9755).
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
that same worker through the existing supervision owner until it explicitly
accepts or declines, within the selected decision budget below.
Exhaustion returns the task to `QUEUED`; startup failure
is a supervisor outcome, distinct from a worker's "not me".

A decline must carry objective machine-readable worktree, pool or machine
exclusions and an explanatory note. The declining worker must finalize its
own workspace before relinquishing the assignment so it can later be cleaned
up. Consider explicit Retry-After eligibility for unresponsive startup after
nudges and repeated same-pool rejection. Revise intent and architecture before
behavioral implementation; the mismatch is a repair hypothesis, not proof of
an operational root cause.

### Selected startup and retry policy

**Operator-selected policy, 2026-10-31; implementation pending:**

- Allow **3 startup attempts** per claim episode, retaining the same assignment
  and reservation while those attempts remain accountable.
- Once the worker is reachable, nudge the same worker every **5 minutes** and
  allow up to **30 minutes** for an explicit accept/decline decision. This
  selected interval supersedes the earlier unspecified cadence; the existing
  supervision owner performs it, not a new timer or agent-owned schedule.
- Retry cooldown is **task-wide**: successive cooldowns are **1 hour, 3 hours,
  6 hours, then 12 hours**, capped at **12 hours**. Preserve retained pool
  assignment and any independently scheduled later due time.
- A successful transition to **`STARTED` resets both the cooldown escalation
  and startup-attempt count**. Reachability, a delivered prompt or an unrelated
  receipt does not perform that reset.

This policy selects budgets and backoff, not an installed capability or a
release. It does not resolve result-rejection reassignment or terminal-worktree
closeout policy.

### Selected pool scope and ordering

**Operator-selected policy, 2026-10-31; reviewed in
[#9773](https://github.com/ThomasMichon/copilot-extensions/pull/9773);
implementation pending:**
Capacity is separate for each **machine/environment pool instance**, not one
shared cap across hosts. When multiple eligible instances have capacity, use
**configurable pool priority**, not registration-ID order alone.

**Agent-recommended representation:** identify an instance by the registration's
stable logical identity together with its actual execution machine and
environment. Keep logical identity distinct from workspace/native identity;
an unidentified execution machine is not a fabricated `local` instance.
Use an integer priority, default `0`, with higher values considered first;
break ties by registration identity, machine and environment. Equal-priority
instances remain separate capacity owners.

The coordinator must obtain a **complete effective pool definition set** from
the existing registration/reconciliation owner before placement. Preserve full
effective permit/reject filters, declared/direct precedence, equivalent-source
deduplication, pauses and operator overrides. The raw `registrations` list is
not that set: current declaration conversion drops filters, while overrides
and source reconciliation happen in the daemon. Do not add an independently
configured pool registry or trust a caller's preferred subset, winner or
available-slot count.

Expand normalized instance definitions and their lossless representation
first, then the owning provider's revision-fenced effective-set publication
and atomic placement. Publication must distinguish machine/environment
instances and replace a scope's complete set atomically; an incomplete or
uncertain expected scope blocks new admission without erasing existing
custody. A definition or published metadata alone does not establish that a
legacy lane enforces the new placement contract. Caller migration and
mixed-cohort fencing remain separate reviewed source gates.

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
   the complete effective instance set, priority ordering, routing, retry
   eligibility, retained assignment and instance capacity at commit;
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
6. Implement the selected task-wide cooldown through the existing scheduling
   authority, preserving any independently scheduled later `not_before`.
   Persist the escalation stage and the active startup-attempt budget; reset
   both only on the successful current `STARTED` transition. Do not reset
   reservation keys/revisions or erase historical attempts, outcomes and
   attachments. Keep retry eligibility separate from wake-delivery backoff
   and monitored suspension. A reset clears this retry policy's counters,
   not another owner's scheduled deadline or unresolved effect custody.

## Remaining policy choices

- Result rejection from `SUBMITTED`: returning work to its retained worker
  versus explicit fenced reassignment or assignment reconsideration.
- Terminal worktree closeout and whether submission should require closeout
  readiness. This amendment does not impose finalize-before-submission or
  reopen completed tasks for housekeeping.

These remain independent policy decisions; neither is resolved by the selected
startup/cooldown policy. Unsatisfiable retained routing must remain visible,
not silently unpinned.

## Compatibility and ownership

Expand the owning coordinator/provider first, then migrate supervised callers
using runtime-advertised support, not payload/package-version assumptions.
Old reservation-plus-worker-claim clients cannot be treated as atomic placement
clients. Define mixed-cohort admission and rollback before switching a lane;
unsupported peers must not silently bypass required capacity/routing semantics.
Preserve direct interactive claims without inventing a supervised reservation.

The [metadata provider boundary](../../../plugins/agent-dispatch/docs/pool-publication.md)
separates revision/generation-fenced storage and control-authenticated transport
from the still-pending admitted complete-source publisher, expected-scope
membership and atomic placement. Transport support is not executing-runtime
qualification. The singleton's OS lock is not a shared coordinator lease.

Do not backfill legacy pool/native identity from labels or mark historical
startup effects settled. Preserve manual sessions, suspended work, attachment
history, existing holds and source claims. This slice does not take #6132's
pool-capacity, #2879's executing-worker nudge, #9255/#8051's startup-progress,
or the existing submission/conversation implementation ownership.

## Validation Plan

The existing scalar `not_before` reservation boundary and preserved
active/suspended paths have source coverage in #9755 (13 new controls and
100 total passing reservation cases). This is partial credit only; it does
not resolve the selected retry scope, atomic placement or mixed-runtime matrix.

- [ ] Fake-clock selection and admission before, exactly at and after retry
  eligibility; direct reservation/claim bypass attempts refuse early work.
  Preserve later scheduled due times and cover the selected retry scope.
- [ ] Prove the exact three-attempt budget, five-minute nudge cadence and
  thirty-minute decision limit through the existing supervisor. Task-wide
  cooldown advances through 1/3/6/12 hours and remains at 12; successful
  current `STARTED` resets both counters. Restart/replay, prompt delivery,
  failed/stale `STARTED` and unanswered nudges cannot manufacture a reset.
- [ ] Priority-ordered all-pool selection, deterministic equal-priority ties,
  mismatching filters, no-capacity waiting, retained-pool waiting and
  unsatisfiable retained assignment are observable. Same logical registration
  on two machine/environment instances has independent capacity. Complete
  effective-set publication preserves filters, source precedence, pauses and
  overrides; stale/partial publication cannot manufacture admission.
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
