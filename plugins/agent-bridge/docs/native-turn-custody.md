# Internal original-turn native custody

Back to the [runtime custody foundation](runtime-custody-foundation.md) and
[restoration effort](../../../efforts/active/bridge-retained-runtime-restoration/README.md).
This is an internal DB owner foundation, not an enrolled native transport,
supported HTTP contract, restoration command or deployment authorization.

## Original admission, never retrospective enrollment

An internal owner may supply `native_consumer` together with `runtime_admission`
to `Database.create_turn`. The consumer carries the exact Bridge/native session,
transport incarnation and process PID/birth observations. The existing DB
transaction retains a single immutable handoff on the original turn, including
the atomic queued-row consumption and keyed-native paths, before returning.
The ordinary caller default remains unqualified and unchanged.

Consumer observations do not authenticate a caller or prove live native custody.
The actual transport must establish its owner reference and recheck those
observations at its effect boundary before relying on this DB primitive.
Neither the ordinary SessionManager prompt task nor the synchronous handoff
authoring transport is wired or enrolled by this foundation.

An existing incomplete turn may already have been sent. A producer association,
an absent completion timestamp or a lost native reply does not prove otherwise.
Migration therefore leaves every old handoff null. No update can attach a new
handoff to an existing turn, including through a keyed retry. A retry selected
with native custody must find the original matching consumer handoff; otherwise
it refuses rather than upgrading old history.

`prepare_native_turn_handoff` only observes a handoff already retained by that
original insertion. It checks the current installation/epoch, enrolled frontend,
original producer association, current DB native context and expected prompt.
It writes nothing and does not mint a handoff or send permission.
The separate original receipt reader remains usable after closure.

## One-shot final admission and original outcomes

`begin_native_turn_handoff` repeats the installation, current open epoch,
producer association, exact original consumer, DB native context and prompt
checks under the existing SQLite write transaction. A true return is for this
invocation only. The original started marker commits with `synchronous=FULL`
before returning; a second invocation cannot receive true, even after process
exit or a lost first reply.

The interval between that commit and the future native call is outstanding
custody, not evidence that the call did or did not happen. Closure can refuse
a not-yet-admitted effect, but cannot declare an admitted attempt settled.
Unknown acceptance never becomes a retry, another turn or a new conversation.

`record_native_turn_handoff` records `native_accepted`, attributed `consumed` or
`unknown` against the original consumer, turn and handoff ID. Reporting remains
available after epoch closure or DB native-context replacement. Acceptance and
consumption have separate retained timestamps; neither certifies semantic
completion. A completed turn cannot erase or settle the handoff.

`read_native_turn_handoff` returns original metadata and outcome timestamps,
never prompt text or fresh send permission. Prepared passive reads perform no
initialization, enrollment, schema repair or event-writer startup.

## Storage and qualification limits

Schema 34 adds nullable fields to the existing `turns` rows, not another queue
or operation ledger. Normal writable migration prepares exact guards before
publishing the version. Passive adoption and already-current writable opens
refuse missing or changed fields/guards instead of reconstructing history.
The guards preserve original turn identity, prompt, delivery key and keyed
receipt, forbid retrospective handoffs, and retain one-shot/outcome observations.
All custody mutations use the existing full-durability transaction owner and
restore the prior connection policy.

Full inventory includes every original turn's native handoff and outcomes,
including terminal and unknown work, and rejects malformed associations. It
continues to report native settlement, other producer cohorts, host/remote
custody and target compatibility as unqualified. No partial storage observation
acknowledges complete runtime closure or target restoration.

The bounded development tests exercise actual SQLite serialization, normal and
adopted owners, frozen legacy migration, strict passive refusal, original keyed
receipts and a real process exit after one-shot admission. These are source
controls without a native SDK/ACP conversation. Actual owner enrollment,
transport integration, attributed native evidence, platform qualification and
guarded installer restoration remain separate open gates.

The current HTTP-50 source capture records schema 34 through actual receiver
observations in both fixture installation modes. Its HTTP generation and
capability floors are unchanged. The existing capture owner refreshes only its
owned current packets while retaining historical provenance; this does not
convert older captures into native-turn qualification.
