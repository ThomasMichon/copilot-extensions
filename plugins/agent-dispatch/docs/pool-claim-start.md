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

## Durable startup budget

The queue's existing pool assignment owns durable startup counters and clocks;
it does not use a separate retry registry. Each explicit claim episode admits
up to three same-reservation startup invocations; advancing requires the prior
invocation's completed return. The count since
successful acceptance survives new episodes; an episode baseline limits each
episode without deleting earlier attempt history. Reachability and transport
delivery never reset either counter.

The original explicit placement API gives each startup invocation a 600-second
deadline, matching the existing supervisor's default reservation window. The
negotiated startup placement API instead accepts a positive finite configured
timeout and persists it in the same claim transaction. A placement replay
does not change it; subsequent same-reservation invocations use that stored
timeout, not a restarted supervisor's current settings. An explicitly
observed current reachable worker instead receives a separate 30-minute
decision deadline. The queue can atomically allocate five-minute nudge slots
for that same recorded native; taking a slot is **not** delivery or acceptance.
Only the successful current `STARTED` write resets the attempt and task-wide
cooldown counters. Acceptance checks the deadline inside the write lock.
Legacy pool assignments without recorded clocks require reconciliation, not
invented historical attempts. Ordinary non-pool worker start is unchanged.

The queue's explicit exhaustion operation returns an expired allocator claim
to `QUEUED`, fences its task generation and applies task-wide 1/3/6/12-hour
cooldowns while preserving any later `not_before`. Its reservation remains
`RELEASING`: missing workspace, native or receipt is not absence. An unresolved
original caller can still publish its late current-revision return for normal
retirement. No timeout itself releases capacity, changes retained pool routing,
erases attachments or permits replacement execution.

The original caller can durably report `startup_failed`, `startup_rejected` or
`startup_unknown` against the exact task, generation, reservation, startup
revision and actual recorded native (including an explicitly unknown native).
These are startup outcomes, not worker declines or proof that no process exists.
A completed failure closes that caller's invocation without freeing its slot.
An unknown outcome leaves the invocation incomplete. An exact replay does not
renew the clock; the original unknown caller can report its later completed
failure. Completed conflicting outcomes refuse. The existing task audit retains
each classification when a new invocation clears the current outcome fields.

Three completed failed/rejected invocations can be exhausted immediately as
`startup_attempts_exhausted`. An incomplete invocation, fewer than three failures,
a stale revision or an already-reachable worker cannot manufacture that result.
Deadline exhaustion remains separately classified as `startup_timeout` or
`decision_timeout`; all three use the same task-wide cooldown and
capacity-preserving release owner.

## Negotiated startup supervision transport

`DispatchClient.pool_startup_contract()` reads the running coordinator's
control-authorized `GET /pool-placements/startup/v1/contract`. This separate
`pool_startup/v1` family advertises durable budgets, configured timeouts,
classified outcomes, same-native decision clocks and capacity-preserving
exhaustion. Its strict version-1 routes do not exist on older providers, so
rollback between discovery and mutation cannot silently invoke an older
placement, start or reservation operation. No package-version comparison,
cached contract, response corruption or transport failure permits a fallback.

| Client operation | Route beneath `/pool-placements/startup/v1` | Meaning |
|---|---|---|
| `place_pool_startup(task_id, reserved_by=..., catalog_revision=..., startup_timeout=...)` | `POST /place` | Atomic configured-timeout claim; only validated `reserved=true` permits initial startup |
| `pool_startup_snapshot(task_id)` | `GET /tasks/{id}` | Coherent budget, reservation, task and actual native observation |
| `observe_pool_reachable(task_id, key, generation=..., startup_revision=..., native_id=...)` | `POST /tasks/{id}/reachable` | Independently observed current worker; not acceptance |
| `take_pool_nudge(...)` | `POST /tasks/{id}/nudge` | One atomic due same-native slot; `None` means no slot, not delivery |
| `record_pool_startup_outcome(..., outcome_kind=..., detail=..., native_id=...)` | `POST /tasks/{id}/outcome` | Original caller's classified outcome; not native absence |
| `retry_pool_startup(..., native_id=..., worktree=..., machine=..., reserved_by=...)` | `POST /tasks/{id}/retry` | One next same-native invocation after a completed failure; not transport |
| `complete_pool_startup(..., outcome_kind=..., detail=...)` | `POST /tasks/{id}/complete` | Exact retry caller return, including an unresolved or late outcome |
| `exhaust_pool_startup(..., reason="deadline"\|"attempts")` | `POST /tasks/{id}/exhaust` | Fenced `QUEUED` with cooldown; unresolved capacity retained |

Every mutation rechecks its exact task and incarnation in the queue transaction.
Responses bind those same facts; malformed successful replies are explicitly
unknown and never replayed automatically. A snapshot with no pool assignment is
not proof of no native effects. Rejected configured placement returns the
structured `pool_claim_rejected` code; admission inhibition keeps its existing
structured owner result. Old explicit placement/start callers and ordinary
non-pool claims remain supported with their original wire forms and defaults.

This is an explicit supervision transport, **not** automatic placement
migration. Suspended recovery remains its existing independent path;
these operations do not change its counters or holds.

## Existing supervisor consumer

The existing reserving and spawned-worker reconciliation points recognize an
unowned allocator `CLAIMED` task through its negotiated coherent startup
snapshot. The recorded allocator supervises that same reservation; a different
lane, held task or changed incarnation is retained without action. An explicit
no-assignment observation preserves the legacy path. Unsupported, malformed or
unavailable startup observations do not authorize legacy gone-worker recovery.

A completed successful invocation needs positive liveness for the retained
native, workspace and execution machine before the supervisor records
reachability. A different or missing live-session result is unknown, not
absence. A failed, rejected or unknown original invocation is not reclassified
as successful merely because its process is live. Once reachable, the durable
five-minute slot controls notification through the existing Bridge transport,
with an exact expected-session fence and recorded host. The message is
`notify`-kind: it does not enqueue work, perform legacy evaluation claiming,
start another native or accept the task.

Failed/unknown delivery consumes only the selected notification slot; a restart
does not immediately resend it or renew the thirty-minute decision deadline.
Expired startup/decision clocks and three completed failures use negotiated
exhaustion, preserving `RELEASING` capacity and the original caller's unresolved
late return. The legacy confirmed-gone path cannot retire this allocator claim.
Admission inhibition and installation governance also preserve it.

Completed failed/rejected calls can admit the next same-native retry only after
an exact native/workspace/host liveness probe. The existing spawn write binds
task, generation, reservation, startup revision, native, workspace, execution
machine and allocator, increments the existing assignment budget and retains the
immutable invocation before transport. Competing or stale retry calls cannot
charge or send a second invocation. An incomplete or unknown original call is
not retry eligibility. Legacy spawn/outcome calls cannot complete or advance an
invocation acquired through the explicit retry route.

The transport uses Bridge's [exact live-CLI admission and keyed receipt
observation](../../agent-bridge/docs/delegation-contract.md#exact-live-cli-inbox-admission-and-observation),
not the ACP native-turn helper or the legacy queued redrive prompt. It sends only
after a validated new retry admission. Its prompt uses `--pool-generation`, not
legacy evaluation claiming. After a lost admission reply, transport response or
supervisor restart, only an observation of the retained invocation is permitted.
Missing, malformed or unsupported receipt evidence remains unknown; it cannot
authorize a send replay, new native or replacement capacity.

The original caller reports `startup_succeeded`, `startup_failed`,
`startup_rejected` or `startup_unknown` through exact completion. Success closes
only startup transport, not task acceptance. The same receipt-only reconciliation
also runs after the worker accepted `STARTED` or the claim exhausted to `QUEUED`.
An exact late completion preserves the task generation, reset counters or
cooldown already established by those independent transitions and keeps a
`RELEASING` reservation's capacity and release intent. Conflicting completion
and unrelated caller identities refuse.

Ordinary placement/worker-prompt migration and installed qualification still
gate the complete policy. These explicit consumers do not activate that lane.

## Integration boundary

The descriptor still reports `automatic_lane_migration: false` and
`startup_retry_policy_complete: false`. Complete configured-source publication,
mixed-cohort exclusion, reservation-preserving startup attempts, decision
nudges/exhaustion/cooldowns and ordinary supervisor/prompt migration remain
required integration work. Neither these callers nor their source tests
authorize installed activation, free unresolved effects or qualify the full
[assignment/startup amendment](../../../efforts/active/agent-dispatch-monitor-and-confirmed-state/assignment-startup-amendment.md).
