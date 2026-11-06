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

The explicit adapter requires the selected CLI's advertised executing-identity
guard and an attributable installed build, not a package version or unknown
source generation. Its independent `admit_workspace` callback receives the
original workspace and a copy of that selected installation/build identity
before record checks or relationship effects. The assembling owner must match
both to its independently qualified participants; the callback is not optional.
The adapter supplies that identity and the original nonce on every relationship
request, refuses conflicting caller fields and requires the same checked
identity in the result. A runtime wrapper selecting a different build therefore
cannot silently inherit the earlier capability observation. Missing support,
provider rollback, changed selection or an unconfirmed response retains the
original relationship rather than downgrading or replaying a weaker request.
The existing capability/record/effect call shape gains no additional probe.
See the canonical [Worktrees relationship contract](../../agent-worktrees/docs/resource-relationship.md#workspace-incarnation-and-executing-writer-support).
These conditional adapter controls still do not assemble the real writer
cohort or activate ordinary coordinator construction.

Queue reconciliation uses the selected adapter's `bind_request` path when
available, passing an isolated copy of the original audited request before
reserve or retirement inspection. This path requires the separately supplied
`admit_request(workspace, writer_identity, request)` callback. It receives fresh
copies before each relationship operation; callback mutation cannot change the
retained request or the next operation. A request-aware adapter refuses unbound
calls, and a bound path without that admission owner cannot silently downgrade.
Legacy explicit three-argument writer callables remain compatible.
Each bound callable keeps its own request and confirmed token, checks coherent
workspace/assignment/authority/recovery facts, and refuses conflicting operation
payloads before effects. Bind and close accept only the token confirmed by the
same callable's original reserve or inspect. The original ground-owner receipt
and post-I/O queue checks remain required. This is an in-process context seam,
not proof that the assembling owner's original-queue/cohort observation has
qualified the actual installed writers. It adds no Worktrees wire field or probe.

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

For nonterminal owned or queued work, native retirement alone does not unassign its
logical pool placement. Administrative recovery refuses before mutation while
that assignment remains bound, with an explicit pool-owner disposition blocker.
Its recovery revision includes the logical assignment. Detachment cannot
silently queue replacement work while stranding the original pool claim.
This includes exhausted startup episodes waiting to retry in their retained
workspace. Reset also refuses any unsettled task relationship, including a
direct claim with no native resources: confirm completion or exact unassignment
to unbound queued work, then reconcile that original claim before resetting.
Recovery revisions include the retained relationships, so a changed claim
outcome requires a fresh diagnostic snapshot. Metadata-only detachment of
submitted/completed work remains independent of these reset prerequisites.

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

## Internal submission preparation

The queue can retain an exact proposed result on its existing task audit event
without changing task state, owner, lease, native binding or assignment.
`prepare_submission` is an internal source prerequisite, not an advertised
HTTP, CLI or MCP capability. It requires the current protected workspace
assignment, exact generation and native owner. Unqualified legacy assignments,
independent holds and closed execution admission refuse preparation.

The retained request binds the original queue, assignment event/request,
workspace incarnation, current continuation, worker/native, result and reference.
Identical retries read the same proposal, including after a queue restart or
ordinary progress/suspension. Different output or binding cannot overwrite it.
Structured results use the queue's existing validation and byte bound; request
ids, references and blocker text also have bounded inputs.

An internal blocker write rechecks the current binding under the queue lock
and retains the original result. It cannot certify readiness or submit work.
The existing completion owner refuses tasks with retained preparations, and a
persisted SQLite guard prevents older completion code from bypassing that
refusal. Prepared audit requests cannot be rewritten or deleted. Unprepared
baseline tasks retain their previous completion behavior.

### Internal independent readiness observation

`observe_submission_readiness` reads the retained proposal and its original
bound task relationship from the queue, not from caller-supplied receipts.
It requires the current managed pool/native binding and the allocator's
original created-workspace provenance. Manual assignments without that
allocation remain explicitly unsupported by this source-only path.

The local coordinator resolves the selected Worktrees command prefix once
per observation and uses its scrubbed environment for discovery, workspace
metadata and readiness. It negotiates the separate
[readiness version-1 contract](../../agent-worktrees/docs/dispatch-worker-closeout.md#selected-runtime-readiness-transport)
before sending readiness syntax. Missing, prior or rolled-back support refuses
without a finalized-closeout fallback. Remote allocations refuse before local
runtime resolution or launch.

The actual workspace record supplies the native head revision. Readiness is
pinned to that revision, original workspace incarnation, original allocation
and exact retained claim reference. Result identity and nonfinalizing,
nonreleasing effects must agree; worker-provided success JSON is not input.
External observation runs outside the SQLite transaction. A fresh locked
queue read rechecks the proposal, task/native incarnation, relationship,
allocation, holds and execution admission before returning evidence.
Lease renewal, liveness/activity bookkeeping and progress updates do not change
that binding. Other task changes, including result, goal, review policy,
ownership or holds, still invalidate it. Normal Worktrees metadata may express
the managed-checkout default as null; explicit false or invalid values refuse,
and the readiness owner independently enforces actual managed custody.

This method neither persists a readiness permission nor advances the task.
Every invocation reacquires evidence; later advancement still needs its own
fresh observation and atomic checks. Both the existing completion refusal and
its rollback-resistant SQLite enforcement remain in effect. The task claim,
workspace, result proposal and native capacity remain retained.

### Internal guarded submission advancement

`advance_prepared_submission(task_id, request_id=...)` is an internal queue
operation, not an advertised HTTP, CLI or MCP capability. It accepts no copied
readiness result or worker-supplied success flag. Before its first transition it
reads replay and preparation in one SQLite snapshot, requires an existing
independent review policy, obtains a new owner observation
through the same selected-runtime path, then compares the original semantic
queue state under `BEGIN IMMEDIATE`. Heartbeats and progress remain independent;
proposal, relationship, native/allocation, holds, admission and review-policy
changes refuse. A prior point-in-time observation is not permission for this call.

The original preparation audit event carries a transaction-local admission
marker. The task update consumes it through a persisted SQLite trigger before
commit, retaining an immutable advancement receipt on that same event. A
failure or process exit rolls back admission, task/result/audit and evaluator
writes together; no committed ready permission is left for an older writer.
The replacement guard is installed before removing the old stricter trigger.
An older migrator may reinstate that stricter refusal; reopening through the
current queue restores the guarded path without an unprotected interval.

Submission shares the existing result/audit, evaluator scheduling and suspended
waiter-retirement owner. It only reaches `SUBMITTED`: manual review remains
pending when no evaluator is selected, and the task's original native/generation,
completing worker, protected task claim and accountable allocation remain
retained. The worktree is not finalized or cleaned up. Independent confirmation
still owns `COMPLETED`.

An exact committed retry reads that receipt and current matching submission
without another observation, transition or evaluator request. An identical
concurrent commit may also satisfy a caller whose later owner read fails; that
only returns the exact committed receipt, never authorizes another transition.
A changed request
or rejected/reassigned binding cannot reuse it. Legacy completion and late-result
enrichment refuse prepared tasks, including after restart; SQL also preserves
the submitted proposal against older backfill code. Internal rejected-proposal
resubmission uses the lineage below. Post-stop same-native recovery,
non-circular decline, negotiated
ordinary callers, manual/absent-workspace applicability and full native
qualification remain open. Ordinary callers and assignment writers stay disabled.

The immutable preparation retains the original native/generation even after
metadata-only agent detachment. Existing recovery may advance current attachment
metadata only after its qualified native-retirement checks; accepted output and
the original receipt/relationship remain unchanged. Submitted work stays
provisional and keeps its claim until independent confirmation. Detachment is
not execution readmission, and a detached incarnation cannot replay the old
advancement as a new submission.

### Internal rejected-proposal resubmission

After an authoritative rejection of a consumed proposal, `prepare_submission`
may append one new immutable preparation with a new request ID. Its task,
queue, worker, generation, native, workspace and original assignment binding
must remain unchanged. The existing rejection owner's audit transition and
matching evaluator steer must name the exact prior result, reference,
submitting owner and completion timestamp. A caller-supplied feedback object,
sender label or copied steer without that transition is not authority.

The new request retains its previous preparation/event references and exact
rejection reason/feedback. Earlier requests and consumed receipts are never
rewritten or rearmed. Concurrent preparation consumes one rejection once;
an identical current request reconciles, while a different unsubmitted proposal
or superseded request ID refuses. Taking the feedback and suspending an
intermediate investigation does not erase its lineage.

Readiness rechecks that original lineage before and after the independent
owner read. Another changed candidate still needs its own fresh readiness and
guarded advancement; no rejection carries a reusable ready permit. All prior
answers, the protected task relationship and original native return binding
remain retained. Older preparers cannot replace these requests, and the
existing persisted completion/receipt guards remain unchanged.

This is internal queue source support, not HTTP/CLI/MCP negotiation, ordinary
writer activation, stopped-native recovery or full reviewer-loop acceptance.
