# Task diagnostics and explicit recovery

Back to [agent-dispatch](../README.md). See also
[task authority](task-authority.md) and [wake custody](execution-wake-custody.md).

## Inspect before choosing an action

`agent-dispatch task-diagnostics <task-id>` reads one bounded coordinator
snapshot. `GET /tasks/{task_id}/diagnostics`, `DispatchClient.task_diagnostics`
and MCP `dispatch_task_diagnostics` expose the same queue evidence.

The result includes the task's current status/owner/generation, target routing,
attachment history, retained reservations, wake/send outcomes, active waiters
and action prerequisites/refusal reasons. Native liveness is explicitly
**unknown**: a stored row is not a fresh Bridge or process observation.
Target worktree intent is not allocated ownership; an open old attachment
does not make the current task owned. Use the existing doctor/entity owners
for their deeper observational chain, not direct writes to their databases.

Histories have a 100-row per-source limit. Truncation is visible and refuses
recovery rather than treating an incomplete history as empty. Diagnostic output
omits prompts, payloads, raw agent receipts, delivery tokens and send identity
blobs. The opaque `revision` fences the captured task and retained records.
Diagnostics never wake, claim, repair, start optional infrastructure or mutate.
The CLI and default stdio MCP diagnostic factories use existing read-only
client discovery: they do not ensure a local coordinator, open SSH failover or
discover control credentials. An absent provider remains explicitly unavailable
through the existing bounded read-retry behavior, not an empty task snapshot.
New strict recovery and wake-reconciliation frontends also require an existing
coordinator rather than implicitly starting it or opening SSH failover; they
retain normal control-authority discovery. Legacy Proposed reset keeps its
original client behavior. Explicit injected MCP client factories remain
authoritative, including callable objects with false truth values.

An action's `allowed` value describes inspected prerequisites, **not authority,
operator confirmation or a promise of successful execution**. Existing lifecycle
actions recheck their own full rules. The separate `authority` result reports
whether coordinator recovery control is configured without exposing its value.

## Distinct actions, distinct effects

| Action | Meaning |
|--------|---------|
| `detach` | Existing target unpinning; hard worktree targeting becomes soft affinity |
| `detach-agent` | Clear the captured agent assignment, preserving target, progress and cards; held work becomes Queued |
| `reset --to proposed` | Default legacy reset; clears existing attempt fields, not retained execution facts |
| `reset --to queued` | Explicit fenced reset to claimable state, subject to all independent admission rules |
| `force-stop` | Existing separate physical session-stop operation; never implied by reset or detach |
| `hold` / `unhold` | Independent operator hold, not physical stopping or unknown-send settlement |
| `release` / `rearm` | Existing suspended-task or exact failed-reservation owners, not universal recovery shortcuts |
| `reconcile-wake` | Observe the original transport's receipt; never send or authorize replay |

Reset and assignment detachment refuse unresolved native bindings, active
reservations/cooling/exclusive custody, active waiters and unproven attempted
wakes. A native conversation needs qualified retirement through its existing
owner; a stopped status, absent prompt, lease expiry or caller assertion is not
retirement. These controls do not terminate or abandon the old execution,
discard independent holds or release an exclusive key. The legacy reset path
also enforces these retained-execution fences.
Retirement uses the existing driver's identity evidence and either its explicit
conversation marker or the supported exact-absence release's terminal state.
A terminal reservation's old `exclusive_released` flag alone is not active
exclusive custody. Ordinary, restricted and CLI identities remain distinct.
Native acquisition receipts count even before agent/session-handle publication.
A failed reservation with only a launched acquisition receipt remains blocked
until its native retirement is qualified.

### Fenced CLI mutations

Read diagnostics, then use its exact revision. Supply a stable request identity
for reset/detach and retain it across an ambiguous response:

```text
agent-dispatch detach-agent <task-id> --expected-revision <revision> --request-id <request-key> --actor <actor> --reason "<reason>"
agent-dispatch reset <task-id> --to queued --expected-revision <revision> --request-id <request-key> --actor <actor> --reason "<reason>"
agent-dispatch reset <task-id> --to proposed --expected-revision <revision> --request-id <request-key> --actor <actor> --reason "<reason>"
```

`--to queued` requires all fencing/audit fields. Supplying strict flags also
selects fenced Proposed reset; do not combine it with `--expected-status`.
Without strict flags, Proposed retains its legacy default and optional
expected-status interface. Invalid values never select a fallback mutation.

The control-only REST endpoint is
`POST /tasks/{task_id}/recovery/{detach_agent|reset_proposed|reset_queued}`.
Its exact JSON fields are `expected_revision`, `request_id`, `actor`, `reason`.
The Python `recover_task` method and MCP `dispatch_detach_agent`,
`dispatch_recover_task`, `dispatch_reset` use those same semantics.
Both MCP transports preserve their existing task-token refusal.

Admission rechecks the full snapshot inside the write transaction. Accepted
mutations increment task generation, preserve attachment history with an
explicit detach reason and append an audit event. The existing queue stores
the request/result receipt atomically. An exact retry returns that receipt
without changing later work; reusing the key with a different request refuses.
Neither current task state nor request identity is an authentication token.

## Wake reconciliation is observation, not retry

```text
agent-dispatch reconcile-wake <task-id> --outbox wake_outbox --wake-id <wake-id> --expected-revision <revision> --actor <actor> --reason "<reason>"
```

`run_waiter_wakes` is the other supported outbox. The corresponding endpoint is
`POST /tasks/{task_id}/wakes/reconcile`; its fields are `outbox`, `wake_id`,
`expected_revision`, `actor`, `reason`. Python `reconcile_wake` and MCP
`dispatch_reconcile_wake` match. No caller-supplied outcome or delivery token
is accepted.

Only a qualified original send with an unambiguous retained ordinary native
Bridge target can use this adapter. It asks Bridge's **receipt-only** standard
send command for the exact original native session, invocation key, caller and
prompt. The command requires protocol 51 plus the running daemon capability
`native_send_receipt: 1`; absence refuses without sending or resuming.

A positive original admission receipt can settle pending/unknown custody to
`sent`. This is acceptance, not consumption or task completion. The queue
rechecks the original snapshot after observation before settling. Original
wake ID, delivery identity, attempts and reservation stay unchanged. A retry of
an accepted settlement is observational; it does not repeat delivery.
Per-wake `reconcile_supported` and refusal reasons use these same queue-only
identity, state and adapter checks. A retained identity blob alone is not
reconciliation support. Transport availability is still checked when invoked.

Missing/corrupt/conflicting receipts, unavailable/old providers, represented CLI
or restricted targets without this adapter, and legacy attempted sends without
qualified original custody remain explicitly unresolved. An empty history
cannot produce `not_sent`. No force-clear, new wake, replacement conversation,
second queue or automatic replay is introduced.

## Support and authority

The running coordinator advertises
`health.task_recovery = {"protocol":"task_recovery/v1","operations":[...]}`.
New clients require that exact supported contract and named operation before
new effects. Old callers retain the original Proposed reset/target-detach
routes on an upgraded provider. New recovery callers refuse a provider rollback
or unsupported operation before mutation; they never try a new effect and
blindly retry an older one. Ordinary legacy commands remain independently usable.

Recovery requires the coordinator's existing control authority. A worker
bearer is not control authority, and scoped task credentials cannot reach these
diagnostic or recovery routes. The separate authentication and wake-custody
identities are explained in [task authority](task-authority.md#identity-is-not-send-custody).
Installation update, physical effects and live recovery still require their
own operator/owner authorization; releasing source does not perform them.
