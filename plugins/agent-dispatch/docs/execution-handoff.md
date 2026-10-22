# Execution effect adapter

Back to [agent-dispatch](../README.md).

`agent_dispatch.execution_effect_adapter` defines the optional adopter boundary
from the reviewed [execution handoff contract](../../../efforts/active/acknowledged-execution-handoff/contract.md).
This is a construction-stage library API with a bounded coordinator sender
integration, not an operator switch or a live handoff command. Execution outside
the explicitly qualified sender cohort is unchanged.

## Ownership

The authoritative coordinator resolves `ExecutionScope` from its installation,
coordinator, repository lane and producer registration. Constructing that value
does not authenticate a caller or authorize ownership transfer. Labels alone
are not a scope resolver.

`EffectRevocation` identifies an existing durable transition operation, the
retiring generation and both execution modes. The caller must persist that
operation before invoking the adapter and reuse it after a lost response.
The adapter cannot mint task claims, replace a workspace, increment a transition
generation or grant destination execution.

The adopter implements asynchronous `protocol_version(scope)` and
`revoke(request)`. Version 1 means that revocation serializes against its final
mutation boundary, including effects prepared before admission closed.
Already accepted effects must finish, be safely cancelled with a known outcome
or remain unresolved. Repeating a request observes the same operation; a
conflicting operation must not replace it. Domain outcomes stay in the adopter's
existing authority, not a second Dispatch effect store.

## Response handling

Call `observe_effect_revocation(adapter, request)` to negotiate support and
validate the response:

| Result | Meaning |
|--------|---------|
| `pending` | Revocation is not yet settled; detail explains the remaining work |
| `blocked` | An explicit blocker prevents acknowledgement |
| `unsupported` | No compatible adapter exists for this scope/version |
| `acknowledged` | Effect admission is closed, enumeration is complete, and no unresolved effect or legacy writer remains |

An unknown admission state, incomplete enumeration, ambiguous accepted effect
or legacy writer cannot produce `acknowledged`. Every response must match the
entire request, including installation, coordinator, repository, producer,
operation, modes and generation. Old or other-scope replies raise
`EffectAdapterError`. Unsupported versions never invoke revocation. Transport
errors and timeouts propagate; they are not successful empty drains.

Even a valid effect acknowledgement is only one prerequisite. The coordinator
still must close execution admission, account for all assignments, persist the
current transition and fence final destination admission. Handback needs a new
operation and generation through the same barrier, not revival of an old
capability.

## Construction and validation boundary

Focused deterministic tests in `tests/test_execution_effect_adapter.py` exercise
receipt validation, unsupported capability, retry identity, both directions,
scope isolation, stale generations and visible transport failure. They do not
prove a concrete adopter's mutation serialization or a live coordinator drain.
The [effort](../../../efforts/active/acknowledged-execution-handoff/README.md)
retains durable coordinator/supervisor integration, adapter-owned final-effect
enforcement, crash recovery and isolated process/handback qualification.
No deployment, schema migration or process action is part of this API.

## Durable admission-closure construction

The internal `TaskQueue.close_execution_admission()` construction primitive
records an initial automatic-to-manual request in the **existing queue DB**,
under the same write lock as task claims, starts/resumes, spawn reservations
and wake-delivery claims. It requires an owner-resolved qualified scope, the
exact queue identity and a current managed producer generation. Producer
generation and execution generation are separate. Labels are not resolution.
An identical retry returns the retained operation; another scope or operation
cannot overwrite it. New natural work may still be recorded, but these queue
admission paths cannot execute it after closure.

`execution_closure_inventory()` reads all matching tasks, reservation history
and wake records in one read transaction without a pagination limit. Detached
waiter deliveries are exposed separately as `run_waiter_wakes`, including
pending records and already claimed delivery identities and leases. It does
not omit terminal tasks' retained allocations, relabel legacy work as safe or
infer process exit from task state. Already accepted launches or deliveries
remain unresolved until their actual runtime is reconciled.

Create-and-claim, steering-triggered resumes and evaluator rejection restoring
submitted-task ownership use the same admission fence;
exact accepted-request replays remain readable without repeating those effects.
An already-started task also preserves its existing start and non-adopting resume
replays, with the same owner and optional generation/session checks. A new start,
resume or owner adoption is not a replay and remains fenced.
Both ordinary and detached-waiter wake claims exclude closed scopes while
leaving unrelated scopes eligible. Detached waiter claim-release-only records
remain deliverable: they release the retained hibernation worktree claim without
resuming its owner. Terminal completion and housekeeping are not new execution.

This remains a construction API with **no closure HTTP/CLI exposure**, acknowledgement,
manual destination admission, cancellation, handback or automatic reopening.
Its inventory always reports `acknowledged: false`, including an empty scope,
and names the missing runtime-enrollment, legacy-association and adopter-effect
barriers. [Authenticated scope resolution](#authenticated-scope-resolution)
provides installation and producer provenance, not runtime admission or
permission from a caller constructing a scope value. Existing unconfigured
execution is unchanged.
Do not call this primitive against a live queue to simulate a supported switch.

## Reservation-send custody construction

The internal `TaskQueue.admit_execution_send()` records one reservation-bound
launch, resume, delivery or redrive stage before a future runtime sends it.
Admission uses the same queue write transaction as scope closure and validates
the queue identity, managed producer generation, current task generation and
retained reservation. `ExecutionSendIdentity` also binds the qualified scope,
execution generation, runtime identity, stage and durable send ID. This first
construction supports only automatic generation 1.

A newly admitted result has `replayed: false`. An exact retry returns the
existing operation with `replayed: true`, even after closure or restart: it is
observation, **not permission to resend**. A different send ID cannot replay the
same reservation/task-generation/stage. A different qualified producer scope
cannot close admission while hiding previously recorded sends.
Both retained closures and custody bind the qualified scope to its repository
and source, including when closure precedes the first send. Producer-generation
rollover does not rebind that scope: fresh operations require the current
producer generation, while older send identities and outcomes remain retained.

`settle_execution_send()` retains explicit transport evidence as `sent`,
`not_sent` or `unknown`. Pending and unknown outcomes survive restart and closure;
timeouts cannot establish absence. A known outcome cannot be overwritten by a
contradictory or late result. A successful send is not proof of task completion,
process exit or adopter-effect settlement. The closure inventory retains every
send, including those associated with terminal tasks.

New spawn attempts, operator rearm and fresh-conversation authorization refuse
pending or unknown custody for the same task or shared exclusive execution key.
New reservations also check the retained logical workspace across task IDs and
allocation revisions. A failed or settled reservation does not establish
transport absence. The refusal names the retained send for reconciliation;
exact active-reservation observation still returns no new send permission.
Explicit `sent` or `not_sent` settlement removes only this ambiguity fence:
existing task, holder, allocation and recovery rules still apply, and a known
send outcome is not process-exit evidence.

Fresh custody now requires a retained coordinator runtime enrollment matching
the exact qualified scope, repository, source and current producer generation,
checked inside the same admission transaction. An arbitrary runtime string,
another source's runtime or a retired producer's enrollment cannot admit a new
send. Existing custody remains observable and settleable without retroactively
enrolling a legacy runtime; replay never grants another send. This accounting
check does not authenticate the in-process caller or prove process liveness.

The [coordinator-owned CI repair path](#coordinator-owned-ci-repair-sends)
uses this accounting for launch and task-turn delivery in already qualified
scopes. Complete runtime enrollment, other authenticated sender integration,
fresh and already admitted launches,
strict resumes, ordinary/restricted transports, redrives and both wake outboxes
remain gates. Runtime callers must enforce the new-versus-replayed distinction
at their actual send boundary and reconcile ambiguous responses through the
existing transport authority. No HTTP/CLI control, acknowledgement, destination
admission or handback is exposed; `acknowledged` remains false. The internal
accounting alone must never be advertised as universal execution fencing.

## Authenticated scope resolution

The control-only `POST /execution-scopes/resolve` records a qualified binding
for an **already managed** `(repo, source)` producer registration. It accepts
only that selector and its expected current producer generation, never a
caller-provided installation, coordinator, scope or supervision label. It
requires configured control authority even when legacy data ingress is open;
data credentials and task-scoped credentials cannot use it.

The coordinator reuses canonical installation-governance receipt validation
before and after binding. Missing receipts, legacy mode, maintenance, generation
drift and foreign provenance refuse resolution. A stable installation identity
is derived from the validator's canonical receipt, marketplace and plugin;
mutable receipt generations and legacy ownership provenance remain bound
separately. The queue resolves its own retained identity and derives the stable
producer-registration identity from the stored repository/source tuple, not
from a replaceable producer generation or a what-to-run supervisor record.

Bindings are retained in the existing queue DB under its write transaction.
Exact retries observe the same binding, including after restart. A producer
rollover requires its current generation without changing scope identity.
Another installation, changed receipt provenance or an incompatible historical
closure/send scope cannot silently rebind the producer or hide its history.
Receipt drift detected after insertion returns an explicit conflict, not a
capability; the retained binding is not deleted as a response-loss workaround.

This endpoint resolves provenance only. It neither enrolls a runtime nor
admits a send, closes execution, acknowledges a transition, transfers a task
or activates a destination. Responses explicitly report `runtime_enrolled:
false` and `acknowledged: false`. Runtime enrollment and integration of every
actual sender and both wake outboxes remain required before any supported
handoff control or universal-fencing claim.

## Coordinator runtime enrollment construction

The control-only `POST /execution-scopes/enroll-coordinator` accepts the same
managed producer selector as scope resolution. It records **this coordinator's**
process, using the existing OS process-start observer, a server-created
application incarnation and canonical installation governance checked before
and after insertion. Callers cannot supply a PID, runtime identity or scope.
Data/task credentials and an unconfigured control authority cannot enroll.
The application incarnation is not a native conversation UUID.

Enrollment extends the existing queue's scope accounting, not a separate
process registry or scheduler. The same write transaction as admission closure
retains each producer-generation-qualified runtime. A replay observes the same
record, including after closure, without permission to send. A new enrollment
after closure is refused. PID reuse, application restart and producer rollover
cannot erase or reuse an old incarnation; retained records survive queue
reopening and remain in the complete closure inventory. A failed final
provenance recheck returns an explicit conflict and preserves the accounting,
rather than deleting a possibly relevant participant.

`runtime_enrolled: true` covers only that recorded coordinator. It does not
assert the process has exited, that other runtimes are enrolled or that a
sender uses custody. `send_admission` and `acknowledged` remain false. Supervisor
enrollment, all actual launch/resume/delivery/redrive boundaries, both wake
paths, legacy qualification, effect settlement and handback remain open.
Execution outside the bounded sender cohort is unchanged and no live transition
command is exposed.

## Coordinator-owned CI repair sends

For a producer scope already qualified through the control-only resolver, the
coordinator-owned CI repair route now enrolls its own process and records custody
immediately before the idle-body launch and the subsequent task-seed delivery.
Both stages share the application's server-created incarnation. Canonical
installation governance and process identity are rechecked before custody;
caller-provided runtime or installation labels are never used. Producer rollover
between stages refuses the old in-flight continuation instead of silently
adopting it into the replacement producer generation.

Launch admission follows the independent CI policy probe. A refusal before
transport creates no ambiguous send record. Once custody is admitted, a successful
launch response with a session identity or an accepted delivery records `sent`.
Timeouts, transport errors, nonzero launch results, missing session identities
and refused delivery replies remain `unknown`, not `not_sent`. If settlement
itself fails, the persisted `pending` record remains a retry fence. Neither
outcome establishes task completion or process exit.
If launch returned a usable session before settlement failed, the factory
preserves that handle and the route requests release without delivering the task
turn or clearing pending custody.

Scope closure and custody admission retain their queue-transaction ordering:
closure first prevents transport; custody first retains an in-flight participant
that blocks acknowledgement. Closure after idle launch but before task delivery
refuses the task turn and preserves the existing release obligation and session
handle. Replaying an active reservation or an admitted stage never sends again.

Unqualified legacy scopes and other sender paths retain their existing behavior.
A missing binding cannot erase historical qualification retained in runtime,
send or closure records. Such history still requires canonical provenance rather
than falling back to the legacy transport path. This cohort is not complete
legacy reconciliation or universal execution fencing. Supervisor senders,
strict resumes, redrives, both wake paths, adopter effects and handback remain
open, and no closure/acknowledgement control is exposed.

`tests/test_coordinator_execution_sender.py` exercises the actual route and
factory against a synthetic queue, installation authority and Bridge transport,
including admission-before-send, closure between stages, ambiguous outcomes,
provenance refusal, producer rollover and retry refusal. It does not launch
real workers or establish installation or running adoption.
