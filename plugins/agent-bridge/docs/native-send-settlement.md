# Original native-send settlement

Back to the [delegation contract](delegation-contract.md).
The [implementation plan](../../../efforts/active/task-relevance-reconciliation/native-wake-settlement.md)
retains the complete delivery and adoption gates.

## Explicit supported operation

The internal owner foundation merged in #12793. The supported front door
extends ordinary `send` with an explicit settlement mode. This is a mutating
control operation, not a passive diagnostic or permission to replay a prompt.
Existing receipt-only observation remains read-only and returns no admission
receipt on a miss.

Discover the executing CLI's `agent-bridge.send` version-1 contract and
`native-send-settlement` capability without contacting a daemon:

```text
<agent-bridge catalog argv[0]> version --contracts-json
```

For an authorized original-key settlement, retain the original Bridge handle,
native identity, operation key, caller and exact prompt:

```text
<agent-bridge catalog argv[0]> --json send <bridge-session> --expected-native-session-id <native-session> --idempotency-key <original-key> --caller <original-caller> --prompt-file original-prompt.txt --settle-native-send --no-wait
```

The CLI uses existing-client discovery without ensure. Before the mutation,
the selected running daemon must advertise HTTP 51 or later and the optional
`native_send_settlement: 1` capability. HTTP generation remains 52. Unsupported
providers refuse before POST; there is no ordinary-send, receipt-only,
creation, resume or other-target fallback. Alternate delivery modes cannot be
combined with settlement.

The authenticated endpoint is
`POST /api/v1/sessions/{bridge-session}/turns/native-bound/send/settle`.
It accepts the existing `NativeSendPrompt` request: `invocation_key`,
`expected_native_session_id`, `prompt`, optional original `caller_id`, and
optional original `no_resume`. Identities and request digests prevent
wrong-target or changed-request settlement; they do not grant authorization.

The primitive validates the exact current session/native holder under the
existing session lock. Its database transaction either returns the original
accepted native-send receipt, re-observes the same request-bound settlement,
or qualifies original non-admission and persists a late-admission exclusion.
It does not resume a session, submit a prompt, fabricate a turn or change task
or workspace state.

Success exits zero with `outcome`/`admission` equal to `accepted` or
`not_admitted`. Acceptance retains the original turn index. Non-admission
includes `fenced: true` and a request-bound digest, without a turn index.
A changed request or unavailable original history refuses, preserving the
unresolved operation. Drain is rechecked under the session lock before
settlement and refuses without effects.

Lost, malformed, contradictory or post-commit server responses exit 70 with
`outcome: unknown` and `reason: native_send_settlement_unknown`. The request
is not automatically retried or downgraded. Preserve the original operation;
an explicitly authorized repeat of that same settlement can recover its
persisted outcome without submitting a prompt. Never resend the original
prompt to resolve a lost settlement response.

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

The selected private-container tests exercise the real parser, running
capability discovery, HTTP owner and original journal; unsupported peers,
unknown acknowledgements, explicit outcome recovery, authentication and drain
refusals; and the underlying original positive/negative outcomes,
request/native mismatch, incomplete history, restart, damaged protection and
independent older-shaped SQLite writer on both sides of settlement.
They use a synthetic actor, not a native Copilot runtime.
The process packet is not admitted into required actor-free unit CI.

Conditional Dispatch consumption, complete current/prior runtime rollback
coverage, source release, authorized adoption and actual original-flow
settlement remain separate gates.
Nothing automatically settles an existing unknown operation.
