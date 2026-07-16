# agent-dispatch — Embody Spawn Supervisor (design)

Status: **in progress** — the spawn-reservation primitive, the supervisor loop
(spawn-at-most-once), **and the liveness-gated lease heartbeat** are built;
dead-embody *auto-recovery* (needs confirmed-death detection), the backlog-catch-up
policy, and the authenticated container transport land in follow-up slices.
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
   releases a fresh attempt). A task that accumulates `--max-attempts` **failed**
   spawn attempts is **dead-lettered** — held, no longer auto-retried, its failed
   history left queryable via `reservations list --state failed` for a human — so
   a persistently-unspawnable task can't drive a retry storm.

CLI:

```
agent-dispatch supervise [--repo R | --all-repos] [--label L ...] \
    [--max-concurrent N] [--max-attempts N] [--no-heartbeat] [--interval S] [--once]
agent-dispatch reservations list [--task ID] [--state S]
agent-dispatch reservations fail|settle <key> [--detail ...]
```

### Lease heartbeat (built) — the live-worker safety net

Each cycle the supervisor also **holds the lease of every confirmed-alive
embodied worker** (`hold_live_leases`, gated on `--no-heartbeat`). For each
`spawned` reservation whose task is leased (`claimed`/`started`), it probes the
embody session's liveness (`tracking.resolve_live_session` → the agent-bridge
live-session registry, cross-machine over SSH for a remote owner) and, **only on
a confirmed-alive result**, sends a lease heartbeat on the task's behalf. This
keeps a live-but-quiet worker (one not emitting progress between phases) from
having its lease expire and being wrongly re-queued — closing the "don't trust
the LLM to emit progress to hold its lease" gap.

The safety hinge: heartbeats fire **only** on a positive liveness result. A
`None` probe collapses *dead* and *bridge-unreachable* together, so it is treated
as neither alive (no heartbeat) nor proof-of-death (no recovery). A genuinely
dead worker therefore stops being heartbeated, its lease expires naturally, and
its task is *held* (its `spawned` reservation blocks re-spawn) for recovery — a
transient bridge miss can't mask a live worker, whose own activity still extends
its lease.

### Deliberately deferred (needs *confirmed-death* detection)

- **Auto-recovery of a dead-but-non-terminal embody.** Auto-releasing a held
  reservation for a fresh attempt requires distinguishing *confirmed dead* from
  *bridge-unreachable* — the current liveness probe collapses both to `None`, so
  auto-recovery on `None` would double-spawn on a transient outage. Until a
  positive "session is gone" signal (or a consecutive-confirmation + grace
  scheme) exists, a dead embody's task is held and surfaced via
  `reservations list` for a manual `reservations fail <key>`.

## Transport for a containerized producer

A producer running in a **Docker container** (e.g. a scheduled sweep container)
reaches the host coordinator over `host.docker.internal` (with
`extra_hosts: host.docker.internal:host-gateway`). Two facts shape the safe bind:

- The coordinator defaults to **loopback** (`127.0.0.1:9847`), which a container
  **cannot** reach.
- On Linux, each compose service gets its **own** bridge network with its own
  host-local gateway (all in `172.16/12`, none LAN-routed), so no *single*
  host-local IP is reachable from every container. The address reachable from all
  of them **and** the host is the wildcard `0.0.0.0` — which also exposes the LAN.

So the containerized-producer transport is: **bind `0.0.0.0`, require a bearer
token, and firewall the port off the LAN** (allow loopback + the Docker bridge
subnets `172.16.0.0/12`; drop the LAN interfaces). The token is enforced by the
**bind-safety guard** (`server.check_bind_safety`): binding a wildcard host
without `AGENT_DISPATCH_TOKEN` is refused outright, so the powerful task-control
API can never land on the network unauthenticated. (A *specific* host-local bind
— loopback, a Windows vEthernet(WSL) IP, or one shared Docker bridge gateway — is
a deliberate non-LAN choice and is not guarded; a future shared-network refinement
could bind one gateway and drop the firewall requirement.) The producer sends the
same token as a bearer credential; producer credentials should be **create-only**,
separate from runner credentials.

## Genericity

Nothing here is specific to any one producer. The reservation is keyed only by
task id + attempt; the supervisor is a generic delegation-layer capability. Its
first consumer is an external nightly-sweep producer, but no consumer-specific
identifier appears in this code.
