# Worktree-owned task obligation evidence

The ordinary `GET /tasks/{task_id}` detail response includes `task_obligation`,
with schema `dispatch-task-obligation`, version `1`. The existing
`claim-status <task_id>` callback forwards this optional object after validating
its identity, version, types and release-outcome consistency. No new CLI flag,
health request or effectful probe is needed.

This is the evidence boundary for the
[reviewed worktree-owned responsibility design](../../../efforts/active/agent-dispatch-monitor-and-confirmed-state/submission-closeout-amendment.md).
The source queue also integrates protected relationships at direct assignment
and allocator workspace attachment, as described below. That integration
requires an explicitly assembled, qualified ground-owner writer; ordinary
coordinator construction leaves it disabled. Neither this evidence nor an
assignment claim finalizes a worktree or authorizes physical cleanup.
Writer-cohort activation, submission readiness and same-worker rejection
recovery remain separate qualification gates.

## Snapshot and outcomes

The coordinator reads the task, logical pool assignment, unresolved spawn
binding, queue identity and assignment/disposition audit events in one SQLite read snapshot.
The object contains:

| Field | Meaning |
|---|---|
| `task_id`, `generation`, `state` | Current task incarnation and lifecycle state |
| `queue_identity` | Durable identity of the original queue, independent of endpoint configuration |
| `assignment` | Exact event id and worker from a direct claim or protected allocator workspace attachment; otherwise null |
| `relationship` | Original retained request for a protected assignment, when present |
| `bindings` | Boolean presence of task owner, native owner, explicit target worktree, hard worktree requirement, unreleased pool assignment and unresolved spawn binding |
| `held` | Any retained task hold field |
| `unassignment` | Latest transition from claimed, started or suspended to queued, with its event id, separately recorded former worker id and prior state; otherwise null |
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
Hard `requires=["worktree:<id>"]` routing remains a binding after yield, just as
an explicit target worktree does. Malformed requirement storage is not positive
absence of a worktree requirement.

The existing audit row retains nullable `unassigned_worker` independently of
its `worker` field, which may instead identify the operator performing recovery.
Yield, suspended release, explicit recovery and pool decline record the actual
former worker in that same disposition transaction. Historical rows and older
writers without this fact cannot establish unassignment evidence; the provider
does not guess the former worker from an audit actor or backfill legacy history.

A reservation with a confirmed release request in `RELEASING` is no longer a
logical task binding. Its separate allocation/native-retirement obligation and
capacity remain accountable. An unreleased pool assignment still blocks the
unassignment outcome even when that reservation is releasing. The existing
decline closeout can therefore report logical unassignment without pretending
that its original native process has stopped.

This object is current evidence, not a transferable release grant. The
claim-owner consumer retains the original task authority, task/worktree
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
references or retarget them through the current default. Ordinary
construction does not enable the qualified writer; upgrade and
rollback must continue to treat missing support explicitly.

Each detail read adds one connection/read transaction and five point-scoped
SELECTs, with no child process, network request, scan of repository files or
per-call schema migration. Task-event lookup uses partial `(task_id, id)`
transition and worker-assignment indexes; pool assignment and
spawn queries use existing task indexes.
This is bounded source/transport qualification, not an installed/native latency
measurement or a complete assignment/finalization qualification.

## Protected retained-relationship callback

The existing explicit local/central `claim-status` callbacks also understand
Worktrees' canonical `l1.` retained-relationship subjects. Legacy raw-task
callbacks retain their previous behavior. Recovery version 1 accepts only bound
subjects; the qualified assignment integration uses recovery version 2.

The recovery object is exactly:

```json
{
  "schema": "dispatch-task-assignment",
  "version": 1,
  "authority": "local",
  "queue_identity": "<original 32-hex queue identity>",
  "task_id": "<original 32-hex task id>"
}
```

Its bound identity is exactly `{"assignment_event_id": 123, "worker_id":
"machine/worktree"}`. The creator must be that worker's qualified
`machine/project/worktree` owner. The surrounding version-2 metadata, token,
request id, phase and canonical encoding remain the existing Worktrees
retained-relationship contract, not another ledger.
Opaque tokens retain Worktrees' full 32-128-character contract; the callback
does not infer their valid range from the current token generator.

The callback reads that exact assignment using the additive
`obligation_assignment_event_id` query on the existing task-detail route.
The selected audit row, queue identity and current disposition share one
SQLite snapshot. Another task's event, another queue at the same endpoint,
an unaudited assignment, malformed data or missing provider support cannot
produce retirement. The latest assignment is returned when the query is absent;
older snapshot readers may ignore the two additive fields.
This event form covers the direct worker claim operation (`note="claim"`).
An allocator's earlier pool-placement actor is not the eventual worktree
worker and cannot qualify it. The protected allocator attachment instead records its actual workspace worker
in a distinct `worktree assignment` audit event with the retained request.
Missing direct-claim evidence is never permission to substitute a supervisor.

An exact `relationship_verification` proves retirement only for `completed`,
or for current unbound `unassigned_queued` whose later disposition names that
same worker. A later assignment or another worker's unassignment does not
retire the original claim. An eventual completion can settle any of that task's
genuine earlier assignments. `quiescent` describes settled **task
responsibility**, not native-process absence; Worktrees' separate native and
cleanup gates still apply.

No callback starts a coordinator or permits SSH failover. It uses only an
explicit original local/central writer, additionally checks the durable queue
identity, and never reconstructs original authority from a changed default.
Version-1 pending subjects, unqualified/URL-selected authorities and unknown
encodings fail visibly. A 404 for this protected relationship is unavailable evidence,
not proof that its task responsibility disappeared. A supported provider
rollback lacking exact assignment evidence likewise retains the obligation.

## Qualified assignment integration

The existing queue owner accepts an `AssignmentWorkspace` containing the
original machine, project, worktree id, absolute path and creation nonce.
The selected Worktrees adapter checks that exact record; it never derives
historical identity from a worker name or the coordinator's current directory.
The adapter negotiates the executing relationship contract and also requires
the assembling owner's independent preservation admission for participating
writers. Current payload support alone cannot qualify an older resident.

The original direct-claim audit event stores one immutable relationship
request in `task_events.task_relationship`. Allocator placement still claims
capacity before a workspace exists. A newly governed reservation retains its
requirement and task generation; actual workspace attachment stores the
creation nonce and exact assignment event in that same reservation. A legacy
reservation cannot acquire this provenance retroactively.

Assignment commits its request before invoking Worktrees outside SQLite's
write transaction. The worktree reserves and binds its outbound `task` claim
using that request id and its expected creation nonce. Dispatch checks the
ground-owner response and rechecks the original assignment before recording
successful protection. A failed or lost response retains the original request,
worker and allocation; it cannot report a successfully admitted assignment.
Exact direct-claim retries and required workspace retries reconcile that same
request rather than allocating another claim.
Initial direct-claim acknowledgments and replays require a bound reconciliation
result and a fresh consistent read of the original claimed status, worker,
generation and assignment event. Cancellation during reconciliation reports a
task error, not a queued task or a previously captured claim as successful
acquisition.

Unconfirmed native capture cleanup is retained by the adapter's resident owner
before queue or HTTP error translation, including failures during construction.
Subsequent adapter calls first attempt bounded settlement of those exact
resources within the same call deadline. They cannot launch another capture
while cleanup remains unconfirmed. Cleanup without a complete supported retry
operation stays retained; an exception cause alone is not custody.
Payload-free capability and record probes supply empty input explicitly, so
they use the same owned Windows Job capture as relationship writes.

Recovery version 2 adds `assignment_event_id` and `worker_id` to the version-1
recovery object. Those facts also bind pending subjects, permitting exact
disposition after an interrupted reserve or bind. The callback checks the
original queue's retained request, not just a matching task id.
Submission and rejection keep the claim; only `COMPLETED` or confirmed
unassignment to unbound `QUEUED` permit its verified close. Abandonment,
dead-letter state, lease expiry and runner exit remain non-settling.
When cancellation finds no claim, reconciliation replays the original reserve
and closes it, retaining its tombstone. Absence alone cannot fence a delayed
in-flight reserve.

Pending protection blocks the queue and pool start gates. Retained SQLite
guards also reject execution or an unprotected new claim from older queue
code; reopening the database without a writer cannot erase the requirement.
The guards bind the current generation, worker and exact attached reservation,
not any historical bound claim. Capacity retirement does not erase the retained
assignment's task responsibility. Host aliases normalize only host whitespace
and case; worktree identity and assignment events remain exact.
The required workspace retry always revisits its owner even when recorded
path/project facts already match. Creator/allocation custody and actual
native-capacity retirement remain independent.

Successor-session adoption records a separately fenced continuation on the same
assignment event in the resume transaction. The original request, receipt and
worktree claim remain unchanged; execution must match the continuation's current
generation and native session.
An exhausted pool episode can likewise continue the original relationship into
a new reservation only after exact retirement of its accountable attempts, with
the same writer authority, creation lineage, workspace and creation nonce.
The new generation and reservation binding are recorded atomically with pool
placement, without closing or replacing the claim. An unrelated unsettled
relationship refuses placement before committing a task claim or consuming a
slot. Timeout itself never settles responsibility.

The durable execution guard applies to execution and first-result admission,
not metadata-only detachment after qualified native retirement. Existing
administrative recovery can therefore detach an already submitted or completed
agent without changing the original relationship, result or claim. A submitted
task retains protection until independent acceptance; later acceptance may
complete it without readmitting execution. Reopening execution still requires
current-generation protection. The replacement guard installs before its
stricter predecessors are removed.

For nonterminal owned work, native retirement alone does not unassign its
logical pool placement. Administrative recovery refuses before mutation while
that assignment remains bound, with an explicit pool-owner disposition blocker.
Its recovery revision includes the logical assignment. Detachment cannot
silently queue replacement work while stranding the original pool claim.

With a qualified writer enabled, atomic `create(..., claim_as=...)` refuses
before creating new work; create queued work and use the protected claim path
with its original workspace instead. A queue without support skips governed
tasks during unpinned selection so unrelated legacy work remains claimable;
explicit selection of a governed task still reports the incompatibility.

The running coordinator reports `task_relationships` version 1 through its
existing health response, including whether a qualified writer is assembled
and its explicit local/central authority. New clients negotiate this before
sending optional `assignment_workspace` claim data or an allocation nonce.
An unsupported selected owner refuses before new syntax/effects; no fallback
drops the requirement. Baseline callers and unrelated historical assignments
retain their previous behavior while ordinary activation is disabled.

`GET /tasks/{task_id}/relationship` observes the latest request, or an exact
`event_id`. Control-authorized
`POST /tasks/{task_id}/relationship/reconcile` accepts only that event id and
revisits the original ground owner. It never accepts a worker-supplied receipt.
These routes are part of the existing task owner, not another obligation ledger.
Source tests exercise both actual owners and the real Worktrees cleanup gate;
they are not proof of installed writer-cohort or native lifecycle qualification.
