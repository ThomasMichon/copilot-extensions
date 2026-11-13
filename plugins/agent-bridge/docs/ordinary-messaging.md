# Ordinary agent messaging

Back to [Agent Bridge](../README.md).

Ordinary messaging resolves a machine, repository command and live receiver,
commits one message to the existing coordinator database, and returns a receipt.
Installation ownership, frontend generation, process birth and process
incarnation are not messaging prerequisites. Authentication, destination scope,
supported endpoint/schema contracts and original message identity still matter.

## Address an existing receiver

These forms use the same receiver and inbox contract:

```text
<repo> bridge send --agent <agent-id> --message "hello"
ssh <machine> -- <repo> bridge send --agent <agent-id> --message "hello"
<repo> bridge send --machine <machine> --agent <agent-id> --message "hello"
```

Positional `send TARGET MESSAGE` remains supported. `TARGET` can be a directly
registered ACP/native session, an unambiguous active agent name, or a registered
receiver's managed worktree handle. A qualified
`live:<machine>:<project>:<worktree>` address disambiguates represented peers.
`--machine` never looks up the target against the caller's local agent catalog.
An ambiguous SSH environment requires an exact declared SSH alias.

A repository front door forwards its project selection to the remote carrier,
which invokes that repository's registered command rather than silently using
the machine-global Bridge. An older local provider without the scoped endpoint
returns an unsupported route before forwarding; there is no global fallback.
Follow-up receipts retain the same machine and project selection.

Raw send never creates, resumes, replaces or hands off a receiver. Explicitly
resume a dormant agent first. An already-running agent without a receiving
registration needs registration diagnosis, not an implicit new session.
`--force` is refused; `--queue` aliases `--delivery queue`.
For complex text, use `--prompt-file PATH` or `--prompt-file -` instead of
reconstructing quoting across shells.

## Admission, delivery and waiting

By default, send returns immediately after durable admission, not after a model
reply. JSON output includes `session_id`, `message_id`, `accepted`, `duplicate`
and the retained receipt when supported. A remote result also names its
`machine` and any selected `project`.

| Receipt stage | Evidence |
|---|---|
| `admitted` | The coordinator committed the original message. |
| `sdk_accepted` | The receiving API acknowledged that original invocation. |
| `consumed` | Attributed root input, or completion of that exact ACP prompt RPC, proves consumption. |

`--wait --wait-timeout SECONDS` adds a finite caller-side observation of the
already-admitted receipt. It never submits another message. A blocked receiver,
timeout, missing observer capability, disconnected observer or malformed
observation does not turn accepted admission into refusal.

```text
<repo> bridge delivery-receipt <session-id> <message-id> --timeout 30
<repo> bridge delivery-receipt <session-id> <message-id> --machine <machine> --timeout 30
```

`--wait-reply` remains a separate, optional wait for the original message's own
reply. ACP reply observation follows the turn linked to that queue row, never
whichever unrelated turn happens to finish next.

An unconfirmed send exits **70** with `outcome: "unknown"` and
`accepted: null`; it is not a refusal or permission to resend. Preserve the
original target and caller operation key. If the message ID is available,
observe that receipt; otherwise inspect the existing session/inbox. HTTP and
carrier send attempts do not automatically replay after connection loss.
An explicit identical keyed request, when independently warranted, returns the
existing row rather than admitting another operation.

## Receiving and recovery

ACP creation registers a connected receiver. The existing heartbeat refreshes
presence and receives queued input independently of installation-governance
maintenance. Stop withdraws its live registration; raw send cannot undo it.
The adapter uses the existing ACP turn owner and the same `live_messages`
queue, receipts and one-shot handoffs as represented SDK receivers.

Mux/interactive sessions register while alive through their receiving extension.
Legacy receiving remains available. Selected
[retained SDK receiving](retained-sdk-consumer.md) preserves the v1 wire shape:
its installation/epoch and process fields are compatibility observations, not
proof required for ordinary admission or delivery.

An offered message retains its original handoff. A receiver reload can finish
an unstarted offer without substituting a new receipt. Once final handoff begins,
a lost acknowledgment or uncertain SDK/ACP outcome never authorizes another
send. Reconnection observes the original result. Actual root-input observation
can release an ACP delivery wait before its turn ends; providers without that
optional observation retain unknown attention until stronger evidence arrives.

Schema **47** prepares compatible admission and the optional unique
`turns.live_message_id` link through the existing writable migration owner.
It preserves queue payloads and original receipts. A closed lifecycle epoch
still fences lifecycle records, but not ordinary messages or their linked
already-admitted ACP turns. Passive owners never migrate storage.
Older HTTP callers and supported v1 receivers retain their existing fields;
an executable that cannot open the prepared schema is an incompatible database
owner, not a reason to discard messages or recreate the database.

This behavior changes neither deployment authority nor independent lifecycle,
retirement, credential, startup-plugin or explicit native-invocation contracts.
