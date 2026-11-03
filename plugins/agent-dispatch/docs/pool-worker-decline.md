# Pool worker decline preparation

The explicit provider can retain a current worker's decline **before** its own
workspace closeout. This is not legacy `yield`, successful finalization,
requeue permission or native retirement. The
[claim/start contract](pool-claim-start.md) advertises
`worker_decline_preparation_versions: [1]` separately from
`worker_decline_closeout_complete: false`.

## Current boundary

`DispatchClient.prepare_pool_decline` reads the running coordinator's health
contract before `POST /tasks/{task_id}/pool-decline`. It supplies version `1`,
`worker_id`, `native_id`, `generation`, `startup_revision`, `reservation_key`,
`exclusions` and `note`. This operation inherits the same coordinator data
authentication as explicit start. A pre-acceptance worker does not yet have
task-scoped ownership, and task-only credentials are not admitted to this route.

The queue rechecks its allocator-owned `CLAIMED` incarnation, completed startup,
exact retained reservation/workspace/native, execution admission, holds,
pending steer and current startup/decision deadline under its write lock.
The nonempty exclusions may name only that worker's actual `worktree:<id>`,
`pool:<registration-id>` or `machine:<execution-machine>`. Foreign, capability,
environment and repository exclusions are refused. A meaningful explanation
is mandatory. A started worker cannot reinterpret its accepted task as an
initial decline; its existing suspend/recovery contract remains separate.

The result uses `pool_worker_decline/v1`, version `1`, and a `decline` record.
That record retains the exact decision binding, sorted unique exclusions,
explanation, creation/update times and optional closeout blocker. It is also
visible in the task detail and coherent startup snapshot as `worker_decline`.
The original allocator assignment, reservation, task generation, startup
counters and task routing are unchanged. The task has **not** accepted work:
its task-row worker owner remains unset, while the decline record identifies
the exact native worker responsible for closeout.

An identical current-incarnation retry returns the original decision, even
after its original decision deadline. It does not refresh timestamps or audit
history, widen exclusions or replace the explanation. Changed identity, holds,
startup revisions or disposition refuse. Malformed or lost replies never
trigger a legacy yield, another coordinator or an automatic POST replay.
The recorded original remains available for explicit recovery.

`DispatchClient.record_pool_decline_blocker` uses the same negotiated contract
and exact binding at `POST /tasks/{task_id}/pool-decline/blocker`, adding
`detail` instead of exclusions/note. It records a failed closeout visibly
without changing the original decision. Identical blocker replay is a no-op.

While this claim is pending closeout, acceptance, startup retry/nudge/exhaustion
and reservation release/retirement cannot bypass it. Existing supervision
observes the decline and retains custody rather than sending another decision
nudge or falling into legacy recovery. No new timer or cleanup owner is added.
An explicitly authorized terminal task disposition retains the historical
decline without deadlocking normal reservation retirement.

## Remaining integration

There is deliberately no successful-closeout or requeue endpoint in this
provider expansion. Ordinary CLI/prompts do not invoke it yet. The next
consumer boundary must negotiate Worktrees support, finalize only the
declining worker's own correct workspace through its normal source/PR,
outbound-claim, foreign/shared-session, pairing and follow-up gates, then
commit exclusions and `QUEUED` atomically at Dispatch. It must preserve
accountable native effects and ordinary physical-cleanup admission.
A caller-supplied `finalized: true` field is not that contract.

Older or rolled-back peers missing preparation support refuse before POST;
there is no equivalent legacy fallback. A provider that ignores a retained
decline is not a supported rollback target for an adopted pending claim.
Source controls do not establish installed adoption, complete decline
closeout, routing enforcement after requeue or end-to-end worker acceptance.
