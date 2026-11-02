# Explicit pool claim and worker acceptance

The explicit version-1 pool placement provider atomically binds an assignment,
occupies capacity and moves a task from `QUEUED` to `CLAIMED` before any workspace
or native process exists. The normal supervisor still uses its legacy
reservation/worker-claim path; these explicit operations do **not** migrate it.
See the [lifecycle architecture](spawn-supervisor.md#assignment-lifecycle-current-versus-target)
and [complete catalog owner](pool-publication.md) for those separate boundaries.

## Allocator caller

`DispatchClient.place_pool_task(task_id, reserved_by=..., catalog_revision=...)`
first reads the running coordinator's control-authorized
`GET /pool-placements/contract`. It requires `pool_placement/v1`, version 1,
the `cli` driver, atomic claim-before-spawn and affirmative worker start.
The POST supplies the whole catalog's revision, not a preferred pool, a subset
or a caller-computed capacity count. The provider rechecks its complete
configured catalog, task routing and occupied capacity in its write transaction.

Only a validated `reserved=true` result permits that caller's fresh startup.
The result must bind the requested task, allocator, catalog revision,
reservation and claim generation. An active reservation replay returns
`reserved=false`; it is custody evidence, **not** permission to spawn again.
Such a replay can carry its original catalog revision after the catalog moves.
The client does not retry unknown outcomes, choose a newer revision or fall
back to the legacy reservation API. A malformed successful reply reports
HTTP-style `502` with an explicitly unknown outcome; a committed claim remains
retained at its provider.

## Affirmative worker caller

A worker that has read its allocator-claimed task and independently decided to
accept can use the existing `start` command:

```text
agent-dispatch start <task-id> --pool-generation <current-task-generation> --owner-session-id <actual-native-session-id>
```

Its own machine/worktree identity is resolved normally; the explicit owner
argument remains available. The caller must use the **actual current** native
identity, already recorded through startup, not a generated placeholder.
Do not run the legacy `claim --evaluation` step for an allocator-owned claim.
Decline/closeout and automatic worker-prompt migration remain separate work.

`DispatchClient.start_pool_task(...)` implements this opt-in path. It reads
`/health.pool_placement` and requires `explicit_start_versions: [1]` in addition
to the placement contract. This descriptor uses ordinary API-read authority;
workers need no control credential. It sends `pool_placement_version: 1`,
the exact generation, worker and native identity to `/tasks/{id}/pool-start`.
This explicit route reuses the start handler and transition owner; an older
provider has no matching route and refuses before mutation even if rollback
occurs after the health read. The caller never retries it as `/start`.
The transaction requires an actual pool assignment and all current startup,
workspace, native, routing and owner fences. It cannot silently start a
legacy interactive claim instead. Response validation requires `STARTED`
under those exact identities; delivery or reachability never substitutes.
An unchanged current-native request can observe its already-accepted result.

Ordinary `start` without `--pool-generation` keeps its existing request and
behavior. Task-only projected workers retain their existing scoped start;
they reject this fleet-worker option rather than silently ignoring it.
Missing or incompatible runtime declarations refuse the explicit operation
before its write. Authentication and provider failures stay visible without
downgrading or changing credentials. No package-version comparison or cached
declaration authorizes a later operation after provider rollback.

## Integration boundary

The descriptor still reports `automatic_lane_migration: false` and
`startup_retry_policy_complete: false`. Complete configured-source publication,
mixed-cohort exclusion, reservation-preserving startup attempts, decision
nudges/exhaustion/cooldowns and ordinary supervisor/prompt migration remain
required integration work. Neither these callers nor their source tests
authorize installed activation, free unresolved effects or qualify the full
[assignment/startup amendment](../../../efforts/active/agent-dispatch-monitor-and-confirmed-state/assignment-startup-amendment.md).
