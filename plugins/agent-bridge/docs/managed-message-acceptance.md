# Durable managed-message acceptance

The additive `durable_managed_messages: 1` capability selects
`POST /api/v1/sessions/{id}/messages`. This is ordinary input acceptance for
an existing managed agent, not immediate turn admission. The existing
`/turns`, native-bound and stop-notice contracts are unchanged. This provider
and Python-client slice does not yet change the default CLI resolver or add
remote forwarding.

The path uses the canonical Bridge session ID. Resolve a user-facing selector
once and retain that ID for the original operation; this endpoint never follows
a native-ID alias to a different managed target during receipt recovery.

The request contains `prompt`, `idempotency_key`, optional `caller_id`, and
optional `expected_native_session_id`. A known stopped, busy or temporarily
disconnected agent remains a valid destination, including while the daemon
drains. Acceptance never resumes, creates, replaces or hands off a session.
Unknown targets and malformed requests are refused. A storage failure returns
`managed_message_write_unconfirmed`, never guessed acceptance.

## One input, original receipt

Before any carrier inspection, one FULL-synchronous SQLite transaction writes
the input to the existing `pending_prompts` queue and its original-key receipt
to `managed_message_receipts`. The receipt journal is not another inbox:
it has no deliverable body and no independent consumer. It retains the
original queue ID, target, key, request digest and native binding after the
pending row is consumed, explicitly removed or its target is deleted.

The 202 receipt contains `session_id`, `queue_id`, `idempotency_key`,
`native_session_id`, `queued: true`, `duplicate`, `delivery_state` and
`turn_index`. `queued: true` records the original durable queue acceptance;
the current stage is `delivery_state`:

| State | Evidence |
|-------|----------|
| `queued` | The original input awaits its owning consumer |
| `handoff_committed` | The queue-to-turn transaction committed the original turn index; SDK submission, consumption and completion are **not** implied |
| `target_changed` | The selected native conversation no longer matches at consumer handoff; the old input was not retargeted |
| `removed` | The queue entry was explicitly removed or its managed target deleted |

Repeating the exact original target, key and input recovers that receipt,
including after consumption or removal. Changing an input under the same key
is a conflict. An ambiguous response is not permission to choose another
target, key or turn-submission path. `BridgeClient.submit_managed_message`
performs fresh capability preflight and one mutation attempt, without a
reconnect, automatic retry or legacy fallback.

## Existing consumer and lifecycle owners

After acceptance, the response's background wake asks the existing ACP queue
consumer to drain only if its current carrier is idle and running. Turn
settlement and explicit resume already drive that same drain. The existing
governed heartbeat also checks resident, idle, live carriers for an original
queued receipt; this recovers a response lost between commit and wake, without
requiring the sender to replay input or creating another delivery worker.
Stopped or unavailable carriers remain dormant.

At handoff the consumer checks both its actual native identity and the stored
session identity against the original binding. Queue removal, terminal
receipt and turn insertion commit together with FULL synchronization. A failed
turn write retains the original queue entry. After a committed handoff,
uncertain effects stay with the existing turn/effect owner; they are never
re-enqueued from the receipt journal.

## Compatibility, preparation and rollback

Prepare the provider before clients select this additive capability. A prior
provider or rollback without it refuses before a client mutation. Authentication
and supported HTTP/schema negotiation still apply; carrier admission and
process-generation observations do not precede the durable write.

Schema 51 adds the receipt journal and pending-state index without rewriting
legacy queued inputs. Writable migration is required; read-only acquisition
and candidate adoption validate the prepared schema without repairing it.
Original-key facts and terminal receipts cannot be overwritten or deleted.
The pending-row guard prevents an older consumer from deleting or changing a
new managed input without the new atomic handoff, rather than silently
bypassing its native-target fence. Legacy unjournaled pending inputs retain
their existing behavior. Rollback must keep the prepared storage and owner
protections; do not strip guards, delete receipts or copy pending input into
another queue.

Controlled tests use synthetic sessions and inert ACP clients. They cover
unavailable carriers, stopped/busy/draining states, original-key recovery,
explicit removal, old-native settlement, failed durable and turn writes,
mixed-version deletion exclusion, consumer wake recovery and one-attempt
client failures. They do not qualify an installed SDK, authorize live queue
replay, deploy the suite or restart another session.
