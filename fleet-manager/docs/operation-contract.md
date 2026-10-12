# Operation intent and target receipt - first contract

Back to [fleet-manager](../README.md) and
[Fleet Control V2](../../efforts/active/fleet-control-v2/README.md).

**Shapes only.** These records do not authenticate, authorize, persist,
schedule, execute or settle any operation. No resident Gateway/node or new
production task queue is installed. They are the first bounded seam of #6241,
not its transactional ledger or distributed fencing proof.

`fleet_manager.operations.OperationIntent` and `TargetReceipt` retain immutable
canonical bytes; exports are independent dictionaries. `from_dict` and direct
construction both validate the strict bounded schema. Shared JSON limits are
1 MiB, depth 12, 256 entries per object/array, duplicate-key refusal and finite
numbers. No supplied credentials, executable, URL, shell or arbitrary payload
fields are allowed. All parameters are bounded non-secret references.

## Intent

Schema `fleet-manager.operation-intent`, version 1:

| Field | Meaning |
| --- | --- |
| `operation` | Caller-selected operation ID, stable across retry |
| `authority` | Canonical coordination domain |
| `principal`, `account` | Explicit actor/account references, not authentication proof |
| `target` | Canonical target reference within that authority |
| `generation` | Positive expected allocation generation |
| `command`, `parameters` | Fixed named operation and its reference-only shape |

All references use the shared bounded identifier syntax. Generation is an
integer 1 through `2**63-1`, not boolean.

Supported shapes are `task.submit: {objective}`,
`workspace.allocate: {repository, objective}` and
`prompt.deliver: {session, message_ref}`. Supporting their shape does not deliver
their execution; the `fleet-manager` CLI exposes no execute/admit command.
Referenced messages, objective content, repository configuration and authorization
must be resolved through a future trusted owner, not from record text.

`request_digest` is SHA-256 of the full canonical intent, including ID and scope.
`check_repeat(candidate)` requires the same authority/principal/account/operation
and byte-identical canonical content. A different target, generation or parameter
under the same identity is rejected. Distinct identities are not compared as
duplicates. The comparison has no durable memory and makes no deduplication claim.
An authenticated server must establish the principal/account namespace itself,
never trust these supplied references as admission authority.

## Receipt

Schema `fleet-manager.target-receipt`, version 1: the same scope and `command`,
plus `request_digest`, positive `sequence`, `status` and `result_ref`.
`check_intent(intent)` compares every scope/generation/command field and digest.

`accepted` claims target acceptance only; `succeeded`/`failed` claim effect
outcome and require a bounded outcome reference; `unknown` reports uncertainty.
Accepted/unknown receipts must have null outcome reference. Successful effect
outcome is not parent-objective completion or released resources.

Sequence is an ordering claim, **not persisted monotonicity**. Consumers must
authenticate target attribution, maintain generation/sequence state, refuse
contradictory transitions, reconcile out-of-order receipts and enforce fences at
the actual effect owner. These records do not perform any of those duties.
A Gateway DB generation cannot revoke a disconnected target by parsing a receipt.
UNKNOWN is not permission to automatically replay a non-idempotent effect.

## Next persistence boundary

The next slice must atomically persist admitted intent, scoped claim, audit and
outbox; nodes independently persist acceptance/deduplication and target receipts.
Reconciliation replays receipts, not effects. No transaction spans Git, SSH or
prompts. Authorization, resource-owner fencing, mixed-version compatibility and
partition reassignment remain explicit reviewed/runtime proof gates.

The first [local operation journal](operation-ledger.md) implements explicit
intent/outbox/audit and receipt transactions without executing effects. Node
persistence, authenticated admission, claims and resource fencing remain later
proof gates.

This contract stays inside the standalone product until its mutation protocol
and lifecycle are proven. Existing `agent-fleet-contracts` remains the owner of
driver/registration/fixed-service routing records; no incompatible edit or
duplicate routing/auth implementation is introduced here.
