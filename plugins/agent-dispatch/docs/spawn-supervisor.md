# agent-dispatch — Embody Spawn Supervisor (design)

Status: **in progress** — the spawn-reservation primitive **and the supervisor
loop** are built (spawn-at-most-once); the lease heartbeat, dead-embody
auto-recovery (both need session-liveness detection), backlog-catch-up policy,
and the authenticated container transport land in follow-up slices.
Public tracker: [ThomasMichon/copilot-extensions#44](https://github.com/ThomasMichon/copilot-extensions/issues/44).

This note is the design of record for turning a **queued task** into **exactly
one host-side embody autopilot session**, durably and idempotently. It realizes
the [agent-fabric](../../../visions/agent-fabric/README.md) vision's
**delegation layer** — specifically `delegate-and-hand-off` (work is delegated to
a spun-off agent with a shared record) and `recover-not-lose` (an interrupted
spawn is reconciled, never silently double-run or lost).

## The problem

`agent-dispatch create --spawn` (and any watcher that "sees a queued task and
runs `embody`") has a fatal gap for an **autonomous PR-authoring** trigger:

- The queue's claim/dedup is **transactional** (a single-writer SQLite
  `BEGIN IMMEDIATE`).
- The **embody spawn is a separate, non-transactional step** (`agent-worktrees
  embody` in a subprocess).

Between *observing* a spawn-eligible task and *actually spawning* it there is an
open window. A crash, a re-poll, or a lease-expiry in that window **double-spawns**
(two autonomous sessions competing on one task, opening rival PRs) or **loses**
the spawn. Concretely, `create --spawn` on a colliding `dedup_key` returns the
existing task but still invoked spawn a second time.

"Usually once" is unacceptable precisely because the side effect is autonomous.

## The primitive: an atomic spawn reservation

A **spawn reservation** is an atomic record, distinct from the execution claim,
that guarantees **exactly one embody spawn per (task, attempt)**.

- **Distinct from the claim.** The execution *claim* is taken later, by the
  embodied worker, under its own worktree identity (`claim`/`start`/`complete`).
  The *reservation* is taken first, by the **spawner** (a `create --spawn` CLI,
  or — later — the supervisor loop), **before** launching embody.
- **Keyed** `dispatch-task:<task_id>:<attempt>`.
- **Lifecycle:** `reserving → spawned → settled`, plus `failed` for a bounded
  retry that mints a fresh attempt.

  | state       | meaning                                                        |
  |-------------|----------------------------------------------------------------|
  | `reserving` | this spawner owns the (task, attempt) spawn; embody not yet confirmed launched. A restart reconciles a reservation stuck here. |
  | `spawned`   | embody launched; the session/worktree handle is recorded.      |
  | `settled`   | the reserved attempt reached a terminal outcome; no more spawning. |
  | `failed`    | spawn failed or was lost; a fresh attempt may now be reserved.  |

- **Exactly-one invariant.** `reserve_spawn(task_id)` is a single
  `BEGIN IMMEDIATE` transaction: if any reservation for the task is **active**
  (`reserving`/`spawned`), it returns `(existing, reserved=False)` — the caller
  must **not** spawn. Otherwise it mints attempt `max(prior)+1` (or `1`),
  `reserving`, and returns `(new, reserved=True)`. A prior `failed`/`settled`
  reservation therefore never blocks a legitimate retry, but no two callers ever
  spawn the same attempt.

### Where it lives

The reservation table lives in the **coordinator's SQLite DB** — the coordinator
is already the queue's single writer, so reservations inherit the same
atomic-under-concurrency guarantee with no new locking. HTTP surface:

```
POST /spawn-reservations               {task_id, reserved_by} -> {reserved, reservation}
POST /spawn-reservations/{key}/spawned  {session_handle, worktree}
POST /spawn-reservations/{key}/fail     {detail}
POST /spawn-reservations/{key}/settle   {detail}
GET  /spawn-reservations                ?task_id&state&limit
GET  /spawn-reservations/{key}
```

`DispatchClient` exposes each as a method. Events (`spawn.reserved`,
`spawn.spawned`, `spawn.failed`, `spawn.settled`) are published on the SSE bus.

### How `create --spawn` uses it (the bug fix)

`create --spawn` now **reserves before spawning**:

1. `reserve_spawn(task_id)`. If `reserved=False`, print a skip note and return
   (an active spawn already exists — the double-spawn is prevented).
2. If `reserved=True`, run the spawn (`embody` or `bridge` backend).
3. On success, `record_spawn(key, session_handle, worktree)`; on non-zero exit
   or no mechanism, `fail_spawn(key, detail=…)` so a later run can retry a fresh
   attempt.

Fail-safe: if the reservation call itself errors, `create --spawn` **does not
spawn** (better to leave the task queued than risk a second autonomous worker).

## The supervisor loop (built)

The reservation primitive makes a safe host-side supervisor possible. The loop
(`supervisor.py`, CLI `agent-dispatch supervise`) is built around one hard
invariant:

> **A task is spawned only when a *fresh* spawn reservation is acquired for it.**

Because `reserve_spawn` returns `reserved=False` whenever an *active*
(`reserving`/`spawned`) reservation already exists, a task that is already being
spawned — or was spawned and later re-queued (its lease expired while the embody
is merely **slow**) — is skipped. **Lease expiry is not treated as death**, so a
slow-but-alive embody is never double-spawned. Each cycle:

1. **reconcile** — settle `spawned` reservations whose task reached a **terminal**
   state (`completed`/`abandoned`). This is the *only* automatic release, and only
   for a provably-finished task, so it can never free a still-running spawn.
2. **poll** — for each eligible queued task (in the lane, due, matching the
   optional **label opt-in**), up to `--max-concurrent` in-flight: `reserve_spawn`
   → if reserved, spawn embody → `record_spawn` (or `fail_spawn` on error, which
   releases a fresh attempt).

CLI:

```
agent-dispatch supervise [--repo R | --all-repos] [--label L ...] \
    [--max-concurrent N] [--interval S] [--once]
agent-dispatch reservations list [--task ID] [--state S]
agent-dispatch reservations fail|settle <key> [--detail ...]
```

### Deliberately deferred (needs embody-session liveness detection)

Two capabilities are **intentionally not** in this slice, because doing them
without liveness detection would reintroduce the double-spawn hazard:

- **Auto-recovery of a dead-but-non-terminal embody.** If an embody genuinely
  dies (lease expires, task re-queues, but the task never reaches a terminal
  state), the supervisor does **not** auto-respawn it — its `spawned` reservation
  is retained and the task is *held* (surfaced via `reservations list`). An
  operator confirms the embody is gone and runs `reservations fail <key>` to
  release it for a fresh attempt. Auto-recovery requires trusting lease-expiry as
  death, which requires liveness detection.
- **Supervisor-driven lease heartbeat.** To keep a live-but-quiet embody's lease
  from expiring (and being wrongly recovered), the supervisor would heartbeat on
  the worker's behalf — but a naive always-heartbeat would *mask* a dead worker.
  Safe heartbeating is gated on the same liveness signal.

The liveness-aware slice (integrating `agent-worktrees`/`agent-bridge` session
status) turns lease-expiry into a trustworthy death signal and unlocks both.

## Genericity

Nothing here is specific to any one producer. The reservation is keyed only by
task id + attempt; the supervisor is a generic delegation-layer capability. Its
first consumer is an external nightly-sweep producer, but no consumer-specific
identifier appears in this code.
