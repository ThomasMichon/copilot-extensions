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
