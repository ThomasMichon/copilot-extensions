# Task relevance receiver

The queue has a **receiver-first** relevance operation:
`reconcile_task_relevance`, with control-authorized HTTP admission for effective
local command emitters and their registrar-selected evaluators. No automatic emitter, evaluator or
periodic source calls it yet. Source adapters, periodic selection
and actual session/workspace closeout remain tracked by
the [active effort](../../../efforts/active/task-relevance-reconciliation/README.md).
This is not an automatic task scrubber.

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
its effective source and evaluator scope in the queue transaction.

`relevance_snapshot` supplies the task generation and latest audit-event
revision. The caller checks its authoritative backing object and reports
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
without reassigning the task. Absent or changed ownership stays pending; this
receiver never guesses a historical owner or creates a replacement agent.

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

`closeout_pending` means **intent retained**, not resources finalized.
`pending_owner_closeout` means no wake was linked to an available original
owner/native. `relevance_receipt` separates immutable acceptance, current wake
status and steering consumption. None proves actual session/workspace cleanup.
Session conclusion, workspace disposition, recovery of cleared historical
owners and externally registered relevance callers remain pending. This slice
does not release reservations or delete files.

## Registered receiver and compatibility

The actual coordinator's `/health.task_relevance` advertises
`task_relevance/v1`, supported version `1`, registration scopes
`local-direct-command-emitter` and `local-effective-emitter-evaluator`,
and whether control authority is configured.
This is additive health metadata; older clients keep their existing behavior.
No CLI command or automatic source adapter is enabled by this capability.

The control-authorized HTTP surface is:

| Route | Meaning |
|---|---|
| `GET /task-relevance/contract` | Current serving receiver's support |
| `GET /task-relevance/tasks/{task_id}?registration_id=...` | Source-scoped task and registration revision snapshot |
| `POST /task-relevance/tasks/{task_id}` | Strict version-1 relevance report |
| `GET /task-relevance/tasks/{task_id}/receipts/{request_id}?registration_id=...` | Original acceptance and current delivery state |

The POST body contains `version`, `registration_id`, `registration_revision`,
`request_id`, `expected_generation`, `expected_revision`, `verdict`, `reason`
and `observation_ref`. The effective-scope form also supplies `effective_revision`
from its pre-observation snapshot. Unknown fields, boolean revisions and other versions are
rejected. Callers cannot choose the reporter, source, origin, machine or
environment. Ordinary data and task-scoped authority do not authorize this
surface.

The receiver resolves its own local machine/environment and holds the existing
operator-override lock through the queue decision. Inside the same SQLite write
as claim and registration mutation, it checks the observed registration revision,
active local registration, command-emitter task output and the emitter-authored
source/origin fields. Fresh admission also consults the configured registrar
reader and existing direct/declaration merge owner. A declaration-superseded
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
