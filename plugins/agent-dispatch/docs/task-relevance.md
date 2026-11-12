# Task relevance receiver

The queue has an **internal, receiver-first** relevance operation:
`reconcile_task_relevance`. It is not exposed through HTTP or CLI and no emitter,
evaluator or periodic source calls it yet. Source adapters, registered-source
admission and actual session/workspace closeout remain tracked by
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
emitter owns a legacy recipe task. The future external receiver must derive
caller scope from the existing registration and attribution owners, not accept
an arbitrary caller assertion.

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

No new wire contract or support capability is advertised. Existing clients and
ordinary tasks retain their behavior. A prior receiver must not receive new
relevance requests, and an older runtime must not resume a database containing
accepted closeout intents without preserving their admission fence. Mixed-version
wire negotiation and the supported rollback path remain prerequisites before
external callers are enabled.
