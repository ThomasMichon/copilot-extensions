# Worktree-owned task obligation evidence

The ordinary `GET /tasks/{task_id}` detail response includes `task_obligation`,
with schema `dispatch-task-obligation`, version `1`. The existing
`claim-status <task_id>` callback forwards this optional object after validating
its identity, version, types and release-outcome consistency. No new CLI flag,
health request or effectful probe is needed.

This is the provider-first evidence boundary for the
[reviewed worktree-owned responsibility design](../../../efforts/active/agent-dispatch-monitor-and-confirmed-state/submission-closeout-amendment.md).
It does **not** create assignment claims, change task transitions, release claims,
finalize worktrees or authorize physical cleanup. Ordinary assignment admission,
claim-owner integration, submission readiness and same-worker rejection recovery
remain separate implementation gates.

## Snapshot and outcomes

The coordinator reads the task, logical pool assignment, unresolved spawn
binding and latest state-changing audit event in one SQLite read snapshot.
The object contains:

| Field | Meaning |
|---|---|
| `task_id`, `generation`, `state` | Current task incarnation and lifecycle state |
| `bindings` | Boolean presence of task owner, native owner, explicit target worktree, unreleased pool assignment and unresolved spawn binding |
| `held` | Any retained task hold field |
| `unassignment` | Latest transition from claimed, started or suspended to queued, with its event id, original worker id and prior state; otherwise null |
| `release_outcome` | `completed`, `unassigned_queued`, or null |

`completed` reports the existing authoritative `COMPLETED` state. This preserves
the current behavior of tasks without required verification, whose completion
operation already records self-attestation. Verification-required tasks remain
`SUBMITTED` until confirmation. This expansion neither changes historical tasks
nor introduces a new independent-evaluation requirement.

`unassigned_queued` requires all of: current `QUEUED`, no task hold, no retained
binding and a recorded state-changing unassignment event. Initial queued work,
an unaudited status edit, a still-bound queued task, `SUBMITTED`, suspension,
abandonment and dead-letter status do not satisfy it. Lease expiry, process exit,
idle time and native liveness are not settlement inputs.

A reservation with a confirmed release request in `RELEASING` is no longer a
logical task binding. Its separate allocation/native-retirement obligation and
capacity remain accountable. An unreleased pool assignment still blocks the
unassignment outcome even when that reservation is releasing. The existing
decline closeout can therefore report logical unassignment without pretending
that its original native process has stopped.

This object is current evidence, not a transferable release grant. A future
claim-owner consumer must retain the original task authority, task/worktree
assignment identity and relevant incarnation/event continuity, then reconcile
the exact claim through its existing owner. A matching raw task id or current
queued status alone cannot establish that relationship. Reassignment invalidates
the current unassignment outcome; it does not turn a prior observation into
permission to release a different assignment.

## Compatibility and cost

The additive object declares support from the **running coordinator** that
produced the response. Old callers retain the existing fields and syntax.
New callbacks talking to old coordinators preserve the old status envelope
without inventing obligation support. An absent field means unsupported; a
present malformed, inconsistent or unsupported object fails the callback,
not `exists: false`. Confirmed 404 remains the only task-absence outcome.
The native-restart opt-in response keeps its separate existing shape.

Explicit `dispatch-local-task:` and `dispatch-central-task:` references retain
their original writer flags. This evidence does not qualify ambiguous legacy
references or retarget them through the current default. No ordinary
claim-settlement caller is migrated by this provider expansion; upgrade and
rollback must continue to treat missing support explicitly.

Each detail read adds one connection/read transaction and four point-scoped
SELECTs, with no child process, network request, scan of repository files or
per-call schema migration. Task-event lookup uses a partial `(task_id, id)`
transition index; pool assignment and spawn queries use existing task indexes.
This is bounded source/transport qualification, not an installed/native latency
measurement or a complete assignment/finalization qualification.
