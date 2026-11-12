# Task relevance receiver

The queue has an **internal, receiver-first** relevance operation:
`reconcile_task_relevance`. It is not exposed through HTTP or CLI and no emitter,
evaluator or periodic source calls it yet. Source adapters, registered-source
admission, wake delivery and actual session/workspace closeout remain tracked by
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
and resource records stay unchanged. Subsequent reports share that closeout;
retries return the original acceptance without repeating the audit or steer.
An accepted closeout blocks ordinary claim, spawn and execution admission,
including recovery of the original goal. It survives reset and requeue.
`needed` does not silently clear an already accepted closeout.

Every new report advances the audit revision, including `needed` and `unknown`.
Thus an older closure report cannot follow a newer accepted source observation
using its old task snapshot. Domain adapters must additionally fence their own
source revisions and reopened/new source episodes.

## Deliberately pending effects

`closeout_pending` and `pending_owner_closeout` mean **intent retained**, not
notification sent or resources finalized. `relevance_receipt` separates original
acceptance from current steering consumption. Consuming a steer does not clear
the closeout or prove native/workspace cleanup.

This slice does not wake suspended owners, select replacement agents, release
reservations or delete files. A later source slice must connect closeout to the
existing wake outbox and original-owner recovery, including unresolved transport
outcomes and actual terminal lifecycle acknowledgement.

No new wire contract or support capability is advertised. Existing clients and
ordinary tasks retain their behavior. A prior receiver must not receive new
relevance requests, and an older runtime must not resume a database containing
accepted closeout intents without preserving their admission fence. Mixed-version
wire negotiation and the supported rollback path remain prerequisites before
external callers are enabled.
