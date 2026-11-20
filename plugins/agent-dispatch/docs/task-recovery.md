# Task diagnostics and explicit recovery

Back to [agent-dispatch](../README.md). See also
[task authority](task-authority.md) and [wake custody](execution-wake-custody.md).

### Isolated proposal records

The Python `proposal_recovery.ProposalIntake` adapter creates one private,
unowned proposal in a fresh directory. Its callers must own the directory
and serialize its lifecycle; never point it at a coordinator queue. It records
the original task ID, creation inputs (including payload text), keyed steer
inputs and original acceptance receipts in `proposal.records`, independently
of each generation's task DB and payload files. A write is acknowledged only
after the independent record commits. Preserve that file with the same privacy
and retention controls as the task contents; it is not diagnostic telemetry.

To retain a draft across queue loss, read the current task and pass its
`steering_revision` and `card_draft_revision` as `expected_revision` and
`expected_draft_revision` to `intake.write_draft(fields, ...)`. Pass `None`
to clear it. A new accepted steer invalidates the draft; retrying its key
does not invalidate a later save. Direct queue save/clear calls are not
recorded by the intake and do not provide this recovery guarantee.

The first successful `write_draft` changes the independent records to a
draft-aware format. An older reader refuses those records; it cannot safely
recover even a cleared draft. On rollback, retain the records and use a
draft-aware reader with a fresh destination. Do not start an empty proposal
or rewrite the records to suppress the refusal. Unmodified v1 records remain
readable for their original proposal and steer guarantees.

`recover(records, new_generation)` uses the ordinary queue creator and steering
consumer to rebuild into an absent directory. It compares every receipt before
moving the journal writer epoch. An unsuccessful replay leaves the old writer
epoch in place; keep the incomplete destination aside and retry into a fresh
directory. A failed sidecar acknowledgment invalidates that intake handle.
An exact steer retry remains idempotent and a conflicting key refuses.
No HTTP endpoint, ordinary queue, existing proposal, executable claim,
consumption, provider wake, or full production history is migrated by this API.

For a quiescent handoff, the current intake owner calls `proposal_transfer.offer`
after its last acknowledged operation. This relinquishes the intake handle.
The incoming build calls `receive(records, new_generation, outgoing=current_path)`.
The records bind the format and path to the outgoing epoch; a different path
is not an alternate source. Compatible import uses SQLite online backup and
payload files; an incompatible offer or absent cache DB instead replays logical
creation and steer records into the new generation. Observe the returned route
before selecting the incoming generation. This opt-in data handoff does not
implement uninterrupted service/lease cutover.

`tools/dispatch_installed_handoff.py` takes distinct installed baseline and
candidate Python executables. For the negative control build a **disposable
copy** of the candidate project with `CACHE_FORMAT = 2` instead of `1` and a
distinct fixture-local package version before building its wheel. Install
each wheel non-editably in its own empty environment, then run with
`--expect-incompatible`. The driver withholds the outgoing DB and payloads
in both directions. The positive control keeps both formats at 1. Never
change the production constant or edit an installed slot to select a test.

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
The separate opaque `reconciliation_revision` is for original-wake
reconciliation. It fences the task's semantic state and the same retained
records, but does not become stale merely because the original owner renews
its lease or publishes liveness/activity observations. Private task content
participates in that fence without appearing in diagnostics.
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

Strict recovery also refuses a pending [source-relevance closeout](task-relevance.md).
Those operations advance task generation, whereas the accepted closeout belongs
to its captured original owner and generation. Diagnostics include that closeout
in the opaque revision and report this refusal for all strict reset/detach
operations; acceptance after a diagnostic read invalidates its revision.
An already committed recovery still returns its original receipt on retry,
without repeating a mutation against a later closeout. Legacy Proposed reset
retains its same-generation behavior and independent execution guards. Neither
path transfers closeout authority to a newer task episode.

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

For this operation, select `reconciliation_revision` from the current
diagnostic response when available. The general `revision` remains accepted
with its stricter timestamp fence for existing callers. Reset and agent
detachment accept only the general revision; a wake-specific revision cannot
authorize those lifecycle changes.

The additional diagnostic field is optional for independently updated
callers. An older receiver supplies only its general revision. After rollback,
a token issued by a newer receiver can be rejected as stale: refresh diagnostics
from that actual receiver rather than retrying an unknown operation.
Meaningful task changes, including payload, routing, holds and ownership,
and any retained-execution change still invalidate wake reconciliation both
before and after provider observation.

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

### Explicit original-key settlement

Passive reconciliation remains the default. Separately authorized recovery may
select `--settle-native-send` on that same command. Python `reconcile_wake` and
the REST body accept the strict boolean `settle_native_send`, defaulting to
false; MCP remains receipt-only. Clients require the running coordinator's
additional `task_recovery/v1` operation `settle_wake` before requesting it.
An older coordinator refuses the unsupported effect; no legacy mutation is tried.

The coordinator resolves the same original outbox, reservation, native target,
key, caller and private prompt internally. It discovers the selected executing
Bridge CLI's version-1 `agent-bridge.send` settlement capability, then invokes
its explicit [original-key settlement](../../agent-bridge/docs/native-send-settlement.md)
front door. That CLI requires the actual daemon's settlement capability before
its non-retrying POST. Receipt-only observation is never upgraded implicitly.
An admitted send still marked `pending` is in flight and refuses settlement
before contacting Bridge. Its original sender must first publish its transport
outcome; expiry alone cannot grant settlement. This prevents a late busy or
not-attempted completion from racing a provider fence, including a lost reply.

A matching accepted result settles `sent`. A matching `not_admitted` result,
including the provider's durable fence and original-request digest, settles
`not_sent` and terminal `failed` delivery status on the original wake row,
before either outbox's lease recovery or drainer can reclaim it. The owner's
task wake-status mirror is updated, without changing the task lifecycle.
The digest covers the version-1 Bridge
request document: session ID and the original invocation key, native ID,
prompt, `agent-dispatch:<wake-id>` caller and `no_resume: false`.
The queue rechecks its opaque revision after the provider operation. A changed
task/outbox refuses local settlement even if Bridge already persisted its
original-key result; fresh diagnostics and an explicit repeat can recover it.
Lost, malformed, conflicting or unavailable responses retain unknown custody.
No automatic retry, ordinary send or other-target fallback occurs.

Settlement preserves attempts, generations, delivery tokens, deadlines,
workspace and native bindings. Its negative detail is not the retryable
busy/not-attempted receipt, so it cannot authorize another delivery of the
fenced key. A pre-existing `not_sent` row is eligible only when it already
retains matching qualified fenced-settlement evidence; a retryable refusal
is rejected before contacting Bridge, even if a settlement reply could be lost.
An already committed matching outcome can be observed again;
contradictory terminal outcomes refuse. Any independent later lifecycle action
retains its own policy and authority gates. This source capability neither
qualifies legacy admission history nor performs runtime adoption.

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
