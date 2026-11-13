# Task relevance receiver

The queue has a **receiver-first** relevance operation:
`reconcile_task_relevance`, with control-authorized HTTP admission for effective
local command emitters and their registrar-selected evaluators. Command emitters
can opt into [retained-receipt source checks](emitter-relevance.md); existing
emitters are unchanged without that configuration. Registered evaluators can opt
into [periodic reconsideration](periodic-relevance.md). Domain source adapters
and actual session/workspace closeout remain tracked by
the [active effort](../../../efforts/active/task-relevance-reconciliation/README.md).
There is no default automatic task scrubber.

## Existing owners and integration seams

| Responsibility | Existing owner |
|---|---|
| Creation receipts and emitter identity | `producers/emitter.py`: `_author_tasks`, retained emitter receipts |
| Registrar declaration and execution scope | `registrar_reconcile.py`, `queue_schedule_registry.py`, `registrations.py` |
| Domain judgment | `producers/evaluator.py` and registrar-selected evaluator implementations |
| Atomic claim and pre-body allocation | `queue_claim_queries.py`, `queue_spawn_reservations.py`, `queue_pool_placement.py` |
| Task/native/workspace history | `task_attachments`, original spawn reservations and task relationship audit events |
| Durable steering and existing wake outbox | `queue_steering.py`, `queue_storage.py`, `wake.py` |
| Original-owner delivery and native selection | `owner_wake_delivery.py`, `coordinator_execution_wakes.py` |
| Terminal resource disposition | Existing reservation conclusion and managed-workspace owners; [housekeeping recovery](cleanup-recovery.md) |

The new boundary is the **queue ownership decision**, not another scheduler,
transport, cleanup service or claim ledger.

## Acceptance

A trusted coordinator caller resolves a `RelevanceScope` containing the
repository lane, source and origin reference. All three must match the finite
task. This structural scope is not authentication or proof that a registered
emitter owns a legacy recipe task. An external receiver must derive
caller scope from the existing registration and attribution owners, not accept
an arbitrary caller assertion. The registered receiver below resolves
its effective source and evaluator scope in the queue transaction, using the
registrar declaration observation captured for that HTTP request before queue
write locks are acquired.

`relevance_snapshot` supplies the task generation and latest audit-event
revision. Registered snapshots also identify the effective emitter spec with
`emitter_spec_revision`, so a stale running emitter cannot adopt a changed
definition's fresh observation scope. The caller checks its authoritative backing object and reports
`needed`, `obsolete` or `unknown`, with a stable request identity, reason and
observation reference. The reference identifies the source observation; Dispatch
does not interpret an opaque string as closure evidence. An incomplete listing
or failed source read must yield `unknown`.

Acceptance takes the same SQLite write lock as claim and reservation. It checks
the task scope, generation and audit revision inside that lock. A claim winning
the race invalidates an older snapshot; refreshing the source check then finds
owned work. A reservation winning the race is visible as retained custody even
before a worktree or native exists.

Only a proposed/queued task with no claim/start indicators, assignment audit,
workspace reference or retained resource records is abandoned. Historical
attachments and preparation count, even if current owner fields have been
cleared. An independent hold is preserved.

All other obsolete non-terminal work receives one durable closeout intent and
one entry in the existing steering inbox. Its status, owner, native, generation
and resource records stay unchanged. A known original owner/native receives one
linked wake in the same transaction. Subsequent reports share that closeout;
retries return the original acceptance without repeating the audit or steer.
An accepted closeout blocks ordinary claim, spawn and execution admission,
including recovery of the original goal. It survives reset and requeue.
`needed` does not silently clear an already accepted closeout.

Every new report advances the audit revision, including `needed` and `unknown`.
Thus an older closure report cannot follow a newer accepted source observation
using its old task snapshot. Domain adapters must additionally fence their own
source revisions and reopened/new source episodes.

## Original-owner delivery

The existing wake drainer sends the closeout prompt to the retained owner and
native conversation. Busy/idle transport handling stays with Bridge. A suspended
task remains suspended: the prompt tells its agent to take closeout steering,
not run `start` or `resume`. Steering consumption selects the closeout instead
of older goal instructions and does not restore a suspended execution lease.
Submitted work can use its recorded `completed_by` worker and retained native
without reassigning the task. An owner absent at acceptance, or a conflicting
later owner, stays pending; this receiver never guesses a historical owner or
creates a replacement agent.

When closeout acceptance already captured the original worker and native,
same-generation yield, reset and requeue may clear the task's current assignment
without invalidating that closeout's linked wake or steering. Only unassigned
proposed/queued work can use this retained target; a different worker, native,
generation or terminal status cannot. Delivery and steering do not restore task
ownership, its execution lease or the original goal. A hosted conversation still
requires its retained reservation and native identity, with release, retirement,
cooling and conclusion guards unchanged. Recovery that advances the generation
remains fenced; preserving intent does not authorize waking a retired native.

Only the outbox row linked through the closeout's actual steer can pass its
original-goal admission fence. A later ordinary/event-note wake cannot supersede
that durable link through the task's latest-wake summary field.
Ordinary goal wakes, run waiters and forged or
changed prompt objects cannot inherit this permission. Independent execution
scope closure, qualified-runtime provenance, delivery token, generation, owner,
native, retirement, cooling, transport custody and cold-pool lane gates still
apply. An independent hold keeps the wake pending without spending attempts;
clearing it does not resume the goal. Cold-body lane acquisition uses the
existing publication owner, not a closeout capacity exemption.

The original outbox keeps send identity even without a qualified execution
scope. Pending/unknown transport outcomes are not replayed after restart.
Supported original-native receipt reconciliation can settle acceptance without
another send; a missing receipt is not proof of absence. A delivered wake may
still be awaiting the agent's work.

At first acceptance the existing closeout row also freezes `resource_binding`
in the same queue transaction. A captured binding identifies the queue, original
relationship audit event/request, managed workspace and, for a placed task,
the exact execution reservation and startup revision. The current pool pointer,
retained native identity and original relationship must agree; selection never
falls back to the newest reservation or a matching workspace in history.
An admitted same-workspace continuation keeps its original relationship event
while the closeout retains the current generation/native.

Missing or inconsistent ownership records produce an `unconfirmed` binding with
a reason; notification still proceeds without authorizing resource release.
Pre-existing closeouts have a null binding, not a reconstruction from newer
task state. Receipt and snapshot reads decode this additive observation field.
Requeue, restart and subsequent reports never replace the first capture.
This is retained selection evidence only: a captured binding is neither
owner acknowledgement nor readiness, terminal disposition, session retirement
or worktree finalization.

`closeout_pending` means **intent retained**, not resources finalized.
`pending_owner_closeout` means no wake was linked to an available original
owner/native. `relevance_receipt` separates immutable acceptance, current wake
status and steering consumption. None proves actual session/workspace cleanup.
Session conclusion, workspace disposition, recovery of previously uncaptured
owners and externally registered relevance callers remain pending. This slice
does not release reservations or delete files.

## Original-claim readiness

The queue provider `prepare_relevance_closeout` prepares settlement
of the captured original task relationship. The conditional HTTP provider below
exposes it; no CLI or automatic caller adopts it yet.
A caller identifies the accepted closeout request, worker, native
and generation; it cannot supply a readiness observation.

The provider reads the captured relationship and exact execution reservation,
then asks the selected local Worktrees runtime for its existing
`agent-worktrees.dispatch-worker-readiness` version-1 observation. That owner
independently checks the current workspace/head, original managed allocation,
landing, holds and foreign obligations while excepting only this exact task
claim. Unsupported, remote, unattributed or conflicting resources retain
custody. External observation runs outside the SQLite transaction; the queue
rechecks the original state under a write lock before retaining the first
readiness on the existing closeout row.

Only that retained, currently valid readiness allows the existing relationship
owner to reconcile the captured assignment claim. The claim callback verifies
the queue and original request/event, not just the task id. A lost close reply
stays visibly retained and can converge through the original request after
restart, without another readiness observation. Holds, changed generations,
changed natives/reservations, cooling or pending retirement still refuse.
The receipt is not a transferable readiness permission.

Preparation changes neither task status nor native, placement or reservation
release flags. In particular it does not yield/requeue or abandon the task:
those transitions could let the supervisor end the original conversation before
its agent finalizes. The agent's actual own-workspace finalization and native
conclusion remain separate integration gates. This provider never deletes a
checkout.

Existing detail and `claim-status` syntax carry the additive
`relevance_closeout_ready` evidence described in
[task obligations](task-obligations.md#snapshot-and-outcomes). Older callbacks
do not recognize that release outcome and retain the claim rather than treating
it as ordinary completion. A prior queue without retained readiness cannot
produce this proof; no fallback to abandonment exists. Baseline operations remain
unchanged, with no old surface retirement. Upgrade the receiver and callback
before enabling any caller; source qualification is not running adoption.

## Finalized-workspace acknowledgement

The internal `finish_relevance_closeout` receiver requires the retained
readiness and a closed original assignment claim. It independently reads the
selected local Worktrees owner's existing `observe-finalized` result, using
the original allocation key rather than a later execution reservation. The
captured workspace creation nonce must still match. No worker-supplied receipt
can substitute for this observation.

The existing task lifecycle owns abandonment. A queue-local transaction guard
revalidates the captured task, closeout, relationship, placement and reservation
after observation and stores `acknowledgement` on the existing closeout row in
that same terminal transaction. A hold, changed incarnation or failed task
write cannot leave an acknowledgement committed separately. IPC never runs
under the SQLite write lock.

Exact retries after restart or a lost response return the committed terminal
outcome without reobserving, repeating the transition audit or reemitting its
terminal event. Conflicting arguments or a changed task episode refuse.
The receipt retains the task's current attachment separately from the original
closeout native: a same-generation yield without resource release may clear
the task's native field without losing the original closeout conversation.
Transition and replay both validate that retained task attachment.
The nullable additive field remains absent for earlier closeouts; no historical
acknowledgement is reconstructed. Acceptance receipts remain original intent,
while the existing current-closeout view exposes this later observation.

This receiver does not finalize a workspace, retire a native, release a
reservation or delete a checkout. Those effects remain with the actual worker
and existing conclusion owners. Its HTTP provider does not enable an automatic caller.
Unsupported Worktrees observation retains custody, not an abandon fallback;
baseline operations and prior readers remain unchanged. Caller migration,
session conclusion and running qualification remain open.

## Original-owner HTTP provider

The serving coordinator advertises additive
`/health.task_relevance.owner_closeout` metadata:
`protocol: task_relevance_closeout/v1`, `supported_versions: [1]`, and
`path: /tasks/{task_id}/relevance-closeout`. This is receiver support, not
proof that any worker, Worktrees runtime or native conclusion owner adopted it.

The existing coordinator authentication boundary protects three operations:

| Method and suffix | Result |
|---|---|
| `GET` with no suffix | Current validated captured closeout, including readiness and acknowledgement |
| `POST /prepare` | Independent readiness and exact original-claim settlement; task/native stay retained |
| `POST /finish` | Independent finalized observation and existing terminal transition |

Both POST bodies strictly require `version: 1`, `request_id`, `worker_id`,
`native_id` and `expected_generation`. They accept no caller-supplied proof.
Responses use `{protocol, version: 1, result}`; prepare returns the queue's
readiness/relationship result and finish returns its task view. Both writes
participate in the existing drain gate; the shared terminal hook releases a
handoff claim and emits the event only for a new transition, not an exact replay.
The existing active-route middleware still applies.

A task-scoped token must match the accepted closeout's original task, worker,
native and generation and remain unexpired under the current coordinator fence.
The provider also validates current resource custody, or the exact committed
acknowledgement for a terminal retry. An accepted owner may therefore inspect
and finish its same-generation unassigned or submitted closeout without gaining
generic task authority. Other task reads, grant minting and lifecycle mutations
retain their existing held/current-owner rules. A scoped header never falls
back to a supplied shared bearer when it is invalid. The existing trusted
data/control bearer policy is unchanged; those callers must still supply the
captured tuple and satisfy the queue's conditional checks.

Read the closeout before preparation: a committed acknowledgement means finish
can be retried without another observation or terminal event; do not prepare
or finalize again. Otherwise prepare and confirm the original claim is closed,
then the original worker must finalize its own workspace before finish.
Preparation alone is not finalization. An expired token after unassignment or
terminal acknowledgement needs recovery through the existing trusted owner;
this provider does not mint a new grant or revive old authority.

This is the provider-expansion phase. Existing callers, source-control relevance
routes and generic administrative abandon retain their semantics. A future
caller must discover support from the running coordinator before effects.
Trusted callers can use the health descriptor. A task-only caller can read its
own closeout and validate the response's protocol/version; this read does not
grant global health access and a legacy receiver returns unsupported/not found.
Missing, malformed or unsupported metadata, including provider
rollback, means closeout is unsupported before effects; no effectful probe or
fallback to generic abandonment is equivalent. No old surface is retired.
Worker finalization integration, native conclusion and running qualification
remain tracked by the [active effort](../../../efforts/active/task-relevance-reconciliation/README.md).

## Registered receiver and compatibility

The actual coordinator's `/health.task_relevance` advertises
`task_relevance/v1`, supported version `1`, registration scopes
`local-direct-command-emitter` and `local-effective-emitter-evaluator`,
`bounded_batches: true`, `periodic_evaluation: true`, and whether control authority is configured.
This is additive health metadata; older clients keep their existing behavior.
Receiver capability alone enables no source adapter or periodic registration.

The control-authorized HTTP surface is:

| Route | Meaning |
|---|---|
| `GET /task-relevance/contract` | Current serving receiver's support |
| `GET /task-relevance/tasks/{task_id}?registration_id=...` | Source-scoped task and registration revision snapshot |
| `POST /task-relevance/tasks/{task_id}` | Strict version-1 relevance report |
| `GET /task-relevance/snapshots?registration_id=...&task_ids=...` | Up to 100 source-scoped snapshots |
| `POST /task-relevance/reports` | Up to 100 individually receipted reports |
| `GET /task-relevance/tasks/{task_id}/receipts/{request_id}?registration_id=...` | Original acceptance and current delivery state |

The POST body contains `version`, `registration_id`, `registration_revision`,
`request_id`, `expected_generation`, `expected_revision`, `verdict`, `reason`
and `observation_ref`. The effective-scope form also supplies `effective_revision`
from its pre-observation snapshot. Unknown fields, boolean revisions and other versions are
rejected. Callers cannot choose the reporter, source, origin, machine or
environment. Ordinary data and task-scoped authority do not authorize this
surface.

Batch reports wrap a `reports` list; each item adds `task_id` to the individual
report body. Batch results contain `items` keyed by task id and, for reports,
request id. Acceptance and confirmed rejection are per task, not atomic across
the whole list. Partial or malformed responses require original-request receipt
recovery; absence of an item is not proof of no effects. Batches require the
additional running-peer capability and never fall back to ordinary abandonment.

The receiver resolves its own local machine/environment and holds the existing
operator-override lock through the queue decision. Inside the same SQLite write
as claim and registration mutation, it checks the observed registration revision,
active local registration, command-emitter task output and the emitter-authored
source/origin fields. Fresh admission also consults the configured registrar
reader and existing direct/declaration merge owner. Each individual or batch
HTTP request discovers declarations once before queue write locks, without
cross-request caching. Direct registrations and their revision are reread in
each task transaction; overrides are locked through each decision.
A declaration-superseded
direct row is refused even before its declaration is mirrored into SQLite;
missing or incomplete discovery is not eligibility evidence. Declared sources
are read from that current observation, never from their eventually mirrored
SQLite rows. An equivalent local direct row is superseded, not a second producer.
It honors concrete and logical local overrides. Another
registration with the same emitter provenance is ambiguous and refused.
That check includes conflicting declarations in the fresh merged set before
their registration rows are mirrored, not only already-published rows.
An unchanged registration upsert does not invalidate observations; changed,
removed/recreated or paused direct registrations do. The additional effective
revision correlates the merged registration definitions, excluding timestamp churn;
declared changes between snapshot and report invalidate the source observation
even before their mirror changes. It is not a lock on arbitrary document writers
or evidence about the backing object's current state.
A draining receiver refuses new
decisions. A fresh abandonment uses the existing result-free terminal event
shape and lifecycle telemetry; an identical report replay emits neither twice.

This is structural source attribution within existing trusted control authority,
not proof that an opaque legacy task was created by a particular process.
An evaluator must be the existing verification selector's unique repo/ref match
(repo-specific before all-repos), in the executing machine/environment. Its task
must also have one active, non-disabled task-authoring emitter whose source/origin
and companion `evaluator_ref` match. An evaluator reference alone does not grant
authority over manual or other-producer tasks. Both selected registrations'
concrete/logical holds apply. Remote and unqualified registrations remain
unsupported; they must not be relabelled as local direct sources to pass admission.
Replacement declarations inherit their superseded direct registrations' override
tokens, for both the selected evaluator and its companion emitter. Fresh
foreign-machine emitter declarations remain visible for provenance collision
detection, without becoming eligible local callers or trusting stale mirrors.
Predecessor hold correlation also includes paused local rows without admitting
them as callers or counting an equivalent replaced emitter as a second source.
Evaluator repo/ref selection precedes hold exclusion: an
all-repos reporter cannot take over a held repo-specific evaluator's work.
Selection retains the verification owner's applicable machine-unpinned evaluator
definitions too. Such a definition can block an all-repos fallback, but does not
become an eligible local relevance reporter merely by participating in selection.
Legacy/manual tasks without matching emitter provenance remain outside scope.

Opt-in client methods check support on their bound running peer before sending
the new form. Effective-scope snapshots use `effective_scope=True` (also inferred
for declared ids); reports carrying `effective_revision` require the new scope.
Missing, invalid or unsupported metadata raises
`DispatchUpgradeRequired` before the report; there is no fallback to ordinary
abandonment. Each effect is sent once. A lost response is recovered from the
original receipt rather than minting a new request. Identical accepted requests
and their receipt reads remain recoverable after registration removal or a new
hold; they do not authorize another effect.
Request identities containing slashes remain recoverable through the receipt
route's path capture; URL quoting alone is not assumed to preserve a segment.

Rollout remains provider-first: release this receiver before migrating source
callers, then qualify periodic selection and actual owner closeout. Previous
direct-emitter bodies and accepted request encodings remain valid on this receiver.
New effective-scope callers refuse a prior direct-only receiver before reporting,
while ordinary reads and old direct-emitter callers retain their baseline.
A rollback to a prior wire receiver refuses the new operation while retaining old operations.
An older database consumer must not resume accepted closeout goals without
preserving their admission fence; rolling back the database or discarding
closeout history is not a supported recovery. No old surface is retired here.
