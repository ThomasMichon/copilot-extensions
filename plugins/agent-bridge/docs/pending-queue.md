# Inspect and reconcile a native session's pending queue

`attention` reports current input/permission/policy blockers. It is not an inbox
census. The separate `pending-queue` command reads the original backend inbox
without calling a claiming or consuming poll endpoint:

```text
<agent-bridge catalog argv[0]> pending-queue <native-session-id> --worktree-id <worktree> --json
```

Run it on the target's Bridge host. The target must be an exact native session
ID and its expected worktree binding, not a worktree alias or a cross-host
display label. `--limit` is 1-100 (default 50); pass `--after <next_after>` to
read the next page. Pages are fresh observations, not a frozen census. An
empty page means no unresolved backend records in that page's selection, not
an empty SDK queue or model consumption.

The writable database owner prepares an additive partial `(session_id, id)`
index for unresolved ordinary originals. Both page operations use that same
predicate, avoiding settled-history scans and sorting under the database lock.
An older read-only database without the index refuses this optional operation
until ordinary writable preparation; unrelated reads and the durable schema
version/receipts are unchanged. Inspection never builds the index itself.

The version-1 JSON contract is `agent-bridge.pending-queue`. It reports original
message IDs, admission/handoff/SDK stages, registration freshness, turn state,
current consumer incarnation and an independently fresh delivery-attention
observation when available. It excludes message bodies and controls. Accepted
but unconsumed records remain visible; absence of legacy consumption tracking
does not prove they are still queued. `sending` and `unknown` mark started
native effects that have not been confirmed submitted; an idle current receiver
re-attempts them (see below), so they are not a permanent state.
A started original whose bounded re-attempts were exhausted leaves
`messages` and is listed under `failed` with stage `failed`, code
`delivery_attempts_exhausted`, the attempt count and its failure time. Late
acceptance or consumption evidence for such an original removes it from that
list while its terminal disposition stays recorded.
That list is bounded (newest 20) and body-free. The existing hook's last emitted count can appear separately
when this serving process observed its diagnostic event in the last 60 seconds.
It is not a current set or a queue census; an observed consumer incarnation
must still match, and legacy notices without one say their binding is unverified.
Missing/stale notices remain unavailable. This bounded in-memory projection
retains no message bodies and does not require the optional telemetry writer.
Receiver-local pending sets and the native SDK queue remain unavailable.

## Explicit reconciliation

After inspecting the target and current consumer incarnation, an authorized
operator can prepare missing, unstarted original offers:

```text
<agent-bridge catalog argv[0]> pending-queue <native-session-id> --worktree-id <worktree> --reconcile --consumer-incarnation <observed-incarnation> --json
```

This is **original-offer preparation**, not a force-delivery command. It uses
the existing compatible-inbox owner and the same bounded original-ID page.
It attaches missing retained handoff identities to safe, unclaimed ordinary
records, preserving their IDs, order, bodies and existing receipts. Repeating
it does not recreate an existing offer. Concurrent normal polling retains the
same handoff and the receiver's one-shot begin grant.

It does not submit or resend native input, wake/replace a session, reset a
cursor, unmute delivery, clear a hook's pending notice or mark input consumed.
Already started, uncertain, SDK-accepted, runtime-custodied, startup and
interrupt records are left unchanged by reconciliation. The ordinary receiver
still owns all delivery, idle re-attempts and final admission. A new/current
incompatible receiver, changed worktree, stale registration or non-serving
owner refuses before preparation. A muted receiver stays muted.

## Idle submission of started records

A record is not left in an ambiguous started state. When the session is idle,
a current receiver whose provider advertises `retained_delivery_reattempt: 1`
submits every ordinary queued record that is not yet `sdk_accepted`,
`consumed`, rejected or failed, including `sending`/`unknown` records started by
an earlier consumer incarnation of the same native session:

1. It first checks the session's own root user-message input for the record's
   retained `<agent-message ... msg-id=...>` header. A match reports
   `consumed` and nothing is sent.
2. Otherwise the backend grants a new attempt of the same original (same ID,
   body, order, handoff and receipt history) to the current enrolled receiver,
   records it in `live_message_attempts`, and the receiver submits it and
   reports `sdk_accepted`. Each attempt grant is one-shot, compare-and-set on
   the attempt number, and keeps the usual authentication, binding, current
   session, worktree and mute checks.
3. The original start counts as attempt one. A request beyond three attempts
   instead records the terminal `failed` disposition shown above.

If history cannot be read, nothing is resent and the check is retried on a
later idle tick; it is never a permanent "no resend". If the receiver goes
idle with queued records and every one of them proves already delivered, it
sends one short resume prompt for that idle episode only. An empty queue, a
real submission or a later tick of the same episode sends nothing, and a
receiver without re-attempt support never sends the resume prompt.

The command first reads the selected executing provider's
`GET /api/v1/live-sessions/pending-queue-contract` declaration, then pins its
request to that provider. An older or rolled-back provider returns an explicit
unsupported result before reconciliation, not an empty queue or a fallback
send. Old messaging and attention operations are unchanged; no synchronized
peer upgrade is required. Failures exit 3. Unknown arguments and page bounds
are parser errors. A failed mutation is never automatically replayed.

All routes retain the service's normal authentication. Queue operations also
use the retained-inbox local ingress boundary; proxied/network ingress is not
supported. No new daemon, inbox, message sender or receipt authority is added.
