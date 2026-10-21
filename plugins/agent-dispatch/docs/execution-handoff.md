# Execution effect adapter

Back to [agent-dispatch](../README.md).

`agent_dispatch.execution_effect_adapter` defines the optional adopter boundary
from the reviewed [execution handoff contract](../../../efforts/active/acknowledged-execution-handoff/contract.md).
This is a construction-stage library API, not an installed coordinator
capability, an operator switch or a live handoff command. It does not change
ordinary Dispatch execution.

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

This remains a construction API with **no HTTP/CLI exposure**, acknowledgement,
manual destination admission, cancellation, handback or automatic reopening.
Its inventory always reports `acknowledged: false`, including an empty scope,
and names the missing runtime-enrollment, legacy-association and adopter-effect
barriers. Installation identity, registration-to-source resolution and control
authentication belong to the future owning coordinator integration, not to a
caller constructing a scope value. Existing unconfigured execution is unchanged.
Do not call this primitive against a live queue to simulate a supported switch.
