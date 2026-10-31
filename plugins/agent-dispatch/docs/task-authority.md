# Task-scoped coordinator authority

A trusted supervisor claims and binds an assigned task using its normal
coordinator credential, then obtains a control-issued grant. The existing
trusted `start` path also returns `task_token` when a signing key and owner
session are configured. Only that short-lived credential is
delivered to a restricted worker; shared and control credentials stay outside
the worker's trust boundary.

Restricted workers require an authenticated coordinator ingress: configure the
listener's shared bearer and use the selected authenticated transport. Legacy
unauthenticated local mode is for trusted callers only; omitting a scoped header
there still selects legacy local authority. Do not expose that mode to a
restricted container and assume possession of a narrow token restricts all
possible requests.

The worker sends `X-Agent-Dispatch-Task-Token`. This header always selects narrow
authority, even if an ambient shared credential is also present or the listener
allows unauthenticated local callers. Invalid tokens fail with 401; valid tokens
on an unlisted route or another task fail with 403.

Supported operations are:

| Method | Task route | Scope |
|--------|------------|-------|
| GET | `/tasks/{task_id}` | Current assigned task |
| GET | `/tasks/{task_id}/payload` | Assigned task payload |
| POST | `/tasks/{task_id}/start` | Start the already-bound incarnation, without minting a token |
| POST | `/tasks/{task_id}/progress` | Bounded progress |
| POST | `/tasks/{task_id}/heartbeat` | Extend owned task lease |
| POST | `/tasks/{task_id}/complete` | Existing completion transition |
| POST | `/tasks/{task_id}/abandon` | Existing abandonment transition |

All other routes are denied, including global queue reads, directory discovery,
event streams, arbitrary claim, token renewal, session adoption and
coordinator management. MCP does not accept task credentials.

Each grant binds task, worker, owner session, task generation, observed
coordinator epoch and finite expiry. The HTTP boundary rechecks current owned
state. Mutations also compare generation and session inside the queue's write
transaction, so replacing the session after HTTP admission cannot authorize a
stale update. The existing transition rules still apply. Mismatching worker
fields are replaced by the token's verified identity, never trusted.

The supervisor must obtain a fresh grant before expiry. A restricted worker
cannot renew its own grant using `start`. Signing-key rotation invalidates old
grants. Tokens must not be logged, included in event records or placed in public
task results.

## Identity is not send custody

Scoped grants are conditional, not universal per-agent or per-worktree tokens:
the trusted start/grant path needs configured control signing authority and an
exact bound owner session. A worktree/worker label alone proves no request's
origin. The signature, expiry, task incarnation and current ownership checks
establish the scoped request authority described above; ordinary trusted local
or shared/control callers use their existing access boundary.

A wake's delivery token is different: it is the leased custody identity of one
outbox attempt, not a worker credential. The retained send identity links that
original attempt to transport settlement. Losing or expiring a delivery lease
does not prove a send absent, and a task grant cannot settle an unknown send.
The [task recovery guide](task-recovery.md) explains the control-only diagnostic
and evidence-bound reconciliation surfaces. They expose neither token value
nor a universal identity scheme.

## Opt-in supervisor delivery

An existing label-gated headless lane can select `restricted_worker` in its
registration spec (or `supervise --restricted-worker '<JSON>'`). This is not
a second supervisor or queue. The JSON has exactly these fields:

```json
{
  "command": ["/path/to/agent-containers"],
  "name": "worker-1",
  "instance_id": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "coordinator_url": "https://coordinator.example.com"
}
```

`command` is the trusted provider's exact attributable invocation argv, including
an interpreter when the platform requires one, not a command supplied by a task.
`instance_id` is the actual provider-returned physical container ID, not its
reusable name. The worker URL must reach the same authenticated coordinator
from inside that container. Configure both shared ingress authentication and
the distinct control signing credential. Legacy unauthenticated ingress
explicitly refuses restricted delivery.

This initial composition supports one explicitly selected, already-provisioned
dispatch-only container. The lane requires labels, `max_concurrent: 1`, and
the headless backend, without fleet, CLI/script overrides, or no-pair allocation.
It does not select, create or replace containers; container-pool placement is
separate. The provider's physical/birth binding remains one task incarnation:
the supervisor cannot reuse it for another task or epoch.

The supervisor reserves through the existing queue, claims through its normal
authenticated API, and records the **returned** claim generation and retained
fencing epoch. It then prepares that exact provider instance, uses Bridge's
keyed provider-native creation without a first prompt or synthetic worktree,
binds the actual native session with the existing generation CAS, obtains a
control-issued grant, and publishes that grant through the provider's bounded
stdin operation. Only after publication does it send the assignment to the
native-fenced Bridge handle. The worker runs task-only `start`, reads its payload
and reports its result directly.
If the authoritative claim returns no matching task, the existing deferral
transition can free the lane only while the same generation remains unclaimed
and the reservation has no delivery receipt, workspace or body. Capability or
target mismatches therefore do not consume the lane's only slot. A raced claim,
unknown response or any prepared startup retains its identity instead.
An empty historical deferral does not bind a later capable ordinary worker;
its start follows the normal acquisition checks. Admitted restricted deliveries
continue to require their exact retained native identity.

Nonsecret, monotonic startup receipts stay on the existing spawn reservation
behind a control-only journal endpoint. They contain no shared/control/task
credential or executable command. Restart reconciliation retains the claim,
physical binding, creation key and original native conversation. A recorded
first-send intent with an uncertain acknowledgement requires explicit repair;
it is not permission to send another first prompt or create another agent.
Acquiring that intent is a one-shot queue transaction, including when two
supervisors overlap. A lost creation acknowledgement can recover the stopped
original conversation through its keyed identity and strict, unprompted resume.
Every acknowledged send retains its delivery receipt and advances spawn
bookkeeping atomically, so completion during or after delivery cannot strand
a reserving lane slot after interrupted generic publication.
Generic confirmed-gone recovery never requeues a delivered restricted task or
replaces its conversation: the provider's physical binding cannot adopt a new
task generation. A stopped delivered worker retains its original incarnation
for explicit repair, even when ordinary automatic recovery is enabled.
The coordinator's independent liveness sweep follows the same rule, while
still recording the observed verdict. It does not requeue, suspend or
dead-letter a restricted incarnation, even at the ordinary recovery-attempt
cap.

On the existing supervision cadence, a positively live, exact native worker
receives a fresh task grant through the same provider operation. Unknown
liveness, a changed native conversation, configuration, generation or epoch
refuses renewal. With heartbeat enabled, the supervisor also uses that grant
for a transactionally fenced lease heartbeat; issuing a grant alone does not
extend a lease. The public scoped-heartbeat client operation also supports
the production client's per-operation coordinator re-resolution.
Activity publication remains independent of that lease heartbeat: the trusted
supervisor observes the retained Bridge handle only while its native identity
matches, then publishes through the existing reservation and task-incarnation
fence. A replaced native session or a concurrent generation/start transition
cannot supply activity for the old observation.
Commands are always taken from the current trusted lane
configuration, never executed from a stored queue receipt.

This is not completed fleet federation. Directory epoch observation is not
durable writer fencing, queue replication, an offline takeover protocol or
automatic container placement. Those require separate authority and lifecycle
proofs before deployment. An independent local database must not become the
writer for centrally owned tasks merely because its directory role is standby.
