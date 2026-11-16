# Original native-send settlement foundation

Back to the [delegation contract](delegation-contract.md).
The [implementation plan](../../../efforts/active/task-relevance-reconciliation/native-wake-settlement.md)
retains the complete delivery and adoption gates.

## Current source boundary

`native_send_settlement.settle` is an internal Bridge owner primitive. It has
no CLI, HTTP route, advertised capability or Dispatch consumer yet. It is not
an operational recovery command. Existing receipt-only observation remains
read-only and returns no admission receipt on a miss.

The primitive validates the exact current session/native holder under the
existing session lock. Its database transaction either returns the original
accepted native-send receipt, re-observes the same request-bound settlement,
or qualifies original non-admission and persists a late-admission exclusion.
It does not resume a session, submit a prompt, fabricate a turn or change task
or workspace state.

Non-admission requires the original completed local workspace birth, matching
native identity and workspace binding, a contiguous keyed-native turn history,
and corroborating running-turn events. Every retained turn's actual admission
document and receipt must validate. Legacy unkeyed input, changed or recreated
native identity, missing/gapped/inconsistent history, invalidated event
continuity and an already admitted identical prompt under another key refuse.
The history scan admits at most 100 turns and 1,000 session-state events;
overflow refuses rather than treating a truncated view as complete.

This proof relies on the native admission owner's write-before-prompt
invariant and its original authoritative database. It cannot certify a
reconstructed or selectively rewritten history as the original one.
Ordinary session status, age, a missing receipt or the exclusion by itself
does not prove the past.

## Persistent admission boundary

Schema 48 retains settlements in the existing Bridge database. Full-sync
settlement and native turn insertion serialize through SQLite transactions.
Turn insert/update triggers reject a settled original delivery key even when
an older writer does not know the new Python helper. Settlement records cannot
be replaced, updated or removed through ordinary writes.

An admitted turn wins as accepted; a valid exclusion wins before a later turn
can be recorded and dispatched. A negative result concerns prompt admission,
not quiescence of a separately admitted startup/resume operation or completion
of application work. Those effects retain their existing owners and custody.
No later prompt or retry is authorized by this result alone.

Writable bootstrap migrates schema 47 additively. A schema already stamped 48
must have its exclusion intact before bootstrap continues; missing or changed
protection refuses instead of being silently recreated. Read-only database
open validates the same protection. Historical read-only runtimes requiring
their exact prior schema refuse schema 48; no rollback may remove the
exclusion to make an older reader succeed.

## Qualification and remaining work

The selected private-container tests exercise original positive/negative
outcomes, request/native mismatch, incomplete history, restart, damaged
protection and an independent older-shaped SQLite writer on both sides of
settlement. They use a synthetic actor, not a native Copilot runtime.
The process packet is not admitted into required actor-free unit CI.

Authenticated front doors, runtime support negotiation, conditional Dispatch
consumption, complete current/prior runtime rollback coverage, source release,
authorized adoption and actual original-flow settlement remain separate gates.
Nothing automatically settles an existing unknown operation.
