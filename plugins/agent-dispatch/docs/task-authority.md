# Task-scoped coordinator authority

A trusted supervisor starts an assigned task using its normal coordinator
credential. When a control signing key and owner session are configured, the
start response includes `task_token`. Only that short-lived credential is
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
| POST | `/tasks/{task_id}/progress` | Bounded progress |
| POST | `/tasks/{task_id}/heartbeat` | Extend owned task lease |
| POST | `/tasks/{task_id}/complete` | Existing completion transition |
| POST | `/tasks/{task_id}/abandon` | Existing abandonment transition |

All other routes are denied, including global queue reads, directory discovery,
event streams, arbitrary claim, start/token renewal, session adoption and
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

This is not completed fleet federation. Directory epoch observation is not
durable writer fencing, queue replication, an offline takeover protocol or
container credential delivery. Those require separate authority and lifecycle
proofs before deployment. An independent local database must not become the
writer for centrally owned tasks merely because its directory role is standby.
