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

## Original allocation cohort

New spawn reservations retain an optional `allocation_cohort` snapshot using
`dispatch-assignment-cohort` version `1`, with the public emitter receipt and
exact-reservation pool assignment described below. Capture follows the actual
pool claim inside the same reservation transaction and precedes workspace or
native preparation. Failure to capture rolls back that claim and reservation.
Existing reservation create/read/list responses expose the additive field;
no separate endpoint, registry or cleanup authority is introduced.

`allocation_reservation_key` identifies the original allocation request.
Retries and later deduplicated task episodes copy that original snapshot,
including its original task and pool, rather than recapturing current source or
placement. A separately admitted replacement allocation revision captures its
own new request. An existing targeted/reused workspace is not stamped by this
queue evidence: the snapshot describes the request, not proof of its creation.
The later assignment audit remains a distinct record of that assignment.

Retained non-null snapshots and their allocation keys cannot be rewritten.
Historical reservations, missing original evidence and reservations created by
a rolled-back provider remain null; migration and later retries do not backfill
them from current state. Null is unknown, not an empty or disposable cohort.
Older readers may ignore the additive field. No request syntax changes, and
the field's schema/version describes support independently of package versions.
New Worktrees creation conditionally forwards this retained snapshot as
`--dispatch-cohort`. Dispatch observes `dispatch_cohort` schema
`dispatch-assignment-cohort`, version `1`, through the selected direct
Worktrees runtime's existing `worktree-allocation capabilities --json` response
(allocation schema/version `1`, origin role). It uses the same resolved
interpreter for creation, not the marketplace payload or package version.
Direct interpreter forms use Python isolated mode for both operations, so a
checkout-local module, `PYTHONPATH` or user site cannot shadow the selected
installed package. Existing mutable-shim baselines do not authorize discovery.
The read uses the existing owned process-tree capture with a five-second
execution timeout, normal tree cleanup and a 65,536-character combined output
limit. Timeout/overflow becomes unknown support only after that owner's cleanup.
Results are cached for up to 32 selected runtime prefixes per process;
changing the selected runtime triggers fresh discovery.
An unavailable observation remains unknown for that process/runtime selection.

Prior providers without the declaration, invalid/unsupported declarations,
mutable-shim launchers and provider rollback use the existing five-field
Dispatch attempt without a cohort flag, with an explicit unattributed warning.
Legacy reservations without a snapshot need no discovery. This optional audit
carrier grants no new lifecycle authority and does not gate baseline creation.
Before the first creation, the existing allocation transaction seals
`allocation_cohort_transport` as `baseline` or `cohort-v1`. New reservations can
start unselected; historical reservations migrate to baseline because an old
creation may already have taken effect. Retries and later task episodes inherit
the original selection. A lost binding response holds creation until the
coordinator confirms the same choice. A baseline attempt never gains a cohort
on upgrade; a cohort-bearing attempt requires a capable provider after rollback,
rather than dropping immutable facts. Migration does not guess legacy evidence.
An older caller omitting the choice cannot re-bind an unrecorded cohort-v1
allocation; it must upgrade and confirm the transport. Metadata-only binding
after a workspace has been recorded remains compatible.
Before selecting cohort-v1, Dispatch checks the complete v1 reference fields,
512-character string and counter limits, and 16-KiB encoded JSON bound. An
unrepresentable new snapshot warns and selects baseline; an already-sealed
cohort-v1 request refuses rather than changing facts.
An effectful creation failure is never replayed using a second syntax.
Creation-key recovery remains with the original owner. Prior callers still work
with upgraded receivers on the baseline. The old form has no scheduled retirement.

Only the new-allocation path sends the snapshot, using its original
`allocation_reservation_key` task/attempt, never the current task's labels or
assignment audit. Carried/targeted worktrees are resolved without discovery or
restamping. Bridge session-state propagation remains separate and open.
Ordinary initial-start validation admits the optional cohort only when it
matches the retained allocation evidence, and requires it for a cohort-v1
creation. All original sessionless task, reservation, attempt, driver,
supervisor, creating-machine and ownership checks still apply before prompting.

## Original session-origin reservation

The reservation owner independently retains `session_origin` in the Bridge
`DispatchSessionOrigin` version-1 shape: original `task_id`, `reservation_key`
and `cohort` (`dispatch-assignment-cohort`, version `1`). It captures the actual
producer receipt and matching pool claim inside the original reservation
transaction, before any body preparation. Failure rolls back the claim.
This is a candidate birth input, not evidence that a native was created:
Bridge's first-save session record alone identifies the actual session origin.
Resuming an older session never backfills its origin.

Session and workspace keys have different lifetimes. A new acquisition delivery
belongs to its current reservation, even when its workspace retains an earlier
allocation or task episode. Ordinary keyed creation instead retains the first
ordinary reservation for that `creation_key`; retrying a lost creation response
cannot substitute a later task's origin. `session_origin_reservation_key`
identifies that original reservation. Driver changes do not borrow an acquisition
request as the original ordinary creation. A new allocation key gets its own
snapshot; an existing targeted/reused workspace is never restamped.

New reservations start with unselected `session_origin_transport`. The additive
`POST /spawn-reservations/{key}/session-origin` accepts exactly
`{"startup_revision": 1, "transport": "baseline"}` or the `origin-v1` transport.
It binds the choice on the original and current reservation under one write lock,
requiring the current reserving startup and a representable snapshot for
`origin-v1`. Concurrent conflicting choices cannot both commit. Evidence and
original-reservation references are immutable; a selected baseline cannot gain
origin metadata after upgrade, and an origin-v1 retry cannot silently downgrade.
Lost responses are reconciled by reading/repeating that same binding, not by
sending another native creation form.

Existing allocation and workspace-preparation operations also seal baseline
when the caller omitted origin selection, before it can send a native request.
An explicit origin choice must precede preparation; later preparation preserves
that choice. All ordinary attempts for a creation key participate in transport
reconciliation, not just its first reservation. An earlier unselected attempt,
or recorded preparation/native history without selection, may already have sent
a baseline request and cannot later gain origin-v1. An intervening rolled-back
provider's baseline is retained; conflicting sealed history refuses visibly
rather than guessing which request won.

The running coordinator advertises `session_origin_binding_versions: [1]`
through its existing pool-placement contract. The explicit client checks that
selected peer before sending the new request. An older caller still uses its
unchanged operations; an older, invalid or rolled-back provider receives no new
binding request. Historical rows migrate to baseline with null evidence and
remain unattributed on restart/retry. The old form has no scheduled retirement.
This expansion does not yet migrate automatic session callers, qualify Bridge
adoption, change startup admission or confer cleanup authority.

Capture adds indexed reservation/creation-key reads plus the existing
producer and exact-assignment point reads, once per newly committed reservation.
Ordinary transport reconciliation examines that creation key's indexed attempt
lineage and returns grouped choices; it does not scan unrelated reservations.
Reads do not enrich or recapture it. No process, network request, registrar scan,
background flow or additional ownership registry is introduced. Binding uses one
existing coordinator transaction and updates at most two reservation rows.

## Original assignment cohort

New protected assignment requests retain an additive `cohort` object with
schema `dispatch-assignment-cohort`, version `1`, in the same original audit
row. The existing task-detail `task_obligation.relationship`, exact-event
query and relationship read expose it; no additional store or endpoint is used.
The enclosing request supplies the task, generation, worker, workspace,
reservation and original queue identity.

`emitter` is the matched managed creation receipt: `repo`, `source`,
`generation`, `request_id`, `producer_id` and `task_id`. It is null without a
matching coordinator-owned receipt and coherent task producer fence.
Caller-supplied source labels alone do not identify an emitter. The receipt
does not contain a producer capability and does not establish that the producer
is still eligible or that a present-day registration owns the task.

`pool` is the original exact-reservation pool assignment, containing `task_id`,
`registration_id`, `machine`, `env`, `reservation_key`, `task_generation` and
`catalog_revision`. A display name, another reservation or the current catalog
cannot substitute for that assignment. Null means that no such assignment was
captured, not positive evidence that no pool ever existed.

Capture runs inside the existing assignment transaction. Retries, restarts,
producer handoff and later catalog/assignment changes read the retained facts,
not recomputed labels. Historical requests without `cohort` remain unattributed;
reads never backfill them. This is informational provenance, not permission to
retire an agent, abandon a task or delete its workspace.

The optional response declares its own schema/version from the executing
coordinator. Existing request and callback formats are unchanged, and older
readers may ignore the addition. A provider rollback still reads previously
retained cohort facts without creating them for new assignments. Consumers
requiring this evidence must retain an explicit unattributed state when it is
absent; they must not infer support from package versions.
Worktrees' original `dispatch_attempt` creation stamp uses the earlier
[allocation snapshot](#original-allocation-cohort), not this later audit.
Bridge session-state propagation remains separate. The selected in-process
request-admission callback receives the assignment cohort with the rest of the
original request; the existing Worktrees reserve/bind wire payload remains unchanged.

Each new protected assignment adds two indexed point reads at most: its task
pool assignment and its managed producer creation receipt. No new process,
network request, registrar scan or per-read enrichment is introduced.

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

The opt-in `WorktreeRelationshipWriter.selected(...)` factory retains the
actual installed Worktrees command before negotiating its executing
capabilities. Same-cell calls reuse the existing receipt/governance boundary;
legacy calls recheck legacy ownership before entering the slot-local retention
bootstrap. Neither form falls back to an ambient executable or adopts a newer
build after selection. Missing/recreated builds refuse through the original
pre-import identity check. Ordinary unpinned callers are unchanged.

That factory additionally negotiates `resident_inspect: 1` and performs an
authenticated `inspect --resident-only` with the original workspace nonce before
each relationship operation. It validates the returned executing serializer,
installation, build and resident generation; it never substitutes the direct
CLI writer. A different compatible resident build can qualify independently,
and each later operation reobserves its current selection rather than caching
an earlier readiness result. This costs one additional bounded Worktrees child
and resident read per relationship operation, not per generic record save.
The factory still requires both independent admission callbacks. Its CLI and
resident observations do not qualify Bridge's future keyed creation, its
original continuation journal or any other participating producer, and cannot
close the writer cohort or enable ordinary construction.

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

The queue independently rereads its original task/event snapshot before each
external relationship call, including retirement inspection and absence
tombstoning. It compares the durable queue identity, selected local/central
authority, audited worker and complete original request with type-preserving
canonical encoding. A changed queue, audit or substituted caller snapshot
refuses before the next owner call; earlier effects remain accountable to
that same retained request. These are optimistic reads, not a transaction
held across external I/O, so the existing post-I/O CAS remains mandatory.
They add one indexed, task-scoped snapshot per relationship call, with no
process or wire probe and no preflight on unrelated record saves.
For an allocator-prepared request, admission also rereads its exact original
reservation by key. Task, workspace/project/path, execution machine, nonce,
required relationship, event and original generation must still agree.
Supported machine-case aliases remain valid. Missing or changed preparation
refuses the next owner call without adopting another reservation or dropping
earlier effects. This additional indexed read retains the original preparation
across startup continuation and retirement; it does not require the historical
reservation to remain active. Neither check qualifies the resident, Bridge or
other writers.

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
Ordinary Bridge startup carries its existing observation timeout into governed
workspace preparation, including a retained attachment retry. The client applies
that budget only to this request's HTTP read phase, without shortening a longer
or disabled read timeout or changing connect/write/pool and unrelated requests.
Legacy ungoverned preparation retains its original transport budget. A read
timeout is still an unresolved original operation, not permission for an
automatic mutation retry.
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
The supervisor's terminal reconcile settles a `COMPLETED` task's exact retained
relationship through the existing reconcile route before releasing its
reservation. A failure or a non-closed answer retains both the reservation and
the claim and retries next cycle, so dispatch-attempt workspace conclusion never
runs against a still-live task claim. Submitted tasks are not settled here.
Bodies whose reservation is already releasing settle the same way before
attempt conclusion. A retired attempt whose workspace cleanup was held only for
outstanding obligations is re-armed to pending, with a fresh attempt budget,
once its task is `COMPLETED` and the relationship closes; other holds stay held.
The re-arm is an atomic compare-and-swap on the exact held detail that refuses an
unexpired cleanup lease and clears a spent one. The coordinator derives the
pending detail from the stored hold and, in the same transaction, refuses any
other hold reason or a reservation whose exact retained relationship is not
closed or absent. Each cycle revisits a bounded number of candidates, counting
failures, and resumes after the last one visited. The supervisor first requires
the coordinator's `/health` to advertise `claim_held_cleanup_rearm`; against an
older coordinator it settles nothing and the cleanup stays held, recoverable
through `recover-held-cleanup`. Roll out the coordinator before supervisors.
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
Prepared audit requests cannot be rewritten or deleted. Ordinary completion
uses the guarded path below; persisted SQLite guards prevent older completion
code from skipping preparation or authoritative advancement. Unprepared
baseline tasks outside protected assignment scope retain their previous
completion behavior.

### Ordinary completion of protected assignments

The existing `complete` spelling, HTTP completion route and registered MCP
completion operation converge on the shared queue owner. A retained workspace
relationship, a reservation's protected-relationship requirement or a retained
preparation positively requires guarded submission. The worker cannot opt out
by omitting workspace or readiness fields. This is not a classification of all
legacy workers as worktree-free, and it does not activate ordinary relationship
writer construction.

Under its existing completion lock, the queue resolves the original workspace,
generation and native binding. It then releases the transaction before invoking
the existing preparation and advancement owners. A first ordinary request
derives its stable ID from that assignment and prior preparation event.
An unconsumed proposal retries its original request; only an exact authoritative
rejection permits one subsequent proposal. Result, reference and binding changes
cannot replace a retained request. An explicit expected-status precondition
remains part of that proposal and is rechecked before authoritative advancement.

The running coordinator advertises `worker_submission_readiness_versions: [1]`
and `worker_submission_scope: "retained-workspace-assignments"` in its existing
pool-placement descriptor. No new caller syntax or prerequisite RPC is needed.
Before first submission, readiness still negotiates the actual selected
Worktrees provider as described below. An unsupported provider, manual protected
assignment without qualified managed allocation, unknown original workspace or
missing independent-review policy refuses visibly; none is success-shaped
`not_applicable` evidence. Valid preparation and advancement failures retain the
proposal, its actionable blocker, task owner and accountable assignment.
Conflicting preparation attempts do not rewrite even the original blocker.

A successful call records `SUBMITTED`, never self-attested acceptance or
permanent worktree finalization. Exact committed retries, including a lost reply
or concurrent submission, return the existing receipt without another owner read,
transition or evaluator request. Historical protected submissions without a
retained proposal cannot be enriched or retroactively qualified through this
path. Existing confirmation of already submitted work is unchanged.

The current `DispatchClient` gives completion a read budget of at least
60 seconds for the three bounded 15-second owner reads and response overhead.
It preserves a longer or disabled selected read timeout, the selected connection,
write and pool budgets, and other operations' timeouts. This adds no read-timeout
retry. Older clients may still have a shorter request budget; a lost response is
an unknown outcome to reconcile against the original retained task/proposal,
not evidence that submission failed or permission for cleanup.

The rollback guard also refuses first-result admission **before** the first
ordinary completion call has retained a proposal. Another persisted guard binds
advancement to that exact proposal. Upgraded and older callers therefore cannot
use legacy completion to bypass this requirement on the same queue. Provider
rollback may refuse unsupported governed work; it never creates weaker admission.
Legacy tasks outside this positively retained scope preserve their existing
completion and late-result behavior. General manual/no-managed-worktree
applicability and installed/native qualification remain open.

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
or rejected/reassigned binding cannot reuse it. Legacy direct writes and late-result
enrichment refuse prepared tasks, including after restart; SQL also preserves
the submitted proposal against older backfill code. Internal rejected-proposal
resubmission uses the lineage below. Post-stop same-native recovery,
manual/absent-workspace applicability and full native qualification remain
open. Decline settles a retained task claim only after its committed requeue;
see [retained task-claim ordering](pool-worker-decline.md#retained-task-claim-ordering). The ordinary completion consumer above does not
enable assignment writer construction or qualify an installed cohort.

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
